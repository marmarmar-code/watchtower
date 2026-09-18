"""Published approval status for staffing-register units."""
from dataclasses import replace
import json
import re

from .changes import SnapshotSource, integer, strings
from .common import SourceError
from .identifiers import valid_orgnr

PAGE = 'https://www.arbeidstilsynet.no/bemanningsvirksomhet/'
API = 'https://www.arbeidstilsynet.no/api/organisation/GetOrganisations'
STATUS = {'0':'Ikke registrert', '1':'Godkjent', '2':'Ikke godkjent'}


def unique_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def unit_record(row):
    if not isinstance(row,dict) or not isinstance(row.get('virksomhet'),dict):
        raise SourceError('Staffing-register unit is missing')
    unit = row['virksomhet']
    required = {'organisasjonsnummer','erHovedenhet','status','erGodkjent'}
    if not required <= set(unit):
        raise SourceError('Staffing-register unit fields are incomplete')
    org, name, status = unit['organisasjonsnummer'], unit.get('navn'), unit['status']
    if not isinstance(org,str) or not re.fullmatch(r'[0-9]{9}',org) or not valid_orgnr(org):
        raise SourceError('Staffing-register organisation number is invalid')
    if name is not None and (not isinstance(name,str) or not name.strip() or len(name) > 500):
        raise SourceError('Staffing-register name is invalid')
    if type(status) is not int or str(status) not in STATUS or type(unit['erGodkjent']) is not bool or type(unit['erHovedenhet']) is not bool:
        raise SourceError('Staffing-register status or unit type is invalid')
    if unit['erGodkjent'] != (status == 1):
        raise SourceError('Staffing-register status and approval flag disagree')
    name = ' '.join(name.split()) if name is not None else None
    return {'key':org, 'title':name or f'Enhet {org}', 'url':PAGE+org, 'published':None,
            'fields':{'name':name, 'unit_type':'Hovedenhet' if unit['erHovedenhet'] else 'Underenhet',
                      'status_code':status, 'approval_status':STATUS[str(status)]}}


