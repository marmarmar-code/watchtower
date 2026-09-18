"""Official registered-capital increases and court reconstruction openings.

Capital is the resulting registered nominal capital, never financing proceeds.
Announcement identity is the provider's immutable notice ID, not the company ID.
"""
from dataclasses import replace
from decimal import Decimal
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .bankruptcy_notices import (BankruptcyNoticesSource, DATE, ORIGIN, PATH,
                                 PAGE_URL, SEARCH_URL, bounded, day, match, text)
from .changes import SnapshotSource, integer, strings
from .common import SourceError


class FinancialNoticesSource(BankruptcyNoticesSource):
    """Reuse bounded transport, double complete reads and rolling-window guard."""
    category = {}
    category_label = ''
    default_window = 3
    maximum_window = 7
    capital = False

    def __init__(self, config, *args, **kwargs):
        SnapshotSource.__init__(self, config, *args, **kwargs)
        if config.urls not in ((), (PAGE_URL,)):
            raise ValueError('Financial notices accept only the official announcement search')
        if self.complete or 'removed' in self.events:
            raise ValueError('Announcement absence does not establish withdrawal or case closure')
        self.window_days = integer(config.options.get('window_days', self.default_window),
                                   'window_days', 1, self.maximum_window)
        self.max_results = integer(config.options.get('max_results', 1000), 'max_results', 1, 4999)
        self.max_details = integer(config.options.get('max_details', 100), 'max_details', 1, 500)
        self.orgnrs = strings(config.options['orgnrs'], 'orgnrs') if 'orgnrs' in config.options else ()
        if len(self.orgnrs) > 100 or any(not re.fullmatch(r'\d{9}', x) for x in self.orgnrs):
            raise ValueError('orgnrs must be up to 100 exact nine-digit organisation numbers')
        self.field_labels = {
            'company': 'Foretak', 'orgnr': 'Organisasjonsnummer', 'announced_date': 'Kunngjøringsdato',
            'announcement_type': 'Kunngjøringstype', 'currency': 'Valuta',
            'registered_capital': 'Registrert aksjekapital', 'legal_form': 'Organisasjonsform',
            'notice_type': 'Dokumenttype', 'notice_note': 'Kildens merknad',
            'decision_date': 'Kjennelse avsagt', 'court': 'Oppført domstol', 'case_number': 'Saksnummer',
            'claim_deadline': 'Frist for å melde krav', 'fristdag': 'Fristdagen (eget kildefelt)',
            'reconstructor': 'Oppført rekonstruktør', 'meeting_notice': 'Opplysning om fordringshavermøte',
            'industry': 'Bransje/stilling', **self.field_labels,
        }

    def _search(self, start, end):
        fmt = lambda value: value.strftime('%d.%m.%Y')
        soup = self._page(SEARCH_URL + '?' + urlencode({
            'datoFra': fmt(start), 'datoTil': fmt(end), **self.category}))
        echoes, counts = {}, []
        for tr in soup.find_all('tr'):
            cells = tr.find_all('td', recursive=False)
            if not cells:
                continue
            label = text(cells[0])
            if label in {'Dato', 'Sted', 'Kunngjøringstype'} and len(cells) in (2, 3):
                if label in echoes:
                    raise SourceError('Financial notice search repeats filter metadata')
                echoes[label] = text(cells[-1])
            if label == 'Antall treff':
                if len(cells) != 2 or not re.fullmatch(r'\d+', text(cells[1])):
                    raise SourceError('Financial notice result count is malformed')
                counts.append(int(text(cells[1])))
        date_echo = fmt(start) if start == end else fmt(start) + ' til ' + fmt(end)
        if echoes != {'Dato': date_echo, 'Sted': 'Hele landet', 'Kunngjøringstype': self.category_label}:
            raise SourceError('Financial notice date or type filter was not applied')
        records, seen = [], set()
        empty_markers = [node for node in soup.find_all('p') if text(node) == 'Ingen kunngjøringer funnet.']
        for anchor in soup.find_all('a', href=True):
            if 'hent_en.jsp' not in anchor['href']:
                continue
            parsed = urlparse(urljoin(ORIGIN + PATH, anchor['href']))
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (parsed.scheme != 'https' or parsed.netloc != 'w2.brreg.no' or parsed.path != PATH + 'hent_en.jsp'
                    or parsed.fragment or set(query) != {'kid', 'sokeverdi', 'spraak'}
                    or any(len(v) != 1 for v in query.values()) or query['spraak'] != ['nb']):
                raise SourceError('Financial notice detail link contract changed')
            tr = anchor.find_parent('tr')
            cells = tr.find_all('td', recursive=False) if tr else []
            width, link_column = ((7 if start == end else 8), 1) if self.capital else (9, 7)
            if len(cells) != width or anchor not in cells[link_column].find_all('a'):
                raise SourceError('Financial notice result row structure changed')
            kid, org = query['kid'][0], query['sokeverdi'][0]
            if not re.fullmatch(r'\d{14}', kid) or not re.fullmatch(r'(?:\d{6}|\d{9})', org) or kid in seen:
                raise SourceError('Financial notice identity is invalid or duplicated')
            seen.add(kid)
            if text(cells[3]).replace(' ', '') != org:
                raise SourceError('Financial notice organisation identity disagrees')
            published = None
            if not self.capital:
                published = day(text(cells[5]))
                if not start <= published <= end:
                    raise SourceError('Financial notice falls outside requested dates')
            elif text(cells[5]):
                # Capital listings currently omit dates; a supplied date must be valid.
                published = day(text(cells[5]))
                if not start <= published <= end:
                    raise SourceError('Financial notice falls outside requested dates')
            records.append({
                'kid': kid, 'orgnr': org, 'date': published.isoformat() if published else None,
                'start': start.isoformat(), 'end': end.isoformat(),
                'kind': self.category_label if self.capital else bounded(text(anchor)),
                'index_name': bounded(text(cells[1])),
                'url': ORIGIN + PATH + 'hent_en.jsp?' + urlencode({'kid': kid, 'sokeverdi': org, 'spraak': 'nb'}),
            })
        if records:
            if empty_markers or counts != [len(records)] or len(records) > self.max_results or len(records) >= 5000:
                raise SourceError('Financial notice count, completeness or result bound is inconsistent')
        elif len(empty_markers) != 1 or counts not in ([], [0]):
            raise SourceError('Empty financial result lacks the observed no-results message')
        return sorted(records, key=lambda row: row['kid'])

    def _sweep(self, start, end):
        index = self._search(start, end)
        # An unrecognised phase must be inspected, not silently excluded.
        if any(row['kind'] != self.selected_kind for row in index):
            raise SourceError('Financial announcement type changed')
        selected = [row for row in index if len(row['orgnr']) == 9
                    and (not self.orgnrs or row['orgnr'] in self.orgnrs)]
        if len(selected) > min(self.max_details, self.max_records):
            raise SourceError('Financial notice selection exceeds the detail bound')
        return index, [self._detail(row) for row in selected]

    def _body(self, index, heading, required, registry):
        soup = self._page(index['url'])
        allowed_headings = (heading,) if isinstance(heading, str) else heading
        headings = [node for node in soup.find_all('h3') if text(node) in allowed_headings]
        if len(headings) != 1 or headings[0].parent.name != 'body':
            raise SourceError('Financial notice detail identity changed')
        body = headings[0].parent
        content = text(body)
        if len(content) > 30000:
            raise SourceError('Financial notice detail is excessive')
        labels = {}
        for tr in body.find_all('tr'):
            cells = tr.find_all('td', recursive=False)
            if len(cells) != 2:
                raise SourceError('Financial notice labelled row changed')
            label = text(cells[0]).replace(' :', ':')
            if label in required:
                if label in labels:
                    raise SourceError('Financial notice repeats a required field')
                labels[label] = bounded(text(cells[1]))
        if not required <= set(labels) or labels['Organisasjonsnummer:'].replace(' ', '') != index['orgnr']:
            raise SourceError('Financial notice organisation or required fields disagree')
        published = day(match(re.escape(registry) + ' (' + DATE + ')$', content, 'publication date')).isoformat()
        if not index['start'] <= published <= index['end'] or index['date'] and published != index['date']:
            raise SourceError('Financial notice list and detail publication dates disagree')
        return labels, content, published, text(headings[0])

    def _item(self, row, event, details, suppress):
        item = SnapshotSource._item(self, row, event, details, suppress)
        fields = row['fields']
        label = 'Rettelse av registrert aksjekapital' if fields.get('notice_type') == 'Rettelse' else self.event_label
        changes = details[1:] if event == 'changed' else ()

        def shown(name, short=None, limit=235):
            field_label = self.field_labels.get(name, name)
            changed = next((value for value in changes if value.startswith(field_label + ': ')), None)
            value = changed[len(field_label) + 2:] if changed else str(fields[name] if fields[name] is not None else 'ikke oppgitt')
            prefix = (short or field_label) + ': '
            available = max(30, limit - len(prefix))
            if len(value) > available:
                before, arrow, after = value.partition(' → ')
                if arrow:
                    half = (available - 7) // 2
                    value = before[:half] + '… → ' + after[:half] + '…'
                else:
                    value = value[:available - 1] + '…'
            return prefix + value

        if self.capital:
            covered = {'registered_capital', 'currency', 'announced_date', 'notice_note', 'orgnr', 'legal_form'}
            info = [label, shown('orgnr') + ' · ' + shown('legal_form'),
                    shown('registered_capital') + ' · ' + shown('currency'), shown('announced_date')]
            if fields['notice_note'] is not None or any(value.startswith(self.field_labels['notice_note'] + ': ') for value in changes):
                info.append(shown('notice_note', limit=490))
        else:
            covered = {'orgnr', 'court', 'case_number', 'announced_date', 'decision_date',
                       'claim_deadline', 'fristdag', 'meeting_notice', 'reconstructor'}
            info = [label + ' · ' + shown('orgnr'), shown('court') + ' · ' + shown('case_number'),
                    shown('announced_date') + ' · ' + shown('decision_date'),
                    shown('claim_deadline') + ' · ' + shown('fristdag'),
                    shown('meeting_notice', limit=490), shown('reconstructor', limit=490)]
        other = [value for value in changes if not any(value.startswith(self.field_labels.get(name, name) + ': ') for name in covered)]
        if other:
            info.append(('Andre endringer: ' + '; '.join(other))[:490])
        # NotificationEntry keeps at most eight lines, each at most 500 characters.
        # Preserve key facts and caveats there, while retaining every full field in state.
        info.append(self.limitation)
        return replace(item, alert_details=tuple(info))


