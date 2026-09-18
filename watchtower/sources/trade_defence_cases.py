"""EU trade-defence case metadata and source-labelled indicative timetables."""
from dataclasses import replace
from datetime import date
import json
import re

from .changes import SnapshotSource, document, shown as shown_value
from .common import SourceError
from .device_actions import _object, _text

ORIGIN = 'https://tron.trade.ec.europa.eu/investigations'
API = ORIGIN + '/api/eucase'
PAGE = ORIGIN + '/ongoing'
MONTHS = dict(zip('January February March April May June July August September October November December'.split(), range(1, 13)))
TIMETABLE = {'predisc': 'indicative_predisclosure', 'provMeasures': 'indicative_provisional',
             'returnCMTSDisc': 'indicative_provisional_comments', 'returnCMTSFinalDisc': 'indicative_final_comments',
             'defMeasures': 'indicative_definitive', 'tpvFrom': 'indicative_verification_from', 'tpvTo': 'indicative_verification_to'}


def calendar_value(value):
    if not isinstance(value, str):
        raise SourceError('Trade-defence timetable value is not source calendar text')
    if value == 'Not applicable':
        return value
    match = re.fullmatch(r'(\d{2}) ([A-Za-z]+) (\d{4})', value or '')
    try:
        if not match:
            raise ValueError
        return date(int(match[3]), MONTHS[match[2]], int(match[1])).isoformat()
    except (KeyError, ValueError) as exc:
        raise SourceError('Trade-defence timetable date is invalid or changed format') from exc


def countries(value):
    if not isinstance(value, list) or len(value) > 200:
        raise SourceError('Trade-defence country list is absent or excessive')
    result = []
    for row in value:
        if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
            raise SourceError('Trade-defence country identity is invalid')
        result.append({'id': row['id'], 'name': _text(row.get('countryName'), 'country', 300),
                       'note': _text(row.get('extraInformation'), 'country note', 2000, nullable=True)})
    if len({row['id'] for row in result}) != len(result):
        raise SourceError('Trade-defence country identities repeat')
    return sorted(result, key=lambda row: row['id'])


def change_excerpt(before, after):
    """Keep the first actual difference visible, including in long single words."""
    before, after = shown_value(before), shown_value(after)
    if len(before) + len(after) <= 170:
        return before + ' → ' + after
    prefix = 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    start = max(0, prefix - 20)
    def excerpt(value):
        result = value[start:start + 75]
        return ('…' if start else '') + result + ('…' if start + 75 < len(value) else '')
    return '(utdrag) ' + (excerpt(before) or 'tomt') + ' → ' + (excerpt(after) or 'tomt')


def ids_excerpt(values):
    return ', '.join(str(value) for value in values[:3]) + (f' (+{len(values)-3} flere)' if len(values) > 3 else '')


def bounded_changes(changes, maximum=490):
    """Keep complete change descriptions and disclose any omitted fields."""
    chosen = []
    for index, change in enumerate(changes):
        remaining = len(changes) - index - 1
        suffix = f'; {remaining} flere feltendringer; se kilden.' if remaining else ''
        if len('; '.join([*chosen, change]) + suffix) > maximum:
            remaining += 1
            suffix = f'; {remaining} flere feltendringer; se kilden.'
            if not chosen:
                return change[:maximum-len(suffix)-1] + '…' + suffix
            return '; '.join(chosen) + suffix
        chosen.append(change)
    return '; '.join(chosen)


class TradeDefenceCasesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('trade_defence_cases accepts only the official EU ongoing-case list')
        if self.complete or 'removed' in self.events:
            raise ValueError('Absence from ongoing cases cannot establish case closure or measure expiry')
        self.field_labels = {
            'case_number': 'Saksnummer', 'product': 'Produkt', 'case_type': 'Undersøkelsestype',
            'category': 'Handelstiltakstype', 'countries': 'Land undersøkt', 'country_notes': 'Landmerknader',
            'article': 'Oppført artikkel', 'measure_status': 'Kildens tiltaksstatus',
            'detail_available': 'Detaljside tilgjengelig', 'documents': 'Dokumentmetadata',
            'indicative_predisclosure': 'Forhåndsinformasjon', 'indicative_provisional': 'Foreløpige tiltak',
            'indicative_provisional_comments': 'Kommentarer til foreløpige tiltak',
            'indicative_final_comments': 'Kommentarer til endelig fremlegg', 'indicative_definitive': 'Endelige tiltak/avslutning',
            'indicative_verification_from': 'Kontrollbesøk fra', 'indicative_verification_to': 'Kontrollbesøk til',
            **self.field_labels,
        }

    def _json(self, path):
        try:
            return json.loads(document(self, API + path), object_pairs_hook=_object)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('Trade-defence source returned invalid JSON') from exc

    def _revision(self):
        value = self._json('/lastUpdate')
        if type(value) is not int or not 946684800000 <= value <= 4102444800000:
            raise SourceError('Trade-defence source revision timestamp is invalid')
        return value

    def _index(self):
        payload = self._json('/list/ongoing')
        if not isinstance(payload, list) or not 1 <= len(payload) <= self.max_records:
            raise SourceError('Trade-defence ongoing list is empty, malformed or exceeds max_records')
        rows, seen = [], set()
        for value in payload:
            if not isinstance(value, dict) or type(value.get('id')) is not int or not 0 < value['id'] < 10**9 or value['id'] in seen:
                raise SourceError('Trade-defence case identity is invalid or repeated')
            seen.add(value['id'])
            number = value.get('caseNumber')
            if not isinstance(number, str) or not re.fullmatch(r'[A-Z]{1,5}\d{1,5}(?:[/-]\d{1,5})*', number):
                raise SourceError('Trade-defence case number is invalid')
            if type(value.get('hasDetailPage')) is not bool:
                raise SourceError('Trade-defence detail-availability flag is missing')
            rows.append({'id': value['id'], 'case_number': number,
                'product': _text(value.get('shortName'), 'product', 3000),
                'case_type': _text(value.get('caseType'), 'case type', 300),
                'article': _text(value.get('articleOfToc'), 'article', 100),
                'country_rows': countries(value.get('caseCountries')),
                'detail_available': value['hasDetailPage']})
        if len({row['case_number'] for row in rows}) != len(rows):
            raise SourceError('Trade-defence case numbers repeat')
        return sorted(rows, key=lambda row: row['id'])

    def _record(self, index):
        fields = {key: index[key] for key in ('case_number', 'product', 'case_type', 'article', 'detail_available')}
        fields.update(countries=[row['name'] for row in index['country_rows']],
                      country_notes=[row for row in index['country_rows'] if row['note']],
                      category=None, measure_status=None, documents=None)
        fields.update({name: None for name in TIMETABLE.values()})
        if index['detail_available']:
            value = self._json('/details/' + str(index['id']))
            if not isinstance(value, dict) or type(value.get('id')) is not int or value.get('id') != index['id'] or value.get('caseNumber') != index['case_number'] or value.get('ongoing') is not True:
                raise SourceError('Trade-defence index and detail identity or ongoing status disagree')
            for name, field in [('shortName', 'product'), ('caseType', 'case_type'), ('articleOfToc', 'article')]:
                if _text(value.get(name), name, 3000) != fields[field]:
                    raise SourceError('Trade-defence index and detail case fields disagree')
            if countries(value.get('caseCountries')) != index['country_rows']:
                raise SourceError('Trade-defence index and detail countries disagree')
            fields['category'] = _text(value.get('category'), 'category', 300)
            fields['measure_status'] = _text(value.get('measStatus'), 'measure status', 300)
            for source, target in TIMETABLE.items():
                fields[target] = calendar_value(value.get(source))
            publications = value.get('publications')
            if not isinstance(publications, list) or len(publications) > 200:
                raise SourceError('Trade-defence publication list is missing or excessive')
            docs = []
            for publication in publications:
                if not isinstance(publication, dict) or type(publication.get('casePublicationId')) is not int or publication['casePublicationId'] <= 0 or type(publication.get('caseId')) is not int or publication.get('caseId') != index['id']:
                    raise SourceError('Trade-defence publication identity disagrees')
                docs.append({'id': publication['casePublicationId'],
                    'type': _text(publication.get('typeOfPublication'), 'publication type', 500),
                    'description': _text(publication.get('contents'), 'publication description', 10000, nullable=True)})
            if len({row['id'] for row in docs}) != len(docs):
                raise SourceError('Trade-defence publication identities repeat')
            fields['documents'] = sorted(docs, key=lambda row: row['id'])
        return {'key': str(index['id']), 'title': index['case_number'] + ' · ' + index['product'],
                'url': ORIGIN + '/case-view?caseId=' + str(index['id']) if index['detail_available'] else PAGE,
                'published': None, 'fields': fields}

    def _sweep(self):
        revision = self._revision()
        index = self._index()
        rows = [self._record(value) for value in index]
        if self._revision() != revision:
            raise SourceError('Trade-defence publication changed during the complete read')
        return rows, revision

    def read_records(self):
        first, second = self._sweep(), self._sweep()
        if first != second:
            raise SourceError('Trade-defence case selection changed between complete reads')
        self._revision_value = first[1]
        return first[0]

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope and old.get('provider_revision', 0) > self._revision_value:
            raise SourceError('Trade-defence source revision regressed; prior state preserved')
        if old.get('scope') == self.scope:
            self._next['rows'] = {**old.get('rows', {}), **self._next['rows']}
            if len(self._next['rows']) > self.max_records * 2:
                raise SourceError('Retained trade-defence cases exceed the history bound')
        self._next['provider_revision'] = self._revision_value
        return items

    def describe_change(self, name, before, after):
        if name == 'documents':
            old = {row['id']: row for row in before or []}; new = {row['id']: row for row in after or []}
            changed = [key for key in sorted(new) if key not in old or new[key] != old[key]]
            removed = sorted(old.keys() - new.keys())
            prior = len(old) if before is not None else 'ikke tilgjengelig'
            current = len(new) if after is not None else 'ikke tilgjengelig'
            parts = [f'Dokumentmetadata: {prior} → {current} oppføringer']
            if changed:
                parts.append('nye/endrede ID-er: ' + ids_excerpt(changed))
            if removed:
                parts.append('fjernede ID-er: ' + ids_excerpt(removed))
            edits = [(key, field) for key in sorted(new.keys() & old.keys()) for field in ('type', 'description')
                     if old[key][field] != new[key][field]]
            if edits:
                key, field = edits[0]
                label = 'type' if field == 'type' else 'beskrivelse'
                parts.append(f'ID {key} {label}: ' + change_excerpt(old[key][field], new[key][field]))
                if len(edits) > 1:
                    parts.append(f'{len(edits)-1} flere dokumentfelt endret')
            return '; '.join(parts)
        return self.field_labels.get(name, name) + ': ' + change_excerpt(before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']; changes = details[1:] if event == 'changed' else ()
        def shown(name, limit=230):
            label = self.field_labels[name] + ': '
            changed = next((line for line in changes if line.startswith(label)), None)
            displayed = ', '.join(fields[name]) or 'ikke oppført' if name == 'countries' else fields[name]
            value = changed[len(label):] if changed else str(displayed if displayed is not None else 'detaljer ikke oppført')
            available = max(40, limit - len(label))
            if len(value) > available:
                before, arrow, after = value.partition(' → ')
                if arrow:
                    half = (available - 7) // 2
                    value = before[:half] + '… → ' + after[:half] + '…'
                else:
                    value = value[:available - 1] + '…'
            return label + value
        timeline = set(TIMETABLE.values()) | {'case_type', 'category', 'countries', 'measure_status'}
        other = [line for line in changes if not any(line.startswith(self.field_labels[name] + ': ') for name in timeline)]
        extra = bounded_changes(other) if other else 'Oppførte dokumenter: ' + (str(len(fields['documents'])) if fields['documents'] is not None else 'detaljside ikke tilgjengelig')
        return replace(item, alert_details=(
            shown('case_type') + ' · ' + shown('category'),
            shown('countries', 245) + ' · ' + shown('measure_status', 245),
            shown('indicative_provisional') + ' · ' + shown('indicative_provisional_comments'),
            shown('indicative_final_comments') + ' · ' + shown('indicative_definitive'),
            shown('indicative_verification_from') + ' · ' + shown('indicative_verification_to'),
            shown('indicative_predisclosure', 490), extra,
            'Tidsplanen er veiledende; bare frister i regelverk og åpningskunngjøring er bindende. Fravær beviser ikke avslutning. Oppstartsdato og dokumentinnhold tolkes ikke.'))
