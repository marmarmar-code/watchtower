"""Public capacity-message revisions, preserving the provider's table structure."""
from dataclasses import replace
from datetime import datetime
from email.utils import parsedate_to_datetime
import re
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError

URL = 'https://ummrss.nordpoolgroup.com/messages/'
LINK = 'https://umm.nordpoolgroup.com/#/messages/'
BASE = ['Unit Name', 'Area', 'Installed Capacity', 'Available Capacity', 'Unavailable Capacity', 'From', 'To']
HEADERS = [BASE, [BASE[0], 'Unit EIC', *BASE[1:]],
           [BASE[0], 'Unit EIC', *BASE[1:5], 'Fuel Type', 'Power Feed-In', *BASE[5:]]]
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def clock(value, seconds=False):
    fmt = '%d.%m.%Y %H:%M:%S' if seconds else '%d.%m.%Y %H:%M'
    try:
        result = datetime.strptime(value, fmt)
        if result.strftime(fmt) != value: raise ValueError()
        return result
    except ValueError as exc:
        raise SourceError('UMM source-local clock is invalid') from exc


class UmmCapacitySource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (URL,)):
            raise ValueError('umm_capacity accepts only the official public RSS feed')
        if self.complete or 'removed' in self.events:
            raise ValueError('RSS absence does not establish restoration or withdrawal')
        areas = config.options.get('areas', ['NO1', 'NO2', 'NO3', 'NO4', 'NO5'])
        if not isinstance(areas, list) or not areas or len(areas) != len(set(areas)) or any(x not in ['NO1','NO2','NO3','NO4','NO5'] for x in areas):
            raise ValueError('areas must be distinct Norwegian bidding-area codes')
        self.areas = set(areas)
        self.max_items = integer(config.options.get('max_items', 500), 'max_items', 1, 2000)
        self.max_table_rows = integer(config.options.get('max_table_rows', 2000), 'max_table_rows', 1, 5000)
        self.field_labels = {'revision':'Revisjon', 'status':'Oppført status', 'published_local':'Oppført publiseringstid',
                             'unavailability_type':'Oppført kategori', 'reason_code':'Årsakskode', 'tables':'Kapasitetstabeller', **self.field_labels}

    def _read(self):
        response = self.get(URL, stream=True, allow_redirects=False, accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200: raise SourceError('UMM feed redirected unexpectedly')
            chunks = []; size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes: raise SourceError('UMM feed exceeds max_bytes')
                chunks.append(chunk)
            raw = b''.join(chunks)
        finally:
            response.close()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise SourceError('UMM feed contains unsupported XML declarations')
        try: root = ET.fromstring(raw)
        except ET.ParseError as exc: raise SourceError('UMM feed is incomplete or invalid XML') from exc
        if root.tag != 'rss' or root.get('version') != '2.0' or len(root.findall('channel')) != 1:
            raise SourceError('UMM RSS identity changed')
        channel = root.find('channel')
        if channel.findtext('title') != 'Nord Pool UMM message RSS Feed' or channel.findtext('description') != 'Urgent Market Messages':
            raise SourceError('UMM channel identity changed')
        items = channel.findall('item')
        if not items or len(items) > self.max_items: raise SourceError('UMM feed item count is empty or excessive')
        records = {}; identities = set(); base_ids = set()
        for item in items:
            if sorted(c.tag for c in item) != sorted(['guid','link','title','pubDate','description']):
                raise SourceError('UMM message fields changed')
            data = {c.tag:c.text or '' for c in item}
            match = re.fullmatch('('+UUID+r')_([1-9][0-9]{0,8})', data['guid'])
            if not match or data['guid'] in identities: raise SourceError('UMM identity is invalid or duplicated')
            identities.add(data['guid']); key, revision = match[1], int(match[2])
            if key in base_ids: raise SourceError('UMM feed repeats a base message identity')
            base_ids.add(key)
            if data['link'] != LINK+key+'/'+str(revision): raise SourceError('UMM link and revision disagree')
            try:
                publication = parsedate_to_datetime(data['pubDate'])
                if publication.tzinfo is None: raise ValueError()
            except (TypeError, ValueError, OverflowError) as exc: raise SourceError('UMM RSS publication time is invalid') from exc
            if not data['title'].strip() or len(data['title']) > 2000: raise SourceError('UMM title is empty or excessive')
            soup = BeautifulSoup(data['description'], 'html.parser')
            metadata = {}; tables = []; selected = False
            for table in soup.find_all('table'):
                headers = [text(c) for c in table.find_all('th')]
                if 'Unit Name' not in headers:
                    for tr in table.find_all('tr'):
                        th = tr.find_all('th', recursive=False); td = tr.find_all('td', recursive=False)
                        if len(th) == len(td) == 1:
                            label = text(th[0]).rstrip(':'); value = text(td[0])
                            if label in metadata: raise SourceError('UMM metadata is duplicated')
                            metadata[label] = value
                    continue
                if headers not in HEADERS: raise SourceError('UMM capacity headers changed')
                rows = []
                for tr in table.find_all('tr')[1:]:
                    cells = tr.find_all('td', recursive=False)
                    if len(cells) != len(headers) or any(c.has_attr('rowspan') or c.has_attr('colspan') for c in cells):
                        raise SourceError('UMM capacity row structure changed')
                    row = dict(zip(headers, [text(c) for c in cells]))
                    if any(len(v)>1000 for v in row.values()) or not any(row.values()): raise SourceError('UMM capacity row is empty or excessive')
                    for name in ['Installed Capacity','Available Capacity','Unavailable Capacity','Power Feed-In']:
                        value = row.get(name, '')
                        if value and not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,6})? MW', value):
                            raise SourceError('UMM capacity value or unit changed')
                    start, end = row['From'], row['To']
                    if bool(start) != bool(end): raise SourceError('UMM interval is incomplete')
                    if start and clock(end) < clock(start): raise SourceError('UMM interval is reversed')
                    selected |= bool(self.areas.intersection(row['Area'].split(' - ')))
                    # Blank cells and order encode parent/subunit and continuation rows.
                    # Preserve them literally; no inherited or summed capacity is invented.
                    rows.append(row)
                if not rows or len(rows)>self.max_table_rows: raise SourceError('UMM table row count is empty or excessive')
                tables.append(rows)
            for label in ['Published','Publisher','Status']:
                if not metadata.get(label): raise SourceError('UMM required metadata is absent')
            clock(metadata['Published'], seconds=True)
            if not tables:
                if 'Type of Unavailability' in metadata: raise SourceError('UMM capacity message lost its table')
                continue  # General messages are outside this capability.
            if metadata.get('Type of Unavailability') not in ['Planned','Unplanned']:
                raise SourceError('UMM capacity classification changed')
            if selected:
                fields = {'revision':revision,'status':metadata['Status'],'publisher':metadata['Publisher'],
                          'published_local':metadata['Published'], 'rss_publication':publication.isoformat(),
                          'unavailability_type':metadata['Type of Unavailability'],'reason_code':metadata.get('Reason Code') or None,
                          'tables':tables}
                row = {'key':key,'title':data['title'].strip(),'url':data['link'],'published':None,'fields':fields}
                if key not in records or records[key]['fields']['revision']<revision: records[key]=row
        return sorted(records.values(), key=lambda r:r['key'])

    def read_records(self):
        first, second = self._read(), self._read()
        if first != second: raise SourceError('UMM selected messages changed between complete reads')
        return first

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            for key, current in self._next['rows'].items():
                prior = old.get('rows', {}).get(key)
                if prior and current['row']['fields']['revision'] < prior['row']['fields']['revision']:
                    raise SourceError('UMM revision regressed; prior snapshot preserved')
        return items

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress); f = row['fields']
        info = ('Nyobservert kapasitetsmelding' if event == 'added' else 'Endret kapasitetsmelding',
                f"Revisjon {f['revision']} · {f['status']} · {f['unavailability_type']}",
                'Oppført publiseringstid: '+f['published_local'],
                'Kapasiteter og intervaller følger kildens tabell. Tomme felt er bevart; tidssonen i tabellen er ikke oppgitt.',
                'Meldingen dokumenterer ikke nødvendigvis et fysisk strømbrudd. RSS-vinduet dekker ikke full historikk; fravær viser ikke gjenoppretting.')
        if event == 'changed':
            info += tuple(d[:600] for d in details[1:] if not d.startswith('Kapasitetstabeller:'))
            if any(d.startswith('Kapasitetstabeller:') for d in details[1:]):
                info += ('Kapasitetstabellen er endret; se meldingen for enheter, verdier og intervaller.',)
        return replace(item, alert_details=info)
