"""Published NYTEK installation-certificate lifecycle, without attachments."""
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
import json
import re
from urllib.parse import urlencode
from uuid import UUID

from .changes import SnapshotSource, document, integer, shown
from .common import SourceError

ENDPOINT = 'https://api.fiskeridir.no/nytek-public/api/v3/certificates'
SORT = ['valid_from, id,DESC']


def clean(value, maximum=3500, *, empty=False):
    if not isinstance(value, str):
        raise SourceError('NYTEK text field changed type')
    value = ' '.join(value.split())
    if len(value) > maximum or (not value and not empty):
        raise SourceError('NYTEK text field is empty or exceeds its bound')
    return value


def day(value, *, optional=False):
    if optional and value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r'20[0-9]{2}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError()
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise SourceError('NYTEK certificate date changed format') from None


def instant(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})', value):
            raise ValueError()
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
        if stamp.year < 2000:
            raise ValueError()
        return stamp.isoformat()
    except (ValueError, TypeError, OverflowError):
        raise SourceError('NYTEK status timestamp changed format') from None


def organization(value):
    if not isinstance(value, dict) or not re.fullmatch(r'[0-9]{9}', str(value.get('orgNr', ''))):
        raise SourceError('NYTEK organization lacks its registry identifier')
    return {'orgnr': value['orgNr'], 'name': clean(value.get('name'), 250)}


class AquacultureCertificatesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (ENDPOINT,)):
            raise ValueError('aquaculture_certificates accepts only the official NYTEK endpoint')
        if self.complete or 'removed' in self.events:
            raise ValueError('Missing certificates are not evidence of withdrawal')
        self.allow_empty = False
        self.max_pages = integer(config.options.get('max_pages', 60), 'max_pages', 1, 100)
        self.max_records = integer(config.options.get('max_records', 1200), 'max_records', 1, 2000)
        self.field_labels = {'status': 'Kildens sertifikatstatus', 'revocation_reason': 'Oppgitt grunn for trekking',
            'revocation_paragraph': 'Oppgitt hjemmel for trekking', 'revocation_reported': 'Trekking rapportert (UTC)',
            'issue_date': 'Kildens utstedelsesdato', 'expiry_date': 'Kildens utløpsdato', 'update_date': 'Sertifikatets oppdateringsdato',
            'max_units': 'Maksimalt antall produksjonsenheter i sertifikatet',
            'units_at_verification': 'Antall installerte enheter ved verifikasjon',
            'site': 'Lokalitet', 'holder': 'Innehaver', 'inspection_body': 'Akkreditert inspeksjonsorgan',
            'revoking_body': 'Organ som rapporterte trekking', 'issue_paragraph': 'Utstedelseshjemmel',
            'audit_log': 'Revisjonshistorikk', **self.field_labels}

    def _record(self, entry, snapshot_time):
        try:
            status, certificate = entry['status'], entry['installationCertificate']
            identifier = str(UUID(certificate['uuid']))
            status_code = status['status']
            if status_code not in ('ISSUED', 'REVOKED'):
                raise SourceError('NYTEK certificate status is unknown')
            valid_from, valid_until = instant(status['validFrom']), instant(status['validUntil'])
            at = datetime.fromisoformat(snapshot_time)
            if not datetime.fromisoformat(valid_from) <= at < datetime.fromisoformat(valid_until):
                raise SourceError('NYTEK returned a status outside the requested snapshot time')
            site_nr = integer(certificate['siteNr'], 'siteNr', 10000, 99999)
            site = {'number': site_nr, 'name': clean(certificate['siteName'], 250)}
            issued, expires = day(certificate['issueDate']), day(certificate['expiryDate'])
            updated = day(certificate.get('certificateUpdateDate'), optional=True)
            date_notes = []
            if issued > expires:
                date_notes.append('Kilden oppgir utløp før utstedelse.')
            if issued > at.date().isoformat() or (updated and updated > at.date().isoformat()):
                date_notes.append('Kilden oppgir en fremtidig utstedelses- eller oppdateringsdato.')
            paragraph = certificate['issueParagraph']
            if paragraph not in ('SECTION_37_PARAGRAPH_1', 'SECTION_37_PARAGRAPH_2'):
                raise SourceError('NYTEK certificate issue paragraph changed')
            maximum = integer(certificate['productionUnitsMax'], 'productionUnitsMax', 1, 130)
            present = certificate.get('productionUnitsPresent')
            if present is not None:
                present = integer(present, 'productionUnitsPresent', 0, 130)
            revocation = status.get('revocation')
            reason, revocation_paragraph, reported, revoking_body = None, None, None, None
            if status_code == 'REVOKED':
                if not isinstance(revocation, dict):
                    raise SourceError('NYTEK revoked status lacks its explicit explanation')
                reason = clean(revocation['reason'], empty=True)
                revocation_paragraph = revocation['revocationParagraph']
                if revocation_paragraph not in ('SECTION_37_PARAGRAPH_2_NUMBER_5', 'SECTION_37_PARAGRAPH_3', 'SECTION_37_PARAGRAPH_4'):
                    raise SourceError('NYTEK revocation paragraph changed')
                reported = instant(revocation['reportTime'])
                if datetime.fromisoformat(reported) > at:
                    raise SourceError('NYTEK revocation report is future-dated')
                revoking_body = organization(revocation['accreditedInspectionBody'])
            elif revocation:
                raise SourceError('NYTEK issued certificate unexpectedly includes a revocation')
            audit = certificate.get('auditLog') or []
            if not isinstance(audit, list) or len(audit) > 20:
                raise SourceError('NYTEK audit history exceeds its schema bound')
            history = []
            for change in audit:
                history.append({'number': integer(change['auditNumber'], 'auditNumber', 1, 20),
                                'date': day(change['dateOfUpdate']),
                                'description': clean(change['description'], 150, empty=True)})
            if len({change['number'] for change in history}) != len(history):
                raise SourceError('NYTEK audit revision identities are duplicated')
            history.sort(key=lambda change: change['number'])
            fields = {'status': status_code, 'revocation_reason': reason, 'revocation_paragraph': revocation_paragraph,
                      'revocation_reported': reported, 'issue_date': issued, 'expiry_date': expires,
                      'update_date': updated, 'max_units': maximum, 'units_at_verification': present,
                      'site': site, 'holder': organization(certificate['licenseHolder']),
                      'inspection_body': organization(certificate['accreditedInspectionBody']),
                      'revoking_body': revoking_body, 'issue_paragraph': paragraph, 'audit_log': history}
            return {'key': identifier, 'title': site['name'] + ' · NYTEK-anleggssertifikat',
                    'url': ENDPOINT + '?site-nr=' + str(site_nr), 'published': None, 'fields': fields,
                    'status_valid_from': valid_from, 'date_notes': date_notes}
        except SourceError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SourceError('NYTEK certificate schema changed') from None

    def _poll(self, snapshot_time):
        rows, count, pages = [], None, None
        for number in range(self.max_pages):
            url = ENDPOINT + '?' + urlencode({'time': snapshot_time, 'page': number, 'size': 20})
            try:
                payload = json.loads(document(self, url))
                if set(payload) != {'content', 'totalElements', 'totalPages', 'page', 'size', 'sort'}:
                    raise ValueError()
                total = integer(payload['totalElements'], 'totalElements', 1, self.max_records)
                total_pages = integer(payload['totalPages'], 'totalPages', 1, self.max_pages)
                if (type(payload['page']) is not int or payload['page'] != number or type(payload['size']) is not int
                        or payload['size'] != 20 or payload['sort'] != SORT or total_pages != (total+19)//20):
                    raise ValueError()
                if count is None:
                    count, pages = total, total_pages
                if total != count or total_pages != pages:
                    raise ValueError()
                content = payload['content']
                if not isinstance(content, list) or len(content) != min(20, total-number*20):
                    raise ValueError()
                rows.extend(self._record(entry, snapshot_time) for entry in content)
                if number+1 == pages:
                    break
            except SourceError:
                raise
            except (KeyError, TypeError, ValueError):
                raise SourceError('NYTEK pagination is incomplete or changed its contract') from None
        if len(rows) != count or len({row['key'] for row in rows}) != count:
            raise SourceError('NYTEK certificate set is incomplete or has duplicate report identifiers')
        return sorted(rows, key=lambda row: row['key'])

    def read_records(self):
        self.snapshot_time = datetime.now(timezone.utc).isoformat()
        rows = self._poll(self.snapshot_time)
        if self._poll(self.snapshot_time) != rows:
            raise SourceError('NYTEK changed between complete reads of the same snapshot time')
        return rows

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get('source_state') or {}).get('records', {})
        if old.get('scope') == self.scope:
            self._next['rows'] = {**old.get('rows', {}), **self._next['rows']}
            if len(self._next['rows']) > self.max_records*2:
                raise SourceError('Retained NYTEK reports exceed the history bound')
        self._next['snapshot_time'] = self.snapshot_time
        return items

    def describe_change(self, name, before, after):
        prior, current = shown(before), shown(after)
        if max(len(prior), len(current)) <= 200:
            return super().describe_change(name, before, after)
        old_words, words = prior.split(), current.split()
        for tag, a, b, c, d in SequenceMatcher(None, old_words, words, autojunk=False).get_opcodes():
            if tag != 'equal':
                prior = ' '.join(old_words[max(0,a-3):max(a+1,b)+3])[:150] or 'ikke oppgitt'
                current = ' '.join(words[max(0,c-3):max(c+1,d)+3])[:150] or 'ikke oppgitt'
                break
        return self.field_labels.get(name,name) + ' (utdrag): ' + prior + ' → ' + current

    def _item(self, row, event, details, suppress):
        fields = row['fields']
        content = ['Nyobservert NYTEK-sertifikatrapport' if event == 'added' else 'Endret NYTEK-sertifikatrapport',
                   'Lokalitetsnummer: ' + str(fields['site']['number'])]
        if event == 'added':
            content.extend(['Kildens sertifikatstatus: ' + fields['status'],
                            'Kildens utstedelsesdato: ' + fields['issue_date'] + ' · Kildens utløpsdato: ' + fields['expiry_date']
                            + (' · ' + ' '.join(row.get('date_notes', [])) if row.get('date_notes') else ''),
                            'Innehaver: ' + fields['holder']['name'] + ' (' + fields['holder']['orgnr'] + ')',
                            'Maksimalt antall produksjonsenheter i sertifikatet: ' + str(fields['max_units'])])
            if fields['audit_log']:
                latest = fields['audit_log'][-1]
                content.append('Revisjonshistorikk (siste av '+str(len(fields['audit_log']))+'): '
                    +str(latest['number'])+' · '+latest['date']+' · '+latest['description'])
        else:
            if row.get('date_notes'):
                content.append(' '.join(row['date_notes']))
            changes = list(details[1:])
            slots = 8 - len(content) - 1  # Keep the source limitation in the delivered notification.
            if len(changes) > slots:
                content.extend(changes[:slots-1])
                content.append(f'{len(changes)-slots+1} flere feltendringer; se kilden.')
            else:
                content.extend(changes)
        content.append('NYTEKs publiserte sertifikatmetadata. Utløpsdato eller fravær alene er ikke dokumentert trekking; produksjonsenheter er ikke fiskemengde. Vedlegg og øvrige tekniske data overvåkes ikke.')
        return super()._item(row, event, content, suppress)
