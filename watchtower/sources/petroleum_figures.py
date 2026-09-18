"""Published petroleum production, reserves and expected investment figures."""
import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io
import re

from .changes import SnapshotSource, canonical, document, integer, strings, shown
from .common import SourceError

BASE = 'https://factpages.sodir.no/public?/Factpages/external/tableview/'
SUFFIX = '&rs:Command=Render&rc:Toolbar=false&rc:Parameters=f&IpAddress=not_used&CultureCode=en&rs:Format=CSV&Top100=false'
PRODUCTION = ('prfPrdOilNetMillSm3', 'prfPrdGasNetBillSm3', 'prfPrdNGLNetMillSm3',
              'prfPrdCondensateNetMillSm3', 'prfPrdOeNetMillSm3', 'prfPrdProducedWaterInFieldMillSm3')
RESERVES = tuple('fld'+prefix+product for prefix in ('Recoverable','Remaining')
                 for product in ('Oil','Gas','NGL','Condensate','OE'))
HEADERS = {
    'production': ('prfInformationCarrier','prfYear','prfMonth',*PRODUCTION,'prfNpdidInformationCarrier'),
    'reserves': ('fldName','fldVersion',*RESERVES,'fldDateOffResEstDisplay','fldNpdidField','DatesyncNPD'),
    'investments': ('fldName','fldInvestmentExpected','fldInvExpFixYear','fldNpdidField'),
}
UNITS = ('mill. Sm3','mrd. Sm3','mill. tonn','mill. Sm3','mill. Sm3 o.e.')
COMMODITIES = ('Olje','Gass','NGL','Kondensat','Oljeekvivalenter')


def number(value):
    if value == '':
        return None
    if not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',value):
        raise SourceError('Petroleum figure is not a published decimal number')
    try:
        parsed=Decimal(value)
        return '0' if parsed.is_zero() else format(parsed.normalize(),'f')
    except InvalidOperation:
        raise SourceError('Invalid petroleum figure') from None


def source_date(value):
    try:
        parsed = datetime.strptime(value,'%d.%m.%Y').date()
        if not date(1970,1,1) <= parsed <= date.today():
            raise ValueError()
        return parsed.isoformat()
    except ValueError:
        raise SourceError('Petroleum source date is invalid or future-dated') from None


class PetroleumFiguresSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config,*args,**kwargs)
        self.profile = config.options.get('profile')
        if self.profile not in HEADERS:
            raise ValueError('petroleum_figures requires production, reserves or investments profile')
        table = {'production':'field_production_monthly','reserves':'field_reserves',
                 'investments':'field_investment_expected'}[self.profile]
        self.url = BASE+table+SUFFIX
        if config.urls not in ((),(self.url,)):
            raise ValueError('petroleum_figures accepts only the corresponding official export')
        if self.complete or 'removed' in self.events or self.allow_empty:
            raise ValueError('Petroleum figures require nonempty snapshots without removal events')
        self.export_limit = integer(config.options.get('max_export_rows',50000),'max_export_rows',100,100000)
        self.months = integer(config.options.get('latest_months',6),'latest_months',1,24)
        self.versions = integer(config.options.get('latest_versions',1),'latest_versions',1,5)
        default_age = {'production':150,'reserves':550,'investments':730}[self.profile]
        self.max_age = integer(config.options.get('max_period_age_days',default_age),
                               'max_period_age_days',30,730)
        self.fields = strings(config.options['field_ids'],'field_ids') if 'field_ids' in config.options else ()
        if len(self.fields)>100 or any(not re.fullmatch(r'[0-9]{1,9}',v) for v in self.fields):
            raise ValueError('field_ids must contain up to 100 exact published numeric IDs')
        self.labels = {}
        if self.profile=='production':
            self.labels = dict(zip(PRODUCTION,('Netto olje (mill. Sm3)','Netto gass (mrd. Sm3)',
                'Netto NGL (mill. Sm3)','Netto kondensat (mill. Sm3)',
                'Netto oljeekvivalenter (mill. Sm3)','Produsert vann (mill. Sm3)')))
        elif self.profile=='reserves':
            self.labels = {name:('Opprinnelig utvinnbar ' if i<5 else 'Gjenværende ')+COMMODITIES[i%5].lower()+' ('+UNITS[i%5]+')'
                           for i,name in enumerate(RESERVES)}
        else:
            self.labels = {'fldInvestmentExpected':'Forventet investering framover (mill. faste NOK)'}
        self.field_labels = {'name':'Felt/funn','as_of':'Kildens anslagsdato',**self.labels,**self.field_labels}

    def _parse(self, raw):
        try:
            if not raw.endswith(b'\n'):
                raise SourceError('Petroleum CSV lacks its final row delimiter')
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')),strict=True)
            if tuple(reader.fieldnames or ()) != HEADERS[self.profile]:
                raise SourceError('Petroleum export columns changed')
            records, identities, periods = [], set(), set()
            for row in reader:
                if len(identities)>=self.export_limit or None in row or None in row.values():
                    raise SourceError('Petroleum export is malformed or exceeds max_export_rows')
                if self.profile=='production':
                    ident,name,year,month = (row[k] for k in ('prfNpdidInformationCarrier','prfInformationCarrier','prfYear','prfMonth'))
                    if not re.fullmatch(r'[0-9]{4}',year) or not re.fullmatch(r'(?:[1-9]|1[0-2])',month):
                        raise SourceError('Petroleum production period changed format')
                    period = f'{year}-{int(month):02}'
                    stamp = date(int(year),int(month),1)
                    numeric = PRODUCTION
                    extra = {}
                elif self.profile=='reserves':
                    ident,name,period = (row[k] for k in ('fldNpdidField','fldName','fldVersion'))
                    if not re.fullmatch(r'[0-9]{4}',period):
                        raise SourceError('Petroleum reserve version is not a year')
                    stamp = date(int(period),12,31)
                    extra = {'as_of':source_date(row['fldDateOffResEstDisplay'])}
                    source_date(row['DatesyncNPD'])
                    numeric = RESERVES
                else:
                    ident,name,period = (row[k] for k in ('fldNpdidField','fldName','fldInvExpFixYear'))
                    if not re.fullmatch(r'[0-9]{4}',period):
                        raise SourceError('Investment price base is not a year')
                    stamp = date(int(period),1,1)
                    extra = {}
                    numeric = ('fldInvestmentExpected',)
                if not date(1970,1,1)<=stamp<=date.today() or not re.fullmatch(r'[0-9]{1,9}',ident) or not name or len(name)>200:
                    raise SourceError('Petroleum identity or observation period is invalid')
                key = canonical([ident,period])
                if key in identities:
                    raise SourceError('Petroleum export duplicates a field and period')
                identities.add(key);periods.add(period)
                records.append({'key':key,'title':name+' · '+period,'url':self.url,'published':None,
                    'field_id':ident,'period':period,'fields':{'name':name,**extra,**{k:number(row[k]) for k in numeric}}})
            if not records:
                raise SourceError('Petroleum export is empty')
            selected_periods = sorted(periods)[-(self.months if self.profile=='production' else self.versions):]
            latest = selected_periods[-1]
            stamp = date.fromisoformat(latest+'-01') if self.profile=='production' else date(int(latest),1 if self.profile=='investments' else 12,1 if self.profile=='investments' else 31)
            if (date.today()-stamp).days>self.max_age:
                raise SourceError('Latest petroleum observation period is older than the configured bound')
            if self.profile=='production':
                ordinals=[int(v[:4])*12+int(v[5:]) for v in selected_periods]
                if ordinals!=list(range(ordinals[-1]-len(ordinals)+1,ordinals[-1]+1)):
                    raise SourceError('Petroleum monthly export has a gap in the selected periods')
            selected=[r for r in records if r['period'] in selected_periods and (not self.fields or r['field_id'] in self.fields)]
            if self.fields and set(self.fields)!={r['field_id'] for r in selected}:
                raise SourceError('Selected petroleum fields lack records in the latest periods')
            return sorted(selected,key=lambda r:r['key']), {'export_rows':len(records),'latest_period':latest}
        except (UnicodeError,csv.Error,ValueError) as exc:
            raise SourceError('Petroleum CSV cannot be decoded under the verified contract') from exc

    def read_records(self):
        records,stats=self._parse(document(self,self.url))
        again,other=self._parse(document(self,self.url))
        if records!=again or stats!=other:
            raise SourceError('Petroleum figures changed between complete reads')
        self.stats=stats
        return records

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope:
            if old.get('latest_period','')>self.stats['latest_period']:
                raise SourceError('Latest petroleum observation period regressed')
            self._next['rows']={**old.get('rows',{}),**self._next['rows']}
            if len(self._next['rows'])>self.max_records*2:
                raise SourceError('Retained petroleum figures exceed the history bound')
        self._next.update(self.stats)
        return items

    def _item(self,row,event,details,suppress):
        values=row['fields'];changes=details[1:] if event=='changed' else ()
        def value(name):
            label=self.field_labels[name]+': '
            return next((d[len(label):] for d in changes if d.startswith(label)),shown(values[name]))
        content=[('Nyobservert periode' if event=='added' else 'Reviderte kildetall')+' · '+row['period']+' · '+value('name')]
        if self.profile=='production':
            content.extend(self.labels[k]+': '+value(k) for k in PRODUCTION)
            content.append('Publiserte månedsvolumer, ikke dagsrate eller prognose. Oljeekvivalenter summeres ikke med komponentene. Negative korreksjoner bevares.')
        elif self.profile=='reserves':
            content.append('Versjon '+row['period']+' · Kildens anslagsdato: '+value('as_of'))
            for i,commodity in enumerate(COMMODITIES):
                content.append(commodity+' ('+UNITS[i]+'): oppr. utvinnbar '+value(RESERVES[i])+' · gjenværende '+value(RESERVES[i+5]))
            content.append('Kildeoppgitte reserveanslag, ikke funn eller produksjon. NGL er i mill. tonn. Null, manglende tall og negative korreksjoner holdes atskilt.')
        else:
            content.extend((self.labels['fldInvestmentExpected']+': '+value('fldInvestmentExpected'),
                'Fra år og prisbasisår: '+row['period']+'; ikke publiseringsdato eller bevis på fersk status.',
                'Forventet sum framover i faste kroner. Ikke faktisk årsforbruk, opprinnelig totalbudsjett eller dokumentert kostnadsoverskridelse. Negative kildeverdier bevares.'))
        return super()._item(row,event,content,suppress)
