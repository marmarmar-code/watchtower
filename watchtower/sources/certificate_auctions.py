"""Published central-bank certificate auction results and revised stated amounts."""
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import re
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError

ORIGIN='https://www.norges-bank.no'
PATH='/tema/markeder-likviditet/Markedsoperasjoner/sentralbanksertifikater/auksjonsresultater/'
URL=ORIGIN+PATH
API=ORIGIN+'/api/NewsList/LoadMoreAndFilter'
MONTHS={name:i+1 for i,name in enumerate(['januar','februar','mars','april','mai','juni','juli','august','september','oktober','november','desember'])}
DETAIL_LABELS=['ISIN:','Forfallsdato:','Tildelingskurs:','Tildelingsrente:','Tildelt volum:','Totalt budvolum:','Tildeling på marginalrente:']


def text(node): return ' '.join(node.get_text(' ',strip=True).split())


def day(value):
    m=re.fullmatch(r'(\d{1,2})\. ([a-z]+) (\d{4})',value)
    try:
        if not m: raise ValueError()
        return datetime(int(m[3]),MONTHS[m[2]],int(m[1])).date().isoformat()
    except (ValueError,KeyError) as exc: raise SourceError('Certificate auction calendar date is invalid') from exc


def amount(value,unit='',signed=False):
    suffix=' '+unit if unit else ''
    if suffix:
        if not value.endswith(suffix): raise SourceError('Certificate auction numeric unit changed')
        value=value[:-len(suffix)]
    pattern=(r'-?' if signed else '')+r'(?:\d{1,3}(?: \d{3})+|\d{1,15})(?:,\d{1,8})?'
    if not re.fullmatch(pattern,value) or len(value)>30: raise SourceError('Certificate auction numeric format changed')
    result=Decimal(value.replace(' ','').replace(',','.'))
    return format(result,'f')


class CertificateAuctionsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(URL,)): raise ValueError('certificate_auctions accepts only the official certificate result index')
        if self.complete or 'removed' in self.events: raise ValueError('Index absence cannot establish cancellation or reversal')
        self.max_pages=integer(config.options.get('max_pages',20),'max_pages',1,50)
        self.max_results=integer(config.options.get('max_results',150),'max_results',1,500)
        self.field_labels={'auction_date':'Auksjonsdato','settlement_date':'Oppgjørsdato','maturity_date':'Forfallsdato',
            'allocation_price':'Tildelingskurs','allocation_yield_percent':'Tildelingsrente (%)',
            'allocated_mnok':'Tildelt volum (MNOK)','bid_mnok':'Totalt budvolum (MNOK)',
            'marginal_allocation_percent':'Tildeling på marginalrente (%)','published_local':'Oppført publiseringstid',**self.field_labels}

    def _page(self,url,full=False):
        r=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code!=200: raise SourceError('Certificate auction request redirected unexpectedly')
            chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes: raise SourceError('Certificate auction response exceeds max_bytes')
                chunks.append(chunk)
            raw=b''.join(chunks)
        finally:r.close()
        if full and not raw.rstrip().lower().endswith(b'</html>'): raise SourceError('Certificate auction document is incomplete')
        if not full and not raw.rstrip().lower().endswith((b'</div>',b'</article>',b'</button>')):
            raise SourceError('Certificate auction index fragment is incomplete')
        try:return BeautifulSoup(raw.decode('utf-8'),'html.parser')
        except UnicodeDecodeError as exc:raise SourceError('Certificate auction encoding changed') from exc

    def _index(self):
        soup=self._page(URL,True)
        meta=soup.select('meta[property="og:url"]');ids=soup.select('input#newsListCurrentPageId');langs=soup.select('input#newsListLanguage')
        if len(meta)!=1 or meta[0].get('content')!=URL or len(ids)!=1 or not re.fullmatch(r'\d{1,9}',ids[0].get('value','')) or len(langs)!=1 or langs[0].get('value')!='no':
            raise SourceError('Certificate auction index identity changed')
        page_id=ids[0]['value']
        def fetch(page,year):
            return self._page(API+'?'+urlencode({'currentPageId':page_id,'page':page,'clickedCategoryFilter':0,'clickedYearFilter':year,'language':'no','tab':'newslist'}))
        initial=fetch(1,0);select=initial.select('select#Resources_SelectedYear')
        if len(select)!=1: raise SourceError('Certificate auction year selector is missing')
        options=select[0].find_all('option',recursive=False);years=[]
        for option in options:
            value=option.get('value','')
            if value=='0':continue
            if not re.fullmatch(r'20\d{2}',value) or text(option)!=value: raise SourceError('Certificate auction year option changed')
            years.append(int(value))
        if not years or len(set(years))!=len(years): raise SourceError('Certificate auction year list is empty or duplicated')
        year=max(years);all_rows=[];seen=set()
        for page in range(1,self.max_pages+1):
            soup=fetch(page,year)
            if page==1:
                selected=soup.select('select#Resources_SelectedYear option[selected]')
                headings=soup.select('h2.article-list__heading')
                if len(selected)!=1 or selected[0].get('value')!=str(year) or len(headings)!=1 or text(headings[0])!='Auksjonsresultater og innbydelser':
                    raise SourceError('Certificate auction year filter was not applied')
            articles=soup.select('article.article-list__item')
            buttons=soup.select('button._jsNewsListLoadMore_newslist')
            if not articles or len(articles)>20 or len(buttons)>1: raise SourceError('Certificate auction page count is invalid')
            if buttons and (len(articles)!=20 or buttons[0].get('data-currentloaded')!=str(page)):
                raise SourceError('Certificate auction pagination marker is inconsistent')
            for article in articles:
                anchors=article.select('h3.article-list__item-heading a[href]');dates=article.select('.article-list__meta .meta')
                if len(anchors)!=1 or len(dates)!=1: raise SourceError('Certificate auction index entry structure changed')
                title=text(anchors[0]);url=urljoin(URL,anchors[0]['href']);parsed=urlsplit(url)
                if parsed.scheme!='https' or parsed.netloc!='www.norges-bank.no' or parsed.query or parsed.fragment or not re.fullmatch(re.escape(PATH)+str(year)+r'/[a-z0-9-]+/',parsed.path) or url in seen:
                    raise SourceError('Certificate auction result URL is invalid or duplicated')
                seen.add(url);stamp=text(dates[0])
                if not stamp.startswith('Publisert: '): raise SourceError('Certificate auction index publication label changed')
                publication=day(stamp.removeprefix('Publisert: '))
                if not publication.startswith(str(year)+'-'): raise SourceError('Certificate auction index returned a different year')
                if title.startswith('Auksjonsresultater i '):result=True
                elif title.startswith('Auksjoner i '):result=False
                else: raise SourceError('Certificate auction index entry type changed')
                all_rows.append({'url':url,'title':title,'publication_date':publication,'result':result})
            if not buttons:break
        else:raise SourceError('Certificate auction index exceeds max_pages before its end')
        selected=[r for r in all_rows if r['result']]
        if not selected or len(selected)>self.max_results: raise SourceError('Certificate result count is empty or excessive')
        return year,sorted(all_rows,key=lambda r:r['url']),selected

    def _detail(self,index):
        soup=self._page(index['url'],True);meta=soup.select('meta[property="og:url"]');title=soup.select('meta[property="og:title"]');stamps=soup.select('meta[property="article:published_time"]')
        if len(meta)!=1 or meta[0].get('content')!=index['url'] or len(title)!=1 or title[0].get('content')!=index['title'] or len(stamps)!=1:
            raise SourceError('Certificate auction detail identity changed')
        stamp=stamps[0].get('content','')
        try:
            if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}',stamp):raise ValueError()
            published=datetime.strptime(stamp,'%d.%m.%Y %H:%M:%S')
        except ValueError as exc:raise SourceError('Certificate auction publication clock is invalid') from exc
        if published.date().isoformat()!=index['publication_date']:raise SourceError('Certificate auction list and detail publication dates disagree')
        tables=soup.select('.article__main-body table')
        if len(tables)!=1:raise SourceError('Certificate auction result table is missing or ambiguous')
        rows=[]
        for tr in tables[0].find_all('tr'):
            cells=tr.find_all('td',recursive=False)
            if len(cells)==1 and not text(cells[0]):continue
            if len(cells)!=2 or any(c.has_attr('colspan') or c.has_attr('rowspan') for c in cells):raise SourceError('Certificate result table row changed')
            rows.append([text(c) for c in cells])
        if len(rows)<9 or [r[0] for r in rows[:2]]!=['Auksjonsdato:','Oppgjørsdato:'] or (len(rows)-2)%7:
            raise SourceError('Certificate result table labels or groups changed')
        auction,settlement=day(rows[0][1]),day(rows[1][1]);records=[];seen=set()
        for offset in range(2,len(rows),7):
            group=rows[offset:offset+7]
            if [r[0] for r in group]!=DETAIL_LABELS:raise SourceError('Certificate instrument labels changed')
            isin,maturity,price,yield_,allocated,bids,marginal=[r[1] for r in group]
            if not re.fullmatch(r'NO\d{10}',isin) or isin in seen:raise SourceError('Certificate instrument identity is invalid or duplicated')
            seen.add(isin);maturity=day(maturity)
            if not auction<=settlement<maturity:raise SourceError('Certificate auction date ordering is inconsistent')
            fields={'isin':isin,'auction_date':auction,'settlement_date':settlement,'maturity_date':maturity,
                    'allocation_price':amount(price),'allocation_yield_percent':amount(yield_,'%',True),
                    'allocated_mnok':amount(allocated,'MNOK'),'bid_mnok':amount(bids,'MNOK'),
                    'marginal_allocation_percent':amount(marginal,'%'),'published_local':stamp}
            if not Decimal('0')<=Decimal(fields['marginal_allocation_percent'])<=Decimal('100'):
                raise SourceError('Certificate marginal allocation percentage is outside its range')
            records.append({'key':auction+':'+isin,'title':'Sentralbanksertifikat '+isin,'url':index['url'],'published':None,'fields':fields})
        title_isins=re.findall(r'NO\d{10}',index['title'])
        if len(title_isins)!=len(set(title_isins)) or set(title_isins)!=seen:raise SourceError('Certificate heading and instrument identities disagree')
        return records

    def _sweep(self):
        year,index,selected=self._index();records=[]
        for row in selected:
            records.extend(self._detail(row))
            if len(records)>self.max_records:raise SourceError('Certificate result instruments exceed max_records')
        if len({r['key'] for r in records})!=len(records):raise SourceError('Certificate auction identity repeats across documents')
        return year,index,sorted(records,key=lambda r:r['key'])

    def read_records(self):
        first,second=self._sweep(),self._sweep()
        if first!=second:raise SourceError('Certificate auction index or results changed between complete reads')
        self._year=first[0]
        return first[2]

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous);old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope and old.get('year',0)>self._year:raise SourceError('Certificate auction year regressed; prior state preserved')
        self._next['year']=self._year
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields']
        info=('Nyobservert auksjonsresultat' if event=='added' else 'Endret publisert auksjonsresultat',
            'Sentralbanksertifikat '+f['isin'],f"Auksjon {f['auction_date']} · oppgjør {f['settlement_date']} · forfall {f['maturity_date']}",
            f"Kurs {f['allocation_price']} · rente {f['allocation_yield_percent']} %",
            f"Tildelt {f['allocated_mnok']} MNOK · budvolum {f['bid_mnok']} MNOK",
            f"Tildeling på marginalrente: {f['marginal_allocation_percent']} %")
        if event=='changed':info+=tuple(d[:900] for d in details[1:])
        return replace(item,alert_details=info+('Kildeoppgitte auksjonsresultater. Publiseringstidens tidssone er ikke oppgitt; resultatene er ikke dagens markedspris.',))
