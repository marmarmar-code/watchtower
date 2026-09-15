"""Published airworthiness directives and their source-reported revisions."""
from dataclasses import replace
from datetime import date, timedelta
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer, strings
from .common import SourceError

ORIGIN = 'https://ad.easa.europa.eu'
BASE = ORIGIN + '/search/'
AD = re.compile(r'(?:[A-Z]{1,4}-)?[0-9]{2,4}(?:-[0-9]{2,4}){1,3}(?:R[0-9]{1,2})?')
EMPTY = 'Sorry, no results found for your query.'


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def value(raw, label, empty=False, limit=6000):
    if not isinstance(raw, str) or len(raw) > limit or (not raw.strip() and not empty):
        raise SourceError(f'Airworthiness {label} is missing or invalid')
    return ' '.join(raw.split())


def day(raw, label):
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', raw):
        raise SourceError(f'Airworthiness {label} is invalid')
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SourceError(f'Airworthiness {label} is invalid') from exc


def official(url, path):
    p = urlparse(url)
    if p.scheme != 'https' or p.netloc != 'ad.easa.europa.eu' or p.query or p.fragment or p.params or not re.fullmatch(path, p.path):
        raise SourceError('Airworthiness link has an unexpected host or path')
    return url


class AirworthinessSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (BASE,)):
            raise ValueError('airworthiness accepts only the official EASA search')
        if self.complete or 'removed' in self.events:
            raise ValueError('Airworthiness publication absence cannot establish withdrawal')
        self.max_records = integer(config.options.get('max_records', 200), 'max_records', 1, 500)
        self.lookback = integer(config.options.get('lookback_days', 30), 'lookback_days', 1, 90)
        self.max_pages = integer(config.options.get('max_pages', 10), 'max_pages', 1, 25)
        self.issuers = strings(config.options.get('issuers', []), 'issuers', empty=True)
        if any(not re.fullmatch(r'[A-Z]{2}', v) for v in self.issuers):
            raise ValueError('issuers must be two-letter source codes')
        if 'ad_numbers' in config.options:
            raise ValueError('Explicit AD monitoring outside the issue window is not supported')
        self.field_labels = {'subject': 'Emne', 'issuer': 'Utstederkode', 'issue_date': 'Utstedelsesdato',
            'effective_date': 'Ikrafttredelsesdato', 'type_designation': 'Typebetegnelse',
            'revision': 'Oppført revisjon', 'correction': 'Oppført korrigering',
            'supersedure': 'Oppført erstatning', 'attachments': 'Vedleggslenker',
            'ata_chapter': 'Oppført ATA-kapittel', **self.field_labels}

    def _window(self):
        last = date.today()
        return last - timedelta(days=self.lookback-1), last

    def _read(self, url, form=None, params=None):
        method = self.get if form is None else self.post
        kwargs = {} if form is None else {'data': form}
        if params:
            kwargs['params'] = params
        r = method(url, **kwargs, stream=True, allow_redirects=False, accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code != 200:
                raise SourceError('Airworthiness source returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Airworthiness response exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            r.close()

    def _page(self, raw, first, last, page):
        soup = BeautifulSoup(raw, 'html.parser')
        titles, tables = soup.select('title'), soup.select('table.ad-list')
        if len(titles) != 1 or text(titles[0]) != 'EASA Safety Publications Tool':
            raise SourceError('Airworthiness page identity changed')
        if not tables and EMPTY in soup.get_text(' ', strip=True) and not soup.select('#toolbar') and page == 1:
            return 0, {}
        headings, counts = soup.select('#toolbar h2'), soup.select('#toolbar h3')
        if len(tables) != 1 or len(headings) != 1 or len(counts) != 1:
            raise SourceError('Airworthiness list schema changed')
        expected = f'List of Mandatory Continuing Airworthiness Information from {first} to {last}'
        if text(headings[0]) != expected:
            raise SourceError('Airworthiness date window changed')
        match = re.fullmatch(r'Displaying records ([0-9]+) to ([0-9]+) out of a total of ([0-9]+) publications\.', text(counts[0]))
        if not match:
            raise SourceError('Airworthiness pagination is absent')
        start, end, total = map(int, match.groups())
        if not 1 <= total <= self.max_pages*20 or start != (page-1)*20+1 or end != min(page*20, total) or start > end:
            raise SourceError('Airworthiness pagination is inconsistent or exceeds bounds')
        trs = tables[0].find_all('tr')
        if len(trs) != end-start+2:
            raise SourceError('Airworthiness page is incomplete')
        rows = {}
        for tr in trs[1:]:
            cells = tr.find_all('td', recursive=False)
            if len(cells) != 7:
                raise SourceError('Airworthiness row schema changed')
            links, flags = cells[0].select('a[href]'), cells[1].select('img')
            if len(links) != 1 or len(flags) != 1:
                raise SourceError('Airworthiness row identity is ambiguous')
            ident = text(links[0])
            if not AD.fullmatch(ident) or ident in rows:
                raise SourceError('Airworthiness number is invalid or repeated')
            official(links[0]['href'], re.escape('/ad/'+ident))
            issue, effective = day(text(cells[2]), 'issue date'), day(text(cells[5]), 'effective date')
            if not first <= issue <= last:
                raise SourceError('Airworthiness issue date is outside the selected window')
            issuer = flags[0].get('alt')
            if not isinstance(issuer, str) or not re.fullmatch(r'[A-Z]{2}', issuer) or flags[0].get('title') != issuer:
                raise SourceError('Airworthiness issuer code is invalid')
            rows[ident] = {'subject': value(text(cells[3]), 'subject'), 'issuer': issuer,
                'issue_date': issue.isoformat(), 'effective_date': effective.isoformat()}
        return total, rows

    def _listing(self, form, first, last):
        total, rows = self._page(self._read(BASE, form), first, last, 1)
        for page in range(2, (total+19)//20+1):
            count, current = self._page(self._read(BASE+'page-'+str(page), form), first, last, page)
            if count != total or rows.keys() & current.keys():
                raise SourceError('Airworthiness pages changed or repeat identities')
            rows.update(current)
        if len(rows) != total:
            raise SourceError('Airworthiness full list is incomplete')
        return rows

    def _export(self, raw, listed):
        try:
            decoded = raw.decode('utf-8-sig')
            if '\x00' in decoded or '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
                raise ValueError('Unsupported XML declaration')
            root = ET.fromstring(decoded)
        except (ValueError, ET.ParseError) as exc:
            raise SourceError('Airworthiness XML is invalid') from exc
        if root.tag != 'list' or any(child.tag != 'item' for child in root) or len(root) != len(listed):
            raise SourceError('Airworthiness XML count or schema disagrees with listing')
        rows = {}
        for item in root:
            def field(name, empty=False):
                nodes = item.findall(name)
                if len(nodes) != 1 or len(nodes[0]):
                    raise SourceError('Airworthiness XML field is absent or repeated')
                return value(nodes[0].text or '', name, empty=empty)
            if field('ad_class') != 'AD':
                raise SourceError('Airworthiness export contains a non-AD publication')
            ident = field('ad_number')
            if ident not in listed or ident in rows:
                raise SourceError('Airworthiness XML identity disagrees with listing')
            fields = {'subject': field('subject'), 'issuer': field('issued_by'),
                'issue_date': field('issue_date'), 'effective_date': field('effective_date')}
            if fields != listed[ident]:
                raise SourceError('Airworthiness HTML and XML fields disagree')
            fields['type_designation'] = field('type_designation')
            containers = item.findall('attachments')
            if len(containers) != 1 or len(containers[0]) > 30:
                raise SourceError('Airworthiness attachment list is absent or excessive')
            urls = []
            for attachment in containers[0]:
                nodes = attachment.findall('uri')
                if attachment.tag != 'attachment' or len(nodes) != 1 or len(nodes[0]):
                    raise SourceError('Airworthiness attachment identity is invalid')
                urls.append(official(value(nodes[0].text, 'attachment', limit=2000), r'/blob/[^/?#]+'))
            if len(set(urls)) != len(urls):
                raise SourceError('Airworthiness attachments repeat')
            fields['attachments'] = sorted(urls)
            rows[ident] = {'key': ident, 'title': ident+' · '+fields['subject'], 'url': ORIGIN+'/ad/'+ident,
                'published': None, 'fields': fields}
        return rows

    def _detail(self, row):
        soup = BeautifulSoup(self._read(row['url']), 'html.parser')
        tables = soup.select('table.table-detail')
        if len(tables) != 1:
            raise SourceError('Airworthiness detail table is absent')
        cells = {}
        for tr in tables[0].find_all('tr', recursive=False):
            pair = tr.find_all('td', recursive=False)
            if len(pair) != 2 or text(pair[0]) in cells:
                raise SourceError('Airworthiness detail fields are ambiguous')
            cells[text(pair[0])] = pair[1]
        required = {'Number','Issued by','Issue date','Effective date','Revision','Correction','Supersedure','ATA Chapter'}
        if not required <= cells.keys() or text(cells['Number']) != row['key']:
            raise SourceError('Airworthiness detail identity or required fields changed')
        fields = row['fields']
        for label, name in [('Issue date','issue_date'),('Effective date','effective_date')]:
            if text(cells[label]) != fields[name]:
                raise SourceError('Airworthiness detail and listing dates disagree')
        flags = cells['Issued by'].select('img[src]')
        if len(flags) != 1 or flags[0]['src'] != ORIGIN+'/static/img/flags/'+fields['issuer'].lower()+'.gif':
            raise SourceError('Airworthiness detail and listing issuers disagree')
        for label, name in [('Revision','revision'),('Correction','correction'),('Supersedure','supersedure'),('ATA Chapter','ata_chapter')]:
            fields[name] = value(text(cells[label]), label, empty=label=='ATA Chapter')
        return row

    def _sweep(self, form, first, last):
        listed = self._listing(form, first, last)
        # Empty results use the site's explicit HTML no-results response. The
        # export has no verified empty-response contract; do not invent one.
        if not listed:
            return {}, {}
        rows = self._export(self._read(BASE, form, {'format':'xml','r':str(self.max_pages*20)}), listed)
        selected = {key:self._detail(row) for key,row in rows.items()
            if not self.issuers or row['fields']['issuer'] in self.issuers}
        if len(selected) > self.max_records:
            raise SourceError('Airworthiness selected publications exceed max_records')
        return rows, selected

    def read_records(self):
        first, last = self._window()
        form = {'fi_action':'advanced','fi_adclass[]':'AD','fi_date_start':first.isoformat(),'fi_date_end':last.isoformat()}
        rows, selected = self._sweep(form, first, last)
        if self._sweep(form, first, last) != (rows, selected):
            raise SourceError('Airworthiness publications changed during reading')
        return [selected[key] for key in sorted(selected)]

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        f = row['fields']
        info = ('Nyobservert AD-publikasjon' if event=='added' else 'Endret AD-publikasjon',
            f"AD: {row['key']} · utsteder {f['issuer']}", f"Utstedelse: {f['issue_date']} · ikrafttredelse: {f['effective_date']}",
            f"Revisjon: {f['revision']}", f"Korrigering: {f['correction']}", f"Erstatning: {f['supersedure']}")
        if event == 'changed':
            info += tuple(d[:800] for d in details[1:])
        return replace(item, alert_details=info+('Publikasjonsopplysninger; kontroller originalen for virkeområde og krav. Ingen konklusjon om et bestemt fly.',))