class StaffingRegisterSource(SnapshotSource):
    def fetch_with_state(self, previous):
        saved = ((previous or {}).get('source_state') or {}).get('records', {})
        self._previous_rows = saved.get('rows', {}) if saved.get('scope') == self.scope else {}
        return super().fetch_with_state(previous)

    def __init__(self, config, *args, **kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE,)):
            raise ValueError('staffing_register accepts only the official staffing register')
        if self.complete or 'removed' in self.events:
            raise ValueError('Staffing-register absence cannot confirm withdrawn approval')
        self.include_subunits = config.options.get('include_subunits',False)
        if type(self.include_subunits) is not bool:
            raise ValueError('include_subunits must be boolean')
        self.orgnrs = strings(config.options.get('orgnrs',[]),'orgnrs',empty=True)
        if len(self.orgnrs) > 100 or any(not re.fullmatch(r'[0-9]{9}',v) or not valid_orgnr(v) for v in self.orgnrs):
            raise ValueError('orgnrs must contain up to 100 valid organisation numbers')
        self.page_size = integer(config.options.get('page_size',500),'page_size',1,500)
        self.max_pages = integer(config.options.get('max_pages',20),'max_pages',1,100)
        self.export_limit = integer(config.options.get('max_export_records',10000),'max_export_records',1,50000)
        self.field_labels = {'name':'Oppført navn', 'unit_type':'Enhetstype', 'status_code':'Statuskode',
                             'approval_status':'Oppført godkjenningsstatus',**self.field_labels}

    def _page(self,page):
        params = {'registerType':'bemanningsvirksomhet','page':page,'pageSize':self.page_size,
                  'query':'','status':'','counties':'','municipalities':'','includeGeoData':'false'}
        response = self.post(API,params=params,headers={'Accept':'application/json'},stream=True,
                             allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200:
                raise SourceError('Staffing register returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64*1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Staffing-register page exceeds max_bytes')
                chunks.append(chunk)
            data = json.loads(b''.join(chunks),object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        except (ValueError,UnicodeError,RecursionError) as exc:
            raise SourceError('Staffing-register response is invalid JSON') from exc
        finally:
            response.close()
        if not isinstance(data,dict) or not isinstance(data.get('data'),list) or not isinstance(data.get('pagination'),dict):
            raise SourceError('Staffing-register page is incomplete')
        info = data.get('registerInfo')
        if not isinstance(info,dict) or info.get('statusKodeMapping') != STATUS:
            raise SourceError('Staffing-register status mapping changed')
        pagination = data['pagination']
        keys = ('pageNumber','pageSize','totalItems','totalPages')
        if any(type(pagination.get(k)) is not int for k in keys):
            raise SourceError('Staffing-register pagination lacks integer counts')
        total, pages = pagination['totalItems'], pagination['totalPages']
        if (pagination['pageNumber'] != page or pagination['pageSize'] != self.page_size
                or not 1 <= total <= self.export_limit or not 1 <= pages <= self.max_pages
                or pages != (total+self.page_size-1)//self.page_size or page > pages
                or len(data['data']) != min(self.page_size,total-(page-1)*self.page_size)):
            raise SourceError('Staffing-register pagination is inconsistent or exceeds bounds')
        rows = [unit_record(row) for row in data['data']]
        if len({row['key'] for row in rows}) != len(rows):
            raise SourceError('Staffing-register page contains duplicate units')
        return total,pages,rows

    def _export(self):
        total, pages, first = self._page(1)
        rows = list(first)
        for page in range(2,pages+1):
            next_total, next_pages, current = self._page(page)
            if (next_total,next_pages) != (total,pages):
                raise SourceError('Staffing-register page counts changed during reading')
            rows.extend(current)
        if len(rows) != total or len({row['key'] for row in rows}) != total:
            raise SourceError('Staffing-register export has missing or repeated units')
        return {row['key']:row for row in rows}

    def read_records(self):
        # Re-read the whole export: first/last anchors alone cannot detect
        # changes in middle pages. Ignore coordinates and page ordering.
        rows = self._export()
        if self._export() != rows:
            raise SourceError('Staffing-register export changed during reading')
        selected = [row for row in rows.values() if self.include_subunits or row['fields']['unit_type']=='Hovedenhet']
        if self.orgnrs:
            by_id = {row['key']:row for row in selected}
            if not set(self.orgnrs) <= set(by_id):
                raise SourceError('An explicitly selected staffing unit is absent or excluded by unit type')
            selected = [by_id[org] for org in self.orgnrs]
        if len(selected) > self.max_records:
            raise SourceError('Staffing-register selection exceeds max_records')
        missing_names = 0
        for row in selected:
            observed_name = row['fields']['name']
            row['observed_name'] = observed_name
            if observed_name is None:
                missing_names += 1
                old = getattr(self, '_previous_rows', {}).get(row['key'], {}).get('row', {})
                known_name = old.get('fields', {}).get('name')
                if known_name:
                    # Keep the last observed name as context, explicitly marked
                    # below. A missing optional API field is not a company rename.
                    row['fields']['name'] = known_name
                    row['title'] = known_name
        self.coverage_warnings = ['staffing_names_missing'] if missing_names else []
        return sorted(selected,key=lambda row:row['key'])

    def _item(self,row,event,details,suppress):
        prior = getattr(self, '_previous_rows', {}).get(row['key'], {}).get('row', {}).get('fields', {})
        if event == 'changed' and prior and prior.get('name') is None:
            # Learning a previously absent name is enrichment, not evidence of a
            # rename. Continue to report any accompanying approval/status change.
            changes = tuple(value for value in details[1:]
                            if not value.startswith(self.field_labels['name'] + ':'))
            details = (details[0], *changes)
            suppress = suppress or not changes
        item = super()._item(row,event,details,suppress)
        label = 'Nyobservert oppføring i bemanningsregisteret' if event=='added' else 'Endret oppføring i bemanningsregisteret'
        info = (label,f"Organisasjonsnummer: {row['key']} · {row['fields']['unit_type']}",
                f"Oppført godkjenningsstatus: {row['fields']['approval_status']}")
        if event == 'changed':
            info += tuple(d[:800] for d in details[1:])
        if row.get('observed_name') is None:
            info += ('Navn mangler i dagens kilde; viser sist observerte navn.' if row['fields']['name']
                     else 'Navn er ikke oppgitt i dagens kilde.',)
        info += ('Registerobservasjon uten vedtaksdato; fravær tolkes ikke som inndratt godkjenning',)
        return replace(item,alert_details=info)
