"""Public RASFF border-rejection summaries, with a validated bounded date window."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
import re

from .changes import SnapshotSource, integer, strings
from .common import SourceError

URL = 'https://webgate.ec.europa.eu/rasff-window/backend/public/notification/search/consolidated/en/'
DETAIL = 'https://webgate.ec.europa.eu/rasff-window/screen/notification/'


def today():
    return datetime.now(timezone.utc).date()


def clock(value):
    try:
        if not isinstance(value,str) or not re.fullmatch(r'\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}',value): raise ValueError()
        return datetime.strptime(value,'%d-%m-%Y %H:%M:%S')
    except ValueError as exc: raise SourceError('RASFF validation clock is invalid') from exc


def label(value):
    if not isinstance(value,str) or not value.strip() or len(value)>10000:
        raise SourceError('RASFF summary text is missing or excessive')
    return value.strip()


def identifier(value):
    if type(value) is not int or not 1<=value<=10**12: raise SourceError('RASFF identity is invalid')
    return value


def catalogue(value):
    if not isinstance(value,dict): raise SourceError('RASFF catalogue field is missing')
    return {'id':identifier(value.get('id')),'description':label(value.get('description'))}


def country(value):
    if not isinstance(value,dict): raise SourceError('RASFF country field is missing')
    code=value.get('isoCode')
    if not isinstance(code,str) or not re.fullmatch(r'[A-Z]{2}',code): raise SourceError('RASFF country code is invalid')
    return {'isoCode':code,'organizationName':label(value.get('organizationName'))}


def unique_object(pairs):
    result={}
    for k,v in pairs:
        if k in result: raise ValueError('duplicate JSON key')
        result[k]=v
    return result


class RasffBorderRejectionsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(URL,)): raise ValueError('rasff_border_rejections accepts only the official public summary search')
        if self.complete or 'removed' in self.events: raise ValueError('Date-window absence cannot establish clearance or withdrawal')
        self.window_days=integer(config.options.get('window_days',30),'window_days',1,90)
        self.max_pages=integer(config.options.get('max_pages',20),'max_pages',1,100)
        self.origins=strings(config.options['origin_countries'],'origin_countries') if 'origin_countries' in config.options else ()
        if any(not re.fullmatch(r'[A-Z]{2}',s) for s in self.origins): raise ValueError('origin_countries requires exact uppercase country codes')
        self.field_labels={'reference':'RASFF-referanse','validated_at':'Kildens valideringsklokke', 'subject':'Oppgitt forhold',
                           'product_category':'Produktkategori','product_type':'Produkttype','classification':'Meldingstype',
                           'risk':'Kildens risikovurdering','notifying_country':'Meldende land','origin_countries':'Oppgitt opprinnelse',**self.field_labels}

    def _page(self,page,start,end):
        body={'parameters':{'pageNumber':page,'itemsPerPage':100,'ordering':'notificationECValidationDate','sorting':'desc'},
              'ecValidDateFrom':start.strftime('%d-%m-%Y')+' 00:00:00','ecValidDateTo':end.strftime('%d-%m-%Y')+' 00:00:00',
              'notificationClassification':[305]}
        r=self.post(URL,json=body,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code!=200: raise SourceError('RASFF summary search redirected unexpectedly')
            chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes: raise SourceError('RASFF response exceeds max_bytes')
                chunks.append(chunk)
            raw=b''.join(chunks)
        finally:r.close()
        try:
            result=json.loads(raw.decode('utf-8'),object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        except (ValueError,UnicodeDecodeError) as exc: raise SourceError('RASFF response is not valid unambiguous JSON') from exc
        if not isinstance(result,dict) or not isinstance(result.get('notifications'),list): raise SourceError('RASFF search schema changed')
        total,pages=result.get('totalElements'),result.get('totalPages')
        if type(total) is not int or type(pages) is not int or total<0 or pages<0:
            raise SourceError('RASFF total count is invalid')
        if pages!=math.ceil(total/100): raise SourceError('RASFF page count disagrees with total')
        if total>self.max_records or pages>self.max_pages: raise SourceError('RASFF total exceeds configured bounds')
        expected=min(100,max(0,total-100*(page-1)))
        if len(result['notifications'])!=expected: raise SourceError('RASFF returned an incomplete page')
        rows=[]
        for n in result['notifications']:
            if not isinstance(n,dict): raise SourceError('RASFF notification is not an object')
            ident=identifier(n.get('notifId'));ref=label(n.get('reference'));stamp=clock(n.get('ecValidationDate'))
            if not re.fullmatch(r'\d{4}\.\d{4,6}',ref): raise SourceError('RASFF reference format changed')
            if not start<=stamp.date()<=end: raise SourceError('RASFF returned a record outside the requested date window')
            classification=catalogue(n.get('notificationClassification'))
            if classification!={'id':305,'description':'border rejection notification'}:
                raise SourceError('RASFF classification filter was not applied')
            origins=n.get('originCountries')
            if not isinstance(origins,list) or len(origins)>300: raise SourceError('RASFF origin list is invalid')
            origins=[country(c) for c in origins]
            if len({c['isoCode'] for c in origins})!=len(origins): raise SourceError('RASFF origin country is duplicated')
            fields={'reference':ref,'validated_at':stamp.isoformat(sep=' '),'subject':label(n.get('subject')),
                    'product_category':catalogue(n.get('productCategory')),'product_type':catalogue(n.get('productType')),
                    'classification':classification,'risk':catalogue(n.get('riskDecision')),
                    'notifying_country':country(n.get('notifyingCountry')),'origin_countries':sorted(origins,key=lambda c:c['isoCode'])}
            rows.append({'key':str(ident),'title':ref+' · '+fields['subject'],'url':DETAIL+str(ident),'published':None,'fields':fields})
        return total,pages,rows

    def _window(self,start,end):
        total,pages,rows=self._page(1,start,end)
        for page in range(2,pages+1):
            count,number,found=self._page(page,start,end)
            if (count,number)!=(total,pages): raise SourceError('RASFF totals changed during pagination')
            rows.extend(found)
        if len(rows)!=total or len({r['key'] for r in rows})!=total or len({r['fields']['reference'] for r in rows})!=total:
            raise SourceError('RASFF identities repeat or total is incomplete')
        stamps=[r['fields']['validated_at'] for r in rows]
        if stamps!=sorted(stamps,reverse=True): raise SourceError('RASFF date ordering was not applied')
        return sorted(rows,key=lambda r:r['key'])

    def read_records(self):
        end=today();start=end-timedelta(days=self.window_days-1)
        first,second=self._window(start,end),self._window(start,end)
        if first!=second: raise SourceError('RASFF date window changed between complete reads')
        return [r for r in first if not self.origins or any(c['isoCode'] in self.origins for c in r['fields']['origin_countries'])]

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress)
        title='Nyobservert grenseavvisning' if event=='added' else 'Endret melding om grenseavvisning'
        return replace(item,alert_details=(title,)+tuple(d[:900] for d in details[1:])+(
            'Risikovurderingen er kildens. Valideringsklokken har ingen oppgitt tidssone. Meldingen fastslår ikke salg eller distribusjon i Norge.',))
