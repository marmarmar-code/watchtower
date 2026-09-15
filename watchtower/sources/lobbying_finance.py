"""Typed, self-reported financial declarations from the public EU register export."""
from dataclasses import replace
from datetime import date,datetime,timezone,timedelta
import io
import re
import xml.etree.ElementTree as ET

from .changes import SnapshotSource,canonical,integer,strings
from .common import SourceError

URL='https://ec.europa.eu/transparencyregister/public/files/ODP/download/XML/latest'
DETAIL='https://transparency-register.europa.eu/search-register-or-update/organisation-detail_en?id='
ROOT='{http://intragate.ec.europa.eu/transparencyregister/odp}ListOfIRPublicDetail'
OWN='Promotes their own interests or the collective interests of their members'
NONCOMMERCIAL='Does not represent commercial interests'


def now():return datetime.now(timezone.utc)


def children(node,required=(),optional=()):
    names=[x.tag for x in node]
    if len(names)!=len(set(names)) or set(names)-set(required)-set(optional) or set(required)-set(names):
        raise SourceError('Lobbying declaration structure changed')


def value(node,optional=False):
    if node is None:
        if optional:return None
        raise SourceError('Lobbying declaration field is absent')
    if list(node) or node.attrib:raise SourceError('Lobbying declaration leaf structure changed')
    result=(node.text or '').strip()
    if not result and optional:return None
    if not result or len(result)>10000 or '\ufffd' in result or any(ord(c)<32 and c not in '\t\r\n' for c in result):
        raise SourceError('Lobbying monitored text is absent, excessive or incompatible')
    return result


def day(node):
    raw=value(node)
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw):raise ValueError()
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:raise SourceError('Lobbying financial period date is invalid') from exc


def timestamp(raw):
    try:
        result=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if result.tzinfo is None:raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError,AttributeError) as exc:raise SourceError('Lobbying export timestamp is invalid') from exc


def number(node,optional=False):
    raw=value(node,optional)
    if raw is None:return None
    if not re.fullmatch(r'\d{1,20}',raw):raise SourceError('Lobbying amount must be a bounded non-negative integer')
    return str(int(raw))


def money(node):
    if node is None or set(node.attrib)!={'type','currency'} or node.get('currency')!='€':
        raise SourceError('Lobbying amount lacks its explicit euro currency and type')
    kind=node.get('type')
    if kind=='AbsoluteCost':
        children(node,['absoluteCost']);return {'type':kind,'currency':'€','amount':number(node.find('absoluteCost'))}
    if kind=='CostRange':
        children(node,['range']);r=node.find('range')
        if r.attrib:raise SourceError('Lobbying cost range attributes changed')
        children(r,optional=['min','max']);low=number(r.find('min'),True);high=number(r.find('max'),True)
        if low is None and high is None or low is not None and high is not None and int(low)>int(high):
            raise SourceError('Lobbying cost range is empty or reversed')
        return {'type':kind,'currency':'€','minimum':low,'maximum':high}
    raise SourceError('Lobbying financial amount type is unsupported')


def entries(node,child,fields):
    if node is None or node.attrib or any(x.tag!=child for x in node) or len(node)>500:
        raise SourceError('Lobbying financial list structure changed')
    result=[]
    for row in node:
        if row.attrib:raise SourceError('Lobbying financial list entry attributes changed')
        children(row,fields)
        result.append({k:money(row.find(k)) if k in {'amount','representationCosts'} else value(row.find(k)) for k in fields})
    # Repeated identical grants are not silently collapsed: the source may omit grant IDs.
    return sorted(result,key=canonical)


