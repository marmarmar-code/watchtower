"""Published inspection reactions by year and main industry, not case records."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError
from .powerbi_public import PublicReport, flat_table

PAGE = 'https://www.arbeidstilsynet.no/om-oss/statistikk/tilsynsstatistikk/'
MODEL = 'Statistikk Arbeidstilsynet Tilsyn'
ENTITY = 'PowerBIEkstern Tilsyn_vedtak'
PROPERTIES = (
    'Ar', 'Hovednaring', 'AntallTilsynVedtaPålegg',
    'AntallTilsynVedtaStansSomPress', 'AntallTilsynVedtaStansVedOverhengendeFare',
    'AntallTilsynVedtaTvangsmulkt', 'AntallTilsynVedtaOvertredelsesgebyr',
)
LABELS = {
    'AntallTilsynVedtaPålegg': 'Tilsyn med pålegg',
    'AntallTilsynVedtaStansSomPress': 'Tilsyn med stans som pressmiddel',
    'AntallTilsynVedtaStansVedOverhengendeFare': 'Tilsyn med stans ved overhengende fare',
    'AntallTilsynVedtaTvangsmulkt': 'Tilsyn med tvangsmulkt',
    'AntallTilsynVedtaOvertredelsesgebyr': 'Tilsyn med overtredelsesgebyr',
}


def report_query(model, limit):
    """Require the actual published visual before flattening its grouping."""
    try:
        sections = [s for s in model['exploration']['sections'] if s['displayName'] == 'Vedtak per næring']
        if len(sections) != 1:
            raise ValueError()
        published = [json.loads(v['query']) for v in sections[0]['visualContainers'] if v.get('query')]
        candidates = [q for q in published if 'AntallTilsynVedtaPålegg' in json.dumps(q, ensure_ascii=False)]
        if len(candidates) != 1:
            raise ValueError()
        result = deepcopy(candidates[0])
        if len(result['Commands']) != 1 or set(result['Commands'][0]) != {'SemanticQueryDataShapeCommand'}:
            raise ValueError()
        command = result['Commands'][0]['SemanticQueryDataShapeCommand']
        query = command['Query']
        if set(query)-{'Version', 'From', 'Select', 'OrderBy'} or query['Version'] != 2 or query['From'] != [{'Name':'p', 'Entity':ENTITY, 'Type':0}]:
            raise ValueError()
        selects = query['Select']
        if len(selects) != len(PROPERTIES):
            raise ValueError()
        for index, (select, prop) in enumerate(zip(selects, PROPERTIES)):
            column = {'Expression': {'SourceRef': {'Source': 'p'}}, 'Property': prop}
            expected = {'Column': column, 'Name': ENTITY+'.'+prop} if index < 2 else {
                'Aggregation': {'Expression': {'Column': column}, 'Function': 0},
                'Name': 'Sum('+ENTITY+'.'+prop+')'}
            if {k:v for k,v in select.items() if k != 'NativeReferenceName'} != expected:
                raise ValueError()
        command['Binding'] = {
            'Primary': {'Groupings': [{'Projections': list(range(len(PROPERTIES)))}]},
            'DataReduction': {'DataVolume':3, 'Primary': {'Window': {'Count':limit}}}, 'Version':1}
        return result, selects
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise SourceError('Inspection report published main-industry table contract changed') from None


class InspectionReportSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.options.get('profile') != 'main_industry':
            raise ValueError('inspection_report requires profile = main_industry')
        if config.urls not in ((), (PAGE,)):
            raise ValueError('inspection_report accepts only the official inspection statistics page')
        if self.complete or 'removed' in self.events or self.allow_empty:
            raise ValueError('Inspection report requires nonempty snapshots without removal events')
        self.industries = strings(config.options.get('main_industries'), 'main_industries')
        if len(self.industries) > 10 or any(v != ' '.join(v.split()) for v in self.industries):
            raise ValueError('main_industries requires 1–10 exact main-industry names')
        self.from_year = integer(config.options.get('from_year', 2024), 'from_year', 2000, 2100)
        self.limit = integer(config.options.get('max_report_rows', 1000), 'max_report_rows', 100, 5000)
        self.max_age = integer(config.options.get('max_model_age_days', 7), 'max_model_age_days', 1, 45)
        self.field_labels = {**LABELS, **self.field_labels}

    def _embed(self):
        response = self.get(PAGE, stream=True, allow_redirects=False)
        try:
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Inspection statistics page exceeds max_bytes')
                chunks.append(chunk)
            soup = BeautifulSoup(b''.join(chunks), 'html.parser')
        finally:
            response.close()
        if [h.get_text(' ', strip=True) for h in soup.select('h1')] != ['Tilsynsstatistikk']:
            raise SourceError('Inspection statistics page identity changed')
        frames = [f.get('src', '') for f in soup.select('iframe')
                  if urlparse(f.get('src', '')).hostname == 'app.powerbi.com']
        if len(frames) != 1:
            raise SourceError('Inspection public report is missing or ambiguous')
        return frames[0]

    def _poll(self):
        report = PublicReport(self, self._embed(), MODEL, self.max_age)
        query, selects = report_query(report.model, self.limit)
        rows = flat_table(report.query(query), selects, self.limit, entities={'p':ENTITY})
        seen = set()
        current_year = datetime.now(timezone.utc).year
        for row in rows:
            year, industry, *values = row
            if type(year) is not int or not 2000 <= year <= current_year:
                raise SourceError('Inspection report year is invalid or future-dated')
            if not isinstance(industry, str) or not industry or industry != ' '.join(industry.split()) or len(industry) > 300:
                raise SourceError('Inspection report main-industry label is invalid')
            if any(type(value) is not int or value < 0 for value in values):
                raise SourceError('Inspection report counts must be nonnegative integers, not missing values')
            identity = (year, industry)
            if identity in seen:
                raise SourceError('Inspection report repeats a year/main-industry identity')
            seen.add(identity)
        return sorted(rows, key=canonical), report.refreshed.isoformat()

    def read_records(self):
        rows, refreshed = self._poll()
        again, refresh_again = self._poll()
        if rows != again or refreshed != refresh_again:
            raise SourceError('Inspection report changed between complete reads')
        selected = [row for row in rows if row[0] >= self.from_year and row[1] in self.industries]
        if {row[1] for row in selected} != set(self.industries):
            raise SourceError('Inspection report lacks a configured main industry in the selected years')
        self.report_rows, self.model_refreshed = len(rows), refreshed
        return [{'key':canonical(row[:2]), 'title':f'Arbeidstilsynet: {row[1]} ({row[0]})',
                 'url':PAGE, 'year':row[0], 'main_industry':row[1],
                 'fields':dict(zip(PROPERTIES[2:], row[2:]))} for row in selected]

    def fetch_with_state(self, previous):
        staged = self._next
        try:
            items = super().fetch_with_state(previous)
            old = ((previous or {}).get('source_state') or {}).get('records', {})
            if old.get('scope') == self.scope:
                if old.get('model_refreshed', '') > self.model_refreshed:
                    raise SourceError('Inspection report source refresh regressed')
                # A rolling report window is not evidence of erased history.
                self._next['rows'] = {**old.get('rows', {}), **self._next['rows']}
                if len(self._next['rows']) > self.max_records * 2:
                    raise SourceError('Inspection retained history exceeds the configured bound')
            self._next.update(model_refreshed=self.model_refreshed, report_rows=self.report_rows)
            return items
        except Exception:
            self._next = staged
            raise

    def _item(self, row, event, details, suppress):
        message = ['Nyobservert årsaggregat' if event == 'added' else 'Reviderte årsaggregater']
        message.extend(details[1:])
        message.append(f"År: {row['year']} · Hovednæring: {row['main_industry']}")
        message.append('Antall tilsyn med vedtak, aggregert per år og hovednæring. '
                       'Tallene er ikke nye enkeltvedtak; samme tilsyn kan ha flere reaksjoner. '
                       'Underliggende næringer og næringsgrupper er ikke dekket.')
        return super()._item(row, event, message, suppress)
