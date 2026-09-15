"""Reported annual pollutant quantities from explicitly selected factsheets."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError

ORIGIN = 'https://www.norskeutslipp.no'
PATH = '/no/Diverse/Virksomhet/'
PATHS = {PATH, '/Templates/NorskeUtslipp/Pages/company.aspx'}
PREFIX = 'ctl00$ContentArea$CompanyTabs$TabContainer1$TabPanelUtslipp$emissionTabEmission$'
MENU = PREFIX+'ListEmissionComponentsRightArea$'
MARKERS = {'(I.T.)':'Ikke tilgjengelig', '(I.R.)':'Ikke rapportert'}


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def factsheet(url):
    p = urlparse(url)
    query = parse_qs(p.query, keep_blank_values=True)
    if (p.scheme != 'https' or p.netloc != 'www.norskeutslipp.no' or p.path not in PATHS or p.fragment or p.params
            or 'CompanyID' not in query or not set(query) <= {'CompanyID','ComponentPageID'}
            or any(len(v)!=1 or not re.fullmatch(r'[1-9][0-9]{0,8}',v[0]) for v in query.values())):
        raise ValueError('emission_values requires an official factsheet with CompanyID and optional ComponentPageID')
    return ORIGIN+p.path+'?'+urlencode({k:query[k][0] for k in ('CompanyID','ComponentPageID') if k in query}), query['CompanyID'][0]


def quantity(raw):
    if raw in MARKERS:
        return None, MARKERS[raw]
    if not re.fullmatch(r'-?(?:[0-9]+|[1-9][0-9]{0,2}(?: [0-9]{3})+)(?:,[0-9]{1,8})?', raw) or len(raw)>40:
        raise SourceError('Emission quantity is neither a supported number nor a documented marker')
    number = Decimal(raw.replace(' ','').replace(',','.'))
    if number == 0:
        return '0', 'Oppgitt tall'
    result = format(number,'f')
    return (result.rstrip('0').rstrip('.') if '.' in result else result), 'Oppgitt tall'


class EmissionValuesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config,*args,**kwargs)
        if self.complete or 'removed' in self.events:
            raise ValueError('Emission factsheet absence cannot establish withdrawn reporting')
        if not 1 <= len(config.urls) <= 10:
            raise ValueError('emission_values requires 1–10 explicit factsheet URLs')
        self.sites = tuple(factsheet(url) for url in config.urls)
        if len({company for _,company in self.sites}) != len(self.sites):
            raise ValueError('Each emission installation must be selected once')
        self.substances = strings(config.options.get('substances'), 'substances')
        if len(self.substances)>10 or any(len(s)>200 or s!=' '.join(s.split()) for s in self.substances):
            raise ValueError('Select up to 10 exact normalized substance labels')
        self.max_years = integer(config.options.get('max_years',50),'max_years',1,100)
        self.field_labels = {'installation':'Anlegg','substance':'Stoff','year':'Oppført år','medium':'Medium',
            'quantity':'Oppgitt mengde','unit':'Kildeoppgitt enhet','availability':'Tilgjengelighet',
            'references':'Cellens fotnotemerker', **self.field_labels}

    def _read(self, url, data=None):
        method = self.get if data is None else self.post
        kwargs = {} if data is None else {'data':data}
        response = method(url,**kwargs,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200:
                raise SourceError('Emission factsheet returned an unexpected redirect')
            chunks, size = [],0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Emission factsheet exceeds max_bytes')
                chunks.append(chunk)
            soup = BeautifulSoup(b''.join(chunks),'html.parser')
        finally:
            response.close()
        forms = soup.select('form')
        if len(forms)!=1 or forms[0].get('method','').lower()!='post':
            raise SourceError('Emission factsheet form is missing or ambiguous')
        try:
            action,_ = factsheet(urljoin(url,forms[0].get('action','')))
        except ValueError as exc:
            raise SourceError('Emission factsheet form identity changed') from exc
        if action != url:
            raise SourceError('Emission factsheet form points at another selection')
        return soup

    def _heading(self,soup):
        headings = [h for h in soup.select('h2') if text(h).startswith('Utslipp av ')]
        if len(headings)!=1:
            raise SourceError('Emission substance heading is absent or ambiguous')
        match = re.fullmatch(r'Utslipp av (.+) \(i (.+ per år)\)',text(headings[0]))
        if not match or len(match.group(1))>200 or len(match.group(2))>100:
            raise SourceError('Emission substance or annual unit is invalid')
        return match.groups()

    def _postback(self,soup,url,link):
        match = re.fullmatch(r"javascript:__doPostBack\('([^']+)',''\)",link.get('href',''))
        allowed = re.escape(MENU)+r'(?:rptEmission\$ctl[0-9]{2}\$LinkButtonEmission|showAllComponents)'
        if not match or not re.fullmatch(allowed,match.group(1)):
            raise SourceError('Emission substance selection has an unexpected action')
        form = soup.select_one('form'); data = {}
        for node in form.select('input[name],select[name]'):
            name = node['name']
            if node.name == 'select':
                options = node.select('option')
                selected = [o for o in options if o.has_attr('selected')]
                if not options or len(selected)>1 or node.has_attr('multiple'):
                    raise SourceError('Emission selection form has an unsupported select')
                val = (selected or options[:1])[0].get('value')
            else:
                kind = node.get('type','text').lower()
                if kind in {'checkbox','radio'}:
                    if not node.has_attr('checked'):
                        continue
                    val = node.get('value','on')
                elif kind in {'hidden','text'}:
                    val = node.get('value','')
                else:
                    continue
            if name in data or not isinstance(val,str):
                raise SourceError('Emission selection form repeats or lacks a field')
            data[name] = val
        if '__VIEWSTATE' not in data or len(data)>100 or sum(len(k)+len(v) for k,v in data.items())>self.max_bytes:
            raise SourceError('Emission selection state is missing or excessive')
        data.update(__EVENTTARGET=match.group(1),__EVENTARGUMENT='')
        return self._read(url,data)

    def _choose(self,soup,url,substance):
        if self._heading(soup)[0] == substance:
            return soup
        for expand in (True,False):
            links = [a for a in soup.select('a[href]') if a.get('id','').startswith(MENU.replace('$','_'))]
            selected = [a for a in links if text(a)==substance]
            if len(selected)==1:
                soup = self._postback(soup,url,selected[0])
                if self._heading(soup)[0] != substance:
                    raise SourceError('Emission selected substance was not returned')
                return soup
            if len(selected)>1:
                raise SourceError('Emission substance selection is ambiguous')
            more = [a for a in links if text(a)=='Flere stoffer']
            if expand and len(more)==1:
                soup = self._postback(soup,url,more[0])
            else:
                break
        raise SourceError('An explicitly selected emission substance is unavailable')

    def _records(self,soup,url,company,substance):
        actual,unit = self._heading(soup)
        if actual != substance:
            raise SourceError('Emission substance identity changed')
        names = [' '.join(' '.join(str(v) for v in h.find_all(string=True,recursive=False)).split()) for h in soup.select('h1')]
        names = [name for name in names if name]
        if len(names)!=1 or len(names[0])>300:
            raise SourceError('Emission installation name is missing or ambiguous')
        page_text = text(soup)
        if any(f'{marker} = {meaning}' not in page_text for marker,meaning in MARKERS.items()):
            raise SourceError('Emission missing-value legend changed')
        selected_years = []
        for suffix in ('from','to'):
            selects = soup.find_all('select',attrs={'name':PREFIX+suffix})
            if len(selects)!=1:
                raise SourceError('Emission selected year range is missing')
            options = selects[0].select('option[selected]')
            if len(options)!=1 or not re.fullmatch(r'[0-9]{4}',options[0].get('value','')):
                raise SourceError('Emission selected year range is invalid')
            selected_years.append(int(options[0]['value']))
        first,last = selected_years
        if not 1900<=first<=last<=date.today().year or last-first+1>self.max_years:
            raise SourceError('Emission year range exceeds bounds')
        tables = [t for t in soup.select('table') if t.find('thead') and 'luft' in text(t.find('thead')) and 'vann' in text(t.find('thead'))]
        if len(tables)!=1:
            raise SourceError('Emission quantity table is missing or ambiguous')
        table = tables[0]
        headers = [text(td) for td in table.select('thead td,thead th')]
        if headers != ['År','Til luft','Til vann'] or len(table.select('tbody'))!=1:
            raise SourceError('Emission quantity table headers changed')
        years,records = set(),[]
        for tr in table.select('tbody tr'):
            cells = tr.find_all('td',recursive=False)
            if len(cells)!=3 or not re.fullmatch(r'[0-9]{4}',text(cells[0])):
                raise SourceError('Emission year row is incomplete')
            year = int(text(cells[0]))
            if year in years or not first<=year<=last:
                raise SourceError('Emission year is duplicated or outside the selected range')
            years.add(year)
            for medium,cell in zip(('luft','vann'),cells[1:]):
                copy = deepcopy(cell); refs=[]
                for ref in copy.select('span.reference'):
                    if text(ref):refs.append(text(ref))
                    ref.decompose()
                amount,availability = quantity(text(copy))
                fields = {'installation':names[0],'substance':substance,'year':year,'medium':medium,
                    'quantity':amount,'availability':availability,'unit':unit,'references':sorted(refs)}
                records.append({'key':canonical([company,substance,year,medium]),
                    'title':f'{names[0]} · {substance} · {year} · {medium}', 'url':url,'published':None,'fields':fields})
        if years != set(range(first,last+1)):
            raise SourceError('Emission annual table is incomplete')
        return records

    def _sweep(self):
        rows={}
        for url,company in self.sites:
            # This public form stores substance/year selections in its own
            # anonymous session. Start each factsheet from its URL defaults.
            self.session.cookies.clear()
            soup=self._read(url)
            for substance in self.substances:
                soup=self._choose(soup,url,substance)
                for row in self._records(soup,url,company,substance):
                    if row['key'] in rows:
                        raise SourceError('Emission records have repeated identities')
                    rows[row['key']]=row
                    if len(rows)>self.max_records:
                        raise SourceError('Emission selection exceeds max_records')
        return rows

    def read_records(self):
        rows=self._sweep()
        if self._sweep()!=rows:
            raise SourceError('Emission figures changed during reading')
        return [rows[key] for key in sorted(rows)]

    def _item(self,row,event,details,suppress):
        f=row['fields']
        item=super()._item(row,event,details,suppress or (event=='added' and f['quantity'] is None))
        info=('Nyobservert utslippsoppføring' if event=='added' else 'Endret utslippsoppføring',
            f"Stoff: {f['substance']} · år {f['year']} · til {f['medium']}",
            f"Oppgitt mengde: {f['quantity'] if f['quantity'] is not None else f['availability']} · {f['unit']}")
        if event=='changed':info+=tuple(d[:800] for d in details[1:])
        return replace(item,alert_details=info+('Faktaarklenken åpner standardvisningen; velg stoffet under Type.', 'Oppførte årsverdier; ingen publiseringsdato eller brudd på utslippstillatelse utledes.',))
