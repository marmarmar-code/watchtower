"""Product-specific quality-defect communications published by DMP."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from difflib import SequenceMatcher
from io import BytesIO
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError
from .dmp_wholesale import clean, official_download

PAGE = 'https://www.dmp.no/tilvirkning-import-og-salg/import-og-grossistvirksomhet-med-legemidler/tiltak-ved-kvalitetssvikt-pa-legemidler/kjare-helsepersonell-brev-om-kvalitetssvikt'
TITLE = 'Kjære helsepersonell-brev om kvalitetssvikt'
CAPTION = 'Liste over kjære helsepersonell-brev for kvalitetssvikt'
DIRECTORY = '/globalassets/documents/tilvirkning-import-og-salg/import--og-grossistvirksomhet/kvalitetssvikt/kjare-helsepersonell-brev/'
MONTHS = {name: index for index, name in enumerate(('januar', 'februar', 'mars', 'april', 'mai', 'juni',
          'juli', 'august', 'september', 'oktober', 'november', 'desember'), 1)}


def periods(value):
    match = re.fullmatch(r'([A-Za-zæøåÆØÅ]+) (20[0-9]{2})(?:, oppdatert ([A-Za-zæøåÆØÅ]+) (20[0-9]{2}))?', value)
    if not match:
        raise SourceError('DMP quality-letter date no longer has a verified month contract')
    def period(month, year):
        number = MONTHS.get(month.casefold())
        if not number:
            raise SourceError('DMP quality-letter month is unknown')
        parsed_period = f'{year}-{number:02}'
        if parsed_period > date.today().strftime('%Y-%m'):
            raise SourceError('DMP quality-letter period lies in the future')
        return parsed_period
    issued = period(match[1], match[2])
    updated = period(match[3], match[4]) if match[3] else ''
    if updated and updated < issued:
        raise SourceError('DMP quality-letter revision predates its original period')
    return issued, updated


def pdf_text(raw, max_pages):
    import pdfplumber
    if not raw.startswith(b'%PDF-'):
        raise SourceError('DMP quality letter is not a PDF')
    try:
        with pdfplumber.open(BytesIO(raw)) as pdf:
            if not 1 <= len(pdf.pages) <= max_pages:
                raise SourceError('DMP quality letter exceeds the page bound')
            parts = []
            for page in pdf.pages:
                if page.width > 2000 or page.height > 2000 or len(page.chars) > 40000:
                    raise SourceError('DMP quality-letter page exceeds extraction bounds')
                text = page.extract_text() or ''
                if len(clean(text)) < 40:
                    raise SourceError('DMP quality letter has an unreadable or scanned page')
                parts.append(text)
        text = clean(unicodedata.normalize('NFKC', ' '.join(parts)).replace('\u00ad', ''))
        if not 200 <= len(text) <= 40000:
            raise SourceError('DMP quality-letter text is empty or exceeds bounds')
        return text
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError('DMP quality letter could not be read') from exc


class DmpQualityLettersSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('dmp_quality_letters accepts only the official DMP quality-letter index')
        if self.complete or 'removed' in self.events:
            raise ValueError('Disappearance of a letter does not establish a lifted quality measure')
        self.allow_empty = False
        self.max_documents = integer(config.options.get('max_documents', 30), 'max_documents', 1, 100)
        self.max_pages = integer(config.options.get('max_pdf_pages', 8), 'max_pdf_pages', 1, 20)
        self.field_labels = {'product': 'Preparat', 'substance': 'Virkestoff',
                             'issued_period': 'Opprinnelig brevperiode', 'updated_period': 'Oppdatert brevperiode',
                             'letter_text': 'Brevtekst', **self.field_labels}

    def _index(self, raw):
        soup = BeautifulSoup(raw, 'html.parser')
        if [clean(node.get_text(' ', strip=True)) for node in soup.select('h1')] != [TITLE]:
            raise SourceError('DMP quality-letter index heading changed')
        tables = [table for table in soup.select('table') if table.caption and clean(table.caption.get_text(' ', strip=True)) == CAPTION]
        if len(tables) != 1:
            raise SourceError('DMP quality-letter table is absent or ambiguous')
        table = tables[0]
        if [clean(cell.get_text(' ', strip=True)) for cell in table.select('thead th')] != ['Preparatnavn', 'Virkestoff', 'Dato']:
            raise SourceError('DMP quality-letter table columns changed')
        rows, seen = [], set()
        for row in table.select('tbody tr'):
            cells = row.find_all('td', recursive=False)
            if len(cells) != 3:
                raise SourceError('DMP quality-letter table has a malformed row')
            product, substance, date_label = (clean(cell.get_text(' ', strip=True)) for cell in cells)
            links = cells[0].select('a[href]')
            if not product or not substance or len(links) != 1:
                raise SourceError('DMP quality-letter product, substance or document is ambiguous')
            issued, updated = periods(date_label)
            key = unicodedata.normalize('NFKC', product).casefold() + '|' + issued
            if key in seen:
                raise SourceError('DMP quality-letter natural identity is duplicated; cannot combine events')
            seen.add(key)
            url = urljoin(PAGE, links[0]['href'])
            parsed = urlparse(url)
            if (parsed.scheme != 'https' or parsed.netloc != 'www.dmp.no' or parsed.query or parsed.fragment
                    or not parsed.path.startswith(DIRECTORY) or not parsed.path.lower().endswith('.pdf')):
                raise SourceError('DMP quality letter leaves its official document directory')
            rows.append({'key': key, 'title': product + ' · kvalitetssviktbrev', 'url': url, 'published': None,
                         'fields': {'product': product, 'substance': substance,
                                    'issued_period': issued, 'updated_period': updated}})
        if not rows or len(rows) > self.max_documents:
            raise SourceError('DMP quality-letter set is empty or exceeds max_documents')
        return sorted(rows, key=lambda row: row['key'])

    def read_records(self):
        index = self._index(official_download(self, PAGE))
        records = []
        for row in index:
            text = pdf_text(official_download(self, row['url']), self.max_pages)
            if row['fields']['product'].split()[0].casefold() not in text.casefold():
                raise SourceError('DMP quality letter does not identify the indexed product')
            records.append({**row, 'fields': {**row['fields'], 'letter_text': text}})
        if self._index(official_download(self, PAGE)) != index:
            raise SourceError('DMP quality-letter index changed between complete reads')
        for row in records:
            if pdf_text(official_download(self, row['url']), self.max_pages) != row['fields']['letter_text']:
                raise SourceError('DMP quality letter changed between complete reads')
        return records

    def describe_change(self, name, before, after):
        if name != 'letter_text':
            return super().describe_change(name, before, after)
        old_words, current_words = before.split(), after.split()
        snippets = []
        for tag, a, b, c, d in SequenceMatcher(None, old_words, current_words, autojunk=False).get_opcodes():
            if tag == 'equal':
                continue
            prior = ' '.join(old_words[max(0, a-3):min(len(old_words), max(a+1, b)+3)])[:100] or 'ikke oppgitt'
            current = ' '.join(current_words[max(0, c-3):min(len(current_words), max(c+1, d)+3)])[:100] or 'ikke oppgitt'
            snippets.append(prior + ' → ' + current)
            if len(snippets) == 2:
                break
        return 'Brevtekst (utdrag): ' + ' | '.join(snippets)

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            self._next['rows'] = {**old.get('rows', {}), **self._next['rows']}
            if len(self._next['rows']) > self.max_records * 2:
                raise SourceError('Retained DMP letters exceed the history bound')
        return items

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']
        info = ('Nyobservert produktbrev om kvalitetssvikt' if event == 'added' else 'Endret produktbrev om kvalitetssvikt',
                'Virkestoff: ' + fields['substance'], 'Opprinnelig brevperiode: ' + fields['issued_period'])
        if fields['updated_period']:
            info += ('Oppdatert brevperiode: ' + fields['updated_period'],)
        if event == 'changed':
            info += tuple(details[1:])
        return replace(item, alert_details=info + ('Brevperioder har månedsoppløsning. Indeksen omfatter publiserte kvalitetssviktbrev, ikke alle tilbakekallinger.',))
