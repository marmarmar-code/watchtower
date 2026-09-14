"""Complete published tariff-rate series for explicitly selected chapters."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import re
import tempfile

from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError

DATA_URL = 'https://data.toll.no/dataset/6243277c-891e-4088-be79-0456d589d033/resource/876e3a41-7a9c-438a-8592-137547a9263c/download/tollavgiftssats.json'
DATASET_URL = 'https://data.toll.no/no/dataset/tollavgiftssats'


def _text(value, name, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) > 200:
        raise SourceError(f'Tariff {name} is invalid')
    return value.strip()


def _date(value, optional=False):
    if optional and value == '':
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise SourceError('Tariff validity date is invalid') from None


def _amount(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,12},[0-9]{1,6}', value):
        raise SourceError('Tariff rate value is invalid')
    return format(Decimal(value.replace(',', '.')).normalize(), 'f')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceError('Tariff JSON repeats an object field')
        result[key] = value
    return result


def _shown(rate):
    value = rate['value'].replace('.', ',')
    unit = rate['unit_description'] or 'enhet ikke oppgitt'
    return f"{rate['country_group']}: {value} · {unit} ({rate['unit_code'] or 'ukjent kode'}) · {rate['valid_from']}–{rate['valid_to'] or 'sluttdato ikke oppgitt'}"


class CustomsTariffsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (DATA_URL,)):
            raise ValueError('customs_tariffs accepts only the official tariff JSON resource')
        if self.complete or 'removed' in self.events:
            raise ValueError('Tariff register absence cannot establish withdrawal')
        if 'max_bytes' in config.options:
            raise ValueError('Use max_tariff_bytes for this complete large tariff resource')
        self.chapters = strings(config.options.get('chapters'), 'chapters')
        if len(self.chapters) > 10 or any(not re.fullmatch(r'(?:0[1-9]|[1-9][0-9])', c) for c in self.chapters):
            raise ValueError('Select up to ten two-digit tariff chapters, 01 through 99')
        self.max_tariff_bytes = integer(config.options.get('max_tariff_bytes', 60000000), 'max_tariff_bytes', 1024, 80000000)
        self.max_register_records = integer(config.options.get('max_register_records', 15000), 'max_register_records', 1, 20000)
        self.max_rates = integer(config.options.get('max_rates_per_commodity', 500), 'max_rates_per_commodity', 1, 2000)
        self.field_labels = {'rates': 'Tollsatser', 'unit_code': 'Vareenhet', 'unit_description': 'Vareenhetens beskrivelse',
                             'additional_unit_code': 'Tilleggsenhet', 'additional_unit_description': 'Tilleggsenhetens beskrivelse', **self.field_labels}

    def read_records(self):
        response = self.get(DATA_URL, stream=True, allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Tariff resource returned an unexpected redirect')
            # Stream to disk instead of retaining a chunk list plus a joined copy.
            # JSON parsing still needs memory for the complete bounded document.
            with tempfile.TemporaryFile() as downloaded:
                size = 0
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > self.max_tariff_bytes:
                        raise SourceError('Tariff resource exceeds max_tariff_bytes')
                    downloaded.write(chunk)
                downloaded.seek(0)
                try:
                    payload = json.load(downloaded, object_pairs_hook=_unique_object)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise SourceError('Tariff resource is invalid JSON') from exc
        finally:
            response.close()
        return self._records(payload)

    def _records(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {'versjon', 'varer'} or payload['versjon'] != '1.2'
                or not isinstance(payload['varer'], list) or not 1 <= len(payload['varer']) <= self.max_register_records):
            raise SourceError('Tariff register envelope or record bound changed')
        seen, present_chapters, rows = set(), set(), []
        for raw in payload['varer']:
            if not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not re.fullmatch(r'\d{8}', raw['id']):
                raise SourceError('Tariff commodity identity is invalid')
            ident = raw['id']
            if ident in seen:
                raise SourceError('Tariff commodity identities repeat')
            seen.add(ident)
            if ident[:2] not in self.chapters:
                continue
            present_chapters.add(ident[:2])
            rows.append(self._record(raw))
            if len(rows) > self.max_records:
                raise SourceError('Selected tariff chapters exceed max_records')
        if present_chapters != set(self.chapters):
            raise SourceError('A selected tariff chapter is absent; previous state preserved')
        return sorted(rows, key=lambda row: row['key'])

    def _record(self, raw):
        if set(raw) != {'id', 'enhet', 'enhetBeskrivelse', 'annenEnhet', 'annenEnhetBeskrivelse', 'avtalesatser'}:
            raise SourceError('Tariff commodity schema changed')
        fields = {key: _text(raw[name], name, nullable=True) for key, name in [
            ('unit_code','enhet'), ('unit_description','enhetBeskrivelse'),
            ('additional_unit_code','annenEnhet'), ('additional_unit_description','annenEnhetBeskrivelse')]}
        agreements = raw['avtalesatser']
        if not isinstance(agreements, list) or not 1 <= len(agreements) <= 200:
            raise SourceError('Tariff agreement list is invalid')
        groups, periods, rates = set(), set(), []
        for agreement in agreements:
            if not isinstance(agreement, dict) or set(agreement) != {'landgruppe', 'sats'}:
                raise SourceError('Tariff agreement schema changed')
            group = agreement['landgruppe']
            if not isinstance(group, str) or not re.fullmatch(r'[A-Z0-9]{1,20}', group) or group in groups:
                raise SourceError('Tariff country group is invalid or duplicated')
            groups.add(group)
            values = agreement['sats']
            if not isinstance(values, list) or not values or len(values) > self.max_rates:
                raise SourceError('Tariff rate list is invalid or exceeds its bound')
            for rate in values:
                if not isinstance(rate, dict) or set(rate) != {'satsVerdi','satsEnhet','satsEnhetBeskrivelse','fomdato','tomdato'}:
                    raise SourceError('Tariff rate schema changed')
                start, end = _date(rate['fomdato']), _date(rate['tomdato'], optional=True)
                if end and start > end:
                    raise SourceError('Tariff validity period is reversed')
                unit = _text(rate['satsEnhet'], 'rate unit', nullable=True)
                if unit and not re.fullmatch(r'[A-Z0-9]{1,10}', unit):
                    raise SourceError('Tariff rate unit code is invalid')
                period_key = (group, unit, start)
                if period_key in periods:
                    raise SourceError('Tariff rate period is duplicated or conflicting')
                periods.add(period_key)
                rates.append({'country_group':group, 'value':_amount(rate['satsVerdi']), 'unit_code':unit,
                              'unit_description':_text(rate['satsEnhetBeskrivelse'], 'rate unit description', nullable=True),
                              'valid_from':start, 'valid_to':end})
                if len(rates) > self.max_rates:
                    raise SourceError('Commodity exceeds max_rates_per_commodity')
        fields['rates'] = sorted(rates, key=canonical)
        return {'key':raw['id'], 'title':'Tollsatser · varenummer '+raw['id'], 'url':DATASET_URL,
                'published':None, 'fields':fields}

    def describe_change(self, name, before, after):
        if name != 'rates':
            return super().describe_change(name, before, after)
        old, new = {canonical(r):r for r in before}, {canonical(r):r for r in after}
        removed, added = sorted(old.keys()-new.keys()), sorted(new.keys()-old.keys())
        lines = ['Reviderte satsoppføringer']
        lines += ['Tidligere: '+_shown(old[k]) for k in removed[:4]]
        lines += ['Nå oppført: '+_shown(new[k]) for k in added[:4]]
        if len(removed) > 4 or len(added) > 4:
            lines.append(f'Totalt {len(removed)} tidligere og {len(added)} nye/reviderte satsoppføringer; se kilden')
        return '\n'.join(lines)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        rates = row['fields']['rates']
        label = 'Nyobservert varenummer med tollsatser' if event == 'added' else 'Endrede tollsatser eller gyldighetsopplysninger'
        info = (label, f"Varenummer: {row['key']} · {len(rates)} satsoppføringer")
        if event == 'changed':
            info += details[1:]
        else:
            info += tuple(_shown(r) for r in rates[:6])
            if len(rates) > 6:
                info += (f'{len(rates)-6} ytterligere satsoppføringer står i kilden',)
        return replace(item, alert_details=info + ('Landgrupper og enheter vises som kildekoder; valuta er ikke utledet',
            'Oppførte perioder er ikke publiseringsdatoer eller en beregnet toll for en konkret import',))
