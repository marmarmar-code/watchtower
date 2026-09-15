"""Published one-stop-shop decision metadata with explicit authority roles."""
from datetime import date, datetime, timezone
import re
from urllib.parse import urlencode, urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from .changes import SnapshotSource, document, integer
from .common import SourceError

PAGE = 'https://www.edpb.europa.eu/registers/register-of-final-one-stop-shop-decisions_en'
CARD = 'details.foss-decision-teaser__details'
ID = re.compile(r'EDPBI:([A-Z]{2}):OSS:D:(20[0-9]{2}):([1-9][0-9]*)')


def today():
    return datetime.now(timezone.utc).date()


def text(node, optional=False):
    value = ' '.join(node.get_text(' ', strip=True).split()) if node else ''
    if (not value and not optional) or len(value) > 4000 or '\ufffd' in value:
        raise SourceError('GDPR decision field is absent or invalid')
    return value or None


class GdprDecisionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('gdpr_decisions accepts only the official register')
        if self.complete or 'removed' in self.events:
            raise ValueError('Register absence cannot establish withdrawal of a decision')
        self.from_year = integer(config.options.get('from_year', 2026), 'from_year', 2018, 2099)
        self.role = config.options.get('authority_role', 'lead')
        if self.role not in ('lead', 'concerned'):
            raise ValueError('authority_role must be lead or concerned')
        self.max_pages = integer(config.options.get('max_pages', 10), 'max_pages', 1, 50)
        self.field_labels = {'decision_date': 'Vedtaksdato', 'lead_authority': 'Ledende myndighet',
                             'concerned_authorities': 'Berørte myndigheter', 'legal_reference': 'Oppgitt rettsgrunnlag',
                             'topics': 'Emner', 'outcome': 'Kildens utfallsmerkelapp', 'documents': 'Vedtaksdokumenter', **self.field_labels}

    def _url(self, page):
        if self.from_year > today().year:
            raise SourceError('GDPR start year is in the future')
        years = str(self.from_year) if self.from_year == today().year else f'{self.from_year}-{today().year}'
        return PAGE + '?' + urlencode({'date': years, ('lsa' if self.role == 'lead' else 'csa')+'[28]': '28', 'page': page})

    def _page(self, raw, page):
        soup = BeautifulSoup(raw, 'html.parser')
        if [text(x) for x in soup.select('h1')] != ['Register of final one-stop-shop decisions']:
            raise SourceError('GDPR register heading changed')
        role = 'lsa' if self.role == 'lead' else 'csa'
        wanted = parse_qs(urlparse(self._url(page)).query)
        form_nodes = soup.select('input[name="date"]')
        if len(form_nodes) != 1 or form_nodes[0].get('value') != wanted['date'][0]:
            raise SourceError('GDPR register did not retain its date filter')
        checked = soup.select('input[type="checkbox"][checked]')
        if len(checked) != 1 or checked[0].get('name') != role+'[28]' or checked[0].get('value') != '28':
            raise SourceError('GDPR register did not retain its authority filter')
        labels = soup.select('label[for="'+checked[0].get('id', '')+'"]')
        if len(labels) != 1 or text(labels[0]) != 'Norway':
            raise SourceError('GDPR authority selection changed identity')
        totals = [re.fullmatch(r'([0-9]+) items?', text(n, True) or '') for n in soup.select('[role="status"]')]
        totals = [int(m[1]) for m in totals if m]
        if len(totals) != 1 or not 1 <= totals[0] <= self.max_records:
            raise SourceError('GDPR advertised total is absent, empty or exceeds max_records')
        total = totals[0]
        pages = (total+10)//11
        if pages > self.max_pages or page >= pages:
            raise SourceError('GDPR pagination exceeds bounds')
        cards = soup.select(CARD)
        if len(cards) != min(11, total-page*11):
            raise SourceError('GDPR page is incomplete')
        # Validate every advertised pagination target rather than following arbitrary links.
        pager_links = soup.select('nav.pager a[href]')
        advertised = set()
        for a in pager_links:
            u = urlparse(urljoin(PAGE, a['href']))
            q = parse_qs(u.query)
            if (u.scheme, u.netloc, u.path, u.fragment) != ('https', 'www.edpb.europa.eu', urlparse(PAGE).path, '') or set(q) != set(wanted):
                raise SourceError('GDPR pagination left the selected register')
            if q.get('date') != wanted['date'] or q.get(role+'[28]') != ['28'] or len(q.get('page', [])) != 1 or not q['page'][0].isdigit():
                raise SourceError('GDPR pagination changed filters')
            n = int(q['page'][0])
            if not 0 <= n < pages:
                raise SourceError('GDPR pagination target exceeds advertised total')
            advertised.add(n)
        if page+1 < pages and page+1 not in advertised:
            raise SourceError('GDPR next page is not advertised')
        rows = []
        for card in cards:
            ids = card.select('.foss-decision-foss-decision-teaser__id')
            ident = text(ids[0]) if len(ids) == 1 else ''
            m = ID.fullmatch(ident)
            times = card.select('time[datetime]')
            if not m or len(times) != 1:
                raise SourceError('GDPR decision identity or date is ambiguous')
            literal = times[0]['datetime']
            try:
                if not re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}T12:00:00Z', literal):
                    raise ValueError
                day = datetime.strptime(literal, '%Y-%m-%dT%H:%M:%SZ').date()
            except ValueError as exc:
                raise SourceError('GDPR decision date changed format') from exc
            if not date(self.from_year, 1, 1) <= day <= today() or int(m[2]) != day.year:
                raise SourceError('GDPR decision is outside the selected date range')
            lead_nodes = card.select('.foss-decision-foss-decision-teaser__lead-sa .member-country-token__code')
            lead = text(lead_nodes[0]) if len(lead_nodes) == 1 else ''
            if lead != m[1].lower():
                raise SourceError('GDPR lead authority disagrees with decision identity')
            lists = card.select('dl.foss-decision-teaser__properties-list')
            if len(lists) != 1:
                raise SourceError('GDPR decision properties are missing')
            parts = lists[0].find_all(['dt', 'dd'], recursive=False)
            expected = ['Main legal reference', 'CSA', 'Relevant topics', 'Outcome']
            groups = {}
            current = None
            for part in parts:
                if part.name == 'dt':
                    current = text(part)
                    if current in groups:
                        raise SourceError('GDPR decision property label repeats')
                    groups[current] = []
                elif current is None:
                    raise SourceError('GDPR decision property lacks a label')
                else:
                    groups[current].append(part)
            if list(groups) != expected or not groups['Main legal reference'] or any(len(groups[k]) != 1 for k in expected[1:]):
                raise SourceError('GDPR decision property labels changed')
            legal = [text(n, True) for n in groups['Main legal reference']]
            concerned = [text(n) for n in groups['CSA'][0].select('.member-country-token__code')]
            if any(not re.fullmatch('[a-z]{2}', n) for n in concerned) or len(set(concerned)) != len(concerned):
                raise SourceError('GDPR concerned authorities are invalid or repeated')
            if self.role == 'lead' and lead != 'no' or self.role == 'concerned' and 'no' not in concerned:
                raise SourceError('GDPR decision is outside the authority filter')
            topics = [text(n) for n in groups['Relevant topics'][0].select('li')]
            if len(set(topics)) != len(topics):
                raise SourceError('GDPR decision topic is repeated')
            documents = set()
            for a in card.select('a[type="application/pdf"][href]'):
                u = urlparse(urljoin(PAGE, a['href']))
                if u.scheme != 'https' or u.netloc != 'www.edpb.europa.eu' or not re.fullmatch(r'/system/files/20[0-9]{2}-[0-9]{2}/[^/]+\.pdf', u.path) or u.query or u.fragment:
                    raise SourceError('GDPR document URL left the official file path')
                documents.add(u.geturl())
            if not documents:
                raise SourceError('GDPR decision has no published document link')
            fields = {'decision_id': ident, 'decision_date': day.isoformat(), 'lead_authority': lead,
                      'concerned_authorities': sorted(concerned), 'legal_reference': legal,
                      'topics': sorted(topics), 'outcome': text(groups['Outcome'][0], True), 'documents': sorted(documents)}
            rows.append({'key': ident, 'title': ident+' – '+(fields['outcome'] or 'Ingen utfallsmerkelapp'),
                         'url': sorted(documents)[0], 'published': None, 'fields': fields})
        return total, rows

    def _read(self):
        total, rows = self._page(document(self, self._url(0)), 0)
        for page in range(1, (total+10)//11):
            count, more = self._page(document(self, self._url(page)), page)
            if count != total:
                raise SourceError('GDPR total changed during pagination')
            rows.extend(more)
        if len(rows) != total or len({r['key'] for r in rows}) != total:
            raise SourceError('GDPR paginated identities are incomplete or repeated')
        return sorted(rows, key=lambda r:r['key'])

    def read_records(self):
        first = self._read()
        if self._read() != first:
            raise SourceError('GDPR complete selection changed between reads')
        return first
