"""Product approvals and temporary permits from Mattilsynet's public API."""
from datetime import datetime, timezone
import json
import re

from .changes import SnapshotSource, document
from .common import SourceError

BASE = 'https://api.plantevernmidler.mattilsynet.io'
ENDPOINTS = {'products': '/godkjente_kjemiske_mikrobiologiske_preparater',
             'temporary_permits': '/midlertidige_tillatelser'}


def _text(row, key, optional=False):
    value = row.get(key)
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 12000:
        raise SourceError(f'Pesticide field {key} is invalid')
    return value.strip()


def _date(row, key, optional=False, allow_future=True):
    value = row.get(key)
    if optional and value is None:
        return None
    try:
        if not isinstance(value, str) or 'T' not in value:
            raise ValueError
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError
        result = result.date()
        if not allow_future and result > datetime.now(timezone.utc).date():
            raise ValueError
        return result.isoformat()
    except ValueError:
        raise SourceError(f'Pesticide field {key} is not a valid source date') from None


class PesticidesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.mode = config.options.get('mode', 'products')
        if self.mode not in ENDPOINTS or config.urls:
            raise ValueError('Select products or temporary_permits; URLs are fixed')
        if self.complete or 'removed' in self.events:
            raise ValueError('Selected approval lists cannot confirm removals')
        self.field_labels = {'name': 'Preparat', 'status': 'Status', 'product_type': 'Type',
            'registration_number': 'Registreringsnummer', 'active_substances': 'Virksomme stoffer',
            'processed': 'Behandlet', 'approved_until': 'Godkjent til', 'remarks': 'Merknad/bruksbetingelser',
            'permission_type': 'Tillatelsestype', **self.field_labels}

    def read_records(self):
        try:
            payload = json.loads(document(self, BASE + ENDPOINTS[self.mode]))
        except (ValueError, UnicodeError) as exc:
            raise SourceError('Mattilsynet pesticide API returned invalid JSON') from exc
        products = payload.get('preparater') if isinstance(payload, dict) else None
        if not isinstance(products, list) or len(products) > self.max_records:
            raise SourceError('Mattilsynet pesticide product list is invalid or too large')
        rows, seen_products = [], set()
        for product in products:
            if not isinstance(product, dict) or type(product.get('id')) is not int or product['id'] <= 0:
                raise SourceError('Pesticide product lacks stable numeric identity')
            product_id = product['id']
            if product_id in seen_products:
                raise SourceError('Pesticide API repeated a product ID')
            seen_products.add(product_id)
            name, status = _text(product, 'navn'), _text(product, 'status')
            if status != 'Godkjent':
                raise SourceError('Approved pesticide endpoint returned a product outside its status scope')
            substances = product.get('virksommeStoffer')
            if not isinstance(substances, list) or not substances or any(not isinstance(substance, dict) for substance in substances):
                raise SourceError('Pesticide active substances are invalid')
            common = {'name': name, 'status': status, 'product_type': _text(product, 'type'),
                      'active_substances': sorted(set(_text(substance, 'virksomtStoff') for substance in substances))}
            if self.mode == 'products':
                fields = {**common, 'registration_number': _text(product, 'registreringsnummer', True),
                    'processed': _date(product, 'behandlet', True, allow_future=False),
                    'approved_until': _date(product, 'godkjentTil'), 'remarks': _text(product, 'merknad', True)}
                rows.append({'key': str(product_id), 'title': name, 'url': f'{BASE}/preparat/{product_id}',
                             'published': None, 'fields': fields})
            else:
                permits = product.get('midlertidigeTillatelser')
                if not isinstance(permits, list) or not permits:
                    raise SourceError('Temporary-permit product has no permission list')
                for permit in permits:
                    if not isinstance(permit, dict):
                        raise SourceError('Temporary permission is invalid')
                    reference = _text(permit, 'bruksbetingelser')
                    if not re.fullmatch(str(product_id) + r'_\d+', reference):
                        raise SourceError('Temporary permission lacks stable product-linked identity')
                    expected_path = '/etikett/bruksbetingelser/' + reference
                    if permit.get('bruksbetingelserURL') != expected_path:
                        raise SourceError('Temporary permission link does not match its identity')
                    rows.append({'key': reference, 'title': name + ' · midlertidig tillatelse',
                        'url': BASE + expected_path, 'published': None, 'fields': {**common,
                        'permission_type': _text(permit, 'godkjenningsType'),
                        'processed': _date(permit, 'behandlet', allow_future=False),
                        'approved_until': _date(permit, 'godkjentTil'),
                        'remarks': _text(permit, 'merknad', True)}})
            if len(rows) > self.max_records:
                raise SourceError('Pesticide records exceed max_records')
        return rows
