"""Annual publication-level production grants from the official award workbook."""
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError
from .workbooks import table_rows

PAGE_URL = 'https://www.medietilsynet.no/mediestotte/produksjonstilskudd/'


def _text(value):
    return ' '.join(value.split())


def _money(value):
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise SourceError('Production grant amount is not numeric') from exc
    if not number.is_finite() or number != number.to_integral_value() or abs(number) > 10000000000:
        raise SourceError('Production grant amount is not a bounded whole NOK amount')
    return int(number)


def _nok(value, *, signed=False):
    return format(value, '+,' if signed else ',').replace(',', ' ') + ' kr'


class ProductionGrantsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE_URL,)):
            raise ValueError('production_grants accepts only the official scheme page')
        if self.complete or 'removed' in self.events:
            raise ValueError('Absence from annual grant tables cannot establish rejection or withdrawal')
        self.max_unpacked_bytes = integer(config.options.get('max_unpacked_bytes', 2000000),
                                          'max_unpacked_bytes', 1024, 10000000)
        self.max_sheet_rows = integer(config.options.get('max_sheet_rows', 2000),
                                      'max_sheet_rows', 5, 10000)
        self.field_labels = {'amount_nok': 'Tildelt beløp', 'previous_amount_nok': 'Foregående årsbeløp',
                             'category': 'Tilskuddskategori', **self.field_labels}

    def _read(self, url):
        parsed = urlparse(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'www.medietilsynet.no'
                or parsed.query or parsed.fragment or parsed.params):
            raise SourceError('Production grant link is not an official attachment')
        response = self.get(url, stream=True, allow_redirects=False,
                            accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Production grant source returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Production grant source exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def _latest_workbook(self):
        soup = BeautifulSoup(self._read(PAGE_URL), 'html.parser')
        choices = {}
        for anchor in soup.select('a[href]'):
            label = _text(anchor.get_text(' ', strip=True))
            match = re.fullmatch(r'Oversikt over tilsk[ou](?:dd|tt) gitt i (20[0-9]{2})', label, re.I)
            if not match:
                continue
            year = int(match.group(1))
            if not 2000 <= year <= date.today().year:
                raise SourceError('Production grant reference year is invalid')
            url = urljoin(PAGE_URL, anchor['href'])
            parsed = urlparse(url)
            if (parsed.scheme != 'https' or parsed.netloc != 'www.medietilsynet.no'
                    or parsed.query or parsed.fragment or parsed.params
                    or not re.fullmatch(r'/globalassets/[a-zA-Z0-9_./-]+\.(?:xlsx|pdf)', parsed.path)):
                raise SourceError('Production grant table link is not an official attachment')
            choices.setdefault(year, set()).add(url)
        if not choices:
            raise SourceError('Production grant annual table links are missing')
        year = max(choices)
        # The authority publishes the Excel version alongside the PDF under the
        # same filename. Prefer explicit Excel links; otherwise validate the
        # sibling workbook. A missing/changed file fails rather than parsing PDF
        # or silently continuing with an obsolete year.
        candidates = {url[:-4] + '.xlsx' if url.endswith('.pdf') else url for url in choices[year]}
        if len(candidates) != 1:
            raise SourceError('Latest production grant workbook is ambiguous')
        return year, candidates.pop()

    def read_records(self):
        year, url = self._latest_workbook()
        rows = table_rows(self._read(url), 'Ark1', self.max_unpacked_bytes,
                          self.max_sheet_rows, 7, allow_cached_formulas=True, allow_blank_rows=True)
        title = _text(rows[0][0])
        if title != f'Endelig tildeling av produksjonstilskudd {year}':
            raise SourceError('Production grant workbook title/year differs from the official link')
        header_indices = [i for i, row in enumerate(rows[:10]) if _text(row[0]) == 'Mediets navn']
        if len(header_indices) != 1:
            raise SourceError('Production grant worksheet header is absent or ambiguous')
        header_index = header_indices[0]
        headers = [_text(value).replace('- ', '') for value in rows[header_index]]
        expected = ['Mediets navn', 'Tilskuddskategori', f'Produksjonstilskudd {year}',
                    f'Produksjonstilskudd {year - 1}', 'Endring i kroner', 'Endring i prosent', '']
        if headers != expected:
            raise SourceError('Production grant worksheet columns or units changed')
        records, seen = [], set()
        for row in rows[header_index + 1:]:
            name, category = _text(row[0]), _text(row[1])
            if not name or not category or row[6]:
                raise SourceError('Production grant publication/category is missing or extra data appeared')
            amount, previous, stated_delta = (_money(value) for value in row[2:5])
            if amount < 0 or previous < 0 or amount - previous != stated_delta:
                raise SourceError('Production grant amounts do not reconcile with the stated change')
            key = f'{year}:publication:{name.casefold()}'
            if key in seen:
                raise SourceError('Production grant publication/year identity is duplicated')
            seen.add(key)
            records.append({'key': key, 'title': f'Produksjonstilskudd {year}: {name}',
                            'url': url, 'published': None, 'reference_year': year,
                            'publication': name, 'fields': {'category': category,
                                'amount_nok': amount, 'previous_amount_nok': previous}})
            if len(records) > self.max_records:
                raise SourceError('Production grant table exceeds max_records')
        return sorted(records, key=lambda row: row['key'])

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        prior = ((previous or {}).get('source_state') or {}).get('records', {})
        if prior.get('scope') == self.scope:
            old_years = [entry['row'].get('reference_year', 0) for entry in prior.get('rows', {}).values()]
            new_years = [entry['row']['reference_year'] for entry in self._next['rows'].values()]
            if old_years and new_years and max(old_years) > min(new_years):
                raise SourceError('Production grant year regressed; previous state preserved')
        return items

    def describe_change(self, name, before, after):
        if name in {'amount_nok', 'previous_amount_nok'}:
            return f'{self.field_labels[name]}: {_nok(before)} → {_nok(after)}'
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        year, amount, previous = row['reference_year'], row['fields']['amount_nok'], row['fields']['previous_amount_nok']
        delta = amount - previous
        comparison = f'Endring fra {year - 1}: {_nok(delta, signed=True)}'
        if previous:
            percent = (Decimal(delta) / Decimal(previous) * 100).quantize(Decimal('0.1'))
            comparison += f' ({percent:+} %)'
        info = ('Nyobservert årstildeling' if event == 'added' else 'Revidert årstildeling',
                f'Tildeling {year}: {_nok(amount)}', f'Tildeling {year - 1}: {_nok(previous)}',
                comparison, f"Tilskuddskategori: {row['fields']['category']}")
        if event == 'changed':
            info += tuple(details[1:])
        info += ('Publisert årstabell; observasjonen angir ikke datoen for et nytt enkeltvedtak.',)
        return replace(item, alert_details=info)
