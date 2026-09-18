"""Complete published Norwegian aviation-operator certificate listing."""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .changes import SnapshotSource, document, integer
from .common import SourceError

PAGE = 'https://www.luftfartstilsynet.no/aktorer/flyselskap/godkjente-flyselskaper/'
TITLE = 'Godkjente fly- og helikopterselskap'


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


class AviationOperatorsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('aviation_operators accepts only the official AOC listing')
        if self.complete or 'removed' in self.events:
            raise ValueError('Absence from the listing does not establish certificate withdrawal')
        self.allow_empty = False
        self.max_pages = integer(config.options.get('max_pages', 10), 'max_pages', 1, 20)
        self.field_labels = {'certificate': 'AOC-nummer', 'company': 'Operatørnavn',
                             'address': 'Oppført adresse', **self.field_labels}

    def _page(self, raw, page_number):
        try:
            html = raw.decode('utf-8')
        except UnicodeError:
            raise SourceError('AOC listing encoding changed') from None
        if not html.rstrip().lower().endswith('</html>'):
            raise SourceError('AOC listing HTML is truncated')
        soup = BeautifulSoup(html, 'html.parser')
        if [text(h) for h in soup.select('h1')] != [TITLE]:
            raise SourceError('AOC listing heading changed')
        containers = soup.select('.m-article-list')
        if len(containers) != 1:
            raise SourceError('AOC listing container is absent or ambiguous')
        container = containers[0]
        ranges = [re.fullmatch(r'Viser treff ([0-9]+) til ([0-9]+) av ([0-9]+)', text(node))
                  for node in container.select('span.a-small-label')]
        ranges = [match for match in ranges if match]
        if len(ranges) != 1:
            raise SourceError('AOC listing lacks one explicit result count')
        start, end, total = map(int, ranges[0].groups())
        if not 1 <= start <= end <= total <= self.max_records:
            raise SourceError('AOC listing range is invalid or exceeds max_records')
        pages = {}
        for anchor in container.select('.m__article-pagination a[href]'):
            label = text(anchor)
            if not re.fullmatch(r'[1-9][0-9]*', label) or int(label) in pages:
                raise SourceError('AOC listing pagination is ambiguous')
            number = int(label)
            expected = PAGE if number == 1 else PAGE + '?p=' + str(number)
            if urljoin(PAGE, anchor['href']) != expected:
                raise SourceError('AOC listing pagination URL changed')
            pages[number] = expected
        if not pages and start == 1 and end == total:
            pages = {1: PAGE}
        if (not pages or len(pages) > self.max_pages or sorted(pages) != list(range(1, len(pages)+1))
                or page_number not in pages or (page_number == 1 and start != 1)
                or (page_number == len(pages) and end != total)):
            raise SourceError('AOC listing pagination is incomplete or exceeds max_pages')
        rows = []
        for card in container.select('.m__article-result'):
            headings, addresses, labels = card.select('h2'), card.select('p.m__article-result-text'), card.select('span.a-small-label')
            if len(headings) != 1 or len(addresses) != 1 or len(labels) != 1:
                raise SourceError('AOC operator record schema changed')
            match = re.fullmatch(r'AOC-nummer: ([A-Z]{2,4}\.AOC\.[0-9]{3})', text(labels[0]))
            company, address = text(headings[0]), text(addresses[0])
            if not match or not company or not address or len(company) > 250 or len(address) > 350:
                raise SourceError('AOC operator identity or address is missing or invalid')
            certificate = match[1]
            rows.append({'key': certificate, 'title': company + ' · ' + certificate,
                         'url': pages[page_number], 'published': None,
                         'fields': {'certificate': certificate, 'company': company, 'address': address}})
        if len(rows) != end-start+1:
            raise SourceError('AOC listing row count disagrees with its result range')
        return rows, start, end, total, pages

    def _poll(self):
        rows, _, end, total, pages = self._page(document(self, PAGE), 1)
        for number, url in sorted(pages.items()):
            if number == 1:
                continue
            extra, start, last, count, links = self._page(document(self, url), number)
            if start != end+1 or count != total or links != pages:
                raise SourceError('AOC listing changed or has a gap between pages')
            rows.extend(extra)
            end = last
        if len(rows) != total or len({row['key'] for row in rows}) != total:
            raise SourceError('AOC listing is incomplete or contains duplicate certificates')
        return sorted(rows, key=lambda row: row['key'])

    def read_records(self):
        rows = self._poll()
        if self._poll() != rows:
            raise SourceError('AOC listing changed between complete reads')
        return rows

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            self._next['rows'] = {**old.get('rows', {}), **self._next['rows']}
            if len(self._next['rows']) > self.max_records*2:
                raise SourceError('Retained AOC certificates exceed the history bound')
        return items

    def _item(self, row, event, details, suppress):
        content = ['Nyobservert AOC-oppføring' if event == 'added' else 'Endret AOC-oppføring',
                   'AOC-nummer: ' + row['fields']['certificate'], *details[1:],
                   'Kilden oppgir ikke vedtaksdato eller gyldighetsperiode. Fravær dokumenterer ikke tilbakekall.']
        return super()._item(row, event, content, suppress)
