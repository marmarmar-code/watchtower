"""PRAC recommendation actions from bounded, explicitly validated PDF layouts."""
from dataclasses import replace
from collections import Counter
from datetime import date,datetime,timedelta,timezone
import hashlib
import io
import re
from urllib.parse import urljoin,urlparse
from bs4 import BeautifulSoup
from .changes import SnapshotSource,integer
from .common import SourceError

PAGE='https://www.ema.europa.eu/en/human-regulatory-overview/post-authorisation/pharmacovigilance-post-authorisation/signal-management/prac-recommendations-safety-signals'
MONTHS={m:i+1 for i,m in enumerate('January February March April May June July August September October November December'.split())}
CATEGORIES={1:'Produktinformasjon: anbefalt endring',2:'Supplerende informasjon',3:'Andre anbefalinger'}
HEADERS=['INN','Signal (EPITT No)','PRAC Rapporteur','Action for MAH','MAH']
BLUE=(0.,.2,.6);GREY=(.882,.89,.949)


def clean(value):
    if not isinstance(value,str):raise SourceError('PRAC field is not text')
    value=' '.join(value.split())
    if not value or len(value)>20000 or '\ufffd' in value or any(ord(c)<32 for c in value):raise SourceError('PRAC field is empty or invalid')
    return value


def today():return datetime.now(timezone.utc).date()


def day(value):
    m=re.fullmatch(r'(\d{1,2}) ([A-Z][a-z]+) (\d{4})',value)
    try:
        if not m:raise ValueError()
        return date(int(m[3]),MONTHS[m[2]],int(m[1])).isoformat()
    except (ValueError,KeyError) as exc:raise SourceError('PRAC date changed format') from exc


def outer_cells(rects):
    if len(rects)>1000:raise SourceError('PRAC page has too many candidate cells')
    # Word exports repeat cell fill behind each text line. Keep containing cell rectangles.
    return [r for r in rects if not any(o is not r and o['x0']<=r['x0']+.1 and o['x1']>=r['x1']-.1 and o['top']<=r['top']+.1 and o['bottom']>=r['bottom']-.1 and o['width']*o['height']>r['width']*r['height']+1 for o in rects)]