def finance(node,interest):
    if node is None or node.attrib:raise SourceError('Lobbying financial declaration is absent')
    children(node,['newOrganisation','closedYear','currentYear'],['complementaryInformation'])
    new=value(node.find('newOrganisation'))
    if new not in {'true','false'}:raise SourceError('Lobbying new-organisation flag is invalid')
    if interest not in {OWN,NONCOMMERCIAL}:raise SourceError('Selected lobbying financial model is not yet supported')
    ngo=interest==NONCOMMERCIAL
    closed=node.find('closedYear');current=node.find('currentYear')
    closed_type='ClosedYearNGOFinancialInformation' if ngo else 'ClosedYearIntermediaryFinancialInformation'
    current_type='CurrentYearNGOFinancialInformation' if ngo else 'CurrentYearIntermediaryFinancialInformation'
    if current.attrib!={'type':current_type}:raise SourceError('Lobbying current-year type disagrees with declared interests')
    children(current,['grants'] if ngo else ['grants','intermediaries'])
    current_result={'source_type':current_type,'grants':entries(current.find('grants'),'grant',['amount','source'])}
    if not ngo:current_result['intermediaries']=entries(current.find('intermediaries'),'intermediary',['name'])
    if new=='true':
        if closed.attrib or list(closed) or (closed.text or '').strip():raise SourceError('New organisation unexpectedly has a closed-year declaration')
        closed_result=None
    else:
        if closed.attrib!={'type':closed_type}:raise SourceError('Lobbying closed-year type disagrees with declared interests')
        required=['startDate','endDate','grants']+(['fundingSources','contributions','totalBudget'] if ngo else ['intermediaries','costs'])
        children(closed,required,['otherSourceInfo'] if ngo else [])
        start,end=day(closed.find('startDate')),day(closed.find('endDate'))
        if start>end:raise SourceError('Lobbying source financial period is reversed')
        closed_result={'source_type':closed_type,'period_start':start,'period_end':end,'grants':entries(closed.find('grants'),'grant',['amount','source'])}
        if ngo:
            budget=money(closed.find('totalBudget'))
            if budget['type']!='AbsoluteCost':raise SourceError('Lobbying NGO budget is not an absolute amount')
            closed_result.update(total_budget=budget,funding_sources=entries(closed.find('fundingSources'),'fundingSource',['source']),
                                 contributions=entries(closed.find('contributions'),'contributor',['name','amount']))
        else:
            costs=money(closed.find('costs'))
            if costs['type']!='CostRange':raise SourceError('Lobbying activity costs are not a source range')
            intermediaries=entries(closed.find('intermediaries'),'intermediary',['name','representationCosts'])
            if any(x['representationCosts']['type']!='CostRange' for x in intermediaries):raise SourceError('Lobbying intermediary costs are not ranges')
            closed_result.update(estimated_activity_costs=costs,intermediaries=intermediaries)
    return {'new_organisation':new=='true','closed_year':closed_result,'current_year_declaration':current_result}


