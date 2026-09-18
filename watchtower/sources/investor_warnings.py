"""Finanstilsynet's own published investor warnings, with complete article text."""
from dataclasses import replace
from datetime import date, datetime
import json
import re
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .changes import SnapshotSource, document, integer
from .common import SourceError
from .device_actions import _object, _text

ORIGIN = 'https://www.finanstilsynet.no'
PAGE = ORIGIN + '/markedsadvarsler/'
API = ORIGIN + '/api/search/investorwarnings/'
AUTHORITY = 'Marknadsåtvaringar frå Finanstilsynet'
MONTHS = dict(zip(('januar februar mars april mai juni juli august september oktober november desember').split(), range(1, 13)))


def today():
    return datetime.now(ZoneInfo('Europe/Oslo')).date()


def local_date(value):
    match = re.fullmatch(r'(\d{1,2})\. ([a-zæøå]+) (\d{4})', value or '')
    try:
        if not match:
            raise ValueError
        return date(int(match[3]), MONTHS[match[2]], int(match[1]))
    except (KeyError, ValueError) as exc:
        raise SourceError('Investor warning publication date is invalid') from exc


def clean(node):
    return ' '.join(node.get_text(' ', strip=True).split())


class InvestorWarningsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('investor_warnings accepts only the official warning register')
        if self.complete or 'removed' in self.events:
            raise ValueError('Register absence does not establish withdrawal of a warning')
        self.years_back = integer(config.options.get('years_back', 2), 'years_back', 1, 3)
        self.max_pages = integer(config.options.get('max_pages', 10), 'max_pages', 1, 20)
        self.field_labels = {'title': 'Kildens overskrift', 'last_published': 'Sist publisert (kilden)',
                             'authority': 'Advarselstype', 'introduction': 'Kildens innledning',
                             'notice_text': 'Advarselstekst', **self.field_labels}

    def _json(self, url):
        try:
            return json.loads(document(self, url), object_pairs_hook=_object)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('Investor warning API returned invalid JSON') from exc

    def _index(self, years, current):
        metadata = self._json(API + 'metadata?l=no')
        selected = [value for value in metadata if isinstance(value, dict) and value.get('id') == 70] if isinstance(metadata, list) else []
        if len(selected) != 1 or selected[0].get('type') != 'NorwegianInvestorWarningPage' or selected[0].get('name') != AUTHORITY:
            raise SourceError('Investor warning authority filter changed')
        rows, seen, totals = [], set(), None
        for page in range(1, self.max_pages + 1):
            params = [('q', ''), ('page', page), ('types', 70), ('language', 'no'), ('sort', 'date')]
            params.extend(('years', year) for year in years)
            result = self._json(API + '?' + urlencode(params))
            if not isinstance(result, dict) or any(type(result.get(key)) is not int for key in ('page', 'pageSize', 'total', 'totalPages')):
                raise SourceError('Investor warning pagination metadata is invalid')
            total, size, pages = result['total'], result['pageSize'], result['totalPages']
            if size != 25 or result['page'] != page or not 0 <= total <= self.max_records:
                raise SourceError('Investor warning selection exceeds bounds or pagination differs')
            if pages != (total + size - 1) // size or pages > self.max_pages:
                raise SourceError('Investor warning total-page count is inconsistent or excessive')
            if totals is not None and totals != (total, pages):
                raise SourceError('Investor warning selection changed during pagination')
            totals = total, pages
            batch = result.get('items')
            if not isinstance(batch, list) or len(batch) != min(size, total - len(rows)):
                raise SourceError('Investor warning result page is partial')
            if total == 0:
                return []
            for value in batch:
                if not isinstance(value, dict) or type(value.get('id')) is not int or not 0 < value['id'] < 10**10 or value['id'] in seen:
                    raise SourceError('Investor warning identity is invalid or repeated')
                seen.add(value['id'])
                if value.get('metaData') != AUTHORITY:
                    raise SourceError('Investor warning is outside the selected authority')
                published = local_date(value.get('published'))
                if published.year not in years or published > current:
                    raise SourceError('Investor warning is outside the selected publication years')
                url = value.get('url')
                parsed = urlparse(url) if isinstance(url, str) else None
                if (not parsed or parsed.scheme != 'https' or parsed.netloc != 'www.finanstilsynet.no'
                        or not re.fullmatch(r'/markedsadvarsler/\d{4}/[^/]+/?', parsed.path) or parsed.query or parsed.fragment):
                    raise SourceError('Investor warning article URL changed')
                rows.append({'key': str(value['id']), 'title': _text(value.get('name'), 'warning title', 2000),
                             'url': url, 'date': published.isoformat()})
            if page == pages:
                if len({row['url'] for row in rows}) != len(rows):
                    raise SourceError('Investor warning URL identity repeats')
                return sorted(rows, key=lambda row: row['key'])
        raise SourceError('Investor warning selection exceeds max_pages')

    def _detail(self, row):
        raw = document(self, row['url'])
        if not raw.rstrip().lower().endswith(b'</html>'):
            raise SourceError('Investor warning article is incomplete')
        soup = BeautifulSoup(raw, 'html.parser')
        headings, bodies = soup.select('main article h1'), soup.select('main article #articleMainBody')
        if len(headings) != 1 or len(bodies) != 1 or clean(headings[0]) != row['title']:
            raise SourceError('Investor warning article identity or body changed')
        section = headings[0].find_parent('section')
        leads = section.find_all('p') if section else []
        if not leads:
            raise SourceError('Investor warning article introduction is absent')
        intro = _text(' '.join(clean(node) for node in leads), 'warning introduction', 20000)
        body = _text(clean(bodies[0]), 'warning body', 50000)
        publications = []
        for script in soup.find_all('script'):
            content = script.get_text()
            if 'React.createElement(PublishInfo,' not in content:
                continue
            try:
                value, consumed = json.JSONDecoder(object_pairs_hook=_object).raw_decode(content.split('React.createElement(PublishInfo,', 1)[1].lstrip())
                publications.append(value['lastChangedDateText'])
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceError('Investor warning publication metadata changed') from exc
        if len(publications) != 1 or not isinstance(publications[0], str) or not publications[0].startswith('Sist publisert: '):
            raise SourceError('Investor warning last-published date is missing or ambiguous')
        published = local_date(publications[0][len('Sist publisert: '):]).isoformat()
        if published != row['date']:
            raise SourceError('Investor warning index and article publication dates disagree')
        fields = {'title': row['title'], 'last_published': published, 'authority': AUTHORITY,
                  'introduction': intro, 'notice_text': body}
        return {'key': row['key'], 'title': row['title'], 'url': row['url'], 'published': published, 'fields': fields}

    def _sweep(self, years, current):
        index = self._index(years, current)
        return index, [self._detail(row) for row in index]

    def read_records(self):
        current = today()
        years = list(range(current.year - self.years_back + 1, current.year + 1))
        first, second = self._sweep(years, current), self._sweep(years, current)
        if first != second:
            raise SourceError('Investor warning register changed between complete reads')
        return first[1]

    def describe_change(self, name, before, after):
        if name in {'title', 'introduction', 'notice_text'}:
            old, new = str(before), str(after)
            prefix = 0
            while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
                prefix += 1
            start = max(0, prefix - 50)
            def excerpt(value):
                end = min(len(value), start + 170)
                return ('…' if start else '') + value[start:end] + ('…' if end < len(value) else '')
            return f'{self.field_labels[name]} (utdrag rundt endring): {excerpt(old)} → {excerpt(new)}'
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']
        info = ['Offisiell markedsadvarsel fra Finanstilsynet', 'Sist publisert (kilden): ' + fields['last_published']]
        if event == 'changed':
            info.extend(details[1:])
        else:
            info.extend(('Kildens innledning: ' + fields['introduction'][:470],
                         'Kildens advarselstekst: ' + fields['notice_text'][:460]))
        # Four substantive fields can change; authority is contract-validated.
        return replace(item, alert_details=tuple(info[:7]) + (
            'Dette gjengir myndighetens advarsel. Navn kan være misbrukt; den navngitte legitime virksomheten kan være offer. Fravær beviser ikke at advarselen er trukket tilbake.',))
