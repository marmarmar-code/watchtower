"""Latest disclosed short-position snapshots from the official register API."""
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re

from .changes import SnapshotSource, integer, strings
from .common import SourceError

ORIGIN='https://ssr.finanstilsynet.no'
DATA_URL=ORIGIN+'/api/v2/instruments'
ISIN=re.compile(r'[A-Z]{2}[A-Z0-9]{9}[0-9]')


def text(value):
    if not isinstance(value,str) or len(value)>1000 or not value.strip():
        raise SourceError('Short register text is empty, invalid or excessive')
    return ' '.join(value.split())


def source_date(value):
    # The published date schema is serialized as an offset-free midnight timestamp.
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T00:00:00)?',value):
        raise SourceError('Short register date is not a date or midnight date label')
    try:parsed=date.fromisoformat(value[:10])
    except ValueError as exc:raise SourceError('Short register date is invalid') from exc
    if not 1900<=parsed.year<=2100:raise SourceError('Short register date is out of bounds')
    return parsed.isoformat()


def percent(value):
    if isinstance(value,bool) or not isinstance(value,(int,Decimal)):
        raise SourceError('Short percentage must be a JSON number')
    try:number=Decimal(value)
    except InvalidOperation as exc:raise SourceError('Short percentage is malformed') from exc
    if not number.is_finite() or not 0<=number<=10000:
        raise SourceError('Short percentage is non-finite or out of bounds')
    if number.as_tuple().exponent < -12 or len(number.as_tuple().digits)>20:
        raise SourceError('Short percentage precision is excessive')
    return format(number,'f').rstrip('0').rstrip('.') if '.' in format(number,'f') else format(number,'f')


def shares(value):
    if type(value) is not int or not 0<=value<=2**63-1:
        raise SourceError('Short shares must be a bounded non-negative integer')
    return value


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise SourceError('Short register JSON repeats a field')
        result[key]=value
    return result


class ShortPositionsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(DATA_URL,)):
            raise ValueError('short_positions accepts only the official register API')
        if self.complete or 'removed' in self.events:
            raise ValueError('Register absence cannot establish a closed short position')
        self.isins=strings(config.options.get('isins'),'isins')
        if len(self.isins)>100 or any(not ISIN.fullmatch(i) for i in self.isins):
            raise ValueError('isins requires up to 100 exact uppercase ISINs')
        self.max_instruments=integer(config.options.get('max_instruments',1000),'max_instruments',1,10000)
        self.max_history_events=integer(config.options.get('max_history_events',30000),'max_history_events',1,100000)
        self.max_positions=integer(config.options.get('max_positions_per_event',100),'max_positions_per_event',1,1000)
        self.field_labels={'issuer':'Utsteder','event_date':'Siste oppførte hendelsesdato',
            'short_percent':'Samlet oppført shortprosent','short_shares':'Samlet oppført antall aksjer',
            'active_positions':'Offentlig oppførte innehaverposisjoner',**self.field_labels}

    def _read(self):
        response=self.get(DATA_URL,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code!=200:raise SourceError('Short register returned an unexpected redirect')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes:raise SourceError('Short register response exceeds max_bytes')
                chunks.append(chunk)
            try:data=json.loads(b''.join(chunks),parse_float=Decimal,object_pairs_hook=unique_object)
            except (ValueError,UnicodeError) as exc:raise SourceError('Short register JSON is malformed') from exc
        finally:response.close()
        if not isinstance(data,list) or not 1<=len(data)<=self.max_instruments:
            raise SourceError('Short instrument list is empty or excessive')
        rows=[];seen=set();event_count=0
        for instrument in data:
            if not isinstance(instrument,dict) or set(instrument)!={'isin','issuerName','events'}:
                raise SourceError('Short instrument schema changed')
            ident=instrument['isin'];issuer=text(instrument['issuerName']);events=instrument['events']
            if not isinstance(ident,str) or not ISIN.fullmatch(ident) or ident in seen:
                raise SourceError('Short instrument identity is invalid or duplicated')
            seen.add(ident)
            if not isinstance(events,list) or not events:
                raise SourceError('Short instrument event history is empty or invalid')
            event_count+=len(events)
            if event_count>self.max_history_events:raise SourceError('Short history exceeds max_history_events')
            dated={}
            for event in events:
                if not isinstance(event,dict) or set(event)!={'date','shortPercent','shares','activePositions'}:
                    raise SourceError('Short event schema changed')
                day=source_date(event['date'])
                if day in dated:raise SourceError('Short instrument repeats an event date')
                total_percent=percent(event['shortPercent']);total_shares=shares(event['shares'])
                positions=event['activePositions']
                if not isinstance(positions,list) or len(positions)>self.max_positions:
                    raise SourceError('Short active-position list is invalid or excessive')
                holders=set();active=[]
                for position in positions:
                    if not isinstance(position,dict) or set(position)!={'date','shortPercent','shares','positionHolder'}:
                        raise SourceError('Short holder-position schema changed')
                    holder=text(position['positionHolder']).upper();position_day=source_date(position['date'])
                    if holder in holders or position_day>day:
                        raise SourceError('Short holder identity repeats or its date exceeds the event date')
                    holders.add(holder)
                    value=percent(position['shortPercent']);amount=shares(position['shares'])
                    if Decimal(value)<Decimal('0.5') or amount==0:
                        raise SourceError('Short active position is below the public disclosure threshold')
                    active.append({'holder':holder,'position_date':position_day,'percent':value,'shares':amount})
                if not active and (Decimal(total_percent)!=0 or total_shares!=0):
                    raise SourceError('Empty short positions disagree with non-zero source totals')
                # Preserve aggregate shares: actual source sums can differ from holder sums.
                dated[day]={'isin':ident,'issuer':issuer,'event_date':day,'short_percent':total_percent,
                            'short_shares':total_shares,'active_positions':sorted(active,key=lambda p:p['holder'])}
            if ident in self.isins:
                latest=dated[max(dated)]
                rows.append({'key':ident,'title':issuer,'url':ORIGIN+'/Home/Details/'+ident,
                             'published':None,'fields':latest})
        if set(self.isins)-seen:raise SourceError('Selected ISIN is missing; prior snapshot preserved')
        return sorted(rows,key=lambda r:r['key'])

    def read_records(self):
        first,second=self._read(),self._read()
        if first!=second:raise SourceError('Selected short snapshots changed between complete reads')
        return first

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        stored=((previous or {}).get('source_state') or {}).get('records',{})
        if stored.get('scope')==self.scope:
            old=stored.get('rows',{})
            for key,current in self._next['rows'].items():
                if key in old and current['row']['fields']['event_date']<old[key]['row']['fields']['event_date']:
                    raise SourceError('Latest short event date regressed; prior snapshot preserved')
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields']
        info=('Nyobservert shortoppføring' if event=='added' else 'Endret shortoppføring',
            f"{f['issuer']} · {f['isin']}",f"Oppført hendelsesdato: {f['event_date']}",
            f"Samlet oppført: {f['short_percent']} % · {f['short_shares']} aksjer")
        for p in f['active_positions'][:10]:
            info+=(f"{p['holder']}: {p['percent']} % · {p['shares']} aksjer · posisjonsdato {p['position_date']}",)
        if len(f['active_positions'])>10:info+=(f"{len(f['active_positions'])-10} flere innehavere i registeret",)
        if not f['active_positions']:info+=('Ingen offentlig oppførte posisjoner i siste hendelse',)
        if event=='changed':info+=tuple(d[:1200] for d in details[1:])
        return replace(item,alert_details=info+('Bare offentlig rapporterte posisjoner; fravær eller null i registeret beviser ikke at en posisjon er lukket',
            'Posisjonsdato og hendelsesdato er kildeopplysninger, ikke tidspunktet for publisering eller innlesing',))
