"""Monthly employer notifications about possible redundancies, by SN2025 industry."""
from dataclasses import replace
from datetime import date
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, document, integer, strings, shown
from .common import SourceError
from .workbooks import table_rows

PAGE = 'https://www.nav.no/no/nav-og-samfunn/statistikk/arbeidssokere-og-stillinger-statistikk/permitteringsvarsel%20og%20permitterte'
TITLE = 'AG200 Melding om permittering og masseoppsigelse. Måned'
TYPES = ('Permittering uten lønn', 'Masseoppsigelse')
SUPPRESSED = 'Skjermet/ikke oppgitt (*)'


def month(value):
    if not re.fullmatch(r'20\d{2}(?:0[1-9]|1[0-2])', value):
        raise SourceError('NAV report month is invalid')
    return value[:4] + '-' + value[4:]


def month_number(value):
    return int(value[:4]) * 12 + int(value[-2:])


def months_complete(values):
    positions = sorted(month_number(m) for m in values)
    if not positions or len(positions) != len(set(positions)) or positions != list(range(positions[0], positions[-1] + 1)):
        raise SourceError('NAV report months are repeated or incomplete')


def count(value, *, suppress=True):
    if value == '*' and suppress:
        return SUPPRESSED
    if not re.fullmatch(r'\d+', value):
        raise SourceError('NAV affected-person count is missing or invalid')
    return int(value)


def report_date(rows):
    values = [value for row in rows for value in row if value.startswith('Rapport oppdatert:')]
    if len(values) != 1 or not re.fullmatch(r'Rapport oppdatert: \d{2}\.\d{2}\.\d{4}', values[0]):
        raise SourceError('NAV report update date is missing or ambiguous')
    d, m, y = values[0].split(': ')[1].split('.')
    try:
        return date(int(y), int(m), int(d))
    except ValueError as exc:
        raise SourceError('NAV report update date is invalid') from exc


