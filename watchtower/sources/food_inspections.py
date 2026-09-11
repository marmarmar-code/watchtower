"""Inspection results from one explicit Mattilsynet Smilefjes restaurant page."""
from dataclasses import replace
from datetime import datetime, timezone
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from .changes import SnapshotSource, document, public_url
from .common import SourceError
from .identifiers import valid_orgnr


class FoodInspectionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if len(config.urls) != 1:
            raise ValueError('Select one explicit Smilefjes restaurant URL')
        self.url = public_url(config.urls[0])
        parsed = urlparse(self.url)
        if parsed.hostname != 'smilefjes.mattilsynet.no' or not re.fullmatch(r'/spisested/[^/]+/[^/]+\.[A-Z0-9]+/', parsed.path) or parsed.query or parsed.fragment:
            raise ValueError('Use an official Smilefjes restaurant page URL')
        self.orgnr = config.options.get('organisation_number')
        if not isinstance(self.orgnr, str) or not valid_orgnr(self.orgnr):
            raise ValueError('organisation_number must identify the selected restaurant')
        if self.complete or 'removed' in self.events:
            raise ValueError('Smilefjes pages cannot confirm inspection removals')
        self.field_labels = {'inspection_date': 'Tilsynsdato', 'result': 'Smilefjes-resultat',
                             'assessment': 'Mattilsynets vurdering', **self.field_labels}

    def read_records(self):
        soup = BeautifulSoup(document(self, self.url), 'html.parser')
        headings = soup.find_all('h1')
        if len(headings) != 1 or not headings[0].get_text(' ', strip=True):
            raise SourceError('Smilefjes restaurant title is absent or ambiguous')
        header = headings[0].parent.get_text(' ', strip=True)
        numbers = re.findall(r'Orgnr\.\s*(\d{9})(?!\d)', header)
        if numbers != [self.orgnr]:
            raise SourceError('Smilefjes page does not match the selected organisation')
        place = headings[0].get_text(' ', strip=True)
        controls = soup.select('[data-select_element_id]')
        if not controls or len(controls) > self.max_records:
            raise SourceError('Smilefjes inspection list is absent or exceeds bounds')
        records = []
        for control in controls:
            key = control.get('data-select_element_id')
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9]+_TilsynAvtale', key):
                raise SourceError('Smilefjes inspection identity is invalid')
            panels = soup.find_all(id=key)
            icons = control.select('[title]')
            if len(panels) != 1 or len(icons) != 1:
                raise SourceError('Smilefjes result is missing or ambiguous')
            result = icons[0].get('title')
            if not isinstance(result, str) or not result.strip() or len(result) > 200:
                raise SourceError('Smilefjes result label is invalid')
            short_date = control.get_text(' ', strip=True)
            try:
                if not re.fullmatch(r'\d{2}\.\d{2}\.\d{2}', short_date):
                    raise ValueError
                inspected = datetime.strptime(short_date, '%d.%m.%y').date()
                if inspected.year < 2016 or inspected > datetime.now(timezone.utc).date():
                    raise ValueError
            except ValueError:
                raise SourceError('Smilefjes inspection date is invalid') from None
            summary = panels[0].find('p', recursive=False)
            if summary is None or not summary.get_text(' ', strip=True):
                raise SourceError('Smilefjes inspection assessment is absent')
            assessment = summary.get_text(' ', strip=True)
            if len(assessment) > 1000:
                raise SourceError('Smilefjes assessment exceeds bounds')
            records.append({'key': key, 'title': f'{place} · tilsyn {inspected.isoformat()}',
                'url': self.url, 'published': None, 'fields': {'inspection_date': inspected.isoformat(),
                'result': result.strip(), 'assessment': assessment}})
        return records

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        label = 'Nytt observert tilsyn' if event == 'added' else 'Endret tilsynsresultat'
        return replace(item, alert_details=(label, *item.alert_details[1:]))
