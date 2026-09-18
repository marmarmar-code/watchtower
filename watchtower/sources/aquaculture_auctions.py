"""Published aquaculture capacity-auction allocations and revisions."""
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, document, integer
from .common import SourceError

PAGE = 'https://www.fiskeridir.no/akvakultur/auksjon-av-produksjonskapasitet'


def text(node):
    return ' '.join(node.get_text(' ',strip=True).split())


def quantity(value):
    if not re.fullmatch(r'(?:[0-9]{1,3}(?: [0-9]{3})+|[0-9]{1,13})',value):
        raise SourceError('Aquaculture auction amount changed format')
    return int(value.replace(' ',''))


def soup(raw):
    try:
        content=raw.decode('utf-8') if isinstance(raw,bytes) else raw
    except UnicodeError:
        raise SourceError('Aquaculture auction text encoding changed') from None
    if not content.rstrip().lower().endswith('</html>'):
        raise SourceError('Aquaculture auction HTML is truncated')
    result=BeautifulSoup(content,'html.parser')
    if len(result.select('h1')) != 1:
        raise SourceError('Aquaculture auction page lacks one heading')
    return result


class AquacultureAuctionsSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE,)):
            raise ValueError('aquaculture_auctions accepts only the official auction index')
        if self.complete or 'removed' in self.events:
            raise ValueError('An absent archive entry is not proof that an award was withdrawn')
        self.from_year=integer(config.options.get('from_year',2024),'from_year',2024,2100)
        self.max_auctions=integer(config.options.get('max_auctions',8),'max_auctions',1,20)
        self.field_labels={'company':'Selskap i resultattabellen','capacity_tonnes':'Vunnet kapasitet (tonn MTB)',
                           'consideration_nok':'Vederlag (NOK)','status':'Resultatstatus','auction_year':'Auksjonsår',
                           'auction_period':'Kildens auksjonsperiode',**self.field_labels}

    def _index(self,raw):
        page=soup(raw)
        if text(page.h1) != 'Auksjon av produksjonskapasitet':
            raise SourceError('Aquaculture auction index identity changed')
        found={}
        for a in page.select('main a[href]'):
            href=urljoin(PAGE,a['href'])
            if not href.startswith(PAGE+'/') or '/_/' in href:
                continue
            years=re.findall(r'(?<!\d)(20\d{2})(?!\d)',text(a))
            if len(years)!=1:
                raise SourceError('Auction index entry lacks one explicit year')
            year=int(years[0])
            if year<self.from_year:
                continue
            parsed=urlparse(href)
            if parsed.query or parsed.fragment or not self.from_year <= year <= 2100:
                raise SourceError('Auction index entry has an unsupported URL or year')
            found[href]=year
        if not 1<=len(found)<=self.max_auctions:
            raise SourceError('Selected auction index is empty or exceeds max_auctions')
        return sorted(found.items())

    def _auction(self,url,year,raw):
        page=soup(raw);heading=text(page.h1)
        if 'auksjon' not in heading.lower() or str(year) not in heading:
            raise SourceError('Auction detail heading does not match the selected archive year')
        tables=page.select('main table')
        if not tables:
            # Upcoming rounds are retained explicitly until results are published.
            if page.find(string=re.compile(r'Selskap som kjøpte kapasitet')):
                raise SourceError('Auction claims a result table which is missing')
            return [{'key':url+'#announcement','title':heading,'url':url,'published':None,
                     'fields':{'status':'resultattabell ikke publisert','auction_year':year}}]
        expected=[['Selskap','Vunnet kapasitet','Vederlag'],['','Tonn MTB tildelt','Samlet vederlag (NOK)']]
        if len(tables)!=2 or [[text(h) for h in t.select('thead th')] for t in tables]!=expected:
            raise SourceError('Aquaculture auction result columns changed')
        body=text(page.select_one('main'))
        periods=re.findall(r'Auksjonen ble holdt ([0-9]{1,2}\.(?:-[0-9]{1,2}\.)? [a-zæøå]+ 20\d{2}) og minsteprisen',body)
        if len(periods)!=1 or str(year) not in periods[0]:
            raise SourceError('Aquaculture auction period is missing or ambiguous')
        rows=[]
        for t in tables:
            records=[]
            for tr in t.select('tbody tr'):
                cells=[text(td) for td in tr.find_all('td',recursive=False)]
                if len(cells)!=3 or not cells[0]:
                    raise SourceError('Aquaculture auction result row is incomplete')
                records.append((cells[0],quantity(cells[1]),quantity(cells[2])))
            rows.append(records)
        companies,areas=rows
        if not 2<=len(companies)<=201 or companies[-1][0]!='Sum' or len({r[0] for r in companies})!=len(companies) or not 1<=len(areas)<=13:
            raise SourceError('Aquaculture auction totals or identities changed')
        total=companies.pop()
        company_totals=tuple(sum(r[i] for r in companies) for i in (1,2))
        area_totals=tuple(sum(r[i] for r in areas) for i in (1,2))
        if company_totals != total[1:] or area_totals != company_totals:
            raise SourceError('Auction company, published total and area totals disagree')
        if any(not re.fullmatch(r'(?:[1-9]|1[0-3])\. .+',r[0]) for r in areas) or len({r[0].split('.')[0] for r in areas})!=len(areas):
            raise SourceError('Auction production-area identities changed')
        records=[{'key':url+'#announcement','title':heading,'url':url,'published':None,
                  'fields':{'status':'resultattabell publisert','auction_year':year}}]
        for name,capacity,payment in companies:
            records.append({'key':canonical([url,name]),'title':name+' – oppdrettsauksjon '+str(year),
                'url':url,'published':None,'fields':{'company':name,'auction_year':year,
                  'auction_period':periods[0],'capacity_tonnes':capacity,'consideration_nok':payment}})
        return records

    def _poll(self):
        entries=self._index(document(self,PAGE))
        records=[]
        for url,year in entries:
            records.extend(self._auction(url,year,document(self,url)))
        return sorted(records,key=lambda r:r['key'])

    def read_records(self):
        records=self._poll()
        if self._poll()!=records:
            raise SourceError('Aquaculture auction results changed between complete reads')
        return records

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope:
            for key, current in self._next['rows'].items():
                prior=old.get('rows',{}).get(key,{}).get('row',{}).get('fields',{})
                now=current['row']['fields']
                if prior.get('status')=='resultattabell publisert' and now.get('status')=='resultattabell ikke publisert':
                    raise SourceError('A previously published auction result table disappeared')
            self._next['rows']={**old.get('rows',{}),**self._next['rows']}
            if len(self._next['rows'])>self.max_records*2:
                raise SourceError('Retained auction allocations exceed the history bound')
        return items

    def _item(self,row,event,details,suppress):
        fields=row['fields']
        if 'company' not in fields:
            content=['Auksjonsår: '+str(fields['auction_year']),fields['status']]
            if event=='changed':
                content.extend(details[1:])
        else:
            content=['Nyobservert tildeling' if event=='added' else 'Endret publisert tildeling']
            content.extend(details[1:])
            content.append('Tonn MTB er tillatelseskapasitet, ikke produsert fisk. Vederlag er kildens NOK-beløp; innbetaling er ikke bekreftet. Selskapsnavn er tabellidentitet, ikke organisasjonsnummer.')
        return super()._item(row,event,content,suppress)
