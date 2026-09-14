"""Classified device recall action records in a bounded FDA date window."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.parse import urlencode

from .changes import SnapshotSource, canonical, digest, integer
from .common import SourceError

API = 'https://api.fda.gov/device/recall.json'
RECALL = re.compile(r'Z-[0-9]{3,6}-[0-9]{4}')


def _date(value, name, nullable=False):
    if nullable and value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise SourceError(f'Device action {name} date is invalid') from None


def _text(value, name, limit=10000, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) > limit or not value.strip():
        raise SourceError(f'Device action {name} is invalid')
    return ' '.join(value.split())


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


class DeviceActionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API,)):
            raise ValueError('device_actions accepts only the official device recall API')
        if self.complete or 'removed' in self.events:
            raise ValueError('A classification window cannot prove removal')
        self.days = integer(config.options.get('classification_days', 30), 'classification_days', 1, 90)
        self.page_size = integer(config.options.get('page_size', 100), 'page_size', 1, 1000)
        self.max_pages = integer(config.options.get('max_pages', 5), 'max_pages', 1, 10)
        self.field_labels = {'event_number': 'Hendelsesnummer', 'product_code': 'Produktkode',
            'recalling_firm': 'Oppført virksomhet', 'reason_for_recall': 'Begrunnelse',
            'root_cause_description': 'Oppgitt årsakstype', 'product_description': 'Produktbeskrivelse',
            'action': 'Oppførte tiltak', 'product_quantity': 'Oppgitt produktmengde',
            'distribution_pattern': 'Oppgitt førstegangsdistribusjon', 'initiated_date': 'Varsling startet',
            'classification_date': 'Klassifiseringsdato', **self.field_labels}

    def _page(self, query):
        response = self.get(API + '?' + urlencode(query), stream=True, allow_redirects=False,
                            accepted_statuses=(301, 302, 303, 307, 308, 404))
        try:
            if response.status_code not in (200, 404):
                raise SourceError('Device action API returned an unexpected redirect')
            raw, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > self.max_bytes:
                    raise SourceError('Device action page exceeds max_bytes')
                raw.append(chunk)
            data = json.loads(b''.join(raw), object_pairs_hook=_object)
            if not isinstance(data, dict):
                raise ValueError
            if response.status_code == 404:
                if data == {'error': {'code': 'NOT_FOUND', 'message': 'No matches found!'}}:
                    return None
                raise SourceError('Device action API returned an unrecognized missing response')
            return data
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('Device action API returned invalid JSON') from exc
        finally:
            response.close()

    def read_records(self):
        last = datetime.now(timezone.utc).date()
        first = last - timedelta(days=self.days - 1)
        query = {'search': f'event_date_posted:[{first.isoformat()} TO {last.isoformat()}]',
                 'sort': 'product_res_number.exact:asc', 'limit': self.page_size, 'skip': 0}
        rows, ids, totals, exported, anchor = [], set(), None, None, None
        for page in range(self.max_pages):
            query['skip'] = page * self.page_size
            data = self._page(query)
            if data is None:
                if page:
                    raise SourceError('Device action later page is missing')
                return []
            meta = data.get('meta')
            if not isinstance(meta, dict) or not isinstance(meta.get('results'), dict):
                raise SourceError('Device action pagination metadata is absent')
            counts = meta['results']
            if any(type(counts.get(k)) is not int for k in ('skip', 'limit', 'total')):
                raise SourceError('Device action pagination counts are invalid')
            total = counts['total']
            updated = _date(meta.get('last_updated'), 'export')
            if updated > last or total < 1 or total > min(self.max_records, self.max_pages * self.page_size):
                raise SourceError('Device action date window exceeds bounds or has invalid export metadata')
            if counts['skip'] != query['skip'] or counts['limit'] != self.page_size:
                raise SourceError('Device action pagination differs from the requested page')
            batch = data.get('results')
            if not isinstance(batch, list) or len(batch) != min(self.page_size, total - len(rows)):
                raise SourceError('Device action page is partial or inconsistent')
            if page == 0:
                totals, exported = total, updated
                anchor = canonical(data)
            elif total != totals or updated != exported:
                raise SourceError('Device action publication changed during pagination')
            for value in batch:
                row = _record(value, first, last)
                if row['fields']['classification_date'] > updated.isoformat():
                    raise SourceError('Device action classification date is after the dataset export')
                if row['key'] in ids or (rows and row['key'] <= rows[-1]['key']):
                    raise SourceError('Device action identities repeat or ordering changed')
                ids.add(row['key']); rows.append(row)
            if len(rows) == total:
                if len({r['cfres_id'] for r in rows}) != total:
                    raise SourceError('Device action internal identifiers repeat')
                if page:
                    query['skip'] = 0
                    if canonical(self._page(query)) != anchor:
                        raise SourceError('Device action first page changed during pagination')
                return rows
        raise SourceError('Device action date window exceeds max_pages')

    def describe_change(self, name, before, after):
        if name == 'code_info_sha256':
            return 'Oppgitte modell-, serie- eller batchkoder er endret; se kilden for hele utvalget'
        if name in {'action', 'product_description', 'reason_for_recall', 'distribution_pattern'}:
            return f'{self.field_labels[name]} er endret: {str(after)[:400]}'
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row['fields']
        if event == 'added':
            details = ('Nyobservert klassifisert produktpost',
                       'Virksomhet: ' + fields['recalling_firm'],
                       'Begrunnelse: ' + fields['reason_for_recall'][:400],
                       'Oppførte tiltak: ' + fields['action'][:600])
        else:
            details = ('Endrede opplysninger i klassifisert produktpost', *details[1:])
        return replace(item, alert_details=(*details,
            'FDA klassifiseringsdato: ' + fields['classification_date'],
            'Kilden oppdateres ukentlig. Dette er kildeopplysninger for undersøkelse, ikke råd til brukere eller bevis på nåværende tilbakekallingsstatus',
            'Senere distribusjon og endringer utenfor det valgte klassifiseringsvinduet dekkes ikke'))


def _record(value, first, last):
    if not isinstance(value, dict):
        raise SourceError('Device action record is malformed')
    ident = value.get('product_res_number')
    if not isinstance(ident, str) or not RECALL.fullmatch(ident):
        raise SourceError('Device action product recall number is invalid')
    internal, event = value.get('cfres_id'), value.get('res_event_number')
    if any(not isinstance(x, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', x) for x in (internal, event)):
        raise SourceError('Device action source identifiers are invalid')
    classified = _date(value.get('event_date_posted'), 'classification')
    initiated = _date(value.get('event_date_initiated'), 'initiation', nullable=True)
    if not first <= classified <= last or (initiated and initiated > classified):
        raise SourceError('Device action dates are inconsistent or outside the selected window')
    code = value.get('product_code')
    if not isinstance(code, str) or not re.fullmatch(r'[A-Z]{3}', code):
        raise SourceError('Device action product code is invalid')
    fields = {'event_number': event, 'product_code': code,
              'classification_date': classified.isoformat(),
              'initiated_date': initiated.isoformat() if initiated else None}
    for name, limit, nullable in [('recalling_firm', 2000, False), ('reason_for_recall', 20000, False),
            ('root_cause_description', 2000, True), ('product_description', 20000, False),
            ('action', 50000, False), ('product_quantity', 5000, True), ('distribution_pattern', 20000, True)]:
        fields[name] = _text(value.get(name), name, limit, nullable)
    fields['code_info_sha256'] = digest(_text(value.get('code_info'), 'code_info', 500000, nullable=True))
    return {'key': ident, 'cfres_id': internal,
            'title': ident + ' · ' + fields['product_description'][:180],
            'url': API + '?' + urlencode({'search': f'product_res_number.exact:"{ident}"', 'limit': 1}),
            'published': classified.isoformat(), 'fields': fields}
