"""Published fish-escape reports in the official external GIS layer."""
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from urllib.parse import urlencode
from uuid import UUID

from .changes import SnapshotSource, document, integer, shown
from .common import SourceError

LAYER = 'https://gis.fiskeridir.no/server/rest/services/eksterne_tjenester/FiskeridirAPI_akva_feature/FeatureServer/1'
TYPES = {'objectid': 'OID', 'globalid': 'GlobalID', 'loknr': 'Integer', 'navn': 'String',
         'rommingsdato': 'Date', 'rommingsdato_antatt': 'Date', 'selskapsnavn': 'String',
         'beskrivelse': 'String', 'art': 'String', 'antall_romt_estimert': 'String',
         'antall_romt_fisk': 'String', 'status': 'String', 'gjenfangst_iverksatt': 'String',
         'gjenfangst_gjennomfort': 'String', 'gjenfangst_beskrivelse': 'String'}
CAVEAT = ('Kun publiserte meldinger i dette GIS-laget; ikke full nasjonal dekning eller alle sluttmeldinger. '
          'Mulig rømming er ikke bekreftet rømming. Ukjent antall er ikke null; anslag og endelig oppgitt antall kan være usikre. Fravær tolkes ikke som avsluttet sak.')


def text(value, maximum=1000, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise SourceError('Escape report text changed type or exceeds its bound')
    value = ' '.join(value.split())
    if not value:
        if required:
            raise SourceError('Escape report lacks a required text field')
        return None
    return value


def instant(value, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not int:
        raise SourceError('Escape report timestamp is not epoch milliseconds')
    try:
        stamp = datetime.fromtimestamp(value / 1000, timezone.utc)
        if not 1990 <= stamp.year <= 2100:
            raise ValueError()
        return stamp.isoformat().replace('+00:00', 'Z')
    except (ValueError, OverflowError, OSError):
        raise SourceError('Escape report timestamp is outside the supported range') from None


def flag(value, choices):
    if value is None or value == '':
        return None
    if value not in choices:
        raise SourceError('Escape report recapture flag changed its vocabulary')
    return choices[value]


class EscapeIncidentsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (LAYER,)):
            raise ValueError('escape_incidents accepts only the official external GIS layer')
        if self.complete or 'removed' in self.events:
            raise ValueError('An absent escape report is not evidence that the case has ended')
        if self.thresholds:
            raise ValueError('Escape counts may be intervals; numeric thresholds are not supported')
        self.allow_empty = False
        self.max_records = integer(config.options.get('max_records', 1000), 'max_records', 1, 2000)
        self.field_labels = {'status': 'Meldingsstatus', 'final_count': 'Endelig oppgitt antall fisk',
            'estimated_count': 'Første anslag, antall fisk', 'recapture_started': 'Gjenfangst iverksatt',
            'recapture_completed': 'Gjenfangst gjennomført', 'recapture_notes': 'Gjenfangstbeskrivelse',
            'event_at': 'Oppgitt rømmingstidspunkt (UTC)', 'assumed_event_at': 'Antatt rømmingstidspunkt (UTC)',
            'site': 'Lokalitet', 'company': 'Rapporterende virksomhet', 'species': 'Art',
            'description': 'Hendelsesbeskrivelse', **self.field_labels}

    def _json(self, url):
        try:
            payload = json.loads(document(self, url))
            if not isinstance(payload, dict) or 'error' in payload or payload.get('exceededTransferLimit'):
                raise ValueError()
            return payload
        except (ValueError, UnicodeError):
            raise SourceError('Escape GIS returned an error, invalid JSON or a truncated selection') from None

    def _metadata(self):
        data = self._json(LAYER + '?f=json')
        try:
            types = {field['name']: field['type'] for field in data['fields']}
            if (data['name'] != 'Rømming' or data['objectIdField'] != 'objectid'
                    or data['globalIdField'] != 'globalid' or data.get('definitionExpression')
                    or data.get('datesInUnknownTimezone') is not False
                    or any(types.get(name) != 'esriFieldType' + kind for name, kind in TYPES.items())):
                raise ValueError()
            return integer(data['maxRecordCount'], 'GIS maxRecordCount', 1, 100000)
        except (KeyError, TypeError, ValueError):
            raise SourceError('Escape GIS layer metadata changed its identity, dates or field contract') from None

    def _record(self, attributes):
        try:
            if not isinstance(attributes, dict) or set(TYPES) - set(attributes):
                raise ValueError()
            identifier = str(UUID(attributes['globalid']))
            if UUID(identifier).int == 0:
                raise ValueError()
            status = text(attributes['status'], 50, required=True)
            if status not in ('Del 1', 'Del 1 oppdatert', 'Del 1 (oppdatert)', 'Del 2'):
                raise ValueError()
            fields = {'status': status, 'final_count': text(attributes['antall_romt_fisk'], 100),
                'estimated_count': text(attributes['antall_romt_estimert'], 100),
                'recapture_started': flag(attributes['gjenfangst_iverksatt'], {'yes':'ja', 'no':'nei'}),
                'recapture_completed': flag(attributes['gjenfangst_gjennomfort'], {'1':'ja', '0':'nei'}),
                'recapture_notes': text(attributes['gjenfangst_beskrivelse'], 250),
                'event_at': instant(attributes['rommingsdato']),
                'assumed_event_at': instant(attributes['rommingsdato_antatt'], optional=True),
                'site': {'number': integer(attributes['loknr'], 'loknr', 10000, 99999),
                         'name': text(attributes['navn'], 50, required=True)},
                'company': text(attributes['selskapsnavn'], 50, required=True),
                'species': text(attributes['art'], 50, required=True),
                'description': text(attributes['beskrivelse'], 1000)}
            # The legacy integer antall_romt is a different field. Never substitute it for missing final counts.
            return {'key': identifier, 'title': fields['site']['name'] + ' · meldt mulig rømming',
                    'url': LAYER + '/query?' + urlencode({'where': "globalid='{" + identifier.upper() + "}'",
                        'outFields': ','.join(TYPES), 'returnGeometry': 'false', 'f': 'pjson'}),
                    'published': None, 'fields': fields}
        except SourceError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SourceError('Escape report schema or stable report identity changed') from None

    def _poll(self, limit):
        base = LAYER + '/query?'
        common = {'where': '1=1', 'f': 'json'}
        count = self._json(base + urlencode({**common, 'returnCountOnly': 'true'})).get('count')
        try:
            integer(count, 'GIS count', 1, min(self.max_records, limit))
            identities = self._json(base + urlencode({**common, 'returnIdsOnly': 'true'}))
            ids = identities['objectIds']
            if (identities.get('objectIdFieldName') != 'objectid' or not isinstance(ids, list)
                    or any(type(key) is not int or key <= 0 for key in ids)
                    or len(ids) != count or len(set(ids)) != count):
                raise ValueError()
            data = self._json(base + urlencode({**common, 'outFields': ','.join(TYPES),
                'returnGeometry': 'false', 'orderByFields': 'objectid', 'resultRecordCount': min(self.max_records, limit)}))
            features = data['features']
            if (data.get('objectIdFieldName') != 'objectid' or data.get('globalIdFieldName') != 'globalid'
                    or not isinstance(features, list) or len(features) != count):
                raise ValueError()
            found = [entry['attributes']['objectid'] for entry in features]
            if any(type(key) is not int for key in found) or len(set(found)) != count or set(found) != set(ids):
                raise ValueError()
            rows = [self._record(entry['attributes']) for entry in features]
            if len({row['key'] for row in rows}) != count:
                raise ValueError()
            return sorted(rows, key=lambda row: row['key'])
        except SourceError:
            raise
        except (KeyError, TypeError, ValueError):
            raise SourceError('Escape GIS count, identities and rows do not prove a complete bounded selection') from None

    def read_records(self):
        limit = self._metadata()
        rows = self._poll(limit)
        if self._poll(limit) != rows:
            raise SourceError('Escape reports changed between complete reads; previous state preserved')
        return rows

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            prior = old.get('rows', {})
            def signature(row):
                fields = row['fields']
                return (fields['site']['number'], fields['event_at'], fields['species'])
            missing = {signature(entry['row']) for key, entry in prior.items() if key not in self._next['rows']}
            if any(signature(entry['row']) in missing for key, entry in self._next['rows'].items() if key not in prior):
                raise SourceError('Escape report GlobalIDs may have been reassigned; inspect identity continuity before accepting new reports')
            self._next['rows'] = {**prior, **self._next['rows']}
            if len(self._next['rows']) > self.max_records * 2:
                raise SourceError('Retained escape reports exceed the history bound')
        return items

    def describe_change(self, name, before, after):
        prior, current = shown(before), shown(after)
        if max(len(prior), len(current)) <= 200:
            return super().describe_change(name, before, after)
        old_words, words = prior.split(), current.split()
        for tag, a, b, c, d in SequenceMatcher(None, old_words, words, autojunk=False).get_opcodes():
            if tag != 'equal':
                prior = ' '.join(old_words[max(0,a-3):max(a+1,b)+3])[:150] or 'ikke oppgitt'
                current = ' '.join(words[max(0,c-3):max(c+1,d)+3])[:150] or 'ikke oppgitt'
                break
        return self.field_labels.get(name,name) + ' (utdrag): ' + prior + ' → ' + current

    def _item(self, row, event, details, suppress):
        fields = row['fields']
        content = [('Nyobservert' if event == 'added' else 'Endret') + ' melding om mulig rømming',
                   f"Lokalitet: {fields['site']['number']} · {fields['company']} · {fields['species']}"]
        if event == 'added':
            content.extend(['Oppgitt rømmingstidspunkt (UTC): ' + fields['event_at']
                + ' · Antatt rømmingstidspunkt (UTC): ' + shown(fields['assumed_event_at']),
                'Meldingsstatus: ' + fields['status'],
                'Første anslag, antall fisk: ' + shown(fields['estimated_count']),
                'Endelig oppgitt antall fisk: ' + shown(fields['final_count']),
                'Gjenfangst iverksatt: ' + shown(fields['recapture_started']) + ' · gjennomført: ' + shown(fields['recapture_completed'])])
        else:
            changes = list(details[1:])
            if len(changes) > 5:
                content.extend(changes[:4])
                content.append(f'{len(changes)-4} flere feltendringer; se kilden.')
            else:
                content.extend(changes)
        content.append(CAVEAT)
        return super()._item(row, event, content, suppress)
