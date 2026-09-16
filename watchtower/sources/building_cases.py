"""Bounded Oslo PBE Saksinnsyn search results."""
from __future__ import annotations
import re
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from .changes import SnapshotSource, document, public_url, integer
from .common import SourceError

DEFAULT_URL='https://innsyn.pbe.oslo.kommune.no/saksinnsyn/main.asp'

class BuildingCasesSource(SnapshotSource):
    def __init__(self, config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if self.complete or 'removed' in self.events: raise ValueError('PBE search is not a complete snapshot; removed is unsupported')
        if config.urls and config.urls != (DEFAULT_URL,): raise ValueError('PBE requires its official search URL')
        self.url=DEFAULT_URL
        raw_query=config.options.get('query','')
        if not isinstance(raw_query,str): raise ValueError('PBE query must be text')
        self.query=raw_query.strip()
        if not self.query or len(self.query)>255: raise ValueError('PBE requires an explicit query up to 255 characters')
        self.field_labels.update(title='Sakstittel',status='Saksstatus hos PBE',latest_document_date='Siste dokumentdato',query='Fritekstsøk')
        self.max_rows=integer(config.options.get('max_rows',100), 'max_rows', 1, 1000)
    def read_records(self):
        url=self.url+'?'+urlencode({'mode':'all','text':self.query})
        soup=BeautifulSoup(document(self,url),'html.parser')
        search=soup.select_one('input#text[name="text"]')
        if search is None or search.get('value','').strip() != self.query:
            raise SourceError('PBE did not confirm the requested search')
        rows=[]
        for tr in soup.select('tr[onclick*="casedet.asp"]'):
            cells=tr.find_all('td')
            if len(cells)!=3: raise SourceError('PBE case row has unexpected columns')
            onclick=tr.get('onclick',''); match=re.search(r'caseno=([0-9]+)',onclick)
            if not match: raise SourceError('PBE row lacks stable case number')
            key=match.group(1); title=' '.join(cells[1].stripped_strings); status=' '.join(cells[2].stripped_strings)
            if not title or not status: raise SourceError('PBE row lacks title or status')
            latest=re.search(r'Siste dok\.\s*([0-9]{2}\.[0-9]{2}\.[0-9]{4})',status)
            fields={'title':title,'status':status,'latest_document_date':latest.group(1) if latest else None,'query':self.query}
            rows.append({'key':key,'title':f'PBE {key} · {title}','url':self.url+'?'+urlencode({'mode':'all','text':self.query}),'published':None,'fields':fields})
        if not rows: raise SourceError('PBE query returned no case rows')
        if len(rows)>self.max_rows: raise SourceError('PBE query exceeded max_rows; narrow the query')
        if len({r['key'] for r in rows})!=len(rows): raise SourceError('PBE query returned duplicate case numbers')
        return rows
