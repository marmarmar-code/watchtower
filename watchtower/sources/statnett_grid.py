"""Published grid reservations and capacity queues, grouped by official case."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, digest, document, integer, strings, shown
from .common import SourceError
from .powerbi_public import PublicReport, flat_table

PAGE = 'https://www.statnett.no/nettkapasitet-til-produksjon-og-forbruk/foresporsler-og-reservasjon-i-nettet/'
ALIASES = {'Saker':'s', 'Stasjoner':'s1', 'Tilknytningsansvarlig':'t'}
MODELS = {'Reservasjoner':'reservation', 'Kapasitetskø':'queue'}
# Fingerprints cover only the published selections and applied filters, not layout.
CONTRACTS = {
    'reservation': {
        '39b44111bea5b0e0d2409bd0ed920b76a5bb10c19d37c9421cab683b2cca6a8a': 'Produksjon',
        '252650989e410653938917802dc5588b15b0ad6327ccc41025abf8fe866a1035': 'Forbruk',
    },
    'queue': {
        'fe1d81c19e06946b52209af3a6294b2ffe8710fea19fa9c0d78f0620b29452c9': 'Forbruk',
        '0a8caf1089fa0a884ddb8dbb3b3179ad41186d567688985283e283920055d885': 'Produksjon',
    },
}
LABELS = {'capacity_mw':'Kapasitet (MW)', 'date':'Kildens reservasjons-/bestillingsdato',
          'planned_date':'Planlagt tilknytning ved reservasjon/bestilling', 'station':'Stasjon',
          'customer':'Nettselskap/kunde', 'end_customer':'Sluttkunde/prosjekt',
          'industry':'Næring', 'region':'Områdeplan', 'price_area':'Prisområde', 'tilko':'Tilko-saksnummer'}


def converted(value):
    if isinstance(value,dict):
        if set(value)=={'SourceRef'} and 'Entity' in value['SourceRef']:
            return {'SourceRef':{'Source':ALIASES[value['SourceRef']['Entity']]}}
        return {k:converted(v) for k,v in value.items()}
    if isinstance(value,list):
        return [converted(v) for v in value]
    return value


def table_queries(model, mode, limit, *, enforce=True):
    try:
        layout = json.loads(model['exploration']['explorationContent']['explorationDocument'])
        result = []
        for page in layout['pages']['pages']:
            for container in page['visualContainers']:
                visual = container['content'].get('visual',{})
                if visual.get('visualType') != 'tableEx':
                    continue
                projections = visual['query']['queryState']['Values']['projections']
                if len(projections) != 12:
                    raise ValueError()
                fields = [converted(p['field']) for p in projections]
                filters = []
                for obj in (layout['report']['content'], page['content'], container['content']):
                    for entry in obj.get('filterConfig',{}).get('filters',[]):
                        if 'filter' in entry:
                            f = entry['filter']
                            if f['Version'] != 2 or f['From'] != [{'Name':'s','Entity':'Saker','Type':0}]:
                                raise ValueError()
                            filters.extend(deepcopy(f['Where']))
                signature = digest({'fields':fields, 'filters':sorted(filters,key=canonical)})
                if enforce and signature not in CONTRACTS.get(mode,{}):
                    raise SourceError('Grid report columns or selection filters changed')
                direction = CONTRACTS.get(mode,{}).get(signature)
                # Contact-person assignments do not identify or change the project.
                selects = [{**field, 'Name':str(i)} for i,field in enumerate(fields[:11])]
                semantic = {'Version':2, 'From':[{'Name':v,'Entity':k,'Type':0} for k,v in ALIASES.items()],
                            'Select':selects, 'Where':filters}
                query = {'Commands':[{'SemanticQueryDataShapeCommand':{'Query':semantic,
                  'Binding':{'Primary':{'Groupings':[{'Projections':list(range(11))}]},
                    'DataReduction':{'DataVolume':4,'Primary':{'Window':{'Count':limit}}},'Version':1},
                  'ExecutionMetricsKind':1}}]}
                result.append((signature,direction,selects,query))
        if len(result) != 2 or len({r[0] for r in result}) != 2:
            raise ValueError()
        return result
    except (KeyError,ValueError,TypeError,IndexError):
        raise SourceError('Grid report layout contract changed') from None


def date(value):
    if value is None:
        return None
    try:
        stamp = datetime.fromtimestamp(value/1000,timezone.utc)
        if not 1990 <= stamp.year <= 2100 or any((stamp.hour,stamp.minute,stamp.second,stamp.microsecond)):
            raise ValueError()
        return stamp.date().isoformat()
    except (ValueError,TypeError,OverflowError):
        raise SourceError('Grid report date value changed format') from None


class StatnettGridSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config,*args,**kwargs)
        if config.urls not in ((),(PAGE,)):
            raise ValueError('statnett_grid accepts only the official public report page')
        if self.complete or 'removed' in self.events:
            raise ValueError('Absence from the grid report does not establish project cancellation')
        self.limit = integer(config.options.get('max_report_rows',5000),'max_report_rows',500,10000)
        self.industries = strings(config.options['industries'],'industries') if 'industries' in config.options else ()
        self.max_age = integer(config.options.get('max_model_age_days',7),'max_model_age_days',1,90)

    def _poll(self):
        soup = BeautifulSoup(document(self,PAGE),'html.parser')
        if [h.get_text(' ',strip=True) for h in soup.select('h1')] != ['Statistikk om tilknytningssaker']:
            raise SourceError('Grid report landing page identity changed')
        frames = [(f.get('title'),f.get('src','').rstrip('"')) for f in soup.select('iframe') if f.get('title') in MODELS]
        if len(frames) != 2 or {x[0] for x in frames} != set(MODELS):
            raise SourceError('Grid report public embeds are missing or ambiguous')
        observations, counts, stamps = [], {}, []
        for title,url in frames:
            report = PublicReport(self,url,'DK for Tilknytningssaker Ekstern',self.max_age)
            stamps.append(report.refreshed.isoformat())
            mode = MODELS[title]
            for _,direction,selects,query in table_queries(report.model,mode,self.limit):
                rows = flat_table(report.query(query),selects,self.limit,{v:k for k,v in ALIASES.items()})
                counts[mode+':'+direction] = len(rows)
                observations.extend((mode,direction,row) for row in rows)
        if len(set(stamps)) != 1:
            raise SourceError('Grid report versions differ during the combined read')
        return observations,counts,stamps[0]

    def _records(self, observations):
        groups, identities = {}, set()
        for mode,direction,row in observations:
            case,tilko,station,region,area,customer,end_customer,industry,mw,observed,planned = row
            if not isinstance(case,str) or not re.fullmatch(r'[0-9]{2}/[0-9]{5}',case):
                raise SourceError('Grid report lacks a valid official case number')
            identity = (case,mode,direction)
            if identity in identities:
                raise SourceError('Grid report contains ambiguous duplicate case rows')
            identities.add(identity)
            if isinstance(mw,bool) or not isinstance(mw,(int,float)) or not 0 < mw < 100000:
                raise SourceError('Grid report capacity is missing or invalid')
            if self.industries and industry not in self.industries:
                continue
            entry = {'phase':mode,'direction':direction,'tilko':tilko,'station':station,'region':region,
                     'price_area':area,'customer':customer,'end_customer':end_customer,'industry':industry,
                     'capacity_mw':mw,'date':date(observed),'planned_date':date(planned)}
            groups.setdefault(case,[]).append(entry)
        records=[]
        for case,entries in sorted(groups.items()):
            entries.sort(key=lambda e:(e['phase'],e['direction']))
            names = sorted({e['end_customer'] or e['customer'] or 'Navn ikke oppgitt' for e in entries})
            records.append({'key':case,'title':'Nettkapasitet: '+' / '.join(names)+' (sak '+case+')',
                            'url':PAGE,'published':None,'fields':{'observations':entries}})
        return records

    def read_records(self):
        observations,counts,stamp = self._poll()
        again,counts2,stamp2 = self._poll()
        records,records2 = self._records(observations),self._records(again)
        if records != records2 or counts != counts2 or stamp != stamp2:
            raise SourceError('Grid report changed between complete reads; retry next poll')
        self.report_counts,self.model_refreshed = counts,stamp
        return records

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records',{})
        if old.get('scope') == self.scope:
            if old.get('model_refreshed','') > self.model_refreshed:
                raise SourceError('Grid model refresh regressed')
            self._next['rows'] = {**old.get('rows',{}),**self._next['rows']}
            if len(self._next['rows']) > self.max_records*2:
                raise SourceError('Retained grid cases exceed the history bound')
        self._next.update(model_refreshed=self.model_refreshed,report_counts=self.report_counts)
        return items

    @staticmethod
    def entry_label(entry):
        return ('Reservert' if entry['phase']=='reservation' else 'I kapasitetskø')+' – '+entry['direction']

    def describe_change(self,name,before,after):
        old = {(e['phase'],e['direction']):e for e in before}
        new = {(e['phase'],e['direction']):e for e in after}
        changes=[]
        for key in sorted(set(old)|set(new)):
            label=self.entry_label(new.get(key,old.get(key)))
            if key not in new:
                changes.append(label+': ikke lenger oppført i dette uttrekket; årsak er ikke oppgitt')
            elif key not in old:
                changes.append(label+': nå oppført med '+shown(new[key]['capacity_mw'])+' MW')
        # Put capacity and dates for every phase ahead of long customer names.
        for field in LABELS:
            for key in sorted(set(old)&set(new)):
                if old[key][field]!=new[key][field]:
                    changes.append(self.entry_label(new[key])+' · '+LABELS[field]+': '+shown(old[key][field])+' → '+shown(new[key][field]))
        return '\n'.join(changes)

    def _item(self,row,event,details,suppress):
        content=['Nyobservert tilknytningssak' if event=='added' else 'Endrede offentlige tilknytningsopplysninger']
        if event=='added':
            content.extend(self.entry_label(e)+': '+shown(e['capacity_mw'])+' MW · '+shown(e['station'])+' · '+shown(e['industry'])+' · Planlagt tilknytning ved reservasjon/bestilling: '+shown(e['planned_date']) for e in row['fields']['observations'])
        else:
            content.extend(line for detail in details[1:] for line in detail.splitlines())
        content.append('Saksnummer: '+row['key']+'. Kø og reservasjon kan forekomme samtidig og summeres ikke. Planlagt dato er ikke faktisk tilknytning; fravær er ikke dokumentert kansellering.')
        return super()._item(row,event,content,suppress)
