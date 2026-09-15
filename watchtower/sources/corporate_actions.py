"""Bounded, source-labelled derivative corporate-action notice revisions."""
from dataclasses import replace
from datetime import datetime
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .changes import SnapshotSource, integer
from .common import SourceError

URL = 'https://live.euronext.com/en/resources/notices-corporate-actions/derivatives-corporate-actions'
HEADERS = ['Notice Number', 'Issued', 'Effective', 'Event Type', 'Symbol', '', '']
MONTHS = {m: i+1 for i, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}


def text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def calendar(value, attachment=False):
    try:
        if attachment:
            if not re.fullmatch(r'\d{2}/\d{2}/\d{4}', value): raise ValueError()
            return datetime.strptime(value, '%d/%m/%Y').date().isoformat()
        m = re.fullmatch(r'(\d{2}) ([A-Za-z]{3}) (\d{4})', value)
        if not m: raise ValueError()
        return datetime(int(m[3]), MONTHS[m[2]], int(m[1])).date().isoformat()
    except (ValueError, KeyError) as exc:
        raise SourceError('Corporate-action calendar date is invalid') from exc


class CorporateActionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (URL,)) or config.options.get('scope', 'derivatives') != 'derivatives':
            raise ValueError('corporate_actions accepts only the official derivative notice index')
        if self.complete or 'removed' in self.events:
            raise ValueError('A bounded notice window cannot establish cancellation')
        if type(config.options.get('page_size', 50)) is not int or config.options.get('page_size', 50) != 50:
            raise ValueError('The verified page_size is 50')
        self.max_pages = integer(config.options.get('max_pages', 1), 'max_pages', 1, 5)
        self.field_labels = {'notice_number': 'Meldingsnummer', 'notice_date': 'Utstedt',
                             'effect_text': 'Oppgitt virkning', 'effect_date': 'Virkningsdato',
                             'action': 'Hendelsestype', 'instrument': 'Instrument',
                             'attachments': 'Vedleggsmetadata', **self.field_labels}

    def _page(self, page):
        url = URL+'?'+urlencode({'alias': 1, 'pageNum': page, 'pageSize': 50})
        r = self.get(url, stream=True, allow_redirects=False, accepted_statuses=(301,302,303,307,308))
        try:
            if r.status_code != 200: raise SourceError('Corporate-action index redirected unexpectedly')
            chunks = []; size = 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes: raise SourceError('Corporate-action index exceeds max_bytes')
                chunks.append(chunk)
            raw = b''.join(chunks)
        finally: r.close()
        if not raw.rstrip().lower().endswith(b'</html>'):
            raise SourceError('Corporate-action page is incomplete')
        try: soup = BeautifulSoup(raw.decode('utf-8'), 'html.parser')
        except UnicodeDecodeError as exc: raise SourceError('Corporate-action encoding changed') from exc
        forms = soup.select('input[name="form_id"][value="AwlNoticesPublicDerivativesFiltersForm_form"]')
        tables = [t for t in soup.find_all('table') if [text(h) for h in t.find_all('th')] == HEADERS]
        if len(forms) != 1 or len(tables) != 1:
            raise SourceError('Derivative notice identity or headers changed')
        table = tables[0]
        bodies = table.find_all('tbody', recursive=False)
        if not bodies or len(bodies) > 50: raise SourceError('Corporate-action page row count is empty or excessive')
        rows = []; keys = set()
        for body in bodies:
            m = re.fullmatch(r'row_ecap_([1-9][0-9]{0,15})', body.get('id', ''))
            if not m or m[1] in keys: raise SourceError('Corporate-action identity is invalid or duplicated')
            ident = m[1]; keys.add(ident)
            trs = body.find_all('tr', recursive=False)
            if len(trs) != 4 or trs[0].get('class') != ['row_'+ident]:
                raise SourceError('Corporate-action row structure changed')
            cells = trs[0].find_all('td', recursive=False)
            if len(cells) != 7 or any(c.get('colspan') or c.get('rowspan') for c in cells):
                raise SourceError('Corporate-action column structure changed')
            for cell, cls in zip(cells[:5], ['noticenumber','noticedate','effect','noticename','instruments']):
                if cls not in cell.get('class', []): raise SourceError('Corporate-action field order changed')
            for button in trs[0].find_all('button'): button.decompose()
            vals = [text(c) for c in cells[:5]]
            if any(not v or len(v)>2000 for v in vals): raise SourceError('Corporate-action metadata is empty or excessive')
            day = calendar(vals[1])
            effective = calendar(vals[2]) if re.fullmatch(r'\d{2} [A-Za-z]{3} \d{4}', vals[2]) else None
            for suffix in ['abstract', 'detail', 'download']:
                if len(body.find_all('div', id='notice-'+suffix+'-div-'+ident)) != 1:
                    raise SourceError('Corporate-action child identity is absent or duplicated')
            download = body.find(id='notice-download-div-'+ident)
            files = download.select('dl.file-component'); attachments = []; seen = set()
            if not files or len(files)>100: raise SourceError('Corporate-action attachment list is empty or excessive')
            for file in files:
                dates = file.select('ul.file-component__header li')
                links = file.find_all('a', href=True)
                if len(dates)!=2 or text(dates[1])!='PDF' or len(links)!=1:
                    raise SourceError('Corporate-action PDF metadata changed')
                link = urljoin(URL, links[0]['href'].strip()); u=urlsplit(link)
                q = parse_qs(u.query, keep_blank_values=True)
                if (u.scheme!='https' or u.netloc!='live.euronext.com' or u.path!='/en/listview/notice-download'
                        or u.fragment or set(q)!={'id','type','attachmentId'} or q['id']!=[ident] or q['type']!=['PDF']
                        or len(q['attachmentId'])!=1 or not re.fullmatch(r'[1-9][0-9]{0,15}',q['attachmentId'][0])):
                    raise SourceError('Corporate-action PDF link violates the official notice identity')
                aid=q['attachmentId'][0]; name=text(links[0])
                if aid in seen or not name.lower().endswith('.pdf') or len(name)>500:
                    raise SourceError('Corporate-action attachment identity or filename is invalid')
                seen.add(aid)
                stable='https://live.euronext.com/en/listview/notice-download?'+urlencode({'id':ident,'type':'PDF','attachmentId':aid})
                attachments.append({'id':aid,'date':calendar(text(dates[0]),True),'filename':name,'url':stable})
            if len(download.find_all('a', href=True))!=len(attachments):
                raise SourceError('Corporate-action attachment list contains unrecognized links')
            rows.append({'key':'euronext:'+ident,'title':vals[0], 'url':URL+'#row_ecap_'+ident,'published':day,
                         'fields':{'notice_number':vals[0],'notice_date':day,'effect_text':vals[2], 'effect_date':effective,
                                   'action':vals[3],'instrument':vals[4],'attachments':sorted(attachments,key=lambda a:a['id'])}})
        if len(table.select('td.noticenumber'))!=len(rows): raise SourceError('Corporate-action rows are not fully accounted for')
        pagers=soup.select('ul.pager.pagination')
        if len(pagers)!=1: raise SourceError('Corporate-action pagination is missing or ambiguous')
        current=[text(a) for a in pagers[0].select('li.active a')]
        if current!=[str(page)]: raise SourceError('Corporate-action returned the wrong page')
        targets=set()
        for a in pagers[0].find_all('a',href=True):
            u=urlsplit(urljoin(URL,a['href']));q=parse_qs(u.query,keep_blank_values=True)
            if (u.scheme!='https' or u.netloc!='live.euronext.com' or u.path!=urlsplit(URL).path or u.fragment
                    or set(q)!={'alias','pageNum','pageSize'} or q['alias']!=['1'] or q['pageSize']!=['50']
                    or len(q['pageNum'])!=1 or not re.fullmatch(r'[1-9][0-9]{0,7}',q['pageNum'][0])):
                raise SourceError('Corporate-action pagination destination changed')
            target=int(q['pageNum'][0]);label=text(a)
            if label.isdigit() and int(label)!=target: raise SourceError('Corporate-action page label disagrees with destination')
            if label=='Next' and target!=page+1: raise SourceError('Corporate-action next page skips records')
            targets.add(target)
        more=page+1 in targets
        if len(rows)<50 and more: raise SourceError('Corporate-action nonterminal page is short')
        return rows, more

    def _window(self):
        rows=[];seen=set()
        for page in range(1,self.max_pages+1):
            found,more=self._page(page)
            for row in found:
                if row['key'] in seen: raise SourceError('Corporate-action identity repeats across pages')
                seen.add(row['key']);rows.append(row)
            if len(rows)>self.max_records: raise SourceError('Corporate-action window exceeds max_records')
            if not more: break
        return sorted(rows,key=lambda r:r['key'])

    def read_records(self):
        first,second=self._window(),self._window()
        if first!=second: raise SourceError('Corporate-action window changed between complete reads')
        return first

    def _item(self,row,event,details,suppress):
        item=super()._item(row,event,details,suppress)
        return replace(item,alert_details=tuple(d[:900] for d in details)+(
            'Børsens oppføring gjelder derivater. Utstedelse, virkning og vedleggsdato er separate kildefelt; dokumentinnhold er ikke lest.',))
