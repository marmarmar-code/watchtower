"""Published legal announcements from bounded annual public archives."""
import bz2
from dataclasses import replace
from datetime import date, datetime
import io
import json
import re
import tarfile

from bs4 import BeautifulSoup

from .changes import SnapshotSource, digest, integer, strings
from .common import SourceError

PAGE_URL = 'https://api.lovdata.no/v1/publicData/list'
ARCHIVE_BASE = 'https://api.lovdata.no/v1/publicData/get/'
DOCUMENT_BASE = 'https://lovdata.no/dokument/'
ANNUAL = re.compile(r'lovtidend-avd1-(20[0-9]{2})\.tar\.bz2')
DOC_ID = re.compile(r'LTI/(lov|forskrift)/(20[0-9]{2}-[0-9]{2}-[0-9]{2})-([1-9][0-9]{0,6})')
LABELS = {'title':'Tittel','legacyID':'Datokode','dateOfPublication':'Oppført kunngjøringstid',
    'dateInForce':'Oppført ikrafttredelse','ministry':'Departement','subunit':'Avdeling',
    'changesToDocuments':'Endringsreferanser','basedOn':'Hjemmelsreferanser','legalArea':'Rettsområde',
    'eeaReferences':'EØS-referanser','miscInformation':'Andre kildeopplysninger',
    'journalNumber':'Journalnummer','titleShort':'Korttittel','lastupdated':'Oppført sist endret'}
REQUIRED = {'dokid','refid','legacyID','title','dateOfPublication','dateInForce','ministry','journalNumber'}
ALLOWED = set(LABELS) | REQUIRED | {'table-of-contents','a11yStatus'}


def text(node):
    return ' '.join(node.get_text(' ',strip=True).split())


class LawGazetteSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE_URL,)):
            raise ValueError('law_gazette accepts only the official public-data list')
        if self.complete or 'removed' in self.events:
            raise ValueError('Gazette archive absence cannot establish repeal or withdrawal')
        self.latest_years = integer(config.options.get('latest_years',1),'latest_years',1,3)
        self.document_types = strings(config.options.get('document_types',['lov','forskrift']),'document_types')
        if set(self.document_types)-{'lov','forskrift'}:
            raise ValueError('document_types supports lov and forskrift')
        self.max_expanded_bytes = integer(config.options.get('max_expanded_bytes',32000000),'max_expanded_bytes',1024,100000000)
        self.max_document_bytes = integer(config.options.get('max_document_bytes',1000000),'max_document_bytes',1024,4000000)
        self.max_archive_records = integer(config.options.get('max_archive_records',5000),'max_archive_records',1,20000)
        self.field_labels = {**LABELS,'body_fingerprint':'Dokumenttekst og ressursreferanser',**self.field_labels}

    def _read(self,url):
        response = self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code!=200:
                raise SourceError('Gazette source returned an unexpected redirect')
            chunks,size = [],0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size>self.max_bytes:
                    raise SourceError('Gazette response exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def _selection(self,raw):
        try:
            data=json.loads(raw)
        except (ValueError,UnicodeError) as exc:
            raise SourceError('Gazette archive list is malformed') from exc
        if not isinstance(data,list) or not 1<=len(data)<=100:
            raise SourceError('Gazette archive list is empty or excessive')
        names,annual=set(),[]
        for row in data:
            if not isinstance(row,dict) or set(row)!={'filename','description','sizeBytes','lastModified'}:
                raise SourceError('Gazette archive list schema changed')
            name=row['filename']
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}',name) or name in names:
                raise SourceError('Gazette archive filename is invalid or repeated')
            names.add(name)
            match=ANNUAL.fullmatch(name)
            if not match:
                continue
            size=row['sizeBytes'];stamp=row['lastModified'];year=int(match.group(1))
            if (not isinstance(size,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',size)
                    or not isinstance(row['description'],str) or not row['description'].strip()
                    or not isinstance(stamp,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z',stamp)):
                raise SourceError('Gazette annual archive metadata is invalid')
            try:
                datetime.strptime(stamp,'%Y-%m-%dT%H:%M:%SZ')
            except ValueError as exc:
                raise SourceError('Gazette archive timestamp is invalid') from exc
            if year>date.today().year:
                raise SourceError('Gazette annual archive is in the future')
            annual.append((year,name,int(size),stamp))
        annual=sorted(annual,reverse=True)[:self.latest_years]
        if len(annual)!=self.latest_years or any(size>self.max_bytes for _,_,size,_ in annual):
            raise SourceError('Selected annual archives are unavailable or too large')
        return annual

    def _document(self,raw,name,year):
        try:
            document=raw.decode('utf-8-sig')
        except UnicodeError as exc:
            raise SourceError('Gazette document is not valid UTF-8') from exc
        soup=BeautifulSoup(document,'html.parser')
        heads,mains,titles=soup.select('header.documentHeader'),soup.select('main.documentBody'),soup.select('head title')
        if len(heads)!=1 or len(mains)!=1 or len(titles)!=1:
            raise SourceError('Gazette document header, body or title is missing or ambiguous')
        metadata={}
        for node in heads[0].select('dd'):
            classes=node.get('class',[])
            if len(classes)!=1 or classes[0] not in ALLOWED or classes[0] in metadata:
                raise SourceError('Gazette document metadata schema changed')
            metadata[classes[0]]=text(node)
        if not REQUIRED<=set(metadata) or any(not metadata[k] for k in REQUIRED):
            raise SourceError('Gazette required document metadata is absent')
        ident=metadata['dokid'];match=DOC_ID.fullmatch(ident)
        if not match:
            raise SourceError('Gazette document identity is invalid')
        kind,dated,number=match.groups()
        try:
            date.fromisoformat(dated)
            publication=datetime.strptime(metadata['dateOfPublication'],'%Y-%m-%d %H:%M')
        except ValueError as exc:
            raise SourceError('Gazette document date or publication text is invalid') from exc
        if (int(dated[:4])!=year or publication.year!=year
                or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}',metadata['dateOfPublication'])):
            raise SourceError('Gazette document does not belong to the selected year')
        legacy=('LOV' if kind=='lov' else 'FOR')+'-'+dated+'-'+number
        prefix='nl' if kind=='lov' else 'sf'
        filename=re.fullmatch(r'lti/'+str(year)+'/'+prefix+'-'+dated.replace('-','')+r'-([0-9]+)\.xml',name)
        checks={'filename':bool(filename) and int(filename.group(1))==int(number),
                'legacy':metadata['legacyID']==legacy,'reference':metadata['refid']==ident[4:],
                'body':mains[0].get('data-lovdata-url')==ident,'title':text(titles[0])==metadata['title']}
        failed=[field for field,ok in checks.items() if not ok]
        if failed:
            raise SourceError('Gazette document identity mismatch: '+ident+' ('+', '.join(failed)+')')
        if any(len(v)>20000 for k,v in metadata.items() if k!='table-of-contents'):
            raise SourceError('Gazette document metadata exceeds bounds')
        body=text(mains[0])
        if not body:
            raise SourceError('Gazette announcement body is empty')
        reference_attributes=('href','src','alt','colspan','rowspan','data-document','data-change-part',
            'data-add-new-part','data-repeal-part','data-move-part','data-lovdata-url',
            'data-gazette-note-date','data-gazette-note-type')
        resources=[(n.name,k,n[k]) for n in mains[0].find_all(True) for k in reference_attributes if n.has_attr(k)]
        fields={k:metadata.get(k) for k in LABELS}
        fields['body_fingerprint']=digest({'text':body,'resources':resources})
        return {'key':ident,'title':metadata['title'],'url':DOCUMENT_BASE+ident,'published':None,
                'archive_year':year,'document_type':kind,'fields':fields}

    def _archive(self,raw,year):
        try:
            decompressor=bz2.BZ2Decompressor()
            expanded=decompressor.decompress(raw,max_length=self.max_expanded_bytes+1)
            if len(expanded)>self.max_expanded_bytes or not decompressor.eof or decompressor.unused_data:
                raise SourceError('Gazette compressed archive is excessive, truncated or concatenated')
            if len(expanded)%512 or len(expanded)<1024 or expanded[-1024:]!=b'\0'*1024:
                raise SourceError('Gazette tar archive lacks complete framing')
            records,names,ids=[],set(),set();end=0
            with tarfile.open(fileobj=io.BytesIO(expanded),mode='r:') as archive:
                for member in archive:
                    if member.offset!=end or member.offset_data!=member.offset+512:
                        raise SourceError('Gazette archive uses unsupported extended headers')
                    end=member.offset_data+((member.size+511)//512)*512
                    if member.name in names or len(names)>=self.max_archive_records+2:
                        raise SourceError('Gazette archive repeats a path or exceeds member bounds')
                    names.add(member.name)
                    if member.isdir():
                        if member.name not in {'lti',f'lti/{year}'} or member.size:
                            raise SourceError('Gazette archive directory is unexpected')
                        continue
                    if (not member.isfile() or not re.fullmatch(r'lti/'+str(year)+r'/(?:nl|sf)-[0-9]{8}-[0-9]{1,7}\.xml',member.name)
                            or not 1<=member.size<=self.max_document_bytes):
                        raise SourceError('Gazette archive member type, path or size is invalid')
                    content=archive.extractfile(member)
                    if content is None:
                        raise SourceError('Gazette archive member is unreadable')
                    with content:
                        data=content.read(self.max_document_bytes+1)
                    if len(data)!=member.size:
                        raise SourceError('Gazette archive member is truncated')
                    row=self._document(data,member.name,year)
                    if row['key'] in ids or len(records)>=self.max_archive_records:
                        raise SourceError('Gazette document identity repeats or exceeds bounds')
                    ids.add(row['key']);records.append(row)
            if not records or any(expanded[end:]):
                raise SourceError('Gazette archive is empty or has unexpected trailing content')
            return sorted(records,key=lambda r:r['key'])
        except (OSError,EOFError,tarfile.TarError) as exc:
            raise SourceError('Gazette archive is malformed') from exc

    def _sweep(self):
        selected=self._selection(self._read(PAGE_URL));records=[]
        for year,name,size,_ in selected:
            raw=self._read(ARCHIVE_BASE+name)
            if len(raw)!=size:
                raise SourceError('Gazette download size disagrees with its published list')
            records.extend(self._archive(raw,year))
        return selected,sorted(records,key=lambda r:r['key'])

    def read_records(self):
        first,second=self._sweep(),self._sweep()
        if first!=second:
            raise SourceError('Gazette archive selection or documents changed between complete reads')
        rows=[r for r in first[1] if r['document_type'] in self.document_types]
        if not rows or len(rows)>self.max_records:
            raise SourceError('Gazette document selection is empty or exceeds max_records')
        return rows

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        stored=((previous or {}).get('source_state') or {}).get('records',{})
        if stored.get('scope')==self.scope and stored.get('rows'):
            before=max(v['row']['archive_year'] for v in stored['rows'].values())
            after=max(v['row']['archive_year'] for v in self._next['rows'].values())
            if after<before:
                raise SourceError('Gazette latest selected year regressed; previous state preserved')
        return items

    def describe_change(self,name,before,after):
        if name=='body_fingerprint':
            return 'Tekst eller ressursreferanser i kunngjøringen er endret'
        return super().describe_change(name,before,after)

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields']
        info=('Nyobservert kunngjøring' if event=='added' else 'Endret kunngjøring',f['legacyID'],
              'Oppført kunngjøringstid: '+f['dateOfPublication'],
              'Oppført ikrafttredelse: '+f['dateInForce'])
        if event=='changed':info+=tuple(d[:800] for d in details[1:])
        return replace(item,alert_details=info+('Kildeopplysninger; ingen selvstendig tolkning av juridisk virkning',
            'Bildereferanser følges, men bildefiler og vedlegg er ikke innlest',))
