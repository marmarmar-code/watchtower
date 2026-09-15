"""Dated bankruptcy-opening announcements and separately labelled estate dates."""
from dataclasses import replace
from datetime import datetime, timedelta
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer, strings
from .common import SourceError

ORIGIN='https://w2.brreg.no'
PATH='/kunngjoring/'
SEARCH_URL=ORIGIN+PATH+'kombisok.jsp'
PAGE_URL=ORIGIN+PATH+'index.jsp'
DATE=r'\d{2}\.\d{2}\.\d{4}'


def today():
    return datetime.now(ZoneInfo('Europe/Oslo')).date()


def text(node):
    return ' '.join(node.get_text(' ',strip=True).split())


def bounded(value):
    if not isinstance(value,str) or not value.strip() or len(value)>1000:
        raise SourceError('Bankruptcy announcement text is empty or excessive')
    return ' '.join(value.split())


def day(value):
    if not re.fullmatch(DATE,value):raise SourceError('Bankruptcy announcement date is malformed')
    try:return datetime.strptime(value,'%d.%m.%Y').date()
    except ValueError as exc:raise SourceError('Bankruptcy announcement date is invalid') from exc


def match(pattern,value,label):
    matches=re.findall(pattern,value)
    if len(matches)!=1:raise SourceError('Bankruptcy announcement '+label+' is missing or ambiguous')
    return matches[0]


class BankruptcyNoticesSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE_URL,)):
            raise ValueError('bankruptcy_notices accepts only the official announcement search')
        if self.complete or 'removed' in self.events:
            raise ValueError('Announcement absence does not establish estate closure or withdrawal')
        self.window_days=integer(config.options.get('window_days',3),'window_days',1,7)
        self.max_results=integer(config.options.get('max_results',1000),'max_results',1,4999)
        self.max_details=integer(config.options.get('max_details',100),'max_details',1,500)
        self.orgnrs=strings(config.options['orgnrs'],'orgnrs') if 'orgnrs' in config.options else ()
        if len(self.orgnrs)>100 or any(not re.fullmatch(r'\d{9}',x) for x in self.orgnrs):
            raise ValueError('orgnrs must be up to 100 exact nine-digit organisation numbers')
        self.field_labels={'company':'Foretak','orgnr':'Organisasjonsnummer','announced_date':'Kunngjøringsdato',
            'opening_date':'Konkurs åpnet','opening_basis':'Åpnet etter','court':'Oppført domstol','case_number':'Saksnummer',
            'claim_deadline':'Frist for å melde krav','fristdag':'Fristdagen (eget kildefelt)',
            'meeting_date':'Første skiftesamling – dato','meeting_time':'Første skiftesamling – oppført klokkeslett',
            'meeting_place':'Første skiftesamling – oppført sted','trustee':'Oppført bostyrer',**self.field_labels}

    def _page(self,url):
        response=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code!=200:raise SourceError('Announcement search returned an unexpected redirect')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes:raise SourceError('Announcement response exceeds max_bytes')
                chunks.append(chunk)
            raw=b''.join(chunks)
        finally:response.close()
        # Observed response bytes are Latin-1 despite the shared header's UTF-8 meta tag.
        if not raw.rstrip().lower().endswith(b'</html>'):
            raise SourceError('Announcement response is incomplete')
        soup=BeautifulSoup(raw.decode('iso-8859-1'),'html.parser')
        if not soup.title or not text(soup.title).endswith('Brønnøysundregistrene'):
            raise SourceError('Announcement page identity changed')
        return soup

    def _search(self,start,end):
        fmt=lambda d:d.strftime('%d.%m.%Y')
        soup=self._page(SEARCH_URL+'?'+urlencode({'datoFra':fmt(start),'datoTil':fmt(end),'id_niva1':'51'}))
        echoes={};counts=[]
        for tr in soup.find_all('tr'):
            cells=tr.find_all('td',recursive=False)
            if not cells:continue
            label=text(cells[0])
            if label in {'Dato','Sted','Kunngjøringstype'} and len(cells)>1:
                # Header rows contain several headings; only the form-echo values match.
                value=text(cells[-1])
                if label=='Dato' and ' til ' not in value:continue
                if label in echoes:raise SourceError('Announcement search repeats filter metadata')
                echoes[label]=value
            if label=='Antall treff':
                if len(cells)!=2 or not re.fullmatch(r'\d+',text(cells[1])):
                    raise SourceError('Announcement result count is malformed')
                counts.append(int(text(cells[1])))
        if echoes!={'Dato':fmt(start)+' til '+fmt(end),'Sted':'Hele landet','Kunngjøringstype':'Konkurs/tvangsavvikling'}:
            raise SourceError('Announcement date or type filter was not applied')
        records=[];seen=set();blank=0
        for anchor in soup.find_all('a',href=True):
            if 'hent_en.jsp' not in anchor['href']:continue
            parsed=urlparse(urljoin(ORIGIN+PATH,anchor['href']));q=parse_qs(parsed.query,keep_blank_values=True)
            if (parsed.scheme!='https' or parsed.netloc!='w2.brreg.no' or parsed.path!=PATH+'hent_en.jsp'
                    or parsed.fragment or set(q)!={'kid','sokeverdi','spraak'} or any(len(v)!=1 for v in q.values()) or q['spraak']!=['nb']):
                raise SourceError('Announcement detail link contract changed')
            tr=anchor.find_parent('tr');cells=tr.find_all('td',recursive=False) if tr else []
            if len(cells)!=9 or anchor not in cells[7].find_all('a'):
                raise SourceError('Announcement result row structure changed')
            if q['kid']==[''] and q['sokeverdi']==[''] and all(not text(c) for c in cells):
                blank+=1;continue
            kid,org=q['kid'][0],q['sokeverdi'][0]
            if not re.fullmatch(r'\d{14}',kid) or not re.fullmatch(r'(?:\d{6}|\d{9})',org) or kid in seen:
                raise SourceError('Announcement identity is invalid or duplicated')
            seen.add(kid)
            if text(cells[3]).replace(' ','')!=org:raise SourceError('Announcement organisation identity disagrees')
            date=day(text(cells[5]));kind=bounded(text(anchor));name=bounded(text(cells[1]))
            if not start<=date<=end:raise SourceError('Announcement falls outside requested dates')
            url=ORIGIN+PATH+'hent_en.jsp?'+urlencode({'kid':kid,'sokeverdi':org,'spraak':'nb'})
            records.append({'kid':kid,'orgnr':org,'date':date.isoformat(),'kind':kind,'index_name':name,'url':url})
        if records:
            if blank or counts!=[len(records)] or len(records)>self.max_results or len(records)>=5000:
                raise SourceError('Announcement count, completeness or result bound is inconsistent')
        elif blank!=1 or counts not in ([],[0]):
            raise SourceError('Empty announcement result lacks the observed empty-row contract')
        return sorted(records,key=lambda x:x['kid'])

    def _detail(self,index):
        soup=self._page(index['url']);headings=[h for h in soup.find_all('h3') if text(h)=='Konkurs - åpning']
        if len(headings)!=1 or headings[0].parent.name!='body':
            raise SourceError('Bankruptcy opening detail identity changed')
        body=headings[0].parent;content=text(body)
        if len(content)>30000:raise SourceError('Bankruptcy detail text is excessive')
        labels={}
        required={'Navn/foretaksnavn:','Organisasjonsnummer:','Konkurs åpnet:','Saksnr:'}
        for tr in body.find_all('tr'):
            cells=tr.find_all('td',recursive=False)
            if len(cells)!=2:raise SourceError('Bankruptcy labelled detail row changed')
            label=text(cells[0]).replace(' :',':')
            if label in required|{'Åpnet etter:'}:
                if label in labels:raise SourceError('Bankruptcy detail repeats a required field')
                labels[label]=bounded(text(cells[1]))
        if not required<=set(labels) or labels['Organisasjonsnummer:'].replace(' ','')!=index['orgnr']:
            raise SourceError('Bankruptcy detail organisation or required fields disagree')
        announcement=day(match(r'Konkursregisteret ('+DATE+r')$',content,'publication date'))
        if announcement.isoformat()!=index['date']:raise SourceError('Announcement list and detail publication dates disagree')
        opening=day(labels['Konkurs åpnet:'])
        claim=day(match(r'Krav i boet meldes .{1,2000}? innen ('+DATE+r') \.',content,'claim deadline'))
        frist=day(match(r'Fristdagen er ('+DATE+r') \.',content,'fristdag'))
        meeting=match(r'Første skiftesamling blir holdt ('+DATE+r') kl\. (\d{2}:\d{2}) i (.{1,1000}?) \. Alle henvendelser',content,'first estate meeting')
        meeting_day=day(meeting[0])
        try:datetime.strptime(meeting[1],'%H:%M')
        except ValueError as exc:raise SourceError('Estate meeting time is invalid') from exc
        court=bounded(match(r'^Konkurs - åpning Ved (.{1,300}?) er det åpnet konkurs i boet til:',content,'court'))
        trustee=bounded(match(r'Alle henvendelser om konkursen rettes til bostyrer (.{1,300}?) \. Konkursregisteret ',content,'trustee'))
        fields={'company':labels['Navn/foretaksnavn:'],'orgnr':index['orgnr'],'announced_date':announcement.isoformat(),
            'opening_date':opening.isoformat(),'opening_basis':labels.get('Åpnet etter:'),'case_number':labels['Saksnr:'],
            'court':court,'claim_deadline':claim.isoformat(),'fristdag':frist.isoformat(),
            'meeting_date':meeting_day.isoformat(),'meeting_time':meeting[1],'meeting_place':bounded(meeting[2]),'trustee':trustee}
        return {'key':index['kid'],'title':fields['company'],'url':index['url'],'published':None,'fields':fields}

    def _sweep(self,start,end):
        index=self._search(start,end)
        selected=[x for x in index if x['kind']=='Konkursåpning' and len(x['orgnr'])==9 and (not self.orgnrs or x['orgnr'] in self.orgnrs)]
        if len(selected)>min(self.max_details,self.max_records):raise SourceError('Bankruptcy selection exceeds the detail bound')
        return index,[self._detail(x) for x in selected]

    def read_records(self):
        end=today();start=end-timedelta(days=self.window_days-1)
        first,second=self._sweep(start,end),self._sweep(start,end)
        if first!=second:raise SourceError('Announcement window changed between complete reads')
        self._window_end=end.isoformat()
        return first[1]

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope and old.get('window_end','')>self._window_end:
            raise SourceError('Announcement window regressed; prior snapshot preserved')
        self._next['window_end']=self._window_end
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields']
        info=('Nyobservert konkursåpning' if event=='added' else 'Endret konkurskunngjøring',
            f"{f['company']} · {f['orgnr']}",f"{f['court']} · sak {f['case_number']}",
            f"Kunngjort {f['announced_date']} · åpnet {f['opening_date']} · {f['opening_basis'] or 'åpningsgrunnlag ikke oppgitt'}",
            f"Frist for å melde krav: {f['claim_deadline']}",f"Fristdagen (eget kildefelt): {f['fristdag']}",
            f"Første skiftesamling: {f['meeting_date']} kl. {f['meeting_time']} · {f['meeting_place']}",
            'Oppført bostyrer: '+f['trustee'])
        if event=='changed':info+=tuple(d[:1000] for d in details[1:])
        return replace(item,alert_details=info+('Datoene er ulike felt i kunngjøringen; kontroller originalen. Fravær fra søkevinduet viser ikke at bobehandlingen er avsluttet.',))
