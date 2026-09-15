"""Changes to the Commission's published novel-food act listings."""
from dataclasses import replace
from datetime import date
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError

PAGE_URL = 'https://food.ec.europa.eu/food-safety/novel-food/authorisations/union-list-novel-foods_en'
MONTHS = {name:i for i,name in enumerate(('January','February','March','April','May','June','July','August','September','October','November','December'),1)}
LABEL = re.compile(r'(Corrigendum to )?Commission Implementing (Regulation|Decision) \(EU\) (20[0-9]{2})/([1-9][0-9]{0,5})')
PATH = re.compile(r'/eli/(reg_impl|dec_impl)/(20[0-9]{2})/([1-9][0-9]{0,5})(?:/corrigendum/(20[0-9]{2}-[0-9]{2}-[0-9]{2}))?/oj')


def text(node):
    return ' '.join(node.get_text(' ',strip=True).split())


class NovelFoodsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((),(PAGE_URL,)):
            raise ValueError('novel_foods accepts only the official Union-list page')
        if self.complete or 'removed' in self.events:
            raise ValueError('A published act listing cannot establish withdrawal')
        self.latest_years = integer(config.options.get('latest_years',2),'latest_years',1,5)
        self.field_labels = {'listed_label':'Listebetegnelse','listed_act_date':'Oppført dato for grunnrettsakten',
                             'description':'Oppført beskrivelse','corrigendum_date':'Rettingsdato i lenken',
                             'type_mismatch':'Liste og lenke oppgir ulik dokumenttype',**self.field_labels}

    def _read(self):
        response = self.get(PAGE_URL,stream=True,allow_redirects=False,accepted_statuses=(301,302,303,307,308))
        try:
            if response.status_code != 200:
                raise SourceError('Novel-food listing returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64*1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('Novel-food listing exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def _record(self, li):
        links = li.select('a[href]')
        if len(links) != 1 or li.find('li') is not None:
            raise SourceError('Novel-food listing row has missing or ambiguous links')
        link = links[0]
        label, full = text(link), text(li)
        match = LABEL.fullmatch(label)
        url = urlparse(link['href'])
        path = PATH.fullmatch(url.path)
        if (not match or not path or url.scheme != 'https' or url.netloc != 'eur-lex.europa.eu'
                or url.query or url.fragment or url.params or match.group(3,4) != path.group(2,3)):
            raise SourceError('Novel-food act label and official URL identity disagree')
        correction = path.group(4)
        if bool(match.group(1)) != bool(correction):
            raise SourceError('Novel-food correction label and URL disagree')
        suffix = full[len(label):] if full.startswith(label) else ''
        detail = re.fullmatch(r' of ([0-9]{1,2}) ([A-Za-z]+) (20[0-9]{2}),? (.+)',suffix)
        if not detail or detail.group(3) != match.group(3) or not 20 <= len(detail.group(4)) <= 3000:
            raise SourceError('Novel-food act date or description is missing or invalid')
        try:
            listed_date = date(int(detail.group(3)),MONTHS[detail.group(2)],int(detail.group(1)))
            corrected_date = date.fromisoformat(correction) if correction else None
        except (ValueError,KeyError):
            raise SourceError('Novel-food date is not a valid calendar date') from None
        if listed_date > date.today() or (corrected_date and (corrected_date < listed_date or corrected_date > date.today())):
            raise SourceError('Novel-food act dates are inconsistent')
        ident = f'{match.group(3)}/{match.group(4)}'
        # The list has an observed type-label inconsistency. Retain its wording
        # and expose the disagreement; neither is a verified legal conclusion.
        mismatch = (match.group(2) == 'Regulation') != (path.group(1) == 'reg_impl')
        key = path.group(1)+':'+ident+(':corrigendum:'+correction if correction else '')
        return {'key':key,'title':f'Nye matvarer · EU {ident}'+(' · retting' if correction else ''),
                'url':link['href'],'published':None,'act_id':ident,
                'fields':{'listed_label':label,'listed_act_date':listed_date.isoformat(),
                          'description':detail.group(4),'corrigendum_date':correction,'type_mismatch':mismatch}}

    def read_records(self):
        soup = BeautifulSoup(self._read(),'html.parser')
        titles, mains = soup.select('h1'), soup.select('main')
        if len(titles) != 1 or text(titles[0]) != 'Union list of novel foods' or len(mains) != 1:
            raise SourceError('Novel-food page identity changed')
        main = mains[0]
        sections = {}
        for heading in main.select('h3, summary'):
            match = re.fullmatch(r'Updates - (20[0-9]{2})',text(heading))
            if not match:
                continue
            year = int(match.group(1))
            if year in sections:
                raise SourceError('Novel-food year section repeats')
            sections[year] = heading.find_parent('details') if heading.name == 'summary' else heading.parent
        if not sections or max(sections) > date.today().year:
            raise SourceError('Novel-food year sections are absent or invalid')
        latest = max(sections)
        selected = set(range(latest-self.latest_years+1,latest+1))
        if not selected <= set(sections):
            raise SourceError('Novel-food selected year sections are incomplete')
        candidates = {}
        for year in selected:
            lists = sections[year].find_all('ul')
            if len(lists) != 1 or not lists[0].find_all('li',recursive=False):
                raise SourceError('Novel-food selected year list is missing or ambiguous')
            if any(node.name and node.name != 'li' for node in lists[0].children):
                raise SourceError('Novel-food year list contains an unsupported row')
            for li in lists[0].find_all('li',recursive=False):
                row = self._record(li)
                if int(row['act_id'].split('/')[0]) != year:
                    raise SourceError('Novel-food act is in the wrong year section')
                candidates[id(li)] = row
        # Corrections also appear in the page's introductory list. Include all
        # selected act-year links, not just the annual update containers.
        anchors = main.select('a[href]')
        if len(anchors) > 2000:
            raise SourceError('Novel-food page exceeds link bounds')
        for link in anchors:
            label_year = re.search(r'\(EU\) (20[0-9]{2})/',text(link))
            url_year = re.search(r'/eli/[^/]+/(20[0-9]{2})/',link['href'])
            if not any(m and int(m.group(1)) in selected for m in (label_year,url_year)):
                continue
            li = link.find_parent('li')
            if li is None:
                raise SourceError('Novel-food act lacks a complete list row')
            if id(li) not in candidates:
                candidates[id(li)] = self._record(li)
        records = {}
        for row in candidates.values():
            if row['key'] in records and row != records[row['key']]:
                raise SourceError('Novel-food act has conflicting duplicate rows')
            records[row['key']] = row
        if len(records) > self.max_records:
            raise SourceError('Novel-food selection exceeds max_records')
        return sorted(records.values(),key=lambda row:row['key'])

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        stored = ((previous or {}).get('source_state') or {}).get('records',{})
        if stored.get('scope') == self.scope:
            old_years = [int(value['row']['act_id'].split('/')[0]) for value in stored.get('rows',{}).values()]
            new_years = [int(value['row']['act_id'].split('/')[0]) for value in self._next['rows'].values()]
            if old_years and new_years and max(new_years) < max(old_years):
                raise SourceError('Novel-food latest published year regressed; previous state preserved')
        return items

    def _item(self, row, event, details, suppress):
        item = super()._item(row,event,details,suppress)
        fields = row['fields']
        info = ('Nyobservert oppføring i EU-listen' if event == 'added' else 'Endret oppføring i EU-listen',
                f"Oppført dato for grunnrettsakten: {fields['listed_act_date']}", fields['description'][:900])
        if fields['corrigendum_date']:
            info += (f"Rettingsdato i lenken: {fields['corrigendum_date']}",)
        if fields['type_mismatch']:
            info += ('Liste og lenke oppgir ulik dokumenttype; kontroller originalen',)
        if event == 'changed':
            info += tuple(d[:800] for d in details[1:])
        info += ('Listeopplysninger; kontroller rettsakten for fulle vilkår og eventuell norsk gjennomføring',)
        return replace(item,alert_details=info)