class CapitalIncreaseNoticesSource(FinancialNoticesSource):
    category = {'id_niva1': '9', 'id_niva2': '16', 'id_niva3': '17'}
    category_label = selected_kind = 'Kapitalforhøyelse'
    capital = True
    event_label = 'Registrert kapitalforhøyelse'
    limitation = 'Beløpet er registrert aksjekapital, ikke innhentet finansiering, emisjonsproveny eller overkurs.'

    def _detail(self, index):
        fields, content, published, heading = self._body(index, ('Endring av kapital', 'Rettelse'), {
            'Foretaksnavn:', 'Organisasjonsnummer:', 'Organisasjonsform:', 'Kapital:'}, 'Foretaksregisteret')
        amount = re.fullmatch(r'([A-Z]{3}) ((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})', fields['Kapital:'])
        if not amount:
            raise SourceError('Registered capital amount or currency is malformed')
        if fields['Foretaksnavn:'] != index['index_name']:
            raise SourceError('Capital notice company and list disagree')
        prefix = content.split('Foretaksnavn:', 1)[0]
        note = prefix[len(heading):].strip()
        if heading == 'Rettelse' and not note.endswith('Rettelse av kapital'):
            raise SourceError('Capital correction context changed')
        if heading != 'Rettelse' and note:
            raise SourceError('Capital notice has an unrecognised introduction')
        monitored = {
            'company': fields['Foretaksnavn:'], 'orgnr': index['orgnr'],
            'announcement_type': self.category_label, 'announced_date': published,
            'currency': amount[1], 'registered_capital': format(Decimal(amount[2].replace('.', '').replace(',', '.')), '.2f'),
            'legal_form': fields['Organisasjonsform:'],
            'notice_type': heading, 'notice_note': bounded(note) if note else None,
        }
        return {'key': index['kid'], 'title': monitored['company'], 'url': index['url'],
                'published': published, 'fields': monitored}


