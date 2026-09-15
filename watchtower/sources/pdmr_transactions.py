"""Bounded public transaction forms, with explicit gaps for unreadable attachments."""
from datetime import datetime, timedelta, timezone
import io
import json
import re
from urllib.parse import urlencode

from .changes import SnapshotSource, integer
from .common import SourceError

PAGE = 'https://newsweb.oslobors.no/'
API = 'https://api3.oslo.oslobors.no'
CONFIG_URL = PAGE + 'urls.json'


def today():
    return datetime.now(timezone.utc).date()


def clean(value):
    if not isinstance(value, str):
        raise SourceError('PDMR text field changed type')
    value = ' '.join(value.split())
    if not value or len(value) > 20000 or '\ufffd' in value or any(ord(c) < 32 for c in value):
        raise SourceError('PDMR text field is empty or invalid')
    return value


def positive(value, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise SourceError('PDMR identifier or count is invalid')
    return value


def decode(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise SourceError('PDMR JSON repeats an object key')
            out[key] = value
        return out
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as exc:
        raise SourceError('PDMR response is not valid JSON') from exc


def parse_attachment(raw, name, title, max_pages=6):
    import pdfplumber
    from .pdmr_mar import parse_mar
    from .pdmr_krt import parse_krt
    if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-1024:]:
        raise SourceError('PDMR attachment is not a complete PDF')
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            if not 1 <= len(pdf.pages) <= max_pages:
                raise SourceError('PDMR PDF page count exceeds bounds')
            if any(p.rotation or not 500 <= p.width <= 900 or not 500 <= p.height <= 1200
                   or len(p.chars) > 30000 for p in pdf.pages):
                raise SourceError('PDMR PDF geometry or text exceeds bounds')
            text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
            if len(text.strip()) < 30:
                return {'status': 'unreadable_scan', 'format': None, 'transactions': []}
            text = clean(text)
            if 'KRT-1500' in text or 'KRT1500' in text:
                return {'status': 'parsed', 'format': 'KRT1500', 'transactions': parse_krt(pdf)}
            if 'Details of the person discharging' in text or 'Details of the primary insider / person closely associated' in text:
                return {'status': 'parsed', 'format': 'MAR', 'transactions': parse_mar(pdf)}
            # The source also attaches a PDF rendering of the announcement itself.
            if (name == 'Download announcement as PDF.pdf' and clean(title) in text
                    and 'Attachments' in text and "Managers' transaction" in text):
                return {'status': 'announcement_copy', 'format': None, 'transactions': []}
            return {'status': 'unsupported_format', 'format': None, 'transactions': []}
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError('PDMR PDF cannot be read with the verified layout') from exc


class PdmrTransactionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('pdmr_transactions accepts only the official public NewsWeb source')
        if self.complete or 'removed' in self.events:
            raise ValueError('A rolling PDMR window cannot establish cancellation')
        ids = config.options.get('issuer_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 20:
            raise ValueError('issuer_ids must explicitly select 1 to 20 numeric issuer IDs')
        self.issuers = tuple(sorted({integer(i, 'issuer_ids', 1, 1000000) for i in ids}))
        self.lookback = integer(config.options.get('lookback_days', 14), 'lookback_days', 1, 31)
        self.max_messages = integer(config.options.get('max_messages', 50), 'max_messages', 1, 100)
        self.max_attachments = integer(config.options.get('max_attachments', 60), 'max_attachments', 1, 200)
        self.max_pages = integer(config.options.get('max_pdf_pages', 6), 'max_pdf_pages', 1, 20)
        self.field_labels = {'documents': 'Transaksjonsskjemaer og lesestatus',
                             'correction_for_message_id': 'Retter melding',
                             'corrected_by_message_id': 'Senere rettet av melding',
                             'coverage': 'Skjemadekning', **self.field_labels}

    def _download(self, url, post=False):
        request = self.post if post else self.get
        response = request(url, stream=True, allow_redirects=False,
                           accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('PDMR endpoint redirected unexpectedly')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('PDMR response exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def _json(self, route, params=None, post=False):
        url = API + '/v1/newsreader/' + route
        if params is not None:
            url += '?' + urlencode(params)
        obj = decode(self._download(url, post))
        if not isinstance(obj, dict) or not isinstance(obj.get('header'), dict):
            raise SourceError('PDMR JSON envelope changed')
        header = obj['header']
        if header.get('result.val') != 0 or header.get('http.code') != 200 or header.get('result.text') != 'OK' or not isinstance(obj.get('data'), dict):
            raise SourceError('PDMR API did not report success')
        return obj['data']

    def _config(self):
        obj = decode(self._download(CONFIG_URL))
        if not isinstance(obj, dict) or obj.get('api_large') != API:
            raise SourceError('PDMR public API host changed; inspect the source contract')

    def _message(self, msg):
        if not isinstance(msg, dict):
            raise SourceError('PDMR message changed type')
        mid = positive(msg.get('messageId'))
        if positive(msg.get('id')) != mid or msg.get('test') is not False:
            raise SourceError('PDMR identity or test flag changed')
        categories = msg.get('category')
        if not isinstance(categories, list) or not categories or any(not isinstance(c, dict) for c in categories) or 1102 not in [c.get('id') for c in categories]:
            raise SourceError('PDMR message lacks the selected transaction category')
        stamp = msg.get('publishedTime')
        if not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', stamp):
            raise SourceError('PDMR publication timestamp lacks explicit UTC')
        try:
            datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        except ValueError as exc:
            raise SourceError('PDMR publication timestamp is invalid') from exc
        fields = {'message_id': mid, 'issuer_id': positive(msg.get('issuerId')),
                  'issuer_text': clean(msg.get('issuerName')), 'title': clean(msg.get('title')),
                  'published_utc': stamp,
                  'correction_for_message_id': positive(msg.get('correctionForMessageId'), True) or None,
                  'corrected_by_message_id': positive(msg.get('correctedByMessageId'), True) or None,
                  'attachment_count': positive(msg.get('numbAttachments'), True)}
        if mid in (fields['correction_for_message_id'], fields['corrected_by_message_id']):
            raise SourceError('PDMR correction points to itself')
        return fields

    def _list(self, query):
        data = self._json('list', query)
        if data.get('overflow') is not False or not isinstance(data.get('messages'), list):
            raise SourceError('PDMR list is incomplete or overflowed; narrow the date window')
        if len(data['messages']) > 5000:
            raise SourceError('PDMR index exceeds bounds')
        rows = [self._message(m) for m in data['messages']]
        if len({m['message_id'] for m in rows}) != len(rows):
            raise SourceError('PDMR index repeats a message')
        selected = [r for r in rows if r['issuer_id'] in self.issuers]
        if len(selected) > self.max_messages:
            raise SourceError('PDMR selection exceeds max_messages')
        return sorted(selected, key=lambda m: m['message_id'])

    def _detail(self, fields):
        msg = self._json('message', {'messageId': fields['message_id']}).get('message')
        if self._message(msg) != fields:
            raise SourceError('PDMR detail differs from the selected index')
        attachments = msg.get('attachments')
        if not isinstance(attachments, list) or len(attachments) != fields['attachment_count']:
            raise SourceError('PDMR attachment count differs from the message')
        out = []
        for att in attachments:
            if not isinstance(att, dict):
                raise SourceError('PDMR attachment metadata changed')
            out.append({'attachment_id': positive(att.get('id')), 'name': clean(att.get('name'))})
        if len({a['attachment_id'] for a in out}) != len(out):
            raise SourceError('PDMR attachment identity is duplicated')
        return sorted(out, key=lambda a: a['attachment_id'])

    def read_records(self):
        self.coverage_warnings = []
        self._config()
        cats = self._json('categories', post=True).get('categories')
        if not isinstance(cats, list) or [c.get('category_en') for c in cats if isinstance(c, dict) and c.get('id') == 1102] != ['MANAGERS’ TRANSACTION']:
            raise SourceError('PDMR category contract changed')
        end = today()
        query = {'category': 1102, 'fromDate': (end - timedelta(days=self.lookback)).isoformat(),
                 'toDate': end.isoformat(), 'market': '', 'issuer': '', 'messageTitle': ''}
        messages = self._list(query)
        if sum(m['attachment_count'] for m in messages) > self.max_attachments:
            raise SourceError('PDMR selection exceeds max_attachments')
        rows, manifests = [], []
        for fields in messages:
            mid = fields['message_id']
            attachments = self._detail(fields)
            manifests.append(attachments)
            docs = []
            for att in attachments:
                url = API + '/v1/newsreader/attachment?' + urlencode({'messageId': mid, 'attachmentId': att['attachment_id']})
                parsed = parse_attachment(self._download(url), att['name'], fields['title'], self.max_pages)
                docs.append({**att, **parsed})
                if parsed['status'] in ('unreadable_scan', 'unsupported_format'):
                    self.coverage_warnings.append(f"PDMR {mid}/{att['attachment_id']}: {parsed['status']}; originalvedlegget må leses.")
            complete = bool(docs) and any(d['status'] == 'parsed' for d in docs) and all(d['status'] in ('parsed', 'announcement_copy') for d in docs)
            if not docs:
                self.coverage_warnings.append(f'PDMR {mid}: ingen vedlagte transaksjonsskjemaer.')
            elif not any(d['status'] == 'parsed' for d in docs) and all(d['status'] == 'announcement_copy' for d in docs):
                self.coverage_warnings.append(f'PDMR {mid}: bare meldingskopi, ingen transaksjonsskjemaer.')
            rows.append({'key': str(mid), 'title': fields['issuer_text'] + ' – ' + fields['title'],
                         'url': PAGE + 'message/' + str(mid),
                         'published': fields['published_utc'],
                         'fields': {**fields, 'documents': docs, 'coverage': 'complete_forms' if complete else 'limited'}})
        # Re-read message manifests and the selected index to reject in-flight corrections.
        for fields, manifest in zip(messages, manifests):
            if self._detail(fields) != manifest:
                raise SourceError('PDMR attachments changed during the read')
        if self._list(query) != messages:
            raise SourceError('PDMR selected index changed during the read')
        self._config()
        return rows

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            for key, current in self._next['rows'].items():
                prior = old.get('rows', {}).get(key)
                if not prior:
                    continue
                parsed = {d['attachment_id'] for d in prior['row']['fields']['documents'] if d['status'] == 'parsed'}
                new_parsed = {d['attachment_id'] for d in current['row']['fields']['documents'] if d['status'] == 'parsed'}
                if not parsed <= new_parsed:
                    raise SourceError('PDMR previously parsed attachment disappeared or became unreadable; prior state preserved')
        return items
