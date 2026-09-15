"""Annual published group relationships and broadcast record revisions."""
from dataclasses import replace
from datetime import date
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError
from .workbooks import table_rows

PAGE_URL = 'https://www.medietilsynet.no/fakta/mediedatabasen/'
PROFILES = {
    'ownership': ('Datasett_avis', 10, {'Avis':'name','Konsern':'group','Utgiverselskap':'publisher'}),
    'radio_content': ('Datasett_RadioInnholdskonsesjon', 11, {'Konsesjonsnummer':'number','Stasjon':'station',
        'Distribusjonstype':'distribution','Virksomhet':'holder','Radiokategori':'category','Blokk (DAB)':'block',
        'DAB-region, nummer':'dab_region','DAB-region, navn':'dab_name','FM-område, nummer':'fm_region','FM-område, navn':'fm_name'}),
    'radio_transmitters': ('Datasett_RadioSenderanleggkonse', 6, {'Konsesjons-nummer':'number','Virksomhet':'holder',
        'Blokk (DAB)':'block','DAB-region, navn':'dab_name','DAB region, nummer':'dab_region'}),
    'tv_content': ('Datasett_TVInnholdskonsesjon', 6, {'Konsesjonsnummer':'number','Stasjon':'station',
        'Distribusjonstype':'distribution','Geografisk nedslagsfelt':'geography','Virksomhet':'holder'}),
}


def _text(value):
    return ' '.join(value.split())


class MediaDatabaseSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.dataset = config.options.get('dataset')
        if not isinstance(self.dataset,str) or self.dataset not in PROFILES:
            raise ValueError('dataset must select ownership, radio_content, radio_transmitters or tv_content')
        if config.urls not in ((),(PAGE_URL,)):
            raise ValueError('media_database accepts only the official database page')
        if self.complete or 'removed' in self.events:
            raise ValueError('Annual database absence cannot establish withdrawal')
        self.max_unpacked_bytes = integer(config.options.get('max_unpacked_bytes',2000000),'max_unpacked_bytes',1024,10000000)
        self.max_sheet_rows = integer(config.options.get('max_sheet_rows',2000),'max_sheet_rows',2,10000)
        self.field_labels = {'group':'Oppført konsern','publisher':'Utgiverselskap','holder':'Virksomhet','station':'Stasjon',
            'distribution':'Distribusjonstype','category':'Kategori','block':'Blokk','dab_region':'DAB-regionnummer',
            'dab_name':'DAB-region','fm_region':'FM-områdenummer','fm_name':'FM-område','geography':'Geografisk nedslagsfelt',**self.field_labels}

    def _read(self,url):
        response=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200:
                raise SourceError('Media database returned an unexpected redirect')
            chunks,size=[],0
            for chunk in response.iter_content(64*1024):
                size+=len(chunk)
                if size>self.max_bytes:
                    raise SourceError('Media database exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def read_records(self):
        soup=BeautifulSoup(self._read(PAGE_URL),'html.parser')
        titles=soup.select('h1')
        if len(titles)!=1 or _text(titles[0].get_text(' ',strip=True))!='Mediedatabasen':
            raise SourceError('Media database page title changed')
        years=set(re.findall(r'opplysninger gjelder for\s+(20[0-9]{2})',soup.get_text(' ',strip=True),re.I))
        if len(years)!=1:
            raise SourceError('Media database reference year is absent or ambiguous')
        year=int(years.pop())
        if not 2000 <= year <= date.today().year:
            raise SourceError('Media database reference year is invalid')
        links=[urljoin(PAGE_URL,a['href']) for a in soup.select('a[href]') if 'Last ned datagrunnlaget' in a.get_text(' ',strip=True)]
        if len(links)!=1:
            raise SourceError('Media database workbook link is absent or ambiguous')
        parsed=urlparse(links[0])
        if (parsed.scheme!='https' or parsed.netloc!='www.medietilsynet.no' or parsed.query or parsed.fragment or parsed.params
                or not re.fullmatch(r'/globalassets/tema/mediedatabasen/[0-9]{6}_mediedatabasen_alle_data\.xlsx',parsed.path)):
            raise SourceError('Media database workbook is not the expected official attachment')
        name,width,mapping=PROFILES[self.dataset]
        rows=table_rows(self._read(links[0]),name,self.max_unpacked_bytes,self.max_sheet_rows,width)
        headers=[_text(v) for v in rows[0]]
        if len(set(headers))!=len(headers) or any(not h for h in headers) or not set(mapping)<=set(headers):
            raise SourceError('Media database worksheet headers changed')
        positions={field:headers.index(header) for header,field in mapping.items()}
        records,seen=[],set()
        for values in rows[1:]:
            data={field:_text(values[index]) or None for field,index in positions.items()}
            if self.dataset=='ownership':
                if not data['name'] or not data['publisher']:
                    raise SourceError('Media database publication or publisher is missing')
                key='name:'+data.pop('name').casefold()
                title=values[positions['name']].strip()
                number=None
                identity='publication_name'
            else:
                number=data.pop('number')
                if not number or not re.fullmatch(r'(?:0|[1-9][0-9]{0,8})',number) or not data['holder']:
                    raise SourceError('Media database number or holder is invalid')
                if 'station' in data and not data['station']:
                    raise SourceError('Media database station is missing')
                title=data.get('station') or data['holder']
                identity='source_number' if number!='0' else 'name_for_zero_number'
                key='number:'+number if number!='0' else 'zero:'+title.casefold()
            if key in seen:
                raise SourceError('Media database record identity is ambiguous or duplicated')
            seen.add(key)
            records.append({'key':key,'title':title,'url':PAGE_URL,'published':None,'reference_year':year,
                            'source_number':number,'identity_basis':identity,'fields':data})
            if len(records)>self.max_records:
                raise SourceError('Media database exceeds max_records')
        return sorted(records,key=lambda r:r['key'])

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        prior=((previous or {}).get('source_state') or {}).get('records',{})
        if prior.get('scope')==self.scope:
            old_years=[r['row'].get('reference_year',0) for r in prior.get('rows',{}).values()]
            new_years=[r['row']['reference_year'] for r in self._next['rows'].values()]
            if old_years and new_years and max(old_years)>min(new_years):
                raise SourceError('Media database reference year regressed; previous state preserved')
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress)
        label='Nyobservert årsoppføring' if event=='added' else 'Revidert årsoppføring'
        info=(label,f"Opplysningene gjelder {row['reference_year']}")
        if self.dataset=='ownership':
            info+=(f"Utgiverselskap: {row['fields']['publisher']}",f"Oppført konsern: {row['fields']['group'] or 'ikke oppgitt'}")
        else:
            info+=(f"Kildens nummer: {row['source_number']} · Virksomhet: {row['fields']['holder']}",)
            if row['identity_basis']=='name_for_zero_number':
                info+=('Nummerfeltet er 0; raden følges etter navn, ikke et unikt konsesjonsnummer',)
        if event=='changed':
            info+=tuple(d[:800] for d in details[1:])
        info+=('Årlige kildeopplysninger; ingen dato for eierskifte, vedtak eller utløp er utledet',)
        return replace(item,alert_details=info)