class ReconstructionNoticesSource(FinancialNoticesSource):
    category = {'id_niva1': '110'}
    category_label = 'Rekonstruksjonsforhandling'
    selected_kind = 'Forhandling om rekonstruksjon'
    default_window = 30
    maximum_window = 90
    event_label = 'Rekonstruksjonsforhandling åpnet'
    limitation = 'Datoene er separate kildefelt. Fravær fra søkevinduet dokumenterer ikke at rekonstruksjonen er avsluttet.'

    def _detail(self, index):
        fields, content, published, heading = self._body(index, 'Rekonstruksjon - åpning', {
            'Navn/foretaksnavn:', 'Organisasjonsnummer:', 'Bransje/stilling:',
            'Kjennelse avsagt:', 'Saksnr:'}, 'Brønnøysundregistrene')
        if fields['Navn/foretaksnavn:'] != index['index_name']:
            raise SourceError('Reconstruction notice company and list disagree')
        court = bounded(match(r'^Rekonstruksjon - åpning Ved (.{1,300}?) er det ved kjennelse åpnet offentlig forhandling om rekonstruksjon for:', content, 'court'))
        claim = day(match(r'Spesifisert oppgave over fordringer meldes til rekonstruktør, .{1,2000}? innen (' + DATE + r') \.', content, 'claim deadline'))
        frist = day(match(r'Fristdagen er (' + DATE + r') \.', content, 'fristdag'))
        reconstructor = bounded(match(r'Spesifisert oppgave over fordringer meldes til rekonstruktør, (.{1,300}?) , ', content, 'reconstructor'))
        meeting = bounded(match(r'Fristdagen er ' + DATE + r' \. (.{1,1000}?) Spørsmål rettes til rekonstruktør\.', content, 'meeting notice'))
        monitored = {
            'company': fields['Navn/foretaksnavn:'], 'orgnr': index['orgnr'],
            'announced_date': published, 'decision_date': day(fields['Kjennelse avsagt:']).isoformat(),
            'court': court, 'case_number': fields['Saksnr:'], 'industry': fields['Bransje/stilling:'],
            'claim_deadline': claim.isoformat(), 'fristdag': frist.isoformat(),
            'reconstructor': reconstructor, 'meeting_notice': meeting,
        }
        return {'key': index['kid'], 'title': monitored['company'], 'url': index['url'],
                'published': published, 'fields': monitored}
