"""Curated post-publication updates, identified by the dataset record rather than DOI."""
import csv
from dataclasses import replace
from datetime import date,datetime,timedelta,timezone
import io
import re
from urllib.parse import quote,unquote

from .changes import SnapshotSource,integer,strings
from .common import SourceError

URL='https://gitlab.com/crossref/retraction-watch-data/-/raw/main/retraction_watch.csv?ref_type=heads&inline=false'
README='https://gitlab.com/crossref/retraction-watch-data/-/raw/main/README.md'
HEADERS=['Record ID','Title','Subject','Institution','Journal','Publisher','Country','Author','URLS','ArticleType','RetractionDate','RetractionDOI','RetractionPubMedID','OriginalPaperDate','OriginalPaperDOI','OriginalPaperPubMedID','RetractionNature','Reason','Paywalled','Notes','']
NATURES={'Retraction':'Tilbaketrekking','Correction':'Korreksjon','Expression of concern':'Bekymringsmelding','Reinstatement':'Gjeninnføring'}


def today():return datetime.now(timezone.utc).date()


def text(raw,required=True):
    value=raw.strip()
    if len(value)>10000 or any(ord(c)<32 and c not in '\t\r\n' for c in value) or required and not value:raise SourceError('Research update field is absent or invalid')
    return value


def tokens(raw):
    # Lists in this export conventionally have a trailing separator.
    value=text(raw);parts=value.split(';')
    if parts[-1]=='':parts.pop()
    result=[text(x) for x in parts]
    if not result or len(result)>200:raise SourceError('Research update list is invalid')
    return result


def day(raw):
    value=text(raw)
    if not re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4} 0:00',value):raise SourceError('Research update date format changed')
    try:return datetime.strptime(value,'%m/%d/%Y %H:%M').date().isoformat()
    except ValueError as exc:raise SourceError('Research update date is invalid') from exc


def doi(raw):
    value=text(raw,False)
    if not value or value.lower()=='unavailable':return None
    if re.search(r'%(?![0-9a-fA-F]{2})',value):raise SourceError('Research DOI has invalid percent encoding')
    value=unquote(value,errors='strict').lower()
    if not re.fullmatch(r'10\.[0-9]{4,9}/[^\s]+',value) or len(value)>500 or any(ord(c)<33 for c in value):raise SourceError('Research DOI is invalid')
    return value


def pmid(raw):
    value=text(raw,False)
    if value in {'','0'}:return None
    if not re.fullmatch(r'[1-9][0-9]{0,9}',value):raise SourceError('Research PubMed identifier is invalid')
    return value


class ResearchCorrectionsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(URL,)):raise ValueError('research_corrections accepts only the official public dataset')
        if self.complete or 'removed' in self.events:raise ValueError('Dataset absence cannot establish reinstatement')
        self.countries=strings(config.options.get('countries',['Norway']),'countries')
        if len(self.countries)>30 or any(len(x)>100 for x in self.countries):raise ValueError('countries requires bounded exact country labels')
        self.max_download_bytes=integer(config.options.get('max_download_bytes',100000000),'max_download_bytes',1024,200000000)
        self.max_export_records=integer(config.options.get('max_export_records',150000),'max_export_records',1,300000)
        self.max_export_age_days=integer(config.options.get('max_export_age_days',14),'max_export_age_days',1,60)
        self.field_labels={'title':'Kildens publikasjonstittel','nature':'Kildens oppdateringstype','notice_date':'Oppdateringsdato','original_date':'Opprinnelig publiseringsdato','reasons':'Kildeoppgitte årsaksmerkelapper','journal':'Publikasjonskanal','publisher':'Utgiver','countries':'Oppgitte land i forfattertilknytninger','authors':'Oppgitte forfattere','institutions':'Oppgitte institusjoner',**self.field_labels}

    def _download(self,url,limit):
        response=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code!=200:raise SourceError('Research dataset redirected unexpectedly')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>limit:raise SourceError('Research dataset exceeds download bounds')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:response.close()

    def _records(self,raw):
        if not raw.endswith(b'\n'):raise SourceError('Research CSV has no complete final line')
        try:
            reader=csv.reader(io.StringIO(raw.decode('utf-8-sig'),newline=''),strict=True)
            if next(reader)!=HEADERS:raise SourceError('Research CSV header changed')
            rows=[];ids=set();countries=set();blank=0;trailer=False
            for r in reader:
                if len(r)!=len(HEADERS) or any(len(v)>10000 for v in r):raise SourceError('Research CSV column count or cell bound changed')
                if not any(v.strip() for v in r):
                    trailer=True;blank+=1
                    if blank>1000:raise SourceError('Research CSV blank trailer exceeds bounds')
                    continue
                if trailer or r[-1]:raise SourceError('Research CSV contains an unexpected populated trailer')
                ident=text(r[0]);country=tokens(r[6])
                if not re.fullmatch(r'[1-9][0-9]{0,9}',ident) or ident in ids:raise SourceError('Research record ID is invalid or repeated')
                ids.add(ident);countries.update(country)
                if len(ids)>self.max_export_records:raise SourceError('Research export record count exceeds bounds')
                if not set(country)&set(self.countries):continue
                title=text(r[1]);nature=text(r[16]);paywall=text(r[18])
                if nature not in NATURES or paywall not in {'Yes','No','Unknown'}:raise SourceError('Research update type or access label changed')
                notice_doi,original_doi=doi(r[11]),doi(r[14]);notice_pmid,original_pmid=pmid(r[12]),pmid(r[15])
                if notice_doi:url='https://doi.org/'+quote(notice_doi,safe='/');link_kind='notice_doi'
                elif notice_pmid:url='https://pubmed.ncbi.nlm.nih.gov/'+notice_pmid+'/';link_kind='notice_pubmed'
                elif original_doi:url='https://doi.org/'+quote(original_doi,safe='/');link_kind='original_doi'
                elif original_pmid:url='https://pubmed.ncbi.nlm.nih.gov/'+original_pmid+'/';link_kind='original_pubmed'
                else:url=URL;link_kind='dataset'
                fields={'title':title,'subjects':sorted(tokens(r[2])),'institutions':text(r[3]),'journal':text(r[4]),'publisher':text(r[5]),'countries':sorted(country),'authors':tokens(r[7]),'source_urls':text(r[8],False) or None,'article_types':sorted(tokens(r[9])),'notice_date':day(r[10]),'notice_doi':notice_doi,'notice_pubmed_id':notice_pmid,'original_date':day(r[13]),'original_doi':original_doi,'original_pubmed_id':original_pmid,'nature':nature,'reasons':sorted(tokens(r[17])),'paywalled':paywall,'notes':text(r[19],False) or None}
                rows.append({'key':ident,'title':title,'url':url,'published':None,'link_kind':link_kind,'source_doi_text':{'notice':r[11],'original':r[14]},'fields':fields})
                if len(rows)>self.max_records:raise SourceError('Selected research records exceed max_records')
        except SourceError:raise
        except (UnicodeError,csv.Error,StopIteration) as exc:raise SourceError('Research CSV is incomplete or invalid') from exc
        if not ids or set(self.countries)-countries:raise SourceError('Selected research country is absent from the dataset')
        self.export_records=len(ids);self.blank_trailer_rows=blank
        return sorted(rows,key=lambda r:r['key'])

    def _read(self):
        try:readme=self._download(README,100000).decode('utf-8-sig')
        except UnicodeError as exc:raise SourceError('Research export metadata encoding is invalid') from exc
        matches=re.findall(r'^This repository contains the latest dataset from Retraction Watch, generated on (\d{4}-\d{2}-\d{2})\.$',readme,re.M)
        if len(matches)!=1:raise SourceError('Research export generation date is absent or ambiguous')
        try:stamp=date.fromisoformat(matches[0])
        except ValueError as exc:raise SourceError('Research export generation date is invalid') from exc
        if not today()-timedelta(days=self.max_export_age_days)<=stamp<=today():raise SourceError('Research export is stale or in the future')
        rows=self._records(self._download(URL,self.max_download_bytes))
        return stamp.isoformat(),self.export_records,rows

    def read_records(self):
        first,second=self._read(),self._read()
        if first!=second:raise SourceError('Research export changed between complete reads')
        self.exported_on=first[0]
        return first[2]

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous);old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope and old.get('exported_on','')>self.exported_on:raise SourceError('Research export generation date regressed')
        self._next['exported_on']=self.exported_on
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields'];info=('Nyobservert forskningsoppdatering' if event=='added' else 'Endret kildeoppføring om forskning','Record ID: '+row['key'],NATURES[f['nature']]+' · '+f['notice_date'],f['journal'],'Kildeoppgitte årsaksmerkelapper: '+ '; '.join(f['reasons']))
        if event=='changed':info+=tuple(d[:900] for d in details[1:])
        if row['link_kind']=='dataset':info+=('Ingen DOI eller PubMed-ID oppgitt; lenken viser datasettet.',)
        elif row['link_kind'].startswith('original'):info+=('Lenken viser den opprinnelige publikasjonen.',)
        return replace(item,alert_details=info+('Oppdateringstypene er forskjellige kildeopplysninger. Årsaksmerkelapper er ikke en selvstendig konklusjon om forfatterne.',))