def parse_pdf(raw,doc,max_pages=20):
    import pdfplumber
    if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-1024:]:raise SourceError('PRAC response is not a complete PDF')
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            if not 1<=len(pdf.pages)<=max_pages:raise SourceError('PRAC page count exceeds bounds')
            pages=pdf.pages;result=[];heads=[];category=0;sections=[];bodies=[]
            for index,page in enumerate(pages):
                if not 590<=page.width<=600 or not 835<=page.height<=845 or page.rotation:raise SourceError('PRAC page dimensions or rotation changed')
                if not 50<=len(page.chars)<=30000 or len(page.rects)>5000:raise SourceError('PRAC page lacks bounded digital text')
                text=page.extract_text();bodies.append(text)
                if not text or '\ufffd' in text:raise SourceError('PRAC text is missing or undecodable')
                if not re.search(r'Page '+str(index+1)+r'/'+str(len(pages))+r'\s*$',text) and index>0:raise SourceError('PRAC page sequence is incomplete')
                if doc['reference'] not in text:raise SourceError('PRAC document reference does not match index')
                lines=page.extract_text_lines(return_chars=True);page_sections=[]
                for line in lines:
                    s=line['text'];size=max(c['size'] for c in line['chars'])
                    if re.match(r'[123]\. ',s) and size>=12 and line['x0']<70:
                        n=int(s[0]);expected={1:'1. Recommendations for update of the product information',2:'2. Recommendations for submission of supplementary',3:'3. Other recommendations'}[n]
                        if not s.startswith(expected):raise SourceError('PRAC section title changed')
                        page_sections.append((line['top'],n));sections.append(n)
                    if re.match(r'1\.[0-9]+\. ',s) and size>=11 and line['x0']<70:heads.append((index,line))
                colored=[r for r in page.rects if r['width']>40 and r['height']>8 and r['non_stroking_color'] in (BLUE,GREY)]
                cells=outer_cells(colored);blue=sorted([r for r in cells if r['non_stroking_color']==BLUE],key=lambda r:(r['top'],r['x0']))
                groups=[]
                for r in blue:
                    if not groups or abs(groups[-1][0]['top']-r['top'])>1:groups.append([])
                    groups[-1].append(r)
                for hdr in groups:
                    if len(hdr)!=5:raise SourceError('PRAC table header cell count changed')
                    hdr.sort(key=lambda r:r['x0']);labels=[clean(page.crop((r['x0'],r['top'],r['x1'],r['bottom'])).extract_text()) for r in hdr]
                    if labels!=HEADERS:raise SourceError('PRAC table columns changed')
                    before=[n for y,n in page_sections if y<hdr[0]['top']];cat=before[-1] if before else category
                    if cat not in (2,3):raise SourceError('PRAC table has no recommendation category')
                    first=hdr[0];following=[g[0]['top'] for g in groups if g[0]['top']>first['top']];end=min(following,default=770)
                    row_cells=sorted([r for r in cells if r['non_stroking_color']==GREY and abs(r['x0']-first['x0'])<2 and abs(r['x1']-first['x1'])<2 and first['bottom']-1<=r['top']<end],key=lambda r:r['top'])
                    if not row_cells:raise SourceError('PRAC table has no visible record cells')
                    for r in row_cells:
                        if r['bottom']>end:raise SourceError('PRAC record crosses a section boundary')
                        vals=[clean(page.crop((h['x0']+.8,r['top']+.8,h['x1']-.8,r['bottom']-.8)).extract_text()) for h in hdr]
                        match=re.fullmatch(r'(.+) \(([0-9]{5})\)',vals[1])
                        if not match:raise SourceError('PRAC signal lacks an unambiguous EPITT identifier')
                        result.append({'category':cat,'epitt':match[2],'substance_text':vals[0],'signal_text':match[1],'rapporteur_text':vals[2],'action_text':vals[3],'mah_text':vals[4],'authorisation_procedure':None,'adoption_date':None,'page':index+1})
                if page_sections:category=page_sections[-1][1]
            if sections!=[1,2,3]:raise SourceError('PRAC three-section structure changed')
            cover=clean(bodies[0])
            if 'PRAC recommendations on signals' not in cover or doc['meeting_text'] not in cover:raise SourceError('PRAC PDF meeting differs from the index')
            numbers=[]
            for index,line in heads:
                page=pages[index];matches=[]
                for tab in page.find_tables():
                    vals=tab.extract()
                    if vals and vals[0][0]=='Authorisation procedure' and tab.bbox[1]>line['top']:matches.append(tab)
                if not matches:raise SourceError('PRAC product recommendation has no metadata table')
                tab=min(matches,key=lambda t:t.bbox[1]);vals=tab.extract()
                if len(vals)!=4 or any(len(r)!=2 for r in vals):raise SourceError('PRAC product metadata table changed')
                meta={clean(k):clean(v) for k,v in vals}
                if set(meta)!={'Authorisation procedure','EPITT No','PRAC Rapporteur','Date of adoption'} or not re.fullmatch(r'[0-9]{5}',meta['EPITT No']):raise SourceError('PRAC product identity or metadata changed')
                title_page=page.crop((line['x0'],line['top'],550,tab.bbox[1]-1))
                # Superscript note markers in a heading are not part of a substance name.
                title_page=title_page.filter(lambda o:o['object_type']!='char' or o['size']>=10)
                title=clean(title_page.extract_text());m=re.fullmatch(r'1\.([0-9]+)\. (.+) – (.+)',title)
                if not m:raise SourceError('PRAC product heading changed')
                numbers.append(int(m[1]));body=page.crop((0,tab.bbox[3]+1,page.width,765)).extract_text() or ''
                for later in pages[index+1:]:
                    if 'Summary of product characteristics' in body:break
                    body+='\n'+(later.crop((0,0,later.width,765)).extract_text() or '')
                    if len(body)>30000:raise SourceError('PRAC recommendation introduction exceeds bounds')
                if 'Summary of product characteristics' not in body:raise SourceError('PRAC proposed wording boundary is absent')
                body=body.split('Summary of product characteristics')[0];starts=list(re.finditer(r'^Recommendation(?: |$)',body,re.M))
                if len(starts)!=1:raise SourceError('PRAC recommendation introduction is absent or ambiguous')
                action=clean(body[starts[0].end():]);adopted=day(meta['Date of adoption'])
                if not doc['meeting_start']<=adopted<=doc['meeting_end']:raise SourceError('PRAC adoption date leaves the meeting range')
                result.append({'category':1,'epitt':meta['EPITT No'],'substance_text':m[2],'signal_text':m[3],'rapporteur_text':meta['PRAC Rapporteur'],'action_text':action,'mah_text':None,'authorisation_procedure':meta['Authorisation procedure'],'adoption_date':adopted,'page':index+1})
            if numbers!=list(range(1,len(heads)+1)) or not heads:raise SourceError('PRAC product recommendation numbering is incomplete')
            visible_epitt=Counter(re.findall(r'\(([0-9]{5})\)', '\n'.join(bodies)))
            visible_epitt.update(re.findall(r'EPITT No ([0-9]{5})(?:\s|$)', '\n'.join(bodies)))
            if visible_epitt!=Counter(r['epitt'] for r in result):raise SourceError('PRAC visible signal count differs from extracted records')
            keys=[(r['category'],r['epitt']) for r in result]
            if len(keys)!=len(set(keys)) or not result:raise SourceError('PRAC recommendation identity is repeated or missing')
            return sorted(result,key=lambda r:(r['category'],r['epitt']))
    except SourceError:raise
    except Exception as exc:raise SourceError('PRAC PDF could not be read with the verified layout') from exc


class PracSignalsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE,)):raise ValueError('prac_signals accepts only the official EMA index')
        if self.complete or 'removed' in self.events:raise ValueError('A rolling recommendation window cannot establish withdrawal')
        self.from_year=integer(config.options.get('from_year',2026),'from_year',2026,2100)
        self.max_documents=integer(config.options.get('max_documents',2),'max_documents',1,6)
        self.max_pages=integer(config.options.get('max_pdf_pages',20),'max_pdf_pages',1,40)
        self.max_age=integer(config.options.get('max_index_age_days',120),'max_index_age_days',30,365)
        self.field_labels={'action_text':'Kildens anbefalingshandling','substance_text':'Oppgitt virkestofftekst','signal_text':'Oppgitt signal','category':'Anbefalingskategori','mah_text':'Kildens MAH-celle','adoption_date':'Oppgitt vedtaksdato',**self.field_labels}

    def _download(self,url):
        r=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code!=200:raise SourceError('PRAC download redirected unexpectedly')
            parts=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes:raise SourceError('PRAC download exceeds max_bytes')
                parts.append(chunk)
            return b''.join(parts)
        finally:r.close()

    def _index(self,raw):
        soup=BeautifulSoup(raw,'html.parser')
        if [x.get_text(' ',strip=True) for x in soup.select('h1')]!=['PRAC recommendations on safety signals']:raise SourceError('PRAC index heading changed')
        docs=[]
        for card in soup.select('[data-ema-document-type="prac-recommendation"]'):
            title=card.select_one('.file-title')
            if title is None:raise SourceError('PRAC document has no title')
            title=clean(title.get_text(' ',strip=True))
            if not title.startswith('PRAC recommendations on signals adopted'):continue
            years=re.findall(r'\b(20[0-9]{2})\b',title)
            if len(years)!=1:raise SourceError('PRAC meeting year is ambiguous')
            if int(years[0])<self.from_year:continue
            m=re.fullmatch(r'PRAC recommendations on signals adopted at the (\d{1,2})(?: ([A-Z][a-z]+))?\s*-\s*(\d{1,2}) ([A-Z][a-z]+) (\d{4}) PRAC meeting',title)
            if not m:raise SourceError('PRAC meeting date range changed')
            start=day(m[1]+' '+(m[2] or m[4])+' '+m[5]);end=day(m[3]+' '+m[4]+' '+m[5])
            if start>end or date.fromisoformat(end)>today():raise SourceError('PRAC meeting date is invalid')
            docs.append({'card':card,'title':title,'meeting_start':start,'meeting_end':end,'meeting_text':title.split('adopted at the ',1)[1]})
        if not docs or len(docs)>100:raise SourceError('PRAC selected index is empty or too large')
        if len({d['meeting_end'] for d in docs})!=len(docs):raise SourceError('PRAC meeting index is duplicated')
        result=[]
        for doc in sorted(docs,key=lambda d:d['meeting_end'],reverse=True)[:self.max_documents]:
            card=doc.pop('card');links=[a['href'] for a in card.select('a[href]') if a['href'].endswith('_en.pdf')]
            if len(links)!=1:raise SourceError('PRAC English document link is ambiguous')
            url=urljoin(PAGE,links[0]);u=urlparse(url)
            if u.netloc!='www.ema.europa.eu' or u.scheme!='https' or u.query or u.fragment or not re.fullmatch(r'/en/documents/prac-recommendation/prac-recommendations-signals-adopted-[a-z0-9-]+_en.pdf',u.path):raise SourceError('PRAC PDF leaves the official recommendation directory')
            refs=card.select('.file-metadata-row .value');refs=[clean(x.get_text(' ',strip=True)) for x in refs];refs=[r for r in refs if r.startswith('EMA/PRAC/')]
            if len(refs)!=1 or not re.fullmatch(r'EMA/PRAC/[0-9]+/20[0-9]{2}',refs[0]):raise SourceError('PRAC document reference is absent or ambiguous')
            times=card.select('.first-publishedfw-normal time')
            if len(times)!=1:raise SourceError('PRAC first publication date is absent or ambiguous')
            literal=times[0].get_text(strip=True)
            try:published=datetime.strptime(literal,'%d/%m/%Y').date()
            except ValueError as exc:raise SourceError('PRAC publication date changed') from exc
            if times[0].get('datetime','')[:10]!=published.isoformat() or not date.fromisoformat(doc['meeting_end'])<=published<=today():raise SourceError('PRAC publication date is inconsistent')
            doc.update(url=url,reference=refs[0],first_published_date=published.isoformat());result.append(doc)
        if date.fromisoformat(result[0]['first_published_date'])<today()-timedelta(days=self.max_age):raise SourceError('PRAC newest publication is stale')
        return result

    def read_records(self):
        docs=self._index(self._download(PAGE));rows=[];hashes=[]
        for doc in docs:
            raw=self._download(doc['url']);hashes.append(hashlib.sha256(raw).hexdigest())
            for record in parse_pdf(raw,doc,self.max_pages):
                page=record.pop('page');category=record.pop('category');epitt=record.pop('epitt')
                fields={**record,'category':category,'epitt':epitt,'document_reference':doc['reference'],'meeting_start':doc['meeting_start'],'meeting_end':doc['meeting_end'],'first_published_date':doc['first_published_date']}
                key=doc['reference']+'|'+str(category)+'|'+epitt
                rows.append({'key':key,'title':'PRAC '+epitt+': '+record['substance_text']+' – '+record['signal_text'],'url':doc['url']+'#page='+str(page),'published':None,'fields':fields})
                if len(rows)>self.max_records:raise SourceError('PRAC records exceed max_records')
        if docs!=self._index(self._download(PAGE)):raise SourceError('PRAC index changed between complete reads')
        for doc,digest in zip(docs,hashes):
            if hashlib.sha256(self._download(doc['url'])).hexdigest()!=digest:raise SourceError('PRAC PDF changed between complete reads')
        self.documents=[{**d,'sha256':h} for d,h in zip(docs,hashes)]
        return rows

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous);old=((previous or {}).get('source_state') or {}).get('records',{})
        latest=self.documents[0]['meeting_end']
        if old.get('scope')==self.scope and old.get('latest_meeting_end','')>latest:raise SourceError('PRAC latest meeting regressed')
        self._next['latest_meeting_end']=latest
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields'];info=('Nyobservert PRAC-anbefaling' if event=='added' else 'Endret PRAC-anbefaling','EPITT: '+f['epitt'],CATEGORIES[f['category']],'Møteslutt: '+f['meeting_end'],'Først publisert: '+f['first_published_date'])
        if event=='changed':info+=tuple(d[:900] for d in details[1:])
        return replace(item,alert_details=info+('Følger kildens anbefalingshandling. Vedtatt eller gjennomført produktendring er ikke fastslått. Foreslått preparatomtale med strykning og understreking må leses i originaldokumentet.',))