class LobbyingFinanceSource(SnapshotSource):
    def __init__(self,config,*args,**kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(URL,)):raise ValueError('lobbying_finance accepts only the official public XML export')
        if self.complete or 'removed' in self.events:raise ValueError('Export absence does not establish cessation of lobbying')
        self.countries=strings(config.options.get('countries',['NORWAY']),'countries')
        if len(self.countries)>30 or any(x!=x.upper() or len(x)>100 for x in self.countries):raise ValueError('countries requires exact uppercase source country names')
        self.max_download_bytes=integer(config.options.get('max_download_bytes',150000000),'max_download_bytes',1024,200000000)
        self.max_export_records=integer(config.options.get('max_export_records',30000),'max_export_records',1,100000)
        self.max_export_age_days=integer(config.options.get('max_export_age_days',7),'max_export_age_days',1,30)
        self.field_labels={'name':'Oppført navn','entity_form':'Organisasjonsform','category':'Registerkategori','country':'Hovedkontorland',
                           'interests_represented':'Oppgitte interesser','financial_declaration':'Innrapporterte finansopplysninger',**self.field_labels}

    def _read(self):
        response=self.get(URL,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code!=200:raise SourceError('Lobbying export redirected unexpectedly')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>self.max_download_bytes:raise SourceError('Lobbying export exceeds max_download_bytes')
                chunks.append(chunk)
            raw=b''.join(chunks)
        finally:response.close()
        if not re.match(br'<\?xml version=[\'"]1\.1[\'"] encoding=[\'"]UTF-8[\'"]\?>',raw):
            raise SourceError('Lobbying export XML version or encoding changed')
        if re.search(br'<!DOCTYPE|<!ENTITY',raw):raise SourceError('Lobbying export must not declare document entities')
        references=0
        def compatible(m):
            nonlocal references
            code=int(m[1],16) if m[1] else int(m[2])
            if code in {*range(1,9),11,12,*range(14,32)}:
                references+=1
                if references>1000:raise SourceError('Lobbying XML compatibility reference count is excessive')
                return b'\xef\xbf\xbd'
            return m[0]
        # These references are permitted in XML 1.1 but unsupported by Expat.
        # Mark the compatibility view; monitored fields reject every replacement marker.
        raw=re.sub(br'&#(?:x([0-9a-fA-F]{1,8})|([0-9]{1,10}));',compatible,raw)
        stack=[];rows=[];ids=set();countries=set();metadata=None;root_children=[]
        try:
            for event,e in ET.iterparse(io.BytesIO(raw),events=('start','end')):
                if event=='start':
                    stack.append(e)
                    if len(stack)==1 and (e.tag!=ROOT or e.attrib):raise SourceError('Lobbying export root changed')
                    if len(stack)>30:raise SourceError('Lobbying export nesting is excessive')
                    if len(stack)==2:root_children.append(e.tag)
                    continue
                if e.tag=='metaData':
                    if len(stack)!=2 or metadata is not None or e.attrib:raise SourceError('Lobbying metadata placement changed')
                    children(e,['exportDate','numberOfIR']);stamp=timestamp(value(e.find('exportDate')))
                    count=value(e.find('numberOfIR'))
                    if not re.fullmatch(r'[1-9][0-9]{0,5}',count) or int(count)>self.max_export_records:raise SourceError('Lobbying export total is invalid or excessive')
                    metadata=(stamp,int(count))
                elif e.tag=='interestRepresentative':
                    if len(stack)!=3 or stack[-2].tag!='resultList' or e.attrib:raise SourceError('Lobbying record placement changed')
                    ident=value(e.find('identificationCode'));location=value(e.find('headOffice/country'))
                    if not re.fullmatch(r'\d{5,16}-\d{2}',ident) or ident in ids:raise SourceError('Lobbying register identity is invalid or duplicated')
                    if len(e.findall('identificationCode'))!=1 or len(e.findall('headOffice'))!=1 or len(e.findall('headOffice/country'))!=1:
                        raise SourceError('Lobbying record selection identity is ambiguous')
                    ids.add(ident);countries.add(location)
                    if len(ids)>self.max_export_records:raise SourceError('Lobbying export record count exceeds bounds')
                    if location in self.countries:
                        for path in ['name','name/originalName','entityForm','registrationCategory','interestRepresented','financialData']:
                            if len(e.findall(path))!=1:raise SourceError('Selected lobbying record repeats or lacks a required field')
                        name=value(e.find('name/originalName'));interest=value(e.find('interestRepresented'))
                        fields={'name':name,'entity_form':value(e.find('entityForm')),'category':value(e.find('registrationCategory')),
                                'country':location,'interests_represented':interest,'financial_declaration':finance(e.find('financialData'),interest)}
                        rows.append({'key':ident,'title':name,'url':DETAIL+ident,'published':None,'fields':fields})
                        if len(rows)>self.max_records:raise SourceError('Selected lobbying records exceed max_records')
                    stack[-2].remove(e);e.clear()
                elif len(stack)==3 and stack[-2].tag=='resultList':raise SourceError('Lobbying export contains an unexpected record type')
                stack.pop()
        except ET.ParseError as exc:raise SourceError('Lobbying export is incomplete or incompatible with the validated XML view') from exc
        if root_children!=['metaData','resultList'] or metadata is None or metadata[1]!=len(ids):raise SourceError('Lobbying export total or end structure disagrees')
        if set(self.countries)-countries:raise SourceError('A selected lobbying country is absent from the export')
        if not now()-timedelta(days=self.max_export_age_days)<=metadata[0]<=now()+timedelta(minutes=5):
            raise SourceError('Lobbying export timestamp is stale or in the future')
        self.compatibility_references=references
        return metadata[0].isoformat(),metadata[1],sorted(rows,key=lambda r:r['key'])

    def read_records(self):
        first,second=self._read(),self._read()
        if first!=second:raise SourceError('Lobbying export selection changed between complete reads')
        self.exported_at=first[0]
        return first[2]

    def fetch_with_state(self,previous):
        items=super().fetch_with_state(previous)
        old=((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope')==self.scope and old.get('exported_at','')>self.exported_at:
            raise SourceError('Lobbying export timestamp regressed; prior snapshot preserved')
        self._next['exported_at']=self.exported_at
        return items

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress);f=row['fields'];closed=f['financial_declaration']['closed_year']
        info=('Nyobservert finansoppføring' if event=='added' else 'Endrede innrapporterte finansopplysninger',f['name']+' · '+row['key'],f['interests_represented'])
        if closed is None:info+=('Ingen avsluttet regnskapsperiode oppgitt for ny organisasjon.',)
        else:
            info+=('Kildens periode: '+closed['period_start']+' – '+closed['period_end'],)
            if 'estimated_activity_costs' in closed:info+=('Anslåtte kostnader for registeromfattede aktiviteter: '+canonical(closed['estimated_activity_costs']),)
            if 'total_budget' in closed:info+=('Organisasjonens totale budsjett, ikke særskilte lobbykostnader: '+canonical(closed['total_budget']),)
        if event=='changed':info+=tuple(d[:900] for d in details[1:])
        return replace(item,alert_details=info+('Selvrapporterte opplysninger. Intervaller er ikke eksakte beløp, og beløp summeres ikke på tvers av organisasjoner eller mellommenn.',))
