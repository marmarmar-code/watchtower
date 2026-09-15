"""Published E.164 number-range assignments and status changes."""
import csv
from dataclasses import replace
import io
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer, strings
from .common import SourceError

PAGE_URL = 'https://nkom.no/telefoni-og-telefonnummer/telefonnummer-og-den-norske-nummerplan/alle-nummerserier-for-norske-telefonnumre/den-norske-nummerplanen-for-telefoni-med-mer-e.164'
DATA_URL = 'https://stenonicprdnoea01.blob.core.windows.net/enonicpubliccontainer/numsys/nkom.no/E164.csv'
TITLE = 'Den norske nummerplanen for telefoni med mer (E.164)'
HEADERS = {'Fra','Til','Tilbyder','Status','Kommentar','Antall','Kategori','Punktkode'}
VISIBLE_HEADERS = ['Fra','Til','Tilbyder','Status','Antall']


def _text(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise SourceError('Number allocation field is invalid')
    return ' '.join(value.split())


def _number(value):
    value = _text(value).replace(' ', '')
    if not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12})', value):
        raise SourceError('Number allocation endpoint must have eight or twelve digits')
    return value


def _count(value):
    value = _text(value)
    if not re.fullmatch(r'(?:[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)', value):
        raise SourceError('Number allocation quantity is invalid')
    return int(value.replace(',', ''))


def _range(row):
    start, end = _number(row['Fra']), _number(row['Til'])
    count = _count(row['Antall'])
    if len(start) != len(end) or int(start) > int(end) or int(end)-int(start)+1 != count:
        raise SourceError('Number allocation endpoints and quantity disagree')
    return start, end, count


class NumberAllocationsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE_URL,)):
            raise ValueError('number_allocations accepts only the official Nkom E.164 page')
        if self.complete or 'removed' in self.events:
            raise ValueError('Number register absence cannot establish withdrawal')
        self.categories = strings(config.options['categories'], 'categories') if 'categories' in config.options else ()
        if any(len(c) > 150 for c in self.categories):
            raise ValueError('Number categories must use bounded exact source labels')
        self.max_register_records = integer(config.options.get('max_register_records', 5000), 'max_register_records', 1, 20000)
        self.field_labels = {'holder':'Tilbyder', 'status':'Status', 'quantity':'Antall numre', 'category':'Kategori',
                             'point_code':'Punktkode', 'comment':'Kommentar', **self.field_labels}

    def _read(self, url):
        response = self.get(url, stream=True, allow_redirects=False, accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200:
                raise SourceError('Number register returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64*1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Number register exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def read_records(self):
        soup = BeautifulSoup(self._read(PAGE_URL), 'html.parser')
        titles = soup.select('h1')
        links = [urljoin(PAGE_URL, a['href']) for a in soup.select('a[href]') if 'E164.csv' in a['href']]
        tables = soup.select('table')
        if len(titles) != 1 or _text(titles[0].get_text(' ',strip=True)) != TITLE or links != [DATA_URL] or len(tables) != 1:
            raise SourceError('Nkom page title, export link or table changed')
        headers = [_text(h.get_text(' ',strip=True)) for h in tables[0].select('thead th')]
        if headers != VISIBLE_HEADERS:
            raise SourceError('Nkom visible table schema changed')
        visible = []
        for tr in tables[0].select('tbody tr'):
            cells = tr.find_all('td', recursive=False)
            if len(cells) != len(headers):
                raise SourceError('Nkom visible table row is incomplete')
            visible.append(dict(zip(headers, [_text(c.get_text(' ',strip=True)) for c in cells])))
        if not 1 <= len(visible) <= 100:
            raise SourceError('Nkom visible table is empty or exceeds its bound')
        return self._records(self._read(DATA_URL), visible)

    def _records(self, raw, visible):
        records, seen, categories, intervals = {}, set(), set(), {}
        try:
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
            if reader.fieldnames is None or len(reader.fieldnames) != len(HEADERS) or set(reader.fieldnames) != HEADERS:
                raise SourceError('Nkom CSV header changed')
            for number, row in enumerate(reader, 1):
                if number > self.max_register_records:
                    raise SourceError('Nkom CSV exceeds max_register_records')
                if None in row or None in row.values():
                    raise SourceError('Nkom CSV has missing or surplus cells')
                row = {key:_text(value) for key,value in row.items()}
                start, end, count = _range(row)
                key = start+'-'+end
                if key in seen:
                    raise SourceError('Nkom number ranges repeat')
                seen.add(key)
                intervals.setdefault(len(start), []).append((int(start),int(end)))
                if row['Status'] not in {'Tildelt','Ledig','Blokkert'} or not row['Kategori'] or (row['Status']=='Tildelt' and not row['Tilbyder']):
                    raise SourceError('Nkom allocation status, category or holder is invalid')
                categories.add(row['Kategori'])
                records[key] = {'key':key,'title':'Nummerområde '+start+'–'+end,'url':PAGE_URL,'published':None,
                    'fields':{'holder':row['Tilbyder'] or None,'status':row['Status'],'quantity':count,
                              'category':row['Kategori'],'point_code':row['Punktkode'] or None,'comment':row['Kommentar'] or None}}
        except (UnicodeError,csv.Error) as exc:
            raise SourceError('Nkom number register CSV is malformed') from exc
        if not records or not set(self.categories) <= categories:
            raise SourceError('Nkom register or a selected category is absent')
        for ranges in intervals.values():
            previous_end = -1
            for start,end in sorted(ranges):
                if start <= previous_end:
                    raise SourceError('Nkom number ranges overlap')
                previous_end = end
        # The HTML is only one page, not proof of a complete export. Check each
        # visible row against the CSV without treating absent ranges as removals.
        visible_keys = set()
        for shown in visible:
            start,end,count = _range(shown);key=start+'-'+end
            if key in visible_keys or key not in records:
                raise SourceError('Nkom visible rows repeat or are missing from the CSV')
            visible_keys.add(key)
            fields = records[key]['fields']
            if fields['holder'] != (shown['Tilbyder'] or None) or fields['status'] != shown['Status'] or fields['quantity'] != count:
                raise SourceError('Nkom HTML and CSV disagree')
        selected = [r for r in records.values() if not self.categories or r['fields']['category'] in self.categories]
        if len(selected) > self.max_records:
            raise SourceError('Selected Nkom ranges exceed max_records')
        return sorted(selected, key=lambda row:row['key'])

    def _item(self, row, event, details, suppress):
        item = super()._item(row,event,details,suppress)
        f = row['fields']
        info = ('Nyobservert nummerområde' if event=='added' else 'Endret nummeroppføring',
                f"{f['status']} · {f['holder'] or 'tilbyder ikke oppgitt'} · {f['category']}",
                f"Antall numre: {f['quantity']}")
        if event=='changed':
            info += tuple(d[:800] for d in details[1:])
        return replace(item,alert_details=info+('Publisert registerendring; første observasjon er ikke tildelingsdato',
            'Tilbyder for nummerområdet er ikke nødvendigvis nåværende tilbyder for et portert enkeltnummer',))
