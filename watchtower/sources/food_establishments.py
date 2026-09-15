"""Approval-list entries for selected official food-establishment sections."""
from collections import Counter
import csv
from dataclasses import replace
from datetime import date
import io
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical
from .common import SourceError

BASE = 'https://www.mattilsynet.no/godkjente-produkter-og-virksomheter/'
PROFILES = {
    'dairy': ('food-of-animal-origin-section-IX-raw-milk-and-dairy-products', 'v_virklist_4_10059.csv', '10059', 'Raw milk and dairy products'),
    'eggs': ('food-of-animal-origin-section-x-eggs-and-egg-products', 'v_virklist_4_10060.csv', '10060', 'Eggs and egg products'),
    'meat': ('section-1-meat-of-domestic-ungulates', 'v_virklist_food_section_1.csv', None, 'Meat of domestic ungulates'),
}
APPROVAL_HEADERS = set('COUNTYID MUNICIPALITY LISTCATEGORYID LISTCATEGORY LISTTYPEID LISTTYPE APPROVALID ORGANIZATIONNAME ORGANIZATIONTOWN CATEGORY ACTIVITIES PRODUCTTYPES ASSOCIATEDACTIVITIES REMARKS CHANPROCEDURE APPROVALFROMDATE APPROVALTODATE'.split())
MEAT_HEADERS = set('GODKJENNINGSNUMMER VIRKSOMHETSNAVN ADRESSE POSTNR POSTSTED PRODUKSJONSFORM ANDRE_PRODFORMER ART MERKNAD SEKSJON'.split())


