"""Changes in explicitly selected entries of an OFAC SDN publication."""
from dataclasses import replace
from datetime import datetime
import hashlib
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError

API = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/SdnList'
DATA_URL = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_XML.ZIP'
PAGE_URL = 'https://ofac.treasury.gov/sanctions-list-service'
NS = '{https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML}'
TYPES = {'Entity', 'Vessel', 'Individual', 'Aircraft'}
ENTRY_FIELDS = {'uid', 'lastName', 'sdnType', 'programList', 'idList', 'akaList', 'addressList', 'remarks', 'vesselInfo'}
LISTS = {
    'idList': ('id', 'ids', {'uid', 'idType', 'idNumber', 'idCountry', 'issueDate', 'expirationDate'}, {'uid', 'idType', 'idNumber'}),
    'akaList': ('aka', 'aliases', {'uid', 'type', 'category', 'lastName'}, {'uid', 'type', 'category', 'lastName'}),
    'addressList': ('address', 'addresses', {'uid', 'address1', 'address2', 'address3', 'city', 'stateOrProvince', 'postalCode', 'country'}, {'uid'}),
}
VESSEL_FIELDS = {'callSign', 'vesselType', 'vesselFlag', 'vesselOwner', 'grossRegisteredTonnage', 'tonnage'}


def _children(node, allowed, required=()):
    result = {}
    for child in node:
        name = child.tag.removeprefix(NS)
        if child.tag != NS + name or name not in allowed or name in result:
            raise SourceError('OFAC XML contains an unknown or repeated field')
        result[name] = child
    if not set(required) <= result.keys():
        raise SourceError('OFAC XML is missing a required field')
    return result


def _text(node):
    if len(node) or node.attrib or not isinstance(node.text, str) or not node.text.strip() or len(node.text) > 100000:
        raise SourceError('OFAC XML text field is invalid')
    return node.text.strip()


def _uid(node):
    value = _text(node)
    if not re.fullmatch(r'[1-9][0-9]{0,11}', value):
        raise SourceError('OFAC UID is invalid')
    return value


def _object(node, allowed, required=()):
    children = _children(node, allowed, required)
    result = {key: _text(value) for key, value in children.items()}
    if 'uid' in children:
        result['uid'] = _uid(children['uid'])
    return result


class OfacSdnSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (DATA_URL,)):
            raise ValueError('ofac_sdn accepts only the official SDN XML ZIP')
        if self.complete or 'removed' in self.events:
            raise ValueError('OFAC absence cannot establish a removal decision')
        self.entry_types = strings(config.options.get('entry_types'), 'entry_types')
        if set(self.entry_types) - {'Entity', 'Vessel'}:
            raise ValueError('entry_types supports Entity and Vessel')
        self.programs = strings(config.options['programs'], 'programs') if 'programs' in config.options else ()
        if any(not re.fullmatch(r'[A-Z0-9-]{1,100}', p) for p in self.programs):
            raise ValueError('programs must contain exact OFAC program codes')
        self.max_xml_bytes = integer(config.options.get('max_xml_bytes', 40000000), 'max_xml_bytes', 1024, 60000000)
        self.max_register_records = integer(config.options.get('max_register_records', 30000), 'max_register_records', 1, 50000)
        self.field_labels = {'name':'Navn', 'type':'Oppføringstype', 'programs':'OFAC-programmer', 'remarks':'Merknader',
                             'ids':'Identifikatorer', 'aliases':'Alias', 'addresses':'Adresser', 'vessel_info':'Fartøyopplysninger', **self.field_labels}

    def _read(self, url, *, metadata=False, download_path=None):
        request = self.post if metadata else self.get
        options = {'json': {}} if metadata else {}
        response = request(url, stream=True, allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308), **options)
        try:
            if response.status_code != 200:
                location = response.headers.get('Location', '')
                target = urlsplit(location)
                if (download_path is None or response.status_code != 302 or target.scheme != 'https'
                        or target.netloc != 'wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com'
                        or target.path != '/' + download_path or target.fragment):
                    raise SourceError('OFAC returned an unexpected redirect')
                return self._read(location)
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > (1000000 if metadata else self.max_bytes):
                    raise SourceError('OFAC response exceeds its byte bound')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def _metadata(self):
        try:
            data = json.loads(self._read(API, metadata=True))
            if not isinstance(data, list) or not 1 <= len(data) <= 100:
                raise ValueError
            rows = [row for row in data if isinstance(row, dict) and row.get('fileName') == 'SDN.XML']
            if len(rows) != 1:
                raise ValueError
            row = rows[0]
            size = row['size']
            hashes = json.loads(row['hashCodes'])
            expected = hashes['SHA-256']
            if (type(size) is not int or not 1 <= size <= self.max_xml_bytes or not isinstance(expected, str)
                    or not re.fullmatch(r'[0-9a-fA-F]{64}', expected)):
                raise ValueError
            archives = [r for r in data if isinstance(r, dict) and r.get('fileName') == 'SDN_XML.ZIP']
            if len(archives) != 1:
                raise ValueError
            archive = archives[0]
            path = archive.get('downloadLink')
            if (type(archive.get('size')) is not int or not 1 <= archive['size'] <= self.max_bytes
                    or not isinstance(path, str) or not re.fullmatch(
                        r'Published/[0-9a-f-]{36}/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9a-f-]{36}/SDN_XML\.ZIP', path)):
                raise ValueError
            return size, expected.lower(), path, archive['size']
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise SourceError('OFAC SDN.XML publication metadata is invalid or exceeds its bound') from exc

    def read_records(self):
        expected_size, expected_hash, download_path, zip_size = self._metadata()
        raw = self._read(DATA_URL, download_path=download_path)
        if len(raw) != zip_size:
            raise SourceError('OFAC ZIP size does not match publication metadata')
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if (len(entries) != 1 or entries[0].filename != 'SDN.XML' or entries[0].flag_bits & 1
                        or entries[0].file_size != expected_size or entries[0].file_size > self.max_xml_bytes):
                    raise SourceError('OFAC ZIP member or size does not match the publication')
                with archive.open(entries[0]) as member:
                    xml = member.read(self.max_xml_bytes + 1)
        except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, EOFError) as exc:
            raise SourceError('OFAC SDN ZIP is invalid') from exc
        if len(xml) != expected_size or hashlib.sha256(xml).hexdigest() != expected_hash:
            raise SourceError('OFAC XML checksum or size does not match the publication')
        try:
            decoded = xml.decode('utf-8-sig')
            if '\x00' in decoded or '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
                raise SourceError('OFAC XML contains forbidden declarations')
            root = ET.fromstring(decoded)
        except (ET.ParseError, UnicodeError, ValueError) as exc:
            raise SourceError('OFAC SDN XML is invalid') from exc
        return self._records(root)

    def _records(self, root):
        if root.tag != NS + 'sdnList' or root.attrib:
            raise SourceError('OFAC XML root changed')
        if any(child.tag not in {NS+'publshInformation', NS+'sdnEntry'} for child in root):
            raise SourceError('OFAC register envelope changed')
        pubs, entries = root.findall(NS+'publshInformation'), root.findall(NS+'sdnEntry')
        if len(pubs) != 1 or not 1 <= len(entries) <= self.max_register_records:
            raise SourceError('OFAC register is empty or exceeds its record bound')
        info = _object(pubs[0], {'Publish_Date','Record_Count'}, {'Publish_Date','Record_Count'})
        if not re.fullmatch(r'[1-9][0-9]*', info['Record_Count']) or int(info['Record_Count']) != len(entries):
            raise SourceError('OFAC published record count does not match the XML')
        try:
            if not re.fullmatch(r'\d{2}/\d{2}/\d{4}', info['Publish_Date']):
                raise ValueError
            datetime.strptime(info['Publish_Date'], '%m/%d/%Y')
        except ValueError:
            raise SourceError('OFAC publication date is invalid') from None
        seen, rows = set(), []
        for entry in entries:
            # Validate identity throughout the register, including excluded types.
            ids, types = entry.findall(NS+'uid'), entry.findall(NS+'sdnType')
            if len(ids) != 1 or len(types) != 1:
                raise SourceError('OFAC identity/type is missing or repeated')
            uid, typ = _uid(ids[0]), _text(types[0])
            if uid in seen or typ not in TYPES:
                raise SourceError('OFAC UID is duplicated or entry type is unknown')
            seen.add(uid)
            if typ not in self.entry_types:
                continue
            children = _children(entry, ENTRY_FIELDS, {'uid','lastName','sdnType','programList'})
            programs_node = children['programList']
            if not 1 <= len(programs_node) <= 100 or any(p.tag != NS+'program' for p in programs_node):
                raise SourceError('OFAC program list is invalid')
            programs = sorted(_text(p) for p in programs_node)
            if len(programs) != len(set(programs)):
                raise SourceError('OFAC programs repeat')
            if self.programs and not set(programs).intersection(self.programs):
                continue
            fields = {'name':_text(children['lastName']), 'type':typ, 'programs':programs,
                      'remarks':_text(children['remarks']) if 'remarks' in children else None,
                      'ids':[], 'aliases':[], 'addresses':[], 'vessel_info':{}}
            for tag, (item_tag, field, allowed, required) in LISTS.items():
                if tag not in children:
                    continue
                container = children[tag]
                if not 1 <= len(container) <= 2000 or any(c.tag != NS+item_tag for c in container):
                    raise SourceError('OFAC detail list is invalid or exceeds its bound')
                values = [_object(c, allowed, required) for c in container]
                if len({v['uid'] for v in values}) != len(values):
                    raise SourceError('OFAC detail UIDs repeat')
                fields[field] = sorted(values, key=canonical)
            if 'vesselInfo' in children:
                fields['vessel_info'] = _object(children['vesselInfo'], VESSEL_FIELDS)
            rows.append({'key':uid, 'title':'OFAC · '+fields['name'], 'url':PAGE_URL, 'published':None, 'fields':fields})
            if len(rows) > self.max_records:
                raise SourceError('Selected OFAC entries exceed max_records')
        return sorted(rows, key=lambda row: row['key'])

    def describe_change(self, name, before, after):
        label = self.field_labels.get(name, name)
        if name in {'ids','aliases','addresses'}:
            return f'{label}: revidert liste ({len(before)} → {len(after)} oppføringer); se kilden'
        if name == 'remarks':
            return f'{label}: endret; se kilden'
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']
        info = ('Nyobservert OFAC-listeoppføring' if event == 'added' else 'Endret OFAC-listeoppføring',
                f"OFAC-ID: {row['key']} · {fields['type']}", 'Programmer: '+', '.join(fields['programs']))
        if event == 'changed':
            info += tuple(d[:800] for d in details[1:])
        return replace(item, alert_details=info+('Listeopplysninger fra OFAC; første observasjon er ikke vedtaksdato',
            'Ingen vurdering av navnetreff, skyld eller rettslig anvendelse',))
