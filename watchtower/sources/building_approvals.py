"""Explicit companies' central construction approvals in DiBK's open API."""
from dataclasses import replace
from datetime import date
import json

from .changes import SnapshotSource, canonical, strings
from .common import SourceError
from .identifiers import valid_orgnr

PAGE = 'https://sgregister.dibk.no/'
API = PAGE + 'api/enterprises/'
AREA_FIELDS = ('function', 'subject_area', 'pbl', 'function_xml', 'subject_area_xml', 'pbl_xml', 'grade')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def enterprise_record(payload, orgnr):
    # The public cache can serve v1 after a v2 request despite Vary: Accept.
    # Both documented single-enterprise envelopes carry the same inner schema.
    if not isinstance(payload, dict) or set(payload) not in ({'enterprise'}, {'dibk-sgdata'}):
        raise SourceError('DiBK enterprise envelope is missing or ambiguous')
    row = next(iter(payload.values()))
    if not isinstance(row, dict):
        raise SourceError('DiBK enterprise envelope is invalid')
    unit, status, areas = row.get('enterprise'), row.get('status'), row.get('valid_approval_areas')
    if (not isinstance(unit, dict) or unit.get('organizational_number') != orgnr
            or not isinstance(unit.get('name'), str) or not unit['name'].strip()
            or len(unit['name']) > 500 or not isinstance(status, dict)
            or type(status.get('approved')) is not bool or 'approval_period_to' not in status
            or not isinstance(areas, list) or len(areas) > 500):
        raise SourceError('DiBK enterprise identity or approval fields are incomplete')
    expiry = status['approval_period_to']
    try:
        if not isinstance(expiry, str) or date.fromisoformat(expiry).isoformat() != expiry:
            raise ValueError('Invalid date')
    except ValueError as exc:
        raise SourceError('DiBK approval end date is invalid') from exc
    normalized = {}
    for area in areas:
        if (not isinstance(area, dict) or any(not isinstance(area.get(k), str)
                or not area[k].strip() or len(area[k]) > 500 for k in AREA_FIELDS)
                or area['grade'] not in {'1', '2', '3'}):
            raise SourceError('DiBK approval area is incomplete')
        value = {key: area[key].strip() for key in AREA_FIELDS}
        # The official export repeats some identical rows. Only exact duplicates
        # are neutral; differing grades or other fields remain separate areas.
        normalized[canonical(value)] = value
    if status['approved'] and not normalized:
        raise SourceError('DiBK approved enterprise has no valid approval areas')
    return {'key': orgnr, 'title': unit['name'].strip(), 'url': PAGE + 'enterprises/' + orgnr,
            'published': None, 'fields': {'name': unit['name'].strip(),
                'approval_status': 'Godkjent' if status['approved'] else 'Ikke godkjent',
                'approval_period_to': expiry,
                'approval_areas': [normalized[k] for k in sorted(normalized)]}}


def area_label(area):
    return f"{area['function']}: {area['subject_area']} ({area['pbl']}), tiltaksklasse {area['grade']}"


def excerpt(text, limit):
    return text if len(text) <= limit else text[:limit - 10] + ' … [utdrag]'


def area_summary(before, after):
    identity = lambda a: (a['function_xml'], a['subject_area_xml'], a['pbl_xml'])
    old, new = {}, {}
    for target, values in ((old, before), (new, after)):
        for area in values:
            target.setdefault(identity(area), []).append(area)
    lines, changed_keys = [], set()
    for key in sorted(old.keys() & new.keys()):
        prior, current = sorted({a['grade'] for a in old[key]}), sorted({a['grade'] for a in new[key]})
        if prior != current:
            a = new[key][0]
            lines.append(excerpt(f"{a['function']}: {a['subject_area']} ({a['pbl']})", 120)
                         + ': tiltaksklasse ' + ', '.join(prior) + ' → ' + ', '.join(current))
            changed_keys.add(key)
    for prefix, values, others in [('Ikke lenger oppført: ', before, after), ('Nyoppført: ', after, before)]:
        other_set = {canonical(a) for a in others}
        lines.extend(prefix + excerpt(area_label(a), 150) for a in values
                     if identity(a) not in changed_keys and canonical(a) not in other_set)
    prefix = f'Godkjenningsområder: {len(before)} → {len(after)}. '
    full = prefix + '; '.join(lines)
    if len(full) <= 490:
        return full
    selected = []
    for line in lines:
        if len('; '.join(selected + [line])) > 360:
            break
        selected.append(line)
    return prefix + f'Utdrag {len(selected)} av {len(lines)} endringer: ' + '; '.join(selected) + '. Se registeret for alle områdene.'


class BuildingApprovalsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('building_approvals accepts only the official DiBK register')
        if self.complete or self.allow_empty or 'removed' in self.events:
            raise ValueError('DiBK absence cannot confirm withdrawal of approval')
        self.orgnrs = strings(config.options.get('orgnrs'), 'orgnrs')
        if len(self.orgnrs) > 25 or any(not valid_orgnr(org) for org in self.orgnrs):
            raise ValueError('orgnrs must contain 1–25 valid organisation numbers')
        if len(self.orgnrs) > self.max_records:
            raise ValueError('max_records is smaller than the selected companies')
        self.field_labels = {'name': 'Oppført navn', 'approval_status': 'Sentral godkjenning',
                             'approval_period_to': 'Godkjenningsperiodens sluttdato',
                             'approval_areas': 'Godkjenningsområder', **self.field_labels}

    def _lookup(self, orgnr):
        response = self.get(API + orgnr, headers={'Accept': 'application/vnd.sgpub.v2'},
                            stream=True, allow_redirects=False,
                            accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('DiBK lookup returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('DiBK lookup exceeds max_bytes')
                chunks.append(chunk)
            payload = json.loads(b''.join(chunks), object_pairs_hook=unique_object,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('DiBK lookup is invalid JSON') from exc
        finally:
            response.close()
        return enterprise_record(payload, orgnr)

    def read_records(self):
        rows = [self._lookup(org) for org in sorted(self.orgnrs)]
        if rows != [self._lookup(org) for org in sorted(self.orgnrs)]:
            raise SourceError('DiBK selected approvals changed during reading')
        return rows

    def describe_change(self, name, before, after):
        if name == 'approval_areas':
            return area_summary(before, after)
        if name == 'name':
            return 'Oppført navn: ' + excerpt(before, 210) + ' → ' + excerpt(after, 210)
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']
        changes = tuple(details[1:]) if event == 'changed' else ()
        status = next((d for d in changes if d.startswith(self.field_labels['approval_status'] + ':')),
                      'Sentral godkjenning: ' + fields['approval_status'])
        expiry = next((d for d in changes if d.startswith(self.field_labels['approval_period_to'] + ':')),
                      'Godkjenningsperiodens sluttdato: ' + fields['approval_period_to'])
        names = tuple(d for d in changes if d.startswith('Oppført navn:'))
        areas = next((d for d in changes if d.startswith('Godkjenningsområder:')),
                     area_summary([], fields['approval_areas']) if event == 'added'
                     else f"Godkjenningsområder: {len(fields['approval_areas'])}, uendret")
        info = ('Nyobservert sentral godkjenning' if event == 'added' else 'Endret sentral godkjenning',
                f"Organisasjonsnummer: {row['key']}", status, expiry, areas, *names)
        info += ('Frivillig sentral godkjenning; dette er ikke en byggetillatelse. Kilde: DiBK.',)
        return replace(item, alert_details=info)
