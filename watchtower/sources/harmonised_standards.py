"""Published standard-reference sets and dates from the Commission summary workbook."""
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import re
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError
from .workbooks import table_rows

PAGE='https://single-market-economy.ec.europa.eu/single-market/goods/european-standards/harmonised-standards/medical-devices_en'
HEADERS=['Legislation','ESO','Reference and title Provision','Start of legal effect','Publication OJ reference','Publication Decision reference','Publication OJ date','End of legal effect','Withdrawal OJ reference','Withdrawal Decision reference','Withdrawal OJ date']
UUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def clean(value):return ' '.join(value.split())


def day(value,optional=False):
    if not value and optional:return None
    if not re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}',value):raise SourceError('Standard summary date format changed')
    try:return datetime.strptime(value,'%d.%m.%Y').date().isoformat()
    except ValueError as exc:raise SourceError('Standard summary date is invalid') from exc


def download_url(value,storage=False):
    u=urlparse(value)
    if u.scheme!='https' or u.params or u.fragment:raise SourceError('Standard workbook URL is invalid')
    if storage:
        if u.netloc!='webgate.ec.europa.eu' or u.query or not re.fullmatch(r'/circabc-ewpp/d/d/workspace/SpacesStore/'+UUID+r'/download',u.path):
            raise SourceError('Standard workbook redirect leaves the observed official storage')
    else:
        query=parse_qs(u.query,keep_blank_values=True)
        if u.netloc!='single-market-economy.ec.europa.eu' or not re.fullmatch(r'/document/download/'+UUID+r'_en',u.path) or set(query)!={'filename'} or len(query['filename'])!=1 or not query['filename'][0].endswith('.xlsx'):
            raise SourceError('Standard workbook link is not the official spreadsheet download')
    return value


class HarmonisedStandardsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE,)):raise ValueError('harmonised_standards currently supports the official medical-device summary page')
        if self.complete or 'removed' in self.events:raise ValueError('Summary absence does not establish withdrawal of a standard')
        self.max_unpacked=integer(config.options.get('max_unpacked_bytes',2000000),'max_unpacked_bytes',1024,20000000)
        self.max_sheet_rows=integer(config.options.get('max_sheet_rows',2000),'max_sheet_rows',4,10000)
        self.references=strings(config.options['references'],'references') if 'references' in config.options else []
        if any(not v.startswith('EN ') or len(v)>150 for v in self.references):raise ValueError('references requires exact EN reference strings')
        self.field_labels={'provision':'Kildens referanser og tittel','standards_body':'Standardiseringsorgan','start_of_legal_effect':'Oppgitt virkningsstart','end_of_legal_effect':'Oppgitt virkningsslutt','publication_oj':'Publisering i EU-tidende','publication_decision':'Publiseringsvedtak','publication_date':'Publiseringsdato i EU-tidende','withdrawal_oj':'Tilbaketrekking i EU-tidende','withdrawal_decision':'Tilbaketrekkingsvedtak','withdrawal_publication_date':'Publisert tilbaketrekkingsdato',**self.field_labels}

    def _get(self,url,redirect=False):
        response=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code in (301,302,303,307,308):
                if not redirect or response.status_code!=302:raise SourceError('Standard source redirected unexpectedly')
                target=download_url(urljoin(url,response.headers.get('Location','')),storage=True)
                return None,target
            if response.status_code!=200:raise SourceError('Standard source status is unexpected')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes:raise SourceError('Standard source exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks),None
        finally:response.close()

    def _read(self):
        raw,_=self._get(PAGE);soup=BeautifulSoup(raw,'html.parser');titles=soup.select('h1')
        if len(titles)!=1 or clean(titles[0].get_text(' ',strip=True))!='Medical devices':raise SourceError('Standard summary page identity changed')
        links=[urljoin(PAGE,a['href']) for a in soup.select('a[href]') if clean(a.get_text(' ',strip=True)) in {'Summary list as xls file','Summary list as xlsx file'}]
        if len(links)!=1:raise SourceError('Standard workbook link is absent or ambiguous')
        link=download_url(links[0]);raw,target=self._get(link,redirect=True)
        if target:raw,_=self._get(target)
        generated,rows=self._records(raw)
        return generated,rows

    def _records(self,raw):
        data=table_rows(raw,'2017-745-Medical Devices',self.max_unpacked,self.max_sheet_rows,11,allow_blank_rows=True)
        if len(data)<4 or any(data[0][1:]) or any(data[1]) or data[2]!=HEADERS:raise SourceError('Standard summary headers changed')
        match=re.fullmatch(r'Generated on (\d{1,2}\.\d{1,2}\.\d{4})',data[0][0])
        if not match:raise SourceError('Standard summary generation date is absent')
        generated=day(match[1])
        if generated>datetime.now(timezone.utc).date().isoformat():raise SourceError('Standard summary generation date is in the future')
        rows=[];seen=set();found=set()
        for row in data[3:]:
            if not any(row):raise SourceError('Standard summary contains a blank interior row')
            law,body,provision,start,oj,decision,pub,end,withdrawal,withdrawal_decision,withdrawal_date=row
            if law!='2017/745 - Medical Devices' or body not in {'CEN','Cenelec'}:raise SourceError('Standard legislation or standards body changed')
            lines=[clean(x) for x in provision.splitlines() if clean(x)]
            references=[x for x in lines if x.startswith('EN ')]
            if not lines or not references or references[0]!=lines[0] or len(lines)==len(references) or len(set(references))!=len(references):raise SourceError('Standard reference set lacks its title or repeats')
            if any(not re.fullmatch(r'EN (?:ISO |IEC |ISO/IEC )?[0-9][0-9A-Za-z:.+ /()\-]*',v) or len(v)>150 for v in references):raise SourceError('Standard reference syntax changed')
            identity=hashlib.sha256(canonical(['2017/745',sorted(references)]).encode()).hexdigest()
            if identity in seen:raise SourceError('Standard reference set is duplicated')
            seen.add(identity)
            for ref in [oj,withdrawal]:
                if ref and not re.fullmatch(r'OJ [LC](?: [0-9]+)?',ref):raise SourceError('Standard OJ reference format changed')
            for ref in [decision,withdrawal_decision]:
                if ref and not re.fullmatch(r'20[0-9]{2}/[0-9]+',ref):raise SourceError('Standard decision reference format changed')
            if not oj or not decision:raise SourceError('Standard publication references are absent')
            fields={'provision':'\n'.join(lines),'standards_body':body,'start_of_legal_effect':day(start),'publication_oj':oj,'publication_decision':decision,'publication_date':day(pub),'end_of_legal_effect':day(end,True),'withdrawal_oj':withdrawal or None,'withdrawal_decision':withdrawal_decision or None,'withdrawal_publication_date':day(withdrawal_date,True)}
            if fields['end_of_legal_effect'] and fields['end_of_legal_effect']<fields['start_of_legal_effect']:raise SourceError('Standard source effect dates are reversed')
            found.update(references)
            if not self.references or set(self.references)&set(references):
                rows.append({'key':identity,'title':references[0],'url':PAGE,'published':None,'legislation':'2017/745','references':sorted(references),'fields':fields})
        if not seen or set(self.references)-found:raise SourceError('A selected standard reference is absent from the summary')
        if len(rows)>self.max_records:raise SourceError('Standard selection exceeds max_records')
        return generated,sorted(rows,key=lambda r:r['key'])

    def read_records(self):
        first,second=self._read(),self._read()
        if first!=second:raise SourceError('Standard summary changed between complete reads')
        self.generated_on=first[0]
        return first[1]

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous);old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope and old.get('generated_on','')>self.generated_on:raise SourceError('Standard summary generation date regressed')
        self._next['generated_on']=self.generated_on
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields']
        info=('Nyobservert standardreferansesett' if event=='added' else 'Endret standardreferanseoppføring',', '.join(row['references']),'Regelverk: '+row['legislation'],'Oppgitt virkningsstart: '+f['start_of_legal_effect'],'Oppgitt virkningsslutt: '+(f['end_of_legal_effect'] or 'ikke oppgitt'))
        if event=='changed':info+=tuple(d[:900] for d in details[1:])
        return replace(item,alert_details=info+('Kommisjonens sammendrag er informativt og gir ikke selv rettsvirkning. En tilbaketrukket standardreferanse er ikke et produktforbud.',))
