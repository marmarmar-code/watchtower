"""Source-labelled platform supervision histories without inferred legal outcomes."""
from dataclasses import replace
from datetime import datetime
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError

URL = 'https://digital-strategy.ec.europa.eu/en/policies/list-designated-vlops-and-vloses'
TITLE = 'Supervision of the designated very large online platforms and search engines under DSA'
PROVIDER = 'Main establishment of the provider in the EU'
LABELS = ['Designated service', 'Type of service under DSA', 'Average monthly active users in millions*',
          'Digital Services Coordinator as of 17 February 2024', 'DSA enforcement actions']
LINK_HOSTS = {'digital-strategy.ec.europa.eu', 'ec.europa.eu', 'eur-lex.europa.eu'}


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def date(value):
    try:
        if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4}', value): raise ValueError()
        return datetime.strptime(value, '%d.%m.%Y').date().isoformat()
    except ValueError as exc:
        raise SourceError('DSA action calendar date is invalid') from exc


class DsaSupervisionSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (URL,)):
            raise ValueError('dsa_supervision accepts only the official supervision overview')
        if self.complete or 'removed' in self.events:
            raise ValueError('Overview absence does not establish termination or case closure')
        self.services = strings(config.options['services'], 'services') if 'services' in config.options else ()
        if len(self.services)>100: raise ValueError('At most 100 exact service names may be selected')
        self.max_services = integer(config.options.get('max_services',100), 'max_services',1,500)
        self.max_actions = integer(config.options.get('max_actions_per_service',200), 'max_actions_per_service',1,1000)
        self.field_labels = {'provider':'Oppført tilbyder', 'service_type':'Kildens tjenesteklassifisering',
                             'coordinator':'Oppført koordinator', 'actions':'Oppførte tilsynshendelser', **self.field_labels}

    def _page(self):
        r = self.get(URL, stream=True, allow_redirects=False, accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code != 200: raise SourceError('DSA overview redirected unexpectedly')
            chunks=[]; size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>self.max_bytes: raise SourceError('DSA overview exceeds max_bytes')
                chunks.append(chunk)
            raw=b''.join(chunks)
        finally: r.close()
        if not raw.rstrip().lower().endswith(b'</html>'):
            raise SourceError('DSA overview is incomplete')
        try: soup=BeautifulSoup(raw.decode('utf-8'), 'html.parser')
        except UnicodeDecodeError as exc: raise SourceError('DSA overview encoding changed') from exc
        headings=soup.find_all('h1')
        if len(headings)!=1 or text(headings[0])!=TITLE: raise SourceError('DSA overview identity changed')
        notes=soup.find_all('h2',id='ecl-inpage-notes')
        if len(notes)!=1 or text(notes[0])!='Notes': raise SourceError('DSA overview end marker is absent')
        main=notes[0].parent
        stamps=main.find_all('pre',recursive=False)
        if len(stamps)!=1: raise SourceError('DSA overview revision stamp is absent')
        match=re.fullmatch(r'Information updated on (\d{1,2}) ([A-Za-z]+) (\d{4})\.',text(stamps[0]))
        months={name:i+1 for i,name in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'])}
        try:
            if not match: raise ValueError()
            stamp=datetime(int(match[3]),months[match[2]],int(match[1])).date().isoformat()
        except (ValueError,KeyError) as exc: raise SourceError('DSA overview revision date is invalid') from exc
        containers=main.find_all('div',class_='ecl-container',recursive=False)
        if not containers or len(containers)>self.max_services: raise SourceError('DSA service count is empty or excessive')
        if len(main.find_all('div',class_='ecl-container'))!=len(containers): raise SourceError('DSA nested service structure changed')
        provider=None; anchor=None; records=[]; keys=set(); provider_anchors=set()
        for container in containers:
            rows=container.find_all('div',class_='ecl-row',recursive=False)
            if len(rows)!=6: raise SourceError('DSA labelled service row count changed')
            values=[]; labels=[]
            for row in rows:
                cols=row.find_all('div',recursive=False)
                if len(cols)!=2: raise SourceError('DSA label/value column structure changed')
                labels.append(text(cols[0])); values.append(cols[1])
            if labels[1:]!=LABELS: raise SourceError('DSA service labels changed')
            if labels[0]==PROVIDER:
                hs=[h for h in values[0].find_all('h2') if text(h)]
                if len(hs)!=1: raise SourceError('DSA provider heading is missing or ambiguous')
                provider=text(hs[0]); anchor=hs[0].get('id','')
                if not re.fullmatch(r'ecl-inpage-[A-Za-z0-9-]+',anchor) or anchor in provider_anchors:
                    raise SourceError('DSA provider anchor is invalid or repeated')
                provider_anchors.add(anchor)
            elif labels[0] or text(values[0]) or provider is None:
                raise SourceError('DSA continuation lacks a preceding provider')
            service,kind,users,coordinator=[text(v) for v in values[1:5]]
            if any(not v or len(v)>2000 for v in [provider,service,kind,users,coordinator]):
                raise SourceError('DSA service metadata is empty or excessive')
            key=service.casefold()
            if key in keys: raise SourceError('DSA service identity is duplicated')
            keys.add(key)
            lists=values[5].find_all('ul',recursive=False)
            if len(lists)!=1: raise SourceError('DSA action list is missing or ambiguous')
            entries=lists[0].find_all('li',recursive=False)
            if not entries or len(entries)>self.max_actions or len(lists[0].find_all('li'))!=len(entries):
                raise SourceError('DSA action list is empty, nested or excessive')
            actions=[]; identities=set()
            for li in entries:
                content=text(li); m=re.fullmatch(r'(\d{2}\.\d{2}\.\d{4}):\s*(.+)',content)
                if not m or len(content)>10000: raise SourceError('DSA action date or description is malformed')
                day=date(m[1]); links=[]
                for a in li.find_all('a',href=True):
                    url=urljoin(URL,a['href']); parsed=urlsplit(url)
                    if parsed.scheme!='https' or parsed.netloc not in LINK_HOSTS or parsed.username or not parsed.path or len(url)>3000:
                        raise SourceError('DSA action link leaves the official source contract')
                    links.append(url)
                if not links: raise SourceError('DSA action has no official evidence link')
                action={'date':day,'description':m[2],'links':sorted(set(links))}
                identity=canonical(action)
                if identity in identities: raise SourceError('DSA action is duplicated')
                identities.add(identity); actions.append(action)
            fields={'provider':provider, 'service_type':kind, 'coordinator':coordinator,
                    'actions':sorted(actions,key=canonical)}
            records.append({'key':key,'title':service,'url':URL+'#'+anchor,'published':None,'fields':fields})
        # All source entries are validated before exact service selection.
        if self.services and set(self.services)-{r['title'] for r in records}:
            raise SourceError('A selected DSA service is absent; prior snapshot preserved')
        return stamp,sorted([r for r in records if not self.services or r['title'] in self.services],key=lambda r:r['key'])

    def read_records(self):
        first,second=self._page(),self._page()
        if first!=second: raise SourceError('DSA overview changed between complete reads')
        self._stamp=first[0]
        return first[1]

    def fetch_with_state(self, previous):
        old=((previous or {}).get('source_state') or {}).get('records',{})
        self._old_rows=old.get('rows',{}) if old.get('scope')==self.scope else {}
        items=super().fetch_with_state(previous)
        if old.get('scope')==self.scope and old.get('overview_date','')>self._stamp:
            raise SourceError('DSA overview date regressed; prior snapshot preserved')
        self._next['overview_date']=self._stamp
        return items

    def _item(self, row, event, details, suppress):
        item=super()._item(row,event,details,suppress); f=row['fields']
        info=('Nyobservert tjeneste i tilsynsoversikten' if event=='added' else 'Endret tilsynsoversikt for tjenesten',
              row['title']+' · '+f['provider'], 'Kildens klassifisering: '+f['service_type'])
        if event=='changed':
            prior=self._old_rows.get(row['key'],{}).get('row',{}).get('fields',{})
            old_actions={canonical(a) for a in prior.get('actions',[])}
            changed=[a for a in f['actions'] if canonical(a) not in old_actions]
            info+=tuple(a['date']+': '+a['description'][:900] for a in changed[:5])
            if len(changed)>5: info+=(f'{len(changed)} nye eller endrede oppføringer; se full oversikt.',)
            info+=tuple(d[:900] for d in details[1:] if not d.startswith('Oppførte tilsynshendelser:'))
            if old_actions-{canonical(a) for a in f['actions']}:
                info+=('Tidligere hendelsestekst er endret eller utelatt i kilden. Dette fastslår ikke at et vedtak er opphevet.',)
        return replace(item,alert_details=info+('Dato og beskrivelse følger kilden. Forespørsler, foreløpige funn og vedtak har ulik betydning; ingen ny rettslig konklusjon utledes.',))
