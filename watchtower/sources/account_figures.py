"""Latest public company-account figures, retaining period and numeric precision."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re

from .changes import SnapshotSource, strings
from .common import SourceError
from .identifiers import valid_orgnr

API = 'https://data.brreg.no/regnskapsregisteret/regnskap/'
FIGURES = {
    'revenue': ('resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter', 'Driftsinntekter'),
    'operating_costs': ('resultatregnskapResultat.driftsresultat.driftskostnad.sumDriftskostnad', 'Driftskostnader'),
    'operating_result': ('resultatregnskapResultat.driftsresultat.driftsresultat', 'Driftsresultat'),
    'net_finance': ('resultatregnskapResultat.finansresultat.nettoFinans', 'Netto finans'),
    'pretax_result': ('resultatregnskapResultat.ordinaertResultatFoerSkattekostnad', 'Resultat før skatt'),
    'annual_result': ('resultatregnskapResultat.aarsresultat', 'Årsresultat'),
    'assets': ('eiendeler.sumEiendeler', 'Sum eiendeler'),
    'current_assets': ('eiendeler.omloepsmidler.sumOmloepsmidler', 'Omløpsmidler'),
    'fixed_assets': ('eiendeler.anleggsmidler.sumAnleggsmidler', 'Anleggsmidler'),
    'equity': ('egenkapitalGjeld.egenkapital.sumEgenkapital', 'Egenkapital'),
    'liabilities': ('egenkapitalGjeld.gjeldOversikt.sumGjeld', 'Gjeld'),
    'short_term_liabilities': ('egenkapitalGjeld.gjeldOversikt.kortsiktigGjeld.sumKortsiktigGjeld', 'Kortsiktig gjeld'),
    'long_term_liabilities': ('egenkapitalGjeld.gjeldOversikt.langsiktigGjeld.sumLangsiktigGjeld', 'Langsiktig gjeld'),
    'equity_and_liabilities': ('egenkapitalGjeld.sumEgenkapitalGjeld', 'Sum egenkapital og gjeld'),
}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON field')
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


def _mapping(value, key):
    item = value.get(key)
    if not isinstance(item, dict):
        raise SourceError(f'Account figures lack the {key} object')
    return item


def _text(value, name, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SourceError(f'Account {name} is invalid')
    return ' '.join(value.split())


def _date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise SourceError('Account period date is invalid') from None


def _number(value, path):
    for key in path.split('.'):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise SourceError('Account numeric field path has a malformed parent')
        value = value.get(key)
    if value is None:
        return None
    if type(value) not in (int, Decimal):
        raise SourceError('Account figure is not a JSON number')
    amount = Decimal(value)
    if not amount.is_finite() or amount.adjusted() > 29 or amount.as_tuple().exponent < -20:
        raise SourceError('Account figure exceeds numeric precision bounds')
    if not amount:
        return '0'
    plain = format(amount, 'f')
    return plain.rstrip('0').rstrip('.') if '.' in plain else plain


class AccountFiguresSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.companies = strings(config.options.get('companies'), 'companies')
        if len(self.companies) > 10 or any(not x.isascii() or not valid_orgnr(x) for x in self.companies):
            raise ValueError('companies must contain at most ten valid organisation numbers')
        if config.urls:
            raise ValueError('account_figures uses the fixed public latest-account endpoint')
        if self.complete or 'removed' in self.events:
            raise ValueError('Latest-account responses cannot confirm removals')
        if self.thresholds:
            raise ValueError('Account figures do not apply thresholds across unspecified scale or currency changes')
        self.field_labels = {key: label for key, (_, label) in FIGURES.items()} | {
            'submission_id': 'Kilde-ID for regnskap', 'journal_number': 'Journalnummer',
            'currency': 'Oppgitt valuta', 'layout': 'Oppstillingsplan',
            'accounting_rules': 'Regnskapsregler', 'liquidation_account': 'Avviklingsregnskap',
            **self.field_labels}
        self._previous_periods = {}

    def fetch_with_state(self, previous):
        stored = ((previous or {}).get('source_state') or {}).get('records', {})
        self._previous_periods = {}
        if isinstance(stored, dict) and stored.get('scope') == self.scope:
            for entry in stored.get('rows', {}).values():
                row = entry['row']
                self._previous_periods[row['organisation_number']] = row['period_end']
        return super().fetch_with_state(previous)

    def _read(self, orgnr):
        response = self.get(API + orgnr, headers={'Accept': 'application/json'}, stream=True,
                            allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Account API returned an unexpected redirect')
            chunks, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > self.max_bytes:
                    raise SourceError('Account response exceeds max_bytes')
                chunks.append(chunk)
            values = json.loads(b''.join(chunks), parse_float=Decimal,
                                parse_constant=_reject_constant, object_pairs_hook=_object)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('Account response is invalid JSON') from exc
        finally:
            response.close()
        # The verified unauthenticated contract returns one latest company account.
        # Do not choose arbitrarily if the service changes its response scope.
        if not isinstance(values, list) or len(values) != 1:
            raise SourceError('Expected exactly one public latest company account')
        row = _record(values[0], orgnr)
        if row['period_end'] < self._previous_periods.get(orgnr, ''):
            raise SourceError('Latest account period regressed; previous state preserved')
        return row

    def read_records(self):
        if len(self.companies) > self.max_records:
            raise SourceError('Account selection exceeds max_records')
        return [self._read(orgnr) for orgnr in sorted(self.companies)]

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']
        if event == 'added':
            details = ('Nyobservert regnskapsperiode med nøkkeltall',
                       *(f'{self.field_labels[k]}: {fields[k] if fields[k] is not None else "ikke oppgitt"}'
                         for k in ('revenue', 'operating_result', 'annual_result', 'assets', 'equity')))
        else:
            details = ('Endrede kildeopplysninger for samme regnskapsperiode', *details[1:])
        return replace(item, alert_details=(*details,
            f'Selskapsregnskap: {row["period_start"]}–{row["period_end"]}; oppgitt valuta: {fields["currency"]}',
            'Tallene gjengis uten omregning. API-et dokumenterer ikke tallskalaen; kontroller årsregnskapet før beløp brukes',
            'Kilden viser siste offentlige selskapsregnskap. Historiske korreksjoner og konserntall er ikke dekket'))


def _record(value, orgnr):
    if not isinstance(value, dict):
        raise SourceError('Account record is malformed')
    company = _mapping(value, 'virksomhet')
    if company.get('organisasjonsnummer') != orgnr or value.get('regnskapstype') != 'SELSKAP':
        raise SourceError('Account company or type differs from the public company scope')
    ident = value.get('id')
    if type(ident) is not int or not 1 <= ident <= 2**63 - 1:
        raise SourceError('Account submission identity is invalid')
    period = _mapping(value, 'regnskapsperiode')
    first, last = _date(period.get('fraDato')), _date(period.get('tilDato'))
    if not date(1900, 1, 1) <= first <= last <= datetime.now(timezone.utc).date():
        raise SourceError('Account period is invalid or not yet ended')
    fields = {'submission_id': ident, 'journal_number': _text(value.get('journalnr'), 'journal number'),
              'currency': _text(value.get('valuta'), 'currency', 50),
              'layout': _text(value.get('oppstillingsplan'), 'layout')}
    liquidation = value.get('avviklingsregnskap')
    if liquidation is not None and type(liquidation) is not bool:
        raise SourceError('Account liquidation flag is invalid')
    fields['liquidation_account'] = liquidation
    principles = value.get('regnkapsprinsipper')
    if principles is not None and not isinstance(principles, dict):
        raise SourceError('Account principles are malformed')
    rules = (principles or {}).get('regnskapsregler')
    fields['accounting_rules'] = _text(rules, 'accounting rules') if rules is not None else None
    for group in ('eiendeler', 'egenkapitalGjeld', 'resultatregnskapResultat'):
        _mapping(value, group)
    fields.update({name: _number(value, path) for name, (path, _) in FIGURES.items()})
    for group in (('assets', 'current_assets', 'fixed_assets'), ('equity', 'liabilities', 'equity_and_liabilities'),
                  ('revenue', 'operating_result', 'annual_result', 'pretax_result')):
        if all(fields[k] is None for k in group):
            raise SourceError('Account response lacks usable figures for a main statement')
    return {'key': f'{orgnr}:SELSKAP:{first.isoformat()}:{last.isoformat()}',
            'organisation_number': orgnr, 'period_start': first.isoformat(), 'period_end': last.isoformat(),
            'title': f'Selskapsregnskap {first.isoformat()}–{last.isoformat()} · {orgnr}',
            'url': API + orgnr, 'fields': fields}
