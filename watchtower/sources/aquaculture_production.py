"""Fiskeridirektoratet monthly aquaculture biomass/production statistics."""
import csv, io, re
from datetime import date,datetime,timezone
import calendar
from .changes import SnapshotSource, document, integer, strings, canonical
from .common import SourceError

URL='https://register.fiskeridir.no/biomassestatistikk/BIOSTAT-LAKS-FLK/biostat-total-flk.csv'
MONTHS=('JANUAR','FEBRUAR','MARS','APRIL','MAI','JUNI','JULI','AUGUST','SEPTEMBER','OKTOBER','NOVEMBER','DESEMBER')
def today(): return datetime.now(timezone.utc).date()
HEAD=['ÅR','MÅNED_KODE','MÅNED','FYLKE','ARTSID','UTSETTSÅR','BEHFISK_STK','BIOMASSE_KG','UTSETT_SMOLT_STK','UTSETT_SMOLT_STK_MINDRE_ENN_500G','FORFORBRUK_KG','UTTAK_STK','UTTAK_KG','UTTAK_SLØYD_KG','UTTAK_HODEKAPPET_KG','UTTAK_RUNDVEKT_KG','DØDFISK_STK','UTKAST_STK','RØMMING_STK','ANDRE_STK','ANDRE_NY_STK','TELLEFEIL_STK']

class AquacultureProductionSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (URL,)):
            raise ValueError('aquaculture_production requires the official CSV')
        if self.complete or 'removed' in self.events:
            raise ValueError('Rolling aquaculture periods cannot establish removals')
        self.url = URL
        self.max_export_records = integer(config.options.get('max_export_records', 20000), 'max_export_records', 1, 20000)
        self.latest_months = integer(config.options.get('latest_months', 3), 'latest_months', 1, 12)
        self.max_age_days = integer(config.options.get('max_age_days', 120), 'max_age_days', 1, 730)
        self.counties = strings(config.options['counties'], 'counties') if 'counties' in config.options else ()
        self.species = strings(config.options['species'], 'species') if 'species' in config.options else ()
        if any(x not in {'LAKS', 'REGNBUEØRRET'} for x in self.species):
            raise ValueError('species must use exact source labels')

    def _read_once(self):
        raw = document(self, self.url)
        try:
            table = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')), delimiter=';', strict=True))
        except (UnicodeError, csv.Error) as exc:
            raise SourceError('Aquaculture CSV is invalid') from exc
        if not table or table[0] != HEAD:
            raise SourceError('Aquaculture CSV headers changed')
        values = table[1:]
        if not 1 <= len(values) <= self.max_export_records:
            raise SourceError('Aquaculture export exceeds row bounds or is empty')
        seen, periods, rows = set(), set(), []
        for cells in values:
            if len(cells) != len(HEAD) or any(len(v) > 200 or '\ufffd' in v or '\x00' in v for v in cells):
                raise SourceError('Aquaculture row width or text is invalid')
            r = dict(zip(HEAD, cells))
            if not re.fullmatch(r'20[0-9]{2}', r['ÅR']) or not re.fullmatch(r'[1-9]|1[0-2]', r['MÅNED_KODE']):
                raise SourceError('Aquaculture year or month is invalid')
            y, m = int(r['ÅR']), int(r['MÅNED_KODE'])
            if y < 2005 or (y, m) > (today().year, today().month) or r['MÅNED'] != MONTHS[m-1]:
                raise SourceError('Aquaculture period or month label is inconsistent')
            if not r['FYLKE'].strip() or r['FYLKE'] != r['FYLKE'].strip() or r['ARTSID'] not in {'LAKS', 'REGNBUEØRRET'}:
                raise SourceError('Aquaculture county or species is invalid')
            # Some source cohorts are dated one year after the observation year.
            cohort = r['UTSETTSÅR']
            if cohort and (not re.fullmatch(r'[12][0-9]{3}', cohort) or not 1900 <= int(cohort) <= y+1):
                raise SourceError('Aquaculture release year is invalid')
            for k in HEAD[6:]:
                pattern = r'-?[0-9]{1,16}' if k.endswith('_STK') or k.startswith('UTSETT_SMOLT_STK') else r'-?[0-9]{1,16}(?:\.[0-9]{1,6})?'
                if not re.fullmatch(pattern, r[k]):
                    raise SourceError('Aquaculture metric is absent or has invalid source format')
            key = canonical([y, m, r['FYLKE'], r['ARTSID'], cohort or None])
            if key in seen:
                raise SourceError('Aquaculture identity is repeated')
            seen.add(key)
            periods.add((y, m))
            rows.append((key, y, m, r))
        newest = max(periods)
        last = date(newest[0], newest[1], calendar.monthrange(*newest)[1])
        if (today()-last).days > self.max_age_days:
            raise SourceError('Aquaculture latest period is stale')
        serial = newest[0]*12 + newest[1]-1
        chosen = {((serial-i)//12, (serial-i)%12+1) for i in range(self.latest_months)}
        if not chosen <= periods:
            raise SourceError('Aquaculture recent monthly window has a gap')
        out, selected_counties, selected_species = [], set(), set()
        for key, y, m, r in rows:
            if (y, m) not in chosen or (self.counties and r['FYLKE'] not in self.counties) or (self.species and r['ARTSID'] not in self.species):
                continue
            selected_counties.add(r['FYLKE'])
            selected_species.add(r['ARTSID'])
            out.append({'key': key, 'title': f"{r['ARTSID']} · {r['FYLKE']} · {y}-{m:02d}", 'url': self.url, 'published': None,
                        'fields': {k: (r[k] if r[k] != '' else None) for k in HEAD}})
        if not out or len(out) > self.max_records or not set(self.counties) <= selected_counties or not set(self.species) <= selected_species:
            raise SourceError('Aquaculture selection is empty, incomplete or exceeds max_records')
        return sorted(out, key=lambda r: r['key']), f'{newest[0]}-{newest[1]:02d}', sorted(values)

    def read_records(self):
        first = self._read_once()
        if self._read_once() != first:
            raise SourceError('Aquaculture full export changed between complete reads')
        rows, self.latest_period, _ = first
        return rows

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope and old.get('latest_period', '') > self.latest_period:
            raise SourceError('Aquaculture latest period regressed')
        self._next['latest_period'] = self.latest_period
        return items
