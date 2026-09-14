"""Published hydropower plant registry status and technical-value changes."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import re

from .changes import SnapshotSource, integer, strings
from .common import SourceError

API = 'https://api.nve.no/web/Powerplant'
PAGE = 'https://www.nve.no/energi/energisystem/vannkraft/oversikt-over-vannkraft/'
FIGURES = {
    'MaksYtelse': ('capacity_mw', 'Oppgitt maksimal ytelse (MW)'),
    'MidProd_91_20': ('mean_production_gwh_1991_2020', 'Midlere årsproduksjon, referanse 1991–2020 (GWh/år)'),
    'BruttoFallhoyde_M': ('head_m', 'Oppgitt brutto fallhøyde (m)'),
    'Slukeevne': ('flow_m3_s', 'Oppgitt slukeevne (m³/s)'),
    'EnEkv': ('energy_equivalent_kwh_m3', 'Oppgitt energiekvivalent (kWh/m³)'),
}
TYPES = {'K': 'Kraftverk', 'PK': 'Pumpekraftverk', 'P': 'Pumpe'}
STATES = {'Idrift': (True, False), 'Under bygging': (False, True), 'Ute av drift': (False, False)}


def unique_object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('Duplicate JSON key')
        result[k] = v
    return result


def text(value, label, limit=300):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SourceError(f'Hydropower {label} is invalid')
    return ' '.join(value.split())


def figure(value):
    if value is None:
        return None
    if type(value) not in (int, Decimal):
        raise SourceError('Hydropower technical value must be a JSON number or null')
    amount = Decimal(value)
    if not amount.is_finite() or abs(amount) > 1e12 or amount.as_tuple().exponent < -15:
        raise SourceError('Hydropower technical value exceeds numeric bounds')
    if not amount:
        return '0'
    value = format(amount, 'f')
    return value.rstrip('0').rstrip('.') if '.' in value else value


def plant_record(data):
    required = set(FIGURES) | {'VannKraftverkID', 'Navn', 'VannKVType', 'VannKVTypeID',
        'Kraftverkstatus', 'ErIDrift', 'UnderBygging', 'UteAvDrift', 'IDriftDato',
        'ElspotomraadeNummer', 'FylkesNr', 'KommuneNr', 'Kommune'}
    if not isinstance(data, dict) or not required <= set(data):
        raise SourceError('Hydropower plant lacks required registry fields')
    ident = data['VannKraftverkID']
    if type(ident) is not int or not 1 <= ident <= 99999999:
        raise SourceError('Hydropower plant identity is invalid')
    kind = data['VannKVTypeID']
    if not isinstance(kind, str) or kind not in TYPES or TYPES[kind] != data['VannKVType']:
        raise SourceError('Hydropower plant type is unknown or inconsistent')
    status = data['Kraftverkstatus']
    if not isinstance(status, str) or status not in STATES or any(type(data[k]) is not bool for k in ('ErIDrift', 'UnderBygging')):
        raise SourceError('Hydropower registry status is invalid')
    if (data['ErIDrift'], data['UnderBygging']) != STATES[status]:
        raise SourceError('Hydropower registry status flags disagree')
    retired = data['UteAvDrift']
    if retired is not None and (type(retired) is not int or not 1800 <= retired <= date.today().year + 1):
        raise SourceError('Hydropower reported retirement year is invalid')
    if (status == 'Ute av drift') != (retired is not None):
        raise SourceError('Hydropower retirement year and registry status disagree')
    commissioned = data['IDriftDato']
    if commissioned is not None:
        try:
            if not isinstance(commissioned, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T00:00:00', commissioned):
                raise ValueError
            commissioned = date.fromisoformat(commissioned[:10]).isoformat()
        except ValueError:
            raise SourceError('Hydropower reported commissioning date is invalid') from None
    area, county, municipality = data['ElspotomraadeNummer'], data['FylkesNr'], data['KommuneNr']
    if not isinstance(area, str) or area not in ('', '1', '2', '3', '4', '5'):
        raise SourceError('Hydropower price area is invalid')
    if not isinstance(county, str) or not re.fullmatch(r'[0-9]{2}', county) or not isinstance(municipality, str) or not re.fullmatch(r'[0-9]{1,4}', municipality):
        raise SourceError('Hydropower geographic code is invalid')
    fields = {'name': text(data['Navn'], 'name'), 'plant_type': TYPES[kind], 'registry_status': status,
              'reported_commissioning_date': commissioned, 'reported_retirement_year': retired,
              'price_area': 'NO' + area if area else None, 'county_code': county,
              'municipality_code': municipality, 'municipality': text(data['Kommune'], 'municipality')}
    fields.update({name: figure(data[k]) for k, (name, _) in FIGURES.items()})
    return {'key': str(ident), 'title': fields['name'], 'url': PAGE, 'fields': fields}


class HydropowerSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API,)):
            raise ValueError('hydropower uses the official NVE plant registry')
        self.ids = strings(config.options.get('plant_ids', []), 'plant_ids', empty=True)
        if any(not re.fullmatch(r'[1-9][0-9]{0,7}', x) for x in self.ids) or len(self.ids) > self.max_records:
            raise ValueError('plant_ids must contain bounded positive registry IDs; empty selects the full export')
        self.export_limit = integer(config.options.get('max_export_records', 5000), 'max_export_records', 1, 10000)
        if self.complete or 'removed' in self.events:
            raise ValueError('Hydropower register absence cannot establish shutdown or removal')
        self.field_labels = {name: label for name, label in FIGURES.values()} | {
            'name': 'Anleggsnavn', 'plant_type': 'Anleggstype', 'registry_status': 'Oppført registerstatus',
            'reported_commissioning_date': 'Kildens idriftsettelsesdato', 'reported_retirement_year': 'Kildens år for avsluttet drift',
            'price_area': 'Oppført prisområde', 'county_code': 'Fylkeskode', 'municipality_code': 'Kommunekode',
            'municipality': 'Oppført kommune', **self.field_labels}

    def _records(self, route):
        response = self.get(API + '/' + route, headers={'Accept': 'application/json'}, stream=True,
                            allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Hydropower API returned an unexpected redirect')
            chunks, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > self.max_bytes:
                    raise SourceError('Hydropower response exceeds max_bytes')
                chunks.append(chunk)
            data = json.loads(b''.join(chunks), parse_float=Decimal, object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('Hydropower response is invalid JSON') from exc
        finally:
            response.close()
        if not isinstance(data, list) or not data or len(data) > self.export_limit:
            raise SourceError('Hydropower export is empty, invalid or exceeds max_export_records')
        rows = [plant_record(d) for d in data]
        if len({r['key'] for r in rows}) != len(rows):
            raise SourceError('Hydropower plant identities are duplicated')
        return {r['key']: r for r in rows}

    def read_records(self):
        all_rows = self._records('GetHydroPowerPlants')
        operating = self._records('GetHydroPowerPlantsInOperation')
        expected = {k: r for k, r in all_rows.items() if r['fields']['registry_status'] == 'Idrift'}
        if operating != expected:
            raise SourceError('Hydropower full and operating exports disagree; previous state preserved')
        if self.ids and not set(self.ids) <= set(all_rows):
            raise SourceError('An explicitly selected hydropower plant is absent; previous state preserved')
        return [all_rows[k] for k in sorted(self.ids or all_rows)]

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        label = 'Nyobservert vannkraftoppføring' if event == 'added' else 'Endret vannkraftoppføring'
        return replace(item, alert_details=(label, 'NVE anleggs-ID: ' + row['key'], *details[1:],
            'Registerstatus og tekniske råverdier fra NVE; ikke sanntidsdrift eller registrering av midlertidige driftsstans',
            'Midlere årsproduksjon gjelder referanse 1991–2020, ikke målt produksjon i inneværende år. Dato og år er oppgitt av kilden'))
