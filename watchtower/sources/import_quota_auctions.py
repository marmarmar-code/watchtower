"""Published import-quota auction allocations, separate from tariff rates."""
from datetime import datetime, timezone
import re
import time
from urllib.parse import urlparse, parse_qs, urljoin
from bs4 import BeautifulSoup, Comment
from .changes import SnapshotSource, integer, strings, canonical
from .common import SourceError

PAGE = 'https://auksjon.landbruksdirektoratet.no/history'
BASE = 'https://auksjon.landbruksdirektoratet.no'
LABELS = ['Totalmengde','Minste mengde','Største mengde','Minstepris','Budøkning','Auksjonen startet','Starttid','Sluttid']
KEYS = ['offered_quantity','minimum_quantity','maximum_quantity','displayed_minimum_bid','bid_increment','source_opening_time','source_start_time','source_end_time']


def today():
    return datetime.now(timezone.utc).date()


def text(node):
    value = ' '.join(node.get_text(' ', strip=True).split())
    if not value or len(value) > 2000 or '\ufffd' in value:
        raise SourceError('Quota auction text is absent or invalid')
    return value


def quantity(value):
    if not re.fullmatch(r'(?:[0-9]{1,3}(?: [0-9]{3})+|[0-9]{1,13})', value):
        raise SourceError('Quota auction quantity changed format')
    return str(int(value.replace(' ', '')))


def price(value):
    if not re.fullmatch(r'(?:[0-9]{1,3}(?: [0-9]{3})+|[0-9]{1,13}),[0-9]{2}', value):
        raise SourceError('Quota auction bid changed format')
    return value.replace(' ', '')


class ImportQuotaAuctionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('import_quota_auctions accepts only the official history')
        if self.complete or 'removed' in self.events:
            raise ValueError('History absence cannot establish cancellation or loss of quota')
        self.prefixes = strings(config.options.get('title_prefixes', ['KORN ']), 'title_prefixes')
        self.unit = config.options.get('quantity_unit', 'tonne')
        if self.unit not in ('kg','tonne'):
            raise ValueError('quantity_unit must be kg or tonne, verified for the selected quotas')
        self.max_items = integer(config.options.get('max_index_items', 200), 'max_index_items', 1, 500)
        self.max_awards = integer(config.options.get('max_awards_per_auction', 100), 'max_awards_per_auction', 1, 500)
        self.field_labels = {'awards': 'Publiserte tildelinger', 'awarded_quantity': 'Totalt tildelt mengde',
                             'offered_quantity': 'Totalmengde', 'displayed_minimum_bid': 'Minstepris (vist)',
                             'source_start_time': 'Starttid (kildens lokale tekst)', 'source_end_time': 'Sluttid (kildens lokale tekst)',
                             'result_availability': 'Tilgjengelig resultattabell', 'quantity_unit': 'Mengdeenhet', **self.field_labels}

    def _download(self, url):
        r = self.get(url, stream=True, allow_redirects=False, accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code != 200:
                raise SourceError('Quota auction response redirected unexpectedly')
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Quota auction response exceeds max_bytes')
                chunks.append(chunk)
            encoding = 'cp1252' if re.search('charset=["\']?iso-8859-1', r.headers.get('Content-Type',''), re.I) else 'utf-8-sig'
            try:
                return b''.join(chunks).decode(encoding)
            except UnicodeError as exc:
                raise SourceError('Quota auction character encoding changed') from exc
        finally:
            r.close()

    def _index(self, raw):
        if not raw.rstrip().lower().endswith('</html>'):
            raise SourceError('Quota history document is truncated')
        soup = BeautifulSoup(raw, 'html.parser')
        if len(soup.select('title')) != 1 or 'Auksjon' not in text(soup.title):
            raise SourceError('Quota history page identity changed')
        years, active = [], []
        for a in soup.select('.swiper-wrapper a[href]'):
            u = urlparse(urljoin(PAGE,a['href']));q = parse_qs(u.query)
            if (u.scheme,u.netloc,u.path,u.fragment) != ('https','auksjon.landbruksdirektoratet.no','/history','') or set(q) != {'year'} or len(q['year']) != 1 or not re.fullmatch(r'20[0-9]{2}',q['year'][0]) or text(a) != q['year'][0]:
                raise SourceError('Quota history year navigation changed')
            year = int(q['year'][0]);years.append(year)
            if 'border-orange' in a.get('class',[]):
                active.append(year)
        if not years or len(set(years)) != len(years) or active != [max(years)] or max(years) != today().year:
            raise SourceError('Quota history did not select the current advertised year')
        nodes = soup.select('div[x-data="aitem_entry()"]')
        if not 1 <= len(nodes) <= self.max_items:
            raise SourceError('Quota history list is empty or exceeds max_index_items')
        rows, seen = [], set()
        for node in nodes:
            links = node.find_all(attrs={'@click':True})
            if len(links) != 1:
                raise SourceError('Quota auction item selector is ambiguous')
            m = re.fullmatch(r'retrieve_data\(([1-9][0-9]{0,9})\)',links[0]['@click'])
            if not m or m[1] in seen:
                raise SourceError('Quota auction item identity is invalid or repeated')
            seen.add(m[1]);rows.append({'id':m[1],'title':text(links[0]),'history_year':active[0]})
        if len(soup.find_all(attrs={'@click':lambda x:x and 'retrieve_data' in x})) != len(rows):
            raise SourceError('Quota history contains unparsed item selectors')
        return sorted(rows,key=lambda r:int(r['id']))

    def _result(self, raw, entry):
        soup = BeautifulSoup(raw, 'html.parser')
        labels = soup.select('div.font-bold.mb-1')
        if [text(x) for x in labels] != LABELS:
            raise SourceError('Quota auction parameter labels changed')
        values = []
        for label in labels:
            sibling = label.find_next_sibling('div')
            if sibling is None or 'mb-5' not in sibling.get('class',[]):
                raise SourceError('Quota auction parameter is missing')
            values.append(text(sibling))
        for i in range(3):
            values[i] = quantity(values[i])
        for i in (3,4):
            values[i] = price(values[i])
        dates = []
        for value in values[5:]:
            try:
                if not re.fullmatch(r'[0-9]{2}\.[0-9]{2}\.20[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}',value):
                    raise ValueError
                dates.append(datetime.strptime(value,'%d.%m.%Y %H:%M:%S'))
            except ValueError as exc:
                raise SourceError('Quota auction time changed format') from exc
        if dates != sorted(dates) or dates[1].year != entry['history_year'] or dates[-1].date() > today():
            raise SourceError('Quota auction times are inconsistent with history')
        markers = [str(x).strip() for x in soup.find_all(string=lambda x:isinstance(x,Comment))]
        if markers.count('SOF i/bid_list') != 1 or markers.count('EOF i/bid_list') != 1:
            raise SourceError('Quota auction result boundaries changed')
        start = raw.find('<!-- SOF i/bid_list -->');end = raw.find('<!-- EOF i/bid_list -->')
        if not 0 <= start < end:
            raise SourceError('Quota auction result boundary order changed')
        section = BeautifulSoup(raw[start+len('<!-- SOF i/bid_list -->'):end], 'html.parser')
        tables = section.select('table')
        awards, total = [], None
        if not tables:
            if section.get_text(strip=True) or section.find(True):
                raise SourceError('Quota auction result table was replaced by unrecognized content')
        else:
            if len(tables) != 1 or [text(x) for x in tables[0].select('thead td')] != ['Firma','Tildelt mengde','Bud pr. enhet']:
                raise SourceError('Quota allocation table headers changed')
            table = tables[0];trs = table.select('tbody tr')
            if not 1 <= len(trs) <= self.max_awards:
                raise SourceError('Quota allocation row count is empty or exceeds bounds')
            seen = set()
            for tr in trs:
                cells = tr.find_all('td', recursive=False)
                if len(cells) != 3 or any(c.get('rowspan') or c.get('colspan') for c in cells):
                    raise SourceError('Quota allocation row shape changed')
                name = text(cells[0])
                if name in seen:
                    raise SourceError('Quota participant occurs more than once; allocation grain is ambiguous')
                seen.add(name)
                awards.append({'participant_name':name,'quantity':quantity(text(cells[1])),'bid_nok_per_unit':price(text(cells[2]))})
            footer = table.select('tfoot tr')
            if len(footer) != 1:
                raise SourceError('Quota allocated total is missing')
            cells = footer[0].find_all('td',recursive=False)
            if len(cells) != 3 or text(cells[0]) != 'Totalt tildelt mengde:' or cells[2].get_text(strip=True):
                raise SourceError('Quota allocated total label changed')
            total = quantity(text(cells[1]))
            if sum(int(a['quantity']) for a in awards) != int(total) or int(total) > int(values[0]):
                raise SourceError('Quota allocation rows disagree with published quantities')
        fields = dict(zip(KEYS,values))
        fields.update(auction_id=entry['id'],history_year=entry['history_year'],quantity_unit=self.unit,
                      awarded_quantity=total,result_availability='published_table' if tables else 'no_award_table',
                      awards=sorted(awards,key=lambda a:canonical(a)))
        return {'key':entry['id'],'title':entry['title'],'url':PAGE+'?year='+str(entry['history_year']),
                'published':None,'fields':fields}

    def _read(self):
        entries = self._index(self._download(PAGE))
        selected = [e for e in entries if any(e['title'].startswith(p) for p in self.prefixes)]
        if not selected or len(selected) > self.max_records:
            raise SourceError('Quota selection is empty or exceeds max_records')
        rows = []
        for e in selected:
            time.sleep(0.25)
            rows.append(self._result(self._download(BASE+'/js_history?aitem_id='+e['id']),e))
        return entries,rows

    def read_records(self):
        first = self._read()
        if self._read() != first:
            raise SourceError('Quota history or allocation result changed between complete reads')
        return first[1]

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope') == self.scope:
            for key,value in old.get('rows',{}).items():
                before = value.get('row',{}).get('fields',{})
                after = self._next.get('rows',{}).get(key,{}).get('row',{}).get('fields',{})
                if before.get('result_availability') == 'published_table' and after.get('result_availability') == 'no_award_table':
                    raise SourceError('Previously published quota allocations disappeared from the result page')
        return items
