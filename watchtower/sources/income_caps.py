"""Published distribution-grid income caps from explicit RME decision years."""
from dataclasses import replace
from decimal import Decimal, localcontext
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError
from .identifiers import valid_orgnr
from .workbooks import table_rows

BASE = 'https://www.nve.no/reguleringsmyndigheten/bransje/bransjeoppgaver/inntektsrammer/'
PAGE_URL = BASE+'inntektsrammer-for-2025-vedtak/'


def text(value):
    return ' '.join(value.split())


def amount(value):
    if len(value) > 80 or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,2})?', value):
        raise SourceError('Income cap value is not a bounded number')
    number = Decimal(value)
    if not number.is_finite() or number.adjusted() > 15 or number.as_tuple().exponent < -25 or len(number.as_tuple().digits) > 40:
        raise SourceError('Income cap value exceeds numeric bounds')
    rendered = format(number, 'f')
    return ('0' if not number else rendered.rstrip('0').rstrip('.') if '.' in rendered else rendered)


def footer_labels(cost_year):
    return [
        ('Kalibrering slik at IR= K', 'Sum IR'),
        ('', 'Sum K'),
        ('', 'Sum (IR-K)'),
        ('', 'Sum(IR-K)/Sum AKG'),
        (f'Rekalibrering: Avvik i faktisk kostnadsgrunnlag for {cost_year} og kostnadsgrunnlag benyttet i vedtak om IR for {cost_year}',
         f'Sum kostnadsgrunnlag u/kap.kostn. fra vedtak om IR for {cost_year}'),
        ('', f'Sum faktisk kostnad u/kap.kostn. for bransjen i {cost_year}'),
        ('', f'Avvik inkludert renter for {cost_year} og {cost_year+1}'),
        ('', 'Sum renter på avvik'),
        ('', 'Sum renter på avvik/Sum AKG'),
    ]


class IncomeCapsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if not 1 <= len(config.urls) <= 3 or len(set(config.urls)) != len(config.urls):
            raise ValueError('income_caps requires one to three distinct official decision pages')
        self.pages = []
        for url in config.urls:
            match = re.fullmatch(re.escape(BASE)+r'inntektsrammer-for-(20[0-9]{2})-vedtak/', url)
            if not match:
                raise ValueError('income_caps accepts only explicit official decision-year pages')
            self.pages.append((url, int(match.group(1))))
        if self.complete or 'removed' in self.events:
            raise ValueError('Published income caps cannot confirm withdrawals')
        self.max_unpacked = integer(config.options.get('max_unpacked_bytes', 20000000), 'max_unpacked_bytes', 1024, 60000000)
        self.max_sheet_rows = integer(config.options.get('max_sheet_rows', 500), 'max_sheet_rows', 10, 3000)
        self.field_labels = {'internal_id':'Internt RME-ID', 'company_name':'Nettselskap',
                             'cost_base_year':'Kostnadsgrunnlagets år', 'cap_1000_nok':'Inntektsramme (1000 NOK)', **self.field_labels}

    def _read(self, url):
        response = self.get(url, stream=True, allow_redirects=False, accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200:
                raise SourceError('Income-cap source returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64*1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Income-cap source exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def _attachment(self, page, year):
        soup = BeautifulSoup(self._read(page), 'html.parser')
        titles = soup.select('h1')
        if len(titles) != 1 or text(titles[0].get_text(' ', strip=True)) != f'Inntektsrammer for {year} - vedtak':
            raise SourceError('Income-cap decision title or year changed')
        label = f'Vedtak om inntektsramme {year} for andre nettselskaper (excel)'
        links = [urljoin(page, a['href']) for a in soup.select('a[href]') if text(a.get_text(' ', strip=True)) == label]
        if len(links) != 1:
            raise SourceError('Income-cap attachment is missing or ambiguous')
        url = urlparse(links[0])
        if (url.scheme != 'https' or url.netloc != 'www.nve.no' or url.query or url.fragment or url.params
                or not re.fullmatch(r'/media/[0-9]+/[a-zA-Z0-9_-]+\.xlsx', url.path)):
            raise SourceError('Income-cap attachment is not an official workbook')
        return links[0]

    def _records(self, raw, page, year):
        rows = table_rows(raw, f'Inntektsramme {year}', self.max_unpacked, self.max_sheet_rows, 27,
                          allow_cached_formulas=True, allow_blank_rows=True)
        if (rows[0][0] != 'Tall i 1000 NOK' or rows[0][18] != 'Beregning og kalibrering av inntektsramme'
                or any(value for i,value in enumerate(rows[0]) if i not in {0,18})):
            raise SourceError('Income-cap unit changed')
        header = [text(v) for v in rows[1]]
        match = re.fullmatch(r'Selskapsnavn \(fra eRapp (20[0-9]{2})\)', header[2])
        if header[:2] != ['Orgnr', 'ID'] or not match or header[3] != f'Inntektsramme {year}' or header[26]:
            raise SourceError('Income-cap table headers changed')
        cost_year = int(match.group(1))
        if cost_year >= year:
            raise SourceError('Income-cap cost-base year is invalid')
        records, orgs, ids, amounts = [], set(), set(), []
        position = 2
        while position < len(rows) and rows[position][0]:
            row = rows[position]
            org, ident, name = row[0], row[1], text(row[2])
            if not valid_orgnr(org) or org in orgs or not re.fullmatch(r'[1-9][0-9]{0,8}', ident) or ident in ids or not name or row[26]:
                raise SourceError('Income-cap company identity is invalid or repeated')
            cap = amount(row[3])
            if Decimal(cap) < 0:
                raise SourceError('Income cap is unexpectedly negative')
            orgs.add(org); ids.add(ident); amounts.append(Decimal(cap))
            records.append({'key':f'{org}:{year}:vedtak', 'title':f'Inntektsramme {year} · {name}',
                            'url':page, 'published':None, 'cap_year':year, 'stage':'vedtak', 'unit':'1000 NOK',
                            'fields':{'internal_id':ident, 'company_name':name, 'cost_base_year':cost_year, 'cap_1000_nok':cap}})
            position += 1
        if not records:
            raise SourceError('Income-cap workbook has no company rows')
        # Blank spacers may occur; every populated row after the companies must
        # be the explicit total or the recognized calibration footer.
        remaining = [r for r in rows[position:] if any(r)]
        expected = footer_labels(cost_year)
        if len(remaining) != 1+len(expected):
            raise SourceError('Income-cap total or calibration footer is incomplete')
        total = remaining[0]
        if any(total[:2]) or text(total[2]) != 'Sum' or total[26]:
            raise SourceError('Income-cap total row changed')
        total_amount = Decimal(amount(total[3]))
        for value in total[4:26]:
            if value:
                amount(value)
        with localcontext() as context:
            context.prec = 80
            if abs(sum(amounts)-total_amount) > Decimal('0.0001'):
                raise SourceError('Income-cap company amounts disagree with the published total')
        for row, labels in zip(remaining[1:], expected):
            if (text(row[8]), text(row[10])) != labels or any(value for i, value in enumerate(row) if i not in {8,10,13}):
                raise SourceError('Income-cap calibration footer changed')
            amount(row[13])
        return records

    def read_records(self):
        records = []
        for page, year in self.pages:
            link = self._attachment(page, year)
            rows = self._records(self._read(link), page, year)
            if self._attachment(page, year) != link:
                raise SourceError('Income-cap attachment changed during reading')
            records.extend(rows)
            if len(records) > self.max_records:
                raise SourceError('Income-cap selection exceeds max_records')
        return sorted(records, key=lambda r:r['key'])

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        info = ('Nyobservert årsoppføring' if event == 'added' else 'Endret publisert rammeoppføring',
                f"Rammeår: {row['cap_year']} · Kostnadsgrunnlag: {row['fields']['cost_base_year']}",
                f"Inntektsramme: {row['fields']['cap_1000_nok']} (1000 NOK)")
        if event == 'changed':
            info += tuple(d[:800] for d in details[1:])
        info += ('Kildens lagrede verdi; en endret celle dokumenterer ikke et nytt vedtak',)
        return replace(item, alert_details=info)
