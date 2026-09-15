"""Explicitly typed public procedural documents with case references."""
from dataclasses import replace
from datetime import date, datetime, timezone
import json
import math
import re
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse

from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError

ORIGIN = 'https://www.eftasurv.int'
PATH = '/esa-at-a-glance/publications/public-access-to-documents/public-documents'
PAGE_URL = ORIGIN+PATH
API_URL = ORIGIN+'/cms/api/node'
ATTACHMENT_PATH = '/cms/sites/default/files/documents/gopro/'
DEFAULT_TYPES = ['Letter of Formal Notice','Reasoned Opinion','Referral to EFTA Court']
REQUIRED = {'title','number','type','caseNumber','caseName','date','country','attachment'}
OPTIONAL = {'state','collegeDecision'}


def bounded(value, *, empty=False):
    if not isinstance(value,str) or len(value)>4000:
        raise SourceError('ESA document field is invalid or excessive')
    result=' '.join(value.split())
    if not result and not empty:
        raise SourceError('ESA required document field is empty')
    return result


def attachment_url(raw):
    if not isinstance(raw,str) or len(raw)>6000 or re.search(r'%(?![0-9a-fA-F]{2})',raw):
        raise SourceError('ESA attachment URL is invalid')
    parsed=urlparse(urljoin(ORIGIN,raw))
    try:
        path=unquote(parsed.path,errors='strict')
    except UnicodeError as exc:
        raise SourceError('ESA attachment URL encoding is invalid') from exc
    if (parsed.scheme!='https' or parsed.netloc!='www.eftasurv.int' or parsed.query or parsed.fragment or parsed.params
            or not path.startswith(ATTACHMENT_PATH) or not path[len(ATTACHMENT_PATH):]
            or '\\' in path or any(p in {'.','..'} for p in path.split('/'))
            or any(ord(c)<32 or ord(c)==127 for c in path)):
        raise SourceError('ESA attachment URL is outside the official document path')
    return ORIGIN+quote(path,safe='/-_.~')


class EftaProceduralDocumentsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE_URL,)):
            raise ValueError('efta_procedural_documents accepts only the official document database')
        if self.complete or 'removed' in self.events:
            raise ValueError('Document absence cannot establish closure, withdrawal or compliance')
        self.country=config.options.get('country','NO')
        if self.country not in {'NO','IS','LI'}:
            raise ValueError('country must be NO, IS or LI')
        self.latest_years=integer(config.options.get('latest_years',1),'latest_years',1,3)
        self.types=strings(config.options.get('document_types',DEFAULT_TYPES),'document_types')
        if len(self.types)>20 or any(len(t)>100 or t!=' '.join(t.split()) for t in self.types):
            raise ValueError('document_types requires bounded exact source labels')
        self.max_pages=integer(config.options.get('max_pages',20),'max_pages',1,100)
        self.max_register_records=integer(config.options.get('max_register_records',5000),'max_register_records',1,20000)
        self.field_labels={'title':'Dokumenttittel','document_number':'Dokumentnummer','document_type':'Dokumenttype',
            'case_number':'Saksnummer','case_name':'Saksnavn','listed_date':'Oppført dokumentdato (UTC)',
            'listed_timestamp':'Kildens datotidsstempel','country_code':'Landkode','country_name':'Land',
            'college_decision':'Oppført kollegievedtak','attachment_description':'Vedleggsbeskrivelse',**self.field_labels}

    def _page(self,year,page):
        search={'page':str(page),'country':self.country}
        if year is not None:search['year']=str(year)
        # The client removes the leading question mark before nesting its search.
        url=API_URL+'?'+urlencode({'url':PATH,'search':urlencode(search)})
        response=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code!=200:
                raise SourceError('ESA document database returned an unexpected redirect')
            chunks,size=[],0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes:raise SourceError('ESA document response exceeds max_bytes')
                chunks.append(chunk)
            try:node=json.loads(b''.join(chunks))
            except (ValueError,UnicodeError) as exc:raise SourceError('ESA document JSON is malformed') from exc
        finally:response.close()
        if (not isinstance(node,dict) or node.get('type')!='listing_page' or node.get('listingId')!='documents'
                or node.get('alias')!=PATH or node.get('title')!='Public document database'):
            raise SourceError('ESA document database identity changed')
        listing=node.get('listing',{})
        if not isinstance(listing,dict) or not isinstance(listing.get('fields'),dict) or not isinstance(listing.get('data'),dict):
            raise SourceError('ESA document list schema is incomplete')
        fields,data=listing['fields'],listing['data']
        years=fields.get('years');countries=fields.get('countries')
        if (not isinstance(years,list) or not years or any(type(y) is not int or not 1990<=y<=date.today().year for y in years)
                or len(years)!=len(set(years)) or len(years)>100 or not isinstance(countries,list)
                or any(not isinstance(c,str) or not re.fullmatch(r'(?:NO|IS|LI)\|[^|]{1,100}',c) for c in countries)
                or len(countries)!=len({c.split('|')[0] for c in countries}) or self.country not in {c.split('|')[0] for c in countries}):
            raise SourceError('ESA year or country selection metadata changed')
        if set(data)!={'page','nodesPerPage','nodesCount','nodes'}:
            raise SourceError('ESA pagination schema changed')
        actual,per,total=data['page'],data['nodesPerPage'],data['nodesCount'];nodes=data['nodes']
        if (type(actual) is not int or actual!=page or type(per) is not int or not 1<=per<=100
                or type(total) is not int or not 0<=total<=1000000 or not isinstance(nodes,list)
                or len(nodes)!=max(0,min(per,total-(page-1)*per)) or page>max(1,math.ceil(total/per))):
            raise SourceError('ESA document page, total or row count is inconsistent')
        return tuple(sorted(years,reverse=True)),per,total,nodes

    def _record(self,node,year):
        if not isinstance(node,dict) or not REQUIRED<=set(node) or set(node)-REQUIRED-OPTIONAL:
            raise SourceError('ESA document row schema changed')
        values={k:bounded(node[k]) for k in ('title','number','type','caseNumber','caseName')}
        if any(not re.fullmatch(r'[0-9]{1,12}',values[k]) for k in ('number','caseNumber')):
            raise SourceError('ESA document or case number is invalid')
        country=node['country'];attachment=node['attachment']
        if (not isinstance(country,dict) or set(country)!={'code','name'} or country['code']!=self.country
                or not isinstance(attachment,dict) or set(attachment)!={'url','description'}):
            raise SourceError('ESA country filter or attachment schema is inconsistent')
        timestamp=node['date']
        if type(timestamp) is not int or not 631152000<=timestamp<=4102444800:
            raise SourceError('ESA document timestamp is invalid')
        listed=datetime.fromtimestamp(timestamp,timezone.utc)
        if year is not None and listed.year!=year:
            raise SourceError('ESA document year filter was not applied')
        if 'state' in node:bounded(node['state'])  # Geographical source label, not case status.
        decision=bounded(node['collegeDecision']) if 'collegeDecision' in node else None
        url=attachment_url(attachment['url'])
        fields={'title':values['title'],'document_number':values['number'],'document_type':values['type'],
            'case_number':values['caseNumber'],'case_name':values['caseName'],
            'listed_date':listed.date().isoformat(),'listed_timestamp':timestamp,
            'country_code':self.country,'country_name':bounded(country['name']),
            'college_decision':decision,'attachment_description':bounded(attachment['description'])}
        return {'key':canonical([values['number'],url]),'title':values['title'],'url':url,'published':None,'fields':fields}

    def _sweep(self):
        available,_,_,_=self._page(None,1)
        years=available[:self.latest_years]
        if len(years)!=self.latest_years:raise SourceError('ESA selected years are unavailable')
        rows=[];keys=set();totals=[]
        for year in years:
            meta,per,total,nodes=self._page(year,1)
            pages=max(1,math.ceil(total/per))
            if meta!=available or pages>self.max_pages or total>self.max_register_records:
                raise SourceError('ESA selected year exceeds bounds or selection changed')
            batch=list(nodes)
            for page in range(2,pages+1):
                next_meta,next_per,next_total,more=self._page(year,page)
                if (next_meta,next_per,next_total)!=(meta,per,total):
                    raise SourceError('ESA pagination metadata changed during collection')
                batch.extend(more)
            if len(batch)!=total:raise SourceError('ESA document collection is incomplete')
            for node in batch:
                row=self._record(node,year)
                if row['key'] in keys:raise SourceError('ESA document identity repeats across selected pages')
                keys.add(row['key']);rows.append(row)
            totals.append((year,total))
        return years,totals,sorted(rows,key=lambda r:r['key'])

    def read_records(self):
        first,second=self._sweep(),self._sweep()
        if first!=second:raise SourceError('ESA selected procedural-document collection changed between complete reads')
        self._latest_year=first[0][0]
        rows=[r for r in first[2] if r['fields']['document_type'] in self.types]
        if len(rows)>self.max_records:raise SourceError('ESA procedural-document selection exceeds max_records')
        return rows

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        stored=((previous or {}).get('source_state') or {}).get('records',{})
        if stored.get('scope')==self.scope and stored.get('latest_year',0)>self._latest_year:
            raise SourceError('ESA latest advertised year regressed; previous state preserved')
        self._next['latest_year']=self._latest_year
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields']
        info=('Nyobservert prosessdokument' if event=='added' else 'Endret prosessdokument',
              f"Sak {f['case_number']} · {f['document_type']}",f['case_name'],
              'Oppført dokumentdato (UTC): '+f['listed_date'])
        if f['college_decision']:info+=('Oppført kollegievedtak: '+f['college_decision'],)
        if event=='changed':info+=tuple(d[:800] for d in details[1:] if not d.startswith(self.field_labels['listed_timestamp']+':'))
        return replace(item,alert_details=info+('Dokumenttypen beskriver denne oppføringen, ikke nåværende saksstatus',
            'Ny rad eller vedleggslenke er ikke nødvendigvis en ny sak; vedleggets innhold er ikke innlest',))
