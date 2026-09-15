"""Change notices for explicit regional warning dates, linking to full warnings."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import json
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .changes import SnapshotSource, canonical, digest, integer, strings
from .common import SourceError

API = 'https://api01.nve.no/hydrology/forecast/avalanche/v6.3.2/api'
# Fields in the documented current detail contract; archive exports differ.
TEXT_FIELDS = ('DangerLevelName', 'AvalancheDanger', 'EmergencyWarning', 'SnowSurface',
               'CurrentWeaklayers', 'LatestAvalancheActivity', 'LatestObservations',
               'ExtremWeatherId', 'ExtremWeatherName', 'MainText', 'RegionName', 'RegionTypeName')
TIME_FIELDS = ('NextWarningTime', 'PublishTime', 'DangerIncreaseTime', 'DangerDecreaseTime')
INT_FIELDS = ('PreviousWarningRegId', 'UtmZone', 'UtmEast', 'UtmNorth',
              'ExposedHeightFill', 'ExposedHeight1', 'RegionTypeId')
LIST_FIELDS = ('CountyList', 'MunicipalityList', 'AvalancheProblems', 'AvalancheAdvices')


def today():
    return datetime.now(ZoneInfo('Europe/Oslo')).date()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?', value):
        raise SourceError('Avalanche warning timestamp is outside the source contract')
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise SourceError('Avalanche warning timestamp is invalid') from None


def normalized(value, depth=0):
    if depth > 12:
        raise SourceError('Avalanche warning JSON nesting exceeds bounds')
    if isinstance(value, str):
        if len(value) > 50000:
            raise SourceError('Avalanche warning text exceeds bounds')
        return ' '.join(value.split())
    if isinstance(value, list):
        if len(value) > 300:
            raise SourceError('Avalanche warning nested list exceeds bounds')
        return [normalized(v, depth + 1) for v in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise SourceError('Avalanche warning object exceeds bounds')
        return {k: normalized(v, depth + 1) for k, v in value.items()}
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and abs(value) <= 1e15:
        return value
    raise SourceError('Avalanche warning contains an invalid JSON value')


def warning_record(data, region, first, last):
    required = set(TEXT_FIELDS + TIME_FIELDS + INT_FIELDS + LIST_FIELDS) | {
        'RegId', 'RegionId', 'LangKey', 'DangerLevel', 'ValidFrom', 'ValidTo', 'IsTendency', 'MountainWeather'}
    if not isinstance(data, dict) or not required <= set(data):
        raise SourceError('Avalanche warning is missing current detail fields')
    if type(data['RegionId']) is not int or str(data['RegionId']) != region or type(data['LangKey']) is not int or data['LangKey'] != 1:
        raise SourceError('Avalanche warning region or language differs from selection')
    if type(data['RegId']) is not int or data['RegId'] < 0 or type(data['IsTendency']) is not bool:
        raise SourceError('Avalanche warning publication identity or tendency flag is invalid')
    if data['DangerLevel'] not in ('0', '1', '2', '3', '4', '5'):
        raise SourceError('Avalanche warning danger level is invalid')
    start, end = timestamp(data['ValidFrom']), timestamp(data['ValidTo'])
    if not first <= start.date() <= last or start.time().isoformat() != '00:00:00' or end.date() != start.date() or end.time().isoformat() != '23:59:59':
        raise SourceError('Avalanche warning is outside the selected whole-day window')
    for name in TEXT_FIELDS:
        if data[name] is not None and not isinstance(data[name], str):
            raise SourceError('Avalanche warning text field is invalid')
    for name in TIME_FIELDS:
        if data[name] is not None:
            timestamp(data[name])
    for name in INT_FIELDS:
        if data[name] is not None and type(data[name]) is not int:
            raise SourceError('Avalanche warning numeric metadata is invalid')
    for name in LIST_FIELDS:
        value = data[name]
        if value is not None and (not isinstance(value, list) or any(not isinstance(v, dict) for v in value)):
            raise SourceError('Avalanche warning detail collection is invalid')
    if data['MountainWeather'] is not None and not isinstance(data['MountainWeather'], dict):
        raise SourceError('Avalanche warning mountain weather is invalid')
    name = data['RegionName']
    if not name or len(name) > 100 or not re.fullmatch(r'[\w -]+', name):
        raise SourceError('Avalanche warning region name is invalid')
    if not data['MainText'] or not data['MainText'].strip():
        raise SourceError('Avalanche warning main message is absent')
    # Validate the full bounded payload before any placeholder suppression.
    payload = normalized({k: v for k, v in data.items() if k != 'Author'})
    for key in ('CountyList', 'MunicipalityList'):
        if payload[key] is not None:
            payload[key] = sorted(payload[key], key=canonical)
    unassessed = data['RegId'] == 0
    if unassessed and (data['DangerLevel'] != '0' or data['IsTendency'] or payload['MainText'] != 'Ikke vurdert'):
        raise SourceError('Avalanche warning placeholder contradicts its assessment fields')
    if not unassessed and (data['PublishTime'] is None or (data['DangerLevel'] == '0' and not data['IsTendency'])):
        raise SourceError('Avalanche warning lacks a valid issued assessment')
    if not unassessed and not data['IsTendency'] and any(data[k] is None for k in ('AvalancheDanger', 'AvalancheProblems', 'AvalancheAdvices', 'MountainWeather')):
        raise SourceError('Avalanche warning issued detail is incomplete')
    day = start.date().isoformat()
    fields = {'assessment': 'not_assessed' if unassessed else ('tendency' if data['IsTendency'] else 'assessed'),
              'danger_level': None if data['DangerLevel'] == '0' else int(data['DangerLevel']),
              'publication_id': None if unassessed else data['RegId'],
              'warning_sha256': None if unassessed else digest(payload)}
    return {'key': region + ':1:' + day, 'title': name + ' · ' + day,
            'url': 'https://www.varsom.no/snoskred/varsling/varsel/' + quote(name, safe='') + '/' + day,
            'published': day, 'fields': fields}


class AvalancheWarningsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API,)):
            raise ValueError('avalanche_warnings uses the documented NVE production API')
        self.regions = strings(config.options.get('regions'), 'regions')
        if len(self.regions) > 10 or any(not re.fullmatch(r'[1-9][0-9]{3}', x) for x in self.regions):
            raise ValueError('regions must contain one to ten explicit four-digit region IDs')
        self.lookback = integer(config.options.get('lookback_days', 1), 'lookback_days', 0, 7)
        self.horizon = integer(config.options.get('forecast_days', 2), 'forecast_days', 0, 2)
        if len(self.regions) * (self.lookback + self.horizon + 1) > self.max_records:
            raise ValueError('Avalanche warning selection exceeds max_records')
        if self.complete or 'removed' in self.events or self.thresholds:
            raise ValueError('Avalanche warning observations do not support removal claims or thresholds')

    def _json(self, url):
        response = self.get(url, headers={'Accept': 'application/json'}, stream=True,
                            allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Avalanche warning API returned an unexpected redirect')
            chunks, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > self.max_bytes:
                    raise SourceError('Avalanche warning response exceeds max_bytes')
                chunks.append(chunk)
            return json.loads(b''.join(chunks), object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('Avalanche warning response is invalid JSON') from exc
        finally:
            response.close()

    def read_records(self):
        current = today()
        first, last = current - timedelta(days=self.lookback), current + timedelta(days=self.horizon)
        expected = {first + timedelta(days=n) for n in range((last - first).days + 1)}
        records = []
        for region in sorted(self.regions):
            data = self._json(f'{API}/Warning/Region/{region}/1/{first}/{last}')
            if not isinstance(data, list) or len(data) != len(expected):
                raise SourceError('Avalanche warning response is incomplete for the requested dates')
            rows = [warning_record(r, region, first, last) for r in data]
            if {r['published'] for r in rows} != {d.isoformat() for d in expected}:
                raise SourceError('Avalanche warning dates are duplicated or missing')
            records.extend(rows)
        return sorted(records, key=lambda r: r['key'])

    def _item(self, row, event, details, suppress):
        # Placeholders are retained to detect a later issued assessment, but their
        # moving transport timestamps never create recurring notification noise.
        unassessed = row['fields']['assessment'] == 'not_assessed'
        item = super()._item(row, event, details, suppress or (unassessed and event == 'added'))
        label = 'Endret publisert skredvarsel' if event == 'changed' else 'Nyobservert publisert skredvarsel'
        if unassessed:
            label = 'Kilden oppgir nå at datoen ikke er vurdert'
        return replace(item, alert_details=(label,
            'Endringsmelding for oppgitt region og dato. Åpne lenken og les hele varselet før bruk',
            'Dette er ikke et komplett farevarsel eller en vurdering av lokale forhold',
            'Varsler fra Snøskredvarslingen i Norge og www.varsom.no'))