def _text(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise SourceError('Food approval row has invalid text')
    return ' '.join(value.split())


def _date(value):
    if not value:
        return None
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise SourceError('Food approval date is invalid') from None


def _codes(value):
    return sorted(set(x.strip() for x in value.split(',') if x.strip()))


class FoodEstablishmentsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.profile = config.options.get('list_type', 'dairy')
        if not isinstance(self.profile, str) or self.profile not in PROFILES:
            raise ValueError('list_type must be dairy, eggs or meat')
        self.slug, self.filename, self.list_id, self.section = PROFILES[self.profile]
        self.url = BASE + self.slug
        if config.urls not in ((), (self.url,)):
            raise ValueError('Select the exact official page for this food approval section')
        if self.complete or 'removed' in self.events:
            raise ValueError('Food approval lists cannot establish withdrawals from absence')
        self.field_labels = {'entries': 'Godkjenningsoppføringer', **self.field_labels}

    def _read(self, url):
        response = self.get(url, stream=True, allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Food approval source returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Food approval source exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def read_records(self):
        soup = BeautifulSoup(self._read(self.url), 'html.parser')
        titles = soup.select('h1')
        if len(titles) != 1 or self.section.lower() not in titles[0].get_text(' ', strip=True).lower():
            raise SourceError('Food approval page does not identify the selected section')
        tables = soup.select('table')
        links = [urljoin(self.url, a['href']) for a in soup.select('a[href]') if a.get_text(' ', strip=True) == 'Last ned (CSV)']
        if len(tables) != 1 or len(links) != 1:
            raise SourceError('Food approval table or download is absent or ambiguous')
        parsed = urlparse(links[0])
        pattern = r'/_/attachment/inline/[0-9a-f-]{36}:[0-9a-f]{40}/' + re.escape(self.filename)
        if (parsed.scheme != 'https' or parsed.netloc != 'mattilsynet-xp7prod.enonic.cloud'
                or not re.fullmatch(pattern, parsed.path) or parsed.query or parsed.fragment or parsed.params):
            raise SourceError('Food approval download is not the expected official attachment')
        headers = [re.sub(r'[ _]', '', h.get_text(' ', strip=True)).lower() for h in tables[0].select('thead th')]
        id_header = 'approvalid' if self.list_id else 'godkjenningsnummer'
        if headers.count(id_header) != 1:
            raise SourceError('Food approval HTML identity column changed')
        column = headers.index(id_header)
        html_ids = []
        for tr in tables[0].select('tbody tr'):
            cells = tr.find_all('td', recursive=False)
            if len(cells) != len(headers):
                raise SourceError('Food approval HTML row is incomplete')
            html_ids.append(_text(cells[column].get_text(' ', strip=True)))
        if not html_ids or len(html_ids) > self.max_records:
            raise SourceError('Food approval HTML list is empty or exceeds max_records')
        return self._records(self._read(links[0]), html_ids)

    def _records(self, raw, html_ids):
        try:
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
            expected = APPROVAL_HEADERS if self.list_id else MEAT_HEADERS
            if reader.fieldnames is None or set(reader.fieldnames) != expected or len(reader.fieldnames) != len(expected):
                raise SourceError('Food approval CSV schema changed')
            records = list(reader)
        except (UnicodeError, csv.Error) as exc:
            raise SourceError('Food approval CSV is malformed') from exc
        if len(records) != len(html_ids) or len(records) > self.max_records:
            raise SourceError('Food approval CSV and published table disagree about completeness')
        grouped, csv_ids = {}, []
        for record in records:
            if None in record or None in record.values():
                raise SourceError('Food approval CSV row has missing or surplus cells')
            r = {k: _text(v) for k, v in record.items()}
            ident = r['APPROVALID' if self.list_id else 'GODKJENNINGSNUMMER']
            if not re.fullmatch(r'[A-Z0-9][A-Z0-9 ]{0,29}', ident):
                raise SourceError('Food approval identifier is invalid')
            csv_ids.append(ident)
            if self.list_id:
                if r['LISTCATEGORYID'] != '1011' or r['LISTCATEGORY'] != 'Approved establishments' or r['LISTTYPEID'] != self.list_id or self.section not in r['LISTTYPE']:
                    raise SourceError('Food approval CSV is outside the selected section')
                start, end = _date(r['APPROVALFROMDATE']), _date(r['APPROVALTODATE'])
                if start and end and start > end:
                    raise SourceError('Food approval period is reversed')
                entry = {'name': r['ORGANIZATIONNAME'], 'town': r['ORGANIZATIONTOWN'], 'categories': _codes(r['CATEGORY']),
                         'activities': r['ACTIVITIES'], 'products': r['PRODUCTTYPES'], 'associated_activities': _codes(r['ASSOCIATEDACTIVITIES']),
                         'remarks': r['REMARKS'], 'channelling_procedure': r['CHANPROCEDURE'], 'valid_from': start, 'valid_to': end}
            else:
                if r['SEKSJON'] != 'Section 1 - Meat of domestic ungulates':
                    raise SourceError('Food approval CSV is outside the selected section')
                entry = {'name': r['VIRKSOMHETSNAVN'], 'town': r['POSTSTED'], 'production_forms': _codes(r['PRODUKSJONSFORM']),
                         'associated_activities': _codes(r['ANDRE_PRODFORMER']), 'species': _codes(r['ART']), 'remarks': _codes(r['MERKNAD'])}
            if not entry['name']:
                raise SourceError('Food approval entry lacks an establishment name')
            grouped.setdefault(ident, {})[canonical(entry)] = entry
        if Counter(csv_ids) != Counter(html_ids):
            raise SourceError('Food approval CSV identities disagree with the published table')
        rows = []
        for ident, entries in sorted(grouped.items()):
            values = [entries[k] for k in sorted(entries)]
            names = sorted({x['name'] for x in values})
            title = f"{ident} · " + ' / '.join(names[:3])
            rows.append({'key': ident, 'title': title, 'url': self.url, 'published': None, 'fields': {'entries': values}})
        return rows

    def describe_change(self, name, before, after):
        if name == 'entries':
            return 'Oppførte virksomhets-, aktivitets- eller godkjenningsopplysninger er endret'
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        entries = row['fields']['entries']
        label = 'Nyobservert godkjenningsnummer i listen' if event == 'added' else 'Endret godkjenningsoppføring'
        info = (label, f"Godkjenningsnummer: {row['key']} · {self.section}", *(() if event == 'added' else details[1:]))
        for entry in entries[:5]:
            codes = entry.get('categories', entry.get('production_forms', []))
            line = f"{entry['name']} · {entry['town']} · Produksjonskoder: {', '.join(codes) or 'ikke oppgitt'}"
            if self.list_id:
                line += f" · Oppført fra/til: {entry['valid_from'] or 'ikke oppgitt'} / {entry['valid_to'] or 'ikke oppgitt'}"
            if entry.get('associated_activities'):
                line += ' · Andre aktiviteter: ' + ', '.join(entry['associated_activities'])
            if entry.get('species'):
                line += ' · Artskoder: ' + ', '.join(entry['species'])
            if entry.get('remarks'):
                remarks = entry['remarks']
                line += ' · Merknad: ' + (', '.join(remarks) if isinstance(remarks, list) else remarks)
            info += (line,)
        if len(entries) > 5:
            info += (f'{len(entries)-5} ytterligere oppføringer står i kilden',)
        return replace(item, alert_details=info + ('Ny observasjon er ikke en ny vedtaksdato; fravær tolkes ikke som inndratt godkjenning',))