class NavRedundancySource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('nav_redundancy accepts only the official monthly NAV report')
        if self.complete or self.allow_empty or 'removed' in self.events:
            raise ValueError('NAV report absence cannot confirm zero notified redundancies')
        self.industries = strings(config.options.get('industry_codes'), 'industry_codes')
        if len(self.industries) > 22 or any(not re.fullmatch(r'[A-V]', code) for code in self.industries):
            raise ValueError('industry_codes must contain 1–22 SN2025 main-industry letters')
        self.from_year = integer(config.options.get('from_year', 2025), 'from_year', 2025, 2100)
        self.max_age = integer(config.options.get('max_report_age_days', 75), 'max_report_age_days', 1, 180)
        self.max_unpacked = integer(config.options.get('max_unpacked_bytes', 5000000), 'max_unpacked_bytes', 1024, 20000000)
        self.field_labels = {'affected_people': 'Berørte personer i innmeldte planer', **self.field_labels}

    def fetch_with_state(self, previous):
        saved = ((previous or {}).get('source_state') or {}).get('records', {})
        self._previous_rows = saved.get('rows', {}) if saved.get('scope') == self.scope else {}
        return super().fetch_with_state(previous)

    def _parse(self, raw, attachment_month, url):
        def sheet(name):
            return table_rows(raw, name, self.max_unpacked, 1000, 60,
                              allow_blank_rows=True, allow_leading_blank_rows=True)
        industries, national, notes = sheet('Næring Hovedområde'), sheet('Tidsserie'), sheet('Om statistikken')
        if len(industries) < 8 or len(national) < 9 or len(notes) < 3:
            raise SourceError('NAV workbook is truncated')
        refreshed = report_date(industries)
        if report_date(national) != refreshed or report_date(notes) != refreshed:
            raise SourceError('NAV worksheets have inconsistent update dates')
        if not 0 <= (date.today() - refreshed).days <= self.max_age:
            raise SourceError('NAV report update date is stale or in the future')
        if (industries[1][1] != 'Kilde: NAV' or industries[1][3] != TITLE
                or national[1][3] != TITLE or industries[2][3] != 'Næring Hovedområde. Antall berørte personer'):
            raise SourceError('NAV workbook identity changed')
        notes_text = ' '.join(value for row in notes for value in row)
        if 'SN2025' not in notes_text or 'Virksomheter med gammel næringstandard vises som ukjent' not in notes_text:
            raise SourceError('NAV industry-standard limitation is missing')
        # Reconcile national totals against the independent time-series sheet.
        if (national[6][2], national[6][6]) != TYPES or national[7][1] != 'Melding mottatt måned':
            raise SourceError('NAV national time-series headers changed')
        if national[7][4] != 'Antall berørte personer' or national[7][7] != 'Antall berørte personer':
            raise SourceError('NAV national measurement headers changed')
        national_counts = {}
        for row in national[8:]:
            period = month(row[1])
            if period in national_counts:
                raise SourceError('NAV national month repeats')
            national_counts[period] = (count(row[4], suppress=False), count(row[7], suppress=False))
        months_complete(national_counts)
        if max(national_counts) != attachment_month:
            raise SourceError('NAV attachment month does not match workbook content')
        if not 0 <= month_number(refreshed.isoformat()[:7]) - month_number(attachment_month) <= 2:
            raise SourceError('NAV latest data period is inconsistent with report date')
        starts = [(index, row[1]) for index, row in enumerate(industries) if row[1] in TYPES]
        if [kind for _, kind in starts] != list(TYPES):
            raise SourceError('NAV industry notification types are incomplete or repeated')
        records, all_periods = [], None
        for block, (start, kind) in enumerate(starts):
            end = starts[block + 1][0] if block + 1 < len(starts) else len(industries)
            headings = [(i, row) for i, row in enumerate(industries[start:end], start)
                        if row[2].startswith('Næring Hovedområde')]
            if len(headings) != 1 or headings[0][1][2] != 'Næring Hovedområde SN2025':
                raise SourceError('NAV industry classification changed; SN2025 is required')
            header_index, header = headings[0]
            columns = {i: month(v) for i, v in enumerate(header) if i >= 4 and v}
            months_complete(columns.values())
            if not 1 <= len(columns) <= 36 or max(columns.values()) != attachment_month:
                raise SourceError('NAV industry periods are incomplete or exceed bounds')
            if all_periods is not None and all_periods != columns:
                raise SourceError('NAV notification types cover different months')
            all_periods = columns
            body = [r for r in industries[header_index + 1:end] if any(r)]
            if not body or body[0][2] != 'I alt' or body[-1][2] != 'Ukjent, gammel næringsstandard':
                raise SourceError('NAV industry total or unknown-standard row is missing')
            totals, seen = {}, set()
            for i, period in columns.items():
                totals[i] = count(body[0][i], suppress=False)
                if period not in national_counts or totals[i] != national_counts[period][block]:
                    raise SourceError('NAV industry totals disagree with national time series')
            for row in body[1:]:
                code, label = row[1:3]
                if not code and label == 'Ukjent, gammel næringsstandard':
                    code = 'unknown_old_standard'
                if (code != 'unknown_old_standard' and not re.fullmatch(r'[A-V]', code)) or not label or code in seen:
                    raise SourceError('NAV industry row identity is invalid or repeated')
                seen.add(code)
                values = {i: count(row[i]) for i in columns}
                if any(row[i] for i in range(4, len(row)) if i not in columns):
                    raise SourceError('NAV industry row has values outside period columns')
                if code not in self.industries:
                    continue
                for i, period in columns.items():
                    if int(period[:4]) < self.from_year:
                        continue
                    records.append({'key': '|'.join(('SN2025', kind, code, period)),
                        'title': f'{kind} – {code} {label} – {period}', 'url': url, 'published': None,
                        'industry_code': code, 'industry': label, 'classification': 'SN2025',
                        'notification_type': kind, 'period': period,
                        'fields': {'affected_people': values[i]}})
            if not set(self.industries) <= seen:
                raise SourceError('An explicitly selected NAV industry is absent')
            for i in columns:
                visible = [count(row[i]) for row in body[1:]]
                if sum(v for v in visible if type(v) is int) > totals[i]:
                    raise SourceError('NAV visible industry counts exceed the national total')
        return sorted(records, key=lambda r: r['key']), refreshed.isoformat(), min(all_periods.values())

    def _export(self):
        soup = BeautifulSoup(document(self, PAGE), 'html.parser')
        links = {}
        for anchor in soup.select('a[href]'):
            if 'melding om permittering og masseoppsigelser' not in anchor.get_text(' ', strip=True).casefold():
                continue
            url = urljoin(PAGE, anchor['href']); parsed = urlparse(url)
            match = re.search(r'/(20\d{4})_AG200[^/]*\.xlsx$', unquote(parsed.path))
            if not match:
                continue
            if parsed.scheme != 'https' or parsed.netloc != 'www.nav.no' or not parsed.path.startswith('/_/attachment/download/') or parsed.query or parsed.fragment:
                raise SourceError('NAV workbook URL is outside the official attachment service')
            period = month(match[1])
            if period in links and links[period] != url:
                raise SourceError('NAV month has ambiguous workbook links')
            links[period] = url
        if not links:
            raise SourceError('NAV monthly notification workbook link is missing')
        period = max(links)
        return self._parse(document(self, links[period]), period, links[period])

    def read_records(self):
        rows, refreshed, first_period = self._export()
        if (rows, refreshed, first_period) != self._export():
            raise SourceError('NAV monthly report changed during reading')
        if not rows:
            raise SourceError('NAV selection is empty')
        latest = max(r['period'] for r in rows)
        previous = getattr(self, '_previous_rows', {})
        if previous and max(v['row']['period'] for v in previous.values()) > latest:
            raise SourceError('NAV latest report month regressed')
        current = {r['key']: r for r in rows}
        for key, saved in previous.items():
            if key not in current:
                if saved['row']['period'] >= first_period:
                    raise SourceError('A previously observed NAV industry-period is absent')
                # Keep history when the published time window moves forward.
                current[key] = saved['row']
        if len(current) > self.max_records:
            raise SourceError('NAV selected history exceeds max_records')
        self.report_summary = {'report_updated': refreshed, 'latest_month': latest,
                               'classification': 'SN2025', 'records': len(current)}
        self.coverage_warnings = ['nav_old_industry_standard_is_unknown']
        if any(r['fields']['affected_people'] == SUPPRESSED for r in rows):
            self.coverage_warnings.append('nav_suppressed_industry_counts')
        return sorted(current.values(), key=lambda r: r['key'])

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        value = details[1] if event == 'changed' and len(details) > 1 else self.field_labels['affected_people'] + ': ' + shown(row['fields']['affected_people'])
        info = ('Nyobservert månedstall for nedbemanningsmeldinger' if event == 'added' else 'Revidert månedstall for nedbemanningsmeldinger',
                f"Mottaksmåned: {row['period']} · {row['notification_type']}",
                f"SN2025 {row['industry_code']}: {row['industry']}", value,
                'Mulige tiltak meldt av arbeidsgiver; ikke faktisk gjennomførte oppsigelser eller permitteringer.',
                'Næring er SN2025; virksomheter med gammel næringsstandard vises som ukjent hos NAV.',
                'Skjermet/ikke oppgitt (*) beholdes ukjent. NAVs innmeldte tall kan inneholde duplikater.')
        return replace(item, alert_details=info)
