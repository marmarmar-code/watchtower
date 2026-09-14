"""Explicit consultation metadata and complete published-response lists."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .changes import SnapshotSource, digest, integer
from .common import SourceError

HOST = 'www.regjeringen.no'
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def clean(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def identity(url):
    p = urlsplit(url)
    match = re.fullmatch(r'/no/dokumenter/[^/]+/id([0-9]{5,9})/', p.path)
    if p.scheme != 'https' or p.netloc != HOST or not match or p.fragment:
        raise SourceError('Consultation URL is outside the official document contract')
    return match[1]


def only(soup, selector, label):
    values = soup.select(selector)
    if len(values) != 1:
        raise SourceError(f'Consultation {label} is missing or ambiguous')
    return values[0]


def page_identity(soup, expected):
    url = only(soup, 'meta[property="og:url"]', 'canonical metadata').get('content', '')
    if identity(url) != expected or urlsplit(url).query:
        raise SourceError('Consultation canonical identity changed')
    heading = clean(only(soup, 'main header.article-header > h1', 'heading'))
    if not heading or len(heading) > 3000:
        raise SourceError('Consultation heading is invalid')
    return url, heading


def calendar_date(value):
    try:
        if not re.fullmatch(r'[0-9]{2}\.[0-9]{2}\.[0-9]{4}', value):
            raise ValueError
        return datetime.strptime(value, '%d.%m.%Y').date().isoformat()
    except ValueError:
        raise SourceError('Consultation calendar date is invalid') from None


def metadata_record(soup, ident):
    url, heading = page_identity(soup, ident)
    block = only(soup, 'main .horing-meta', 'status block')
    fields = {}
    for strong in block.select('p > strong'):
        name = clean(strong)
        if name not in ('Status:', 'Høringsfrist:'):
            continue
        if name in fields:
            raise SourceError('Consultation metadata repeats a field')
        fields[name] = clean(strong.parent).removeprefix(name).strip()
    if set(fields) != {'Status:', 'Høringsfrist:'} or not fields['Status:'] or len(fields['Status:']) > 200:
        raise SourceError('Consultation status or deadline is absent')
    return {'key': ident, 'title': heading, 'url': url,
            'fields': {'title': heading, 'status': fields['Status:'], 'deadline': calendar_date(fields['Høringsfrist:'])}}


def response_list(soup, ident, limit):
    url, _ = page_identity(soup, ident)
    form = only(soup, 'form#searchPageNavigationSearchForm', 'response search form')
    if identity(urljoin(url, form.get('action', ''))) != ident:
        raise SourceError('Consultation response form changed case')
    search = only(form, 'input[name="consterm"]', 'response search')
    if search.get('value', '').strip():
        raise SourceError('Consultation response list is filtered by search text')
    selected = only(form, 'select[name="horingssvar_filter"] option[selected]', 'response category selection')
    selection = urlsplit(urljoin(url, selected.get('value', '')))
    if identity(urljoin(url, selected.get('value', ''))) != ident or 'horingssvar_filter' in parse_qs(selection.query):
        raise SourceError('Consultation response list is filtered by category')
    count = clean(only(form, '.results .count', 'response total'))
    match = re.fullmatch(r'Søket ditt gav ([0-9 ]+) treff\.', count)
    if not match:
        raise SourceError('Consultation response total is unrecognized')
    total = int(match[1].replace(' ', ''))
    if total > limit:
        raise SourceError('Consultation response list exceeds max_list_entries')
    listing = only(form, 'ul[data-horingssvar-list]', 'response list')
    nodes = listing.find_all('li', recursive=False)
    if len(nodes) != total:
        raise SourceError('Consultation response list is incomplete or paginated')
    records, seen = [], set()
    for node in nodes:
        uid, category = node.get('id', ''), node.get('data-instans', '')
        a = only(node, 'a[href]', 'response link')
        href = urljoin(url, a['href']); query = parse_qs(urlsplit(href).query)
        if not UUID.fullmatch(uid) or uid in seen or identity(href) != ident or query != {'uid': [uid]}:
            raise SourceError('Consultation response identity is invalid or duplicated')
        name = clean(a)
        if not name or len(name) > 1000 or not category or len(category) > 200:
            raise SourceError('Consultation response label or category is invalid')
        seen.add(uid); records.append({'uid': uid, 'name': name, 'category': category, 'url': href})
    return sorted(records, key=lambda row: row['uid'])


def response_record(soup, ident, selected):
    base, heading = page_identity(soup, ident)
    expected = re.sub(r'\s*\(uten merknader\)$', '', selected['name']).strip()
    if heading not in ('Høringssvar fra ' + expected, 'Innspill fra ' + expected):
        raise SourceError('Consultation response heading differs from the selected respondent')
    block = only(soup, 'main .hearing-answer', 'response detail')
    stamp = clean(only(block, '.hearing-answer-timestamp', 'response date'))
    if not stamp.startswith('Dato:'):
        raise SourceError('Consultation response date lacks its label')
    published = calendar_date(stamp.removeprefix('Dato:').strip())
    types = [s for s in block.select('div > strong') if clean(s) == 'Svartype:']
    if len(types) != 1:
        raise SourceError('Consultation response type is missing or ambiguous')
    kind = clean(types[0].parent).removeprefix('Svartype:').strip()
    if kind not in ('Med merknad', 'Uten merknad'):
        raise SourceError('Consultation response type is unrecognized')
    body = clean(only(block, '.article-body', 'response body'))
    if len(body) > 200000:
        raise SourceError('Consultation response body exceeds text bounds')
    attachments = []
    for a in block.select('ul.link-list > li > a[href]'):
        href = urljoin(base, a['href']); parsed = urlsplit(href)
        m = re.fullmatch(r'/no/dokumenter/[^/]+/id' + re.escape(ident) + r'/Download/', parsed.path)
        q = parse_qs(parsed.query)
        if parsed.scheme != 'https' or parsed.netloc != HOST or not m or parsed.fragment or set(q) != {'vedleggId'} or len(q['vedleggId']) != 1 or not UUID.fullmatch(q['vedleggId'][0]):
            raise SourceError('Consultation attachment link is outside the verified contract')
        attachments.append({'id': q['vedleggId'][0], 'title': clean(a)})
    if len(attachments) > 50 or len({a['id'] for a in attachments}) != len(attachments):
        raise SourceError('Consultation attachment identities repeat or exceed bounds')
    if kind == 'Med merknad' and not body and not attachments:
        raise SourceError('Consultation response with comments has no text or attachments')
    return {'key': ident + ':' + selected['uid'], 'title': heading, 'url': base + '?' + urlencode({'uid': selected['uid']}),
            'published': published, 'fields': {'respondent': expected, 'category': selected['category'],
            'response_type': kind, 'date': published, 'body_sha256': digest(body),
            'attachments': sorted(attachments, key=lambda a: a['id'])}}


class ConsultationsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if not 1 <= len(config.urls) <= 5:
            raise ValueError('consultations requires one to five explicit document URLs')
        ids = [identity(u) for u in config.urls]
        if len(set(ids)) != len(ids) or any(urlsplit(u).query for u in config.urls):
            raise ValueError('consultation URLs must be distinct cases without query parameters')
        self.profile = config.options.get('profile', 'metadata')
        if self.profile not in ('metadata', 'responses'):
            raise ValueError('consultation profile must be metadata or responses')
        if self.complete or 'removed' in self.events:
            raise ValueError('Consultation observations cannot establish removal')
        self.exclude_private = config.options.get('exclude_private_people', True)
        if type(self.exclude_private) is not bool:
            raise ValueError('exclude_private_people must be boolean')
        self.list_limit = integer(config.options.get('max_list_entries', 500), 'max_list_entries', 1, 2000)
        self.detail_limit = integer(config.options.get('max_responses', 50), 'max_responses', 1, 100)
        self.field_labels = {'title': 'Tittel', 'status': 'Oppgitt status', 'deadline': 'Høringsfrist',
            'respondent': 'Svar fra', 'category': 'Instanskategori', 'response_type': 'Svartype', 'date': 'Oppgitt svardato', **self.field_labels}

    def _page(self, url, ident):
        for _ in range(4):
            if identity(url) != ident:
                raise SourceError('Consultation redirect changed the case')
            response = self.get(url, stream=True, allow_redirects=False,
                                accepted_statuses=(301, 302, 303, 307, 308))
            try:
                if response.status_code != 200:
                    destination = urljoin(url, response.headers.get('Location', ''))
                    if not response.headers.get('Location') or parse_qs(urlsplit(destination).query) != parse_qs(urlsplit(url).query):
                        raise SourceError('Consultation redirect changed the request selection')
                    url = destination
                    continue
                chunks, length = [], 0
                for chunk in response.iter_content(64 * 1024):
                    length += len(chunk)
                    if length > self.max_bytes:
                        raise SourceError('Consultation page exceeds max_bytes')
                    chunks.append(chunk)
                return BeautifulSoup(b''.join(chunks), 'html.parser')
            finally:
                response.close()
        raise SourceError('Consultation returned too many redirects')

    def read_records(self):
        rows = []
        for url in self.config.urls:
            ident = identity(url)
            soup = self._page(url, ident)
            if self.profile == 'metadata':
                rows.append(metadata_record(soup, ident))
                continue
            base, _ = page_identity(soup, ident)
            index_url = base + '?showSvar=true'
            index = self._page(index_url, ident)
            selected = response_list(index, ident, self.list_limit)
            filtered = [r for r in selected if not self.exclude_private or r['category'] != 'Privatperson']
            if len(rows) + len(filtered) > min(self.detail_limit, self.max_records):
                raise SourceError('Consultation selected responses exceed max_responses or max_records')
            for response in filtered:
                rows.append(response_record(self._page(response['url'], ident), ident, response))
            if response_list(self._page(index_url, ident), ident, self.list_limit) != selected:
                raise SourceError('Consultation response list changed during detail reads')
        return sorted(rows, key=lambda row: row['key'])

    def describe_change(self, name, before, after):
        if name == 'body_sha256':
            return 'Publisert svartekst er endret'
        if name == 'attachments':
            return 'Publiserte vedleggslenker eller vedleggstitler er endret'
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        if self.profile == 'metadata':
            title = 'Nyobservert høring' if event == 'added' else 'Endret høringsfrist eller saksopplysning'
            extra = ('Fristen er en dato uten oppgitt klokkeslett. Høringsstatus er ikke et vedtaksutfall',)
        else:
            title = 'Nyobservert publisert svar' if event == 'added' else 'Endret publisert svar'
            if event == 'added':
                details = ('', 'Svartype: ' + row['fields']['response_type'], 'Oppgitt dato: ' + row['fields']['date'],
                           f'Vedleggslenker: {len(row["fields"]["attachments"])}')
            extra = ('Kun publisert HTML-tekst og vedleggslenker følges; vedleggenes filinnhold og senere vedtaksutfall er ikke kontrollert',)
        return replace(item, alert_details=(title, *details[1:], *extra))
