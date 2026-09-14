"""Published spectrum-register entries, with separate general-rule selections."""
from dataclasses import replace
from datetime import datetime
import json
import re

from .changes import SnapshotSource, integer, strings
from .common import SourceError

PAGE_URL = 'https://frekvens.nkom.no/'
DATA_URL = PAGE_URL+'frekvensportalen_service/rest/rightofuseinfos'
GENERAL_RULES = {'9999999':'Fribruk', '9999998':'Radioamatørregel'}
FREQUENCIES = ('lowerFrequency','higherFrequency','downlinkLowerFrequency',
               'downlinkHigherFrequency','uplinkLowerFrequency','uplinkHigherFrequency')
SCHEMA = set(FREQUENCIES) | {'id','application','company','expiry','nationalcoverage',
                           'localcoverage','shortComments','rightofuseinfono','duplex'}


def _text(value, *, empty=False):
    if not isinstance(value,str) or len(value)>2000:
        raise SourceError('Spectrum register text is invalid or excessive')
    result = ' '.join(value.split())
    if not empty and not result:
        raise SourceError('Spectrum register required text is empty')
    return result


def _object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise SourceError('Spectrum JSON repeats a field')
        result[key] = value
    return result


class FrequencyLicencesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE_URL,)):
            raise ValueError('frequency_licences accepts only the official spectrum portal')
        if self.complete or 'removed' in self.events:
            raise ValueError('Spectrum register absence cannot establish withdrawal')
        self.include_general_rules = config.options.get('include_general_rules',False)
        if not isinstance(self.include_general_rules,bool):
            raise ValueError('include_general_rules must be boolean')
        self.permit_numbers = strings(config.options['permit_numbers'],'permit_numbers') if 'permit_numbers' in config.options else ()
        if len(self.permit_numbers)>100 or any(not re.fullmatch(r'[0-9]{1,12}',v) for v in self.permit_numbers):
            raise ValueError('permit_numbers must contain up to 100 exact numeric source labels')
        if not self.include_general_rules and set(self.permit_numbers)&set(GENERAL_RULES):
            raise ValueError('General-rule numbers require include_general_rules')
        self.max_register_records = integer(config.options.get('max_register_records',5000),'max_register_records',1,20000)
        self.field_labels = {'holder':'Oppført innehaver','permit_number':'Oppført tillatelsesnummer',
            'application':'Anvendelse','expiry':'Oppført utløpstid (UTC)','national_coverage':'Nasjonal dekning',
            'local_coverage':'Lokal dekning','comment':'Kommentar','duplex':'Duplex',
            'frequency_hz':'Frekvensgrenser (Hz)','downlink_hz':'DL-grenser (Hz)',
            'uplink_hz':'UL-grenser (Hz)','entry_type':'Oppføringstype', **self.field_labels}

    def _read(self):
        response = self.get(DATA_URL,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200:
                raise SourceError('Spectrum register returned an unexpected redirect')
            chunks,size = [],0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size>self.max_bytes:
                    raise SourceError('Spectrum register exceeds max_bytes')
                chunks.append(chunk)
            return self._records(b''.join(chunks))
        finally:
            response.close()

    def _records(self,raw):
        try:
            data = json.loads(raw,object_pairs_hook=_object)
        except (ValueError,UnicodeError) as exc:
            raise SourceError('Spectrum register JSON is malformed') from exc
        if not isinstance(data,list) or not 1<=len(data)<=self.max_register_records:
            raise SourceError('Spectrum register is empty or exceeds max_register_records')
        records,seen = [],set()
        for row in data:
            if not isinstance(row,dict) or set(row)!=SCHEMA:
                raise SourceError('Spectrum register row schema changed')
            ident = row['id']
            if type(ident) is not int or not 1<=ident<=2**53-1 or ident in seen:
                raise SourceError('Spectrum register identity is invalid or duplicated')
            seen.add(ident)
            if type(row['duplex']) is not bool or type(row['nationalcoverage']) is not bool:
                raise SourceError('Spectrum register boolean is invalid')
            if any(type(row[k]) is not int or not 0<=row[k]<=10**15 for k in FREQUENCIES):
                raise SourceError('Spectrum frequency must be bounded non-negative integer Hz')
            intervals = [[row[a],row[b]] for a,b in zip(FREQUENCIES[::2],FREQUENCIES[1::2])]
            if any(lo>hi for lo,hi in intervals):
                raise SourceError('Spectrum frequency interval is inverted')
            active = intervals[1:] if row['duplex'] else intervals[:1]
            unused = intervals[:1] if row['duplex'] else intervals[1:]
            if any(hi==0 for _,hi in active) or any(pair!=[0,0] for pair in unused):
                raise SourceError('Spectrum intervals disagree with duplex mode')
            permit = _text(row['rightofuseinfono'])
            if not re.fullmatch(r'[0-9]{1,12}',permit):
                raise SourceError('Spectrum permit number is invalid')
            expiry = row['expiry']
            if not isinstance(expiry,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+0000',expiry):
                raise SourceError('Spectrum expiry timestamp format changed')
            try:
                datetime.strptime(expiry,'%Y-%m-%dT%H:%M:%S%z')
            except ValueError as exc:
                raise SourceError('Spectrum expiry timestamp is invalid') from exc
            fields = {'holder':_text(row['company']),'permit_number':permit,
                'application':_text(row['application']),'expiry':expiry,
                'national_coverage':row['nationalcoverage'],'local_coverage':_text(row['localcoverage'],empty=True),
                'comment':_text(row['shortComments'],empty=True),'duplex':row['duplex'],
                'frequency_hz':intervals[0],'downlink_hz':intervals[1],'uplink_hz':intervals[2],
                'entry_type':GENERAL_RULES.get(permit,'Tillatelsesoppføring')}
            records.append({'key':str(ident),'title':f'Frekvensoppføring {ident} · nummer {permit}',
                            'url':PAGE_URL,'published':None,'fields':fields})
        return sorted(records,key=lambda r:r['key'])

    def read_records(self):
        first,second = self._read(),self._read()
        if first!=second:
            raise SourceError('Spectrum register changed between complete reads')
        if not set(self.permit_numbers)<={r['fields']['permit_number'] for r in first}:
            raise SourceError('An explicitly selected spectrum permit number is absent')
        selected = [r for r in first if (self.include_general_rules or r['fields']['permit_number'] not in GENERAL_RULES)
                    and (not self.permit_numbers or r['fields']['permit_number'] in self.permit_numbers)]
        if not selected or len(selected)>self.max_records:
            raise SourceError('Spectrum selection is empty or exceeds max_records')
        return selected

    def _item(self,row,event,details,suppress):
        item = super()._item(row,event,details,suppress)
        f = row['fields']
        ranges = (f"DL: {f['downlink_hz']} Hz · UL: {f['uplink_hz']} Hz" if f['duplex'] else f"Frekvensgrenser: {f['frequency_hz']} Hz")
        info = ('Nyobservert frekvensoppføring' if event=='added' else 'Endret frekvensoppføring',
                f"{f['holder']} · {f['application']} · {f['entry_type']}",ranges,
                'Oppført utløpstid (UTC): '+f['expiry'])
        if event=='changed':
            info += tuple(d[:800] for d in details[1:])
        return replace(item,alert_details=info+('Ny rad er ikke nødvendigvis en ny tillatelse; flere rader kan dele nummer',
            'Oppført utløpstid er ikke bevis på aktiv status; første observasjon er ikke vedtaksdato',
            'Lenken åpner portalen; søk på oppført tillatelsesnummer',))
