"""FDA 510(k) decisions, separate from recalls and medicine authorisations."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlencode

from .changes import SnapshotSource, integer, strings
from .common import SourceError
from .device_actions import _date, _object, _text

API = 'https://api.fda.gov/device/510k.json'


def today():
    return datetime.now(timezone.utc).date()


class DeviceClearancesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API,)):
            raise ValueError('device_clearances accepts only the official FDA 510(k) API')
        if self.complete or 'removed' in self.events:
            raise ValueError('A decision window cannot establish withdrawal or removal')
        self.days = integer(config.options.get('decision_days', 90), 'decision_days', 1, 366)
        self.page_size = integer(config.options.get('page_size', 100), 'page_size', 1, 1000)
        self.max_pages = integer(config.options.get('max_pages', 5), 'max_pages', 1, 10)
        self.max_export_age = integer(config.options.get('max_export_age_days', 60), 'max_export_age_days', 1, 366)
        self.countries = strings(config.options.get('country_codes', ['NO', 'DK', 'SE', 'FI', 'IS']), 'country_codes')
        if len(self.countries) > 30 or any(not re.fullmatch(r'[A-Z]{2}', value) for value in self.countries):
            raise ValueError('country_codes must contain up to 30 two-letter uppercase country codes')
        self.field_labels = {
            'applicant': 'Oppført søker', 'country_code': 'Søkers oppførte postland',
            'device_name': 'Utstyr', 'product_code': 'Produktkode', 'decision_code': 'Vedtaksutfall – kode',
            'decision_description': 'Vedtaksutfall – kildetekst', 'decision_date': 'Vedtaksdato',
            'date_received': 'Søknad mottatt', 'clearance_type': 'Innsendingsmåte',
            'advisory_committee': 'Fagpanel – kode', 'advisory_committee_description': 'Fagpanel',
            **self.field_labels,
        }

    def _page(self, query):
        response = self.get(API + '?' + urlencode(query), stream=True, allow_redirects=False,
                            accepted_statuses=(301, 302, 303, 307, 308, 404))
        try:
            if response.status_code not in (200, 404):
                raise SourceError('FDA decision API returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError('FDA decision response exceeds max_bytes')
                chunks.append(chunk)
            payload = json.loads(b''.join(chunks), object_pairs_hook=_object)
            if not isinstance(payload, dict):
                raise ValueError
            if response.status_code == 404:
                if payload != {'error': {'code': 'NOT_FOUND', 'message': 'No matches found!'}}:
                    raise SourceError('FDA decision API returned an unrecognised missing response')
                return None
            return payload
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError('FDA decision API returned invalid JSON') from exc
        finally:
            response.close()

    def _sweep(self, first, last):
        countries = ' OR '.join('country_code:' + value for value in sorted(self.countries))
        query = {'search': f'decision_date:[{first.isoformat()} TO {last.isoformat()}] AND ({countries})',
                 'sort': 'k_number.exact:asc', 'limit': self.page_size, 'skip': 0}
        rows, expected_total, exported = [], None, None
        for page in range(self.max_pages):
            query['skip'] = page * self.page_size
            data = self._page(query)
            if data is None:
                if page:
                    raise SourceError('FDA decision later page is missing')
                return [], None
            meta = data.get('meta')
            counts = meta.get('results') if isinstance(meta, dict) else None
            if not isinstance(counts, dict) or any(type(counts.get(k)) is not int for k in ('skip', 'limit', 'total')):
                raise SourceError('FDA decision pagination counts are missing or invalid')
            total = counts['total']
            updated = _date(meta.get('last_updated'), 'export')
            if not 0 <= (last - updated).days <= self.max_export_age:
                raise SourceError('FDA decision dataset export is stale or in the future')
            if not 1 <= total <= min(self.max_records, self.page_size * self.max_pages):
                raise SourceError('FDA decision selection exceeds the complete-fetch bounds')
            if counts['skip'] != query['skip'] or counts['limit'] != self.page_size:
                raise SourceError('FDA decision pagination differs from the request')
            if page and (total != expected_total or updated != exported):
                raise SourceError('FDA decision publication changed during pagination')
            expected_total, exported = total, updated
            batch = data.get('results')
            if not isinstance(batch, list) or len(batch) != min(self.page_size, total - len(rows)):
                raise SourceError('FDA decision page is partial or inconsistent')
            for value in batch:
                row = self._record(value, first, last, updated)
                if rows and row['key'] <= rows[-1]['key']:
                    raise SourceError('FDA decision identity repeats or ordering changed')
                rows.append(row)
            if len(rows) == total:
                return rows, exported.isoformat()
        raise SourceError('FDA decision selection exceeds max_pages')

    def _record(self, value, first, last, exported):
        if not isinstance(value, dict) or not isinstance(value.get('k_number'), str):
            raise SourceError('FDA decision record or identifier is malformed')
        ident = value['k_number']
        if not re.fullmatch(r'(?:K|BK|DEN)\d{6}', ident):
            raise SourceError('FDA decision identifier is invalid')
        decided = _date(value.get('decision_date'), 'decision')
        received = _date(value.get('date_received'), 'received')
        if not first <= decided <= last or decided > exported or received > decided:
            raise SourceError('FDA decision dates disagree or fall outside the selected window')
        fields = {name: _text(value.get(name), name, 10000) for name in (
            'applicant', 'country_code', 'device_name', 'product_code', 'decision_code',
            'decision_description', 'clearance_type', 'advisory_committee', 'advisory_committee_description')}
        if fields['country_code'] not in self.countries:
            raise SourceError('FDA decision post country is outside the requested selection')
        if not re.fullmatch(r'[A-Z]{3}', fields['product_code']) or not re.fullmatch(r'[A-Z]{4}', fields['decision_code']):
            raise SourceError('FDA decision product or outcome code is malformed')
        fields.update(decision_date=decided.isoformat(), date_received=received.isoformat())
        return {'key': ident, 'title': ident + ' · ' + fields['applicant'] + ' · ' + fields['device_name'][:180],
                'url': API + '?' + urlencode({'search': f'k_number.exact:"{ident}"', 'limit': 1}),
                'published': None, 'fields': fields}

    def read_records(self):
        last = today()
        first = last - timedelta(days=self.days - 1)
        one, two = self._sweep(first, last), self._sweep(first, last)
        if one != two:
            raise SourceError('FDA decision selection changed between complete reads')
        self._exported, self._window_end = one[1], last.isoformat()
        return one[0]

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            if old.get('window_end', '') > self._window_end:
                raise SourceError('FDA decision window regressed; prior state preserved')
            if self._exported and (old.get('exported') or '') > self._exported:
                raise SourceError('FDA decision export regressed; prior state preserved')
        self._next.update(window_end=self._window_end, exported=self._exported or old.get('exported'))
        return items

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        def shown(name, limit=235):
            label = self.field_labels[name] + ': '
            changed = next((line for line in details[1:] if line.startswith(label)), None) if event == 'changed' else None
            value = changed[len(label):] if changed else row['fields'][name]
            available = max(30, limit - len(label))
            if len(value) > available:
                before, arrow, after = value.partition(' → ')
                if arrow:
                    half = (available - 7) // 2
                    value = before[:half] + '… → ' + after[:half] + '…'
                else:
                    value = value[:available - 1] + '…'
            return label + value
        return replace(item, alert_details=(
            'FDA 510(k) – oppførte vedtaksopplysninger',
            shown('applicant') + ' · ' + shown('country_code'),
            shown('device_name', 390) + ' · ' + shown('product_code', 80),
            shown('decision_code', 90) + ' · ' + shown('decision_description', 390),
            shown('decision_date') + ' · ' + shown('date_received'),
            shown('clearance_type', 150) + ' · ' + shown('advisory_committee', 90) + ' · ' + shown('advisory_committee_description', 230),
            'Utfallet er kildens kode og tekst, ikke en generell påstand om godkjenning eller klinisk effekt. Datasetteksport: ' + str(self._exported),
            'Land er søkers oppførte postland. Senere rettinger utenfor vedtaksvinduet og eventuell tilbaketrekking dekkes ikke.'))
