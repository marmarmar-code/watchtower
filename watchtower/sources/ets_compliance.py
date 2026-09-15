"""Published Union Registry compliance codes and independent source totals."""
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import re
from urllib.parse import urljoin, urlparse, parse_qs
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError
from bs4 import BeautifulSoup
from .changes import SnapshotSource, integer, strings
from .common import SourceError
from .workbooks import NS, REL, RID, _xml, _rows

PAGE='https://climate.ec.europa.eu/areas-action/carbon-markets/eu-emissions-trading-system-eu-ets/union-registry_en'
HEADERS=['REGISTRY_CODE','INSTALLATION_NAME','INSTALLATION_IDENTIFIER','PERMIT_IDENTIFIER','MAIN_ACTIVITY_TYPE_CODE','COMPLIANCE_CODE','CH_COMPLIANCE_CODE','COMPLIANCE_STATUS_LATEST_YEAR','TOTAL_VERIFIED_EMISSIONS','CH_TOTAL_VERIFIED_EMISSIONS','TOTAL_SURRENDERED_ALLOWANCES','YEAR_OF_FIRST_EMISSIONS','YEAR_OF_LAST_EMISSIONS','ACCOUNT_CLOSURE']
CODES={'A','B','C','-','EXCLUDED SINCE 2021','NO COMPLIANCE CALCULATION'}


def today():return datetime.now(timezone.utc).date()


def clean(s, optional=False):
    if not isinstance(s,str):raise SourceError('ETS field is not text')
    s=' '.join(s.split())
    if (not s and not optional) or len(s)>4000 or '\ufffd' in s:raise SourceError('ETS field is empty or invalid')
    return s or None


class EtsComplianceSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE,)):raise ValueError('ets_compliance accepts only the official Union Registry index')
        if self.complete or 'removed' in self.events:raise ValueError('Report absence cannot establish loss of permission or compliance')
        self.countries=strings(config.options.get('countries',['NO']),'countries')
        if any(not re.fullmatch('[A-Z]{2}',c) for c in self.countries):raise ValueError('countries must be exact registry codes')
        self.activities=strings(config.options['activity_codes'],'activity_codes') if 'activity_codes' in config.options else ()
        if any(not re.fullmatch('[0-9]{1,2}',x) for x in self.activities):raise ValueError('activity_codes must be exact numeric strings')
        self.max_rows=integer(config.options.get('max_export_rows',25000),'max_export_rows',10,50000)
        self.max_unpacked=integer(config.options.get('max_unpacked_bytes',20000000),'max_unpacked_bytes',10000,50000000)
        self.max_age=integer(config.options.get('max_report_age_days',450),'max_report_age_days',30,730)
        self.field_labels={'compliance_code':'Kildens oppgjørskode','ch_compliance_code':'Kildens CH-oppgjørskode','compliance_status_latest_year':'Kildens siste statusår','total_verified_emissions':'Kildetotal: verifiserte utslipp','ch_total_verified_emissions':'Kildetotal: CH-utslipp','total_surrendered_allowances':'Kildetotal: innleverte kvoter','account_closure':'Kildens kontostatus','report_year':'Rapportår',**self.field_labels}

    def _download(self,url):
        r=self.get(url,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code!=200:raise SourceError('ETS report redirected unexpectedly')
            chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes:raise SourceError('ETS download exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:r.close()

    def _index(self,raw):
        soup=BeautifulSoup(raw,'html.parser')
        if [h.get_text(' ',strip=True) for h in soup.select('h1')]!=['Union Registry']:raise SourceError('ETS index heading changed')
        reports=[]
        for a in soup.select('a[href]'):
            title=' '.join(a.get_text(' ',strip=True).split());m=re.fullmatch(r'Compliance data for (20[0-9]{2})',title)
            if not m or int(m[1])<2021:continue
            url=urljoin(PAGE,a['href']);u=urlparse(url);q=parse_qs(u.query)
            if u.scheme!='https' or u.netloc!='climate.ec.europa.eu' or not re.fullmatch(r'/document/download/[a-f0-9-]{36}_en',u.path) or u.fragment or set(q)!={'filename'} or q['filename']!=['compliance_'+m[1]+'_code_en.xlsx']:
                raise SourceError('ETS compliance download link changed')
            parent=a.find_parent('li');literal=' '.join(parent.get_text(' ',strip=True).split()) if parent else ''
            match=re.fullmatch(r'(\d\d/\d\d/20\d\d) - '+re.escape(title),literal)
            if not match:raise SourceError('ETS report publication date is ambiguous')
            try:published=datetime.strptime(match[1],'%d/%m/%Y').date()
            except ValueError as exc:raise SourceError('ETS report publication date is invalid') from exc
            year=int(m[1])
            if not date(year,1,1)<=published<=today() or year>=today().year:raise SourceError('ETS report dates are inconsistent')
            reports.append({'year':year,'published_date':published.isoformat(),'url':url})
        if not reports or len({r['year'] for r in reports})!=len(reports):raise SourceError('ETS report index is empty or ambiguous')
        selected=max(reports,key=lambda r:r['year'])
        if date.fromisoformat(selected['published_date'])<today()-timedelta(days=self.max_age):raise SourceError('ETS latest compliance report is stale')
        return selected

    def _table(self,raw):
        try:
            with ZipFile(BytesIO(raw)) as z:
                infos=z.infolist()
                if not 1<=len(infos)<=100 or len({i.filename for i in infos})!=len(infos) or sum(i.file_size for i in infos)>self.max_unpacked or any(i.flag_bits&1 or i.filename.startswith('/') or '..' in i.filename.split('/') for i in infos):raise SourceError('ETS workbook exceeds archive bounds')
                wb=_xml(z.read('xl/workbook.xml'));sheets=wb.findall(NS+'sheets/'+NS+'sheet')
                if wb.tag!=NS+'workbook' or [s.get('name') for s in sheets]!=['Export Worksheet','activity codes'] or len({s.get(RID) for s in sheets})!=2:raise SourceError('ETS workbook sheets changed')
                rels=_xml(z.read('xl/_rels/workbook.xml.rels'));links=rels.findall(REL+'Relationship')
                if rels.tag!=REL+'Relationships' or len({r.get('Id') for r in links})!=len(links):raise SourceError('ETS workbook relationships changed')
                root=_xml(z.read('xl/sharedStrings.xml'))
                if root.tag!=NS+'sst':raise SourceError('ETS shared strings changed')
                shared=[''.join(t.text or '' for t in si.iter(NS+'t')) for si in root.findall(NS+'si')]
                tables=[]
                for sheet in sheets:
                    matched=[r for r in links if r.get('Id')==sheet.get(RID)]
                    if len(matched)!=1 or matched[0].get('TargetMode')=='External':raise SourceError('ETS worksheet target changed')
                    target=matched[0].get('Target','');target=target[1:] if target.startswith('/xl/') else 'xl/'+target
                    if not re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml',target):raise SourceError('ETS worksheet target is unsafe')
                    xml=_xml(z.read(target))
                    dimensions=xml.findall(NS+'dimension')
                    end_col='N' if sheet.get('name')=='Export Worksheet' else 'B'
                    dim=re.fullmatch(r'A1:'+end_col+r'([1-9][0-9]*)',dimensions[0].get('ref','')) if len(dimensions)==1 else None
                    source_rows=xml.findall(NS+'sheetData/'+NS+'row')
                    if not dim or not source_rows or source_rows[-1].get('r')!=dim[1]:raise SourceError('ETS worksheet dimension disagrees with final row')
                    if sheet.get('name')=='Export Worksheet':
                        data=xml.find(NS+'sheetData');rows=list(data) if data is not None else []
                        if not rows or rows[0].get('r')!='1' or any(c.find(NS+'f') is not None or c.find(NS+'v') is not None or c.find(NS+'is') is not None for c in rows[0]):raise SourceError('ETS initial blank worksheet row changed')
                        data.remove(rows[0])
                        # The source starts its header on row 2. Offset references in memory only.
                        for row in data:
                            n=row.get('r','')
                            if not n.isdigit() or not 2<=int(n)<=self.max_rows:raise SourceError('ETS row index exceeds bounds')
                            row.set('r',str(int(n)-1))
                            for cell in row:
                                ref=cell.get('r','');m=re.fullmatch(r'([A-Z]+)'+n,ref)
                                if not m:raise SourceError('ETS cell index changed')
                                cell.set('r',m[1]+str(int(n)-1))
                    tables.append(_rows(xml,shared,self.max_rows,14 if sheet.get('name')=='Export Worksheet' else 2))
        except SourceError:raise
        except (BadZipFile,KeyError,ValueError,UnicodeError,RuntimeError,ParseError) as exc:raise SourceError('ETS workbook is invalid') from exc
        rows,activity_rows=tables
        if rows[0]!=HEADERS or activity_rows[0]!=['value','code']:raise SourceError('ETS report column headers changed')
        activities={}
        for text,code in activity_rows[1:]:
            if not re.fullmatch(r'[0-9]{1,2}',code) or code in activities:raise SourceError('ETS activity code is invalid or repeated')
            activities[code]=clean(text)
        return rows[1:],activities

    def _records(self,raw,doc):
        rows,activities=self._table(raw);out=[];seen=set();found=set()
        for r in rows:
            country,name,ident,permit,activity,code,ch,latest,emissions,ch_emissions,surrendered,first,last,closure=r
            if not re.fullmatch('[A-Z]{2}',country) or not re.fullmatch(r'[1-9][0-9]*',ident) or activity not in activities or code not in CODES or ch not in CODES|{'NOT APPLICABLE'}:raise SourceError('ETS identity, activity or status code changed')
            for value,markers in [(emissions,{'NOT CALCULATED'}),(ch_emissions,{'NOT CALCULATED','NOT APPLICABLE'}),(surrendered,set())]:
                if value not in markers and not re.fullmatch(r'[0-9]{1,16}',value):raise SourceError('ETS reported total changed format')
            if not re.fullmatch('20[0-9]{2}',first) or not 2005<=int(first)<=doc['year']+1:raise SourceError('ETS first emission year changed')
            if latest and (not re.fullmatch('20[0-9]{2}',latest) or not 2021<=int(latest)<=doc['year']):raise SourceError('ETS latest compliance year changed')
            # The source includes a future final-emissions year (2030); retain it literally.
            if last!='NOT SET' and (not re.fullmatch('20[0-9]{2}',last) or not 2005<=int(last)<=2099):raise SourceError('ETS final emission year changed')
            if closure!='OPEN':
                try:datetime.strptime(closure,'%d-%b-%Y')
                except ValueError as exc:raise SourceError('ETS account closure format changed') from exc
            name=clean(name,True);permit=clean(permit,True)
            if country not in self.countries or self.activities and activity not in self.activities:continue
            key=str(doc['year'])+'|'+country+'|'+ident
            if key in seen:raise SourceError('ETS selected installation identity repeats with conflicting or duplicate rows')
            seen.add(key);found.add(country)
            fields={h.lower():v for h,v in zip(HEADERS,r)}
            fields.update(installation_name=name,permit_identifier=permit,compliance_status_latest_year=int(latest) if latest else None,report_year=doc['year'],report_published_date=doc['published_date'],activity_text=activities[activity])
            out.append({'key':key,'title':'Kvoteoppgjør: '+(name or ident)+' – '+code,'url':doc['url'],'published':None,'fields':fields})
            if len(out)>self.max_records:raise SourceError('ETS selected records exceed max_records')
        if found!=set(self.countries):raise SourceError('ETS selected registry code has no rows')
        return sorted(out,key=lambda r:r['key'])

    def read_records(self):
        doc=self._index(self._download(PAGE));rows=self._records(self._download(doc['url']),doc)
        if self._index(self._download(PAGE))!=doc or self._records(self._download(doc['url']),doc)!=rows:raise SourceError('ETS selected report changed between complete reads')
        self.report=doc;return rows

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous);old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope and old.get('latest_report_year',0)>self.report['year']:raise SourceError('ETS latest report regressed')
        self._next['latest_report_year']=self.report['year'];return items
