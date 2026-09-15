"""Public BRREG list of years with an annual-account copy available."""
from datetime import datetime, timezone
import json

from .brreg import ANNUAL_REPORT_URL
from .changes import SnapshotSource, document, strings
from .common import SourceError
from .identifiers import valid_orgnr


class AccountDocumentsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.companies = strings(config.options.get('companies'), 'companies')
        if len(self.companies) > 10 or any(not valid_orgnr(number) for number in self.companies):
            raise ValueError('companies must contain at most ten valid organisation numbers')
        if config.urls:
            raise ValueError('account_documents uses the fixed public BRREG year-list endpoint')
        if self.complete or 'removed' in self.events:
            raise ValueError('The year list cannot confirm document removals')
        self.field_labels = {'organisation_number': 'Organisasjonsnummer',
                             'year': 'Regnskapsår', 'availability': 'Opplysning', **self.field_labels}

    def read_records(self):
        rows = []
        for orgnr in self.companies:
            url = ANNUAL_REPORT_URL.format(orgnr=orgnr, year='aar')
            try:
                years = json.loads(document(self, url))
            except (ValueError, UnicodeError) as exc:
                raise SourceError('BRREG year list is invalid JSON') from exc
            current_year = datetime.now(timezone.utc).year
            if not isinstance(years, list) or any(not isinstance(year, str) or len(year) != 4
                    or not year.isascii() or not year.isdigit() or not 1900 <= int(year) <= current_year for year in years):
                raise SourceError('BRREG year list contains invalid years')
            if len(years) != len(set(years)):
                raise SourceError('BRREG year list repeated a year')
            for year in sorted(years):
                rows.append({'key': f'{orgnr}:{year}', 'title': f'Årsregnskap {year} · {orgnr}',
                    'url': url, 'published': None, 'fields': {'organisation_number': orgnr,
                    'year': year, 'availability': 'Året er oppført i BRREGs liste over tilgjengelige regnskapskopier'}})
                if len(rows) > self.max_records:
                    raise SourceError('BRREG year list exceeds max_records')
        return rows
