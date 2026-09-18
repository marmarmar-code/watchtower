"""DMP's complete published register of pharmaceutical wholesale authorisations."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from io import BytesIO
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError
from .workbooks import NS, table_rows

PAGE = 'https://www.dmp.no/tilvirkning-import-og-salg/import-og-grossistvirksomhet-med-legemidler/virksomheter-som-kan-importere/godkjente-legemiddelgrossister'
HEADERS = ('Autorisasjonsnummer', 'Dokumentnummer', 'Foretak', 'Organisasjonsnummer',
           'Lokasjon', 'Tillatelsestype', 'Gyldig fra', 'Gyldig til')
SHEET = 'Godkjente grossister'


def official_download(source, url):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'www.dmp.no' or parsed.query or parsed.fragment:
        raise SourceError('DMP document leaves the official host')
    response = source.get(url, stream=True, allow_redirects=False)
    try:
        if response.status_code != 200:
            raise SourceError('DMP document redirected or failed')
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > source.max_bytes:
                raise SourceError('DMP document exceeds max_bytes')
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        response.close()


def clean(value):
    return ' '.join(value.split())


def excel_date(value, required=False):
    if not value and not required:
        return ''
    if not re.fullmatch(r'[0-9]{4,5}', value):
        raise SourceError('DMP licence date is not a bounded Excel day value')
    parsed_date = date(1899, 12, 30) + timedelta(days=int(value))
    if not date(1990, 1, 1) <= parsed_date <= date(2100, 1, 1):
        raise SourceError('DMP licence date is outside supported bounds')
    return parsed_date.isoformat()


class DmpWholesaleSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('dmp_wholesale accepts only the official DMP register')
        if self.complete or 'removed' in self.events:
            raise ValueError('Absence from the DMP export does not establish licence withdrawal')
        self.allow_empty = False
        self.max_records = integer(config.options.get('max_records', 1000), 'max_records', 1, 3000)
        self.max_unpacked = integer(config.options.get('max_unpacked_bytes', 4000000), 'max_unpacked_bytes', 1024, 10000000)
        self.field_labels = {'authorisation': 'Autorisasjonsnummer', 'document': 'Dokumentnummer',
                             'company': 'Foretak', 'orgnr': 'Organisasjonsnummer', 'locations': 'Lokasjoner',
                             'licence_type': 'Tillatelsestype', 'valid_from': 'Gyldig fra', 'valid_to': 'Gyldig til',
                             **self.field_labels}

    def _workbook_link(self, raw):
        soup = BeautifulSoup(raw, 'html.parser')
        if [clean(node.get_text(' ', strip=True)) for node in soup.select('h1')] != [SHEET.replace('grossister', 'legemiddelgrossister')]:
            raise SourceError('DMP wholesale register heading changed')
        links = {urljoin(PAGE, node['href']) for node in soup.select('a[href]')
                 if clean(node.get_text(' ', strip=True)).casefold() == 'last ned excel-oversikt'}
        if len(links) != 1:
            raise SourceError('DMP wholesale workbook is absent or ambiguous')
        url = links.pop()
        if not urlparse(url).path.lower().endswith('.xlsx'):
            raise SourceError('DMP wholesale export is not XLSX')
        return url

    def _parse(self, raw):
        rows = table_rows(raw, SHEET, self.max_unpacked, self.max_records * 3 + 3, 8, allow_blank_rows=True)
        with ZipFile(BytesIO(raw)) as archive:
            properties = ET.fromstring(archive.read('xl/workbook.xml')).find(NS + 'workbookPr')
        if properties is not None and properties.get('date1904', '0') not in ('0', 'false'):
            raise SourceError('DMP wholesale workbook uses an unsupported date epoch')
        if len(rows) < 4 or rows[0] != [SHEET, '', '', '', '', '', '', ''] or tuple(rows[2]) != HEADERS:
            raise SourceError('DMP wholesale workbook title or column contract changed')
        match = re.fullmatch(r'Oversikt oppdatert: (\d{2}\.\d{2}\.\d{4})', rows[1][0])
        try:
            as_of = datetime.strptime(match[1], '%d.%m.%Y').date()
        except (TypeError, ValueError):
            raise SourceError('DMP wholesale register date is missing or invalid') from None
        if any(rows[1][1:]) or not date(2000, 1, 1) <= as_of <= date.today():
            raise SourceError('DMP wholesale register date is outside supported bounds')
        grouped, raw_count = {}, 0
        for cells in rows[3:]:
            if not any(cells):
                raise SourceError('DMP wholesale register contains an interior blank record')
            raw_count += 1
            auth, doc, name, orgnr, location, kind, start, end = map(clean, cells)
            if (not re.fullmatch(r'[0-9]{4,8}-[0-9]{1,4}', auth)
                    or not re.fullmatch(r'[0-9]{2}/[0-9]{1,8}-[0-9]{1,4}', doc)
                    or not re.fullmatch(r'[0-9]{9}', orgnr)
                    or not name or not location or kind != 'Grossisttillatelse'):
                raise SourceError('DMP wholesale authorisation record violates its field contract')
            start, end = excel_date(start, True), excel_date(end)
            if end and end < start:
                raise SourceError('DMP wholesale licence validity is reversed')
            fields = {'authorisation': auth, 'document': doc, 'company': name, 'orgnr': orgnr,
                      'licence_type': kind, 'valid_from': start, 'valid_to': end}
            if auth in grouped:
                prior = grouped[auth]
                if prior['fields'] != fields or location in prior['locations']:
                    raise SourceError('DMP authorisation is duplicated or has conflicting subrecords')
                prior['locations'].append(location)
            else:
                grouped[auth] = {'fields': fields, 'locations': [location]}
        if not grouped or len(grouped) > self.max_records:
            raise SourceError('DMP wholesale authorisation set is empty or exceeds max_records')
        records = []
        for auth, row in sorted(grouped.items()):
            fields = {**row['fields'], 'locations': sorted(row['locations'])}
            records.append({'key': auth, 'title': fields['company'] + ' · grossisttillatelse ' + auth,
                            'url': PAGE, 'published': None, 'fields': fields, 'register_as_of': as_of.isoformat()})
        return records, as_of.isoformat(), raw_count

    def read_records(self):
        url = self._workbook_link(official_download(self, PAGE))
        records, self.register_as_of, self.raw_row_count = self._parse(official_download(self, url))
        if self._workbook_link(official_download(self, PAGE)) != url:
            raise SourceError('DMP wholesale workbook link changed between complete reads')
        repeated, repeated_date, repeated_count = self._parse(official_download(self, url))
        if records != repeated or repeated_date != self.register_as_of or repeated_count != self.raw_row_count:
            raise SourceError('DMP wholesale register changed between complete reads')
        return records

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            if old.get('register_as_of', '') > self.register_as_of:
                raise SourceError('DMP wholesale register date regressed; previous state preserved')
            self._next['rows'] = {**old.get('rows', {}), **self._next['rows']}
            if len(self._next['rows']) > self.max_records * 2:
                raise SourceError('Retained DMP authorisations exceed the history bound')
        self._next['register_as_of'] = self.register_as_of
        return items

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']
        context = ('Nyobservert grossisttillatelse' if event == 'added' else 'Endret grossisttillatelse',
                   'Autorisasjon: ' + fields['authorisation'], 'DMP-oversikt datert: ' + row['register_as_of'])
        if event == 'added':
            info = ('Dokument: ' + fields['document'], 'Gyldig fra: ' + fields['valid_from'],
                    'Gyldig til: ' + (fields['valid_to'] or 'ikke oppgitt'), 'Lokasjoner: ' + '; '.join(fields['locations']))
        else:
            info = tuple(details[1:])
        return replace(item, alert_details=(*context, *info,
            'Publisert registeroppføring; fravær eller utløpsdato alene dokumenterer ikke tilbakekall.'))
