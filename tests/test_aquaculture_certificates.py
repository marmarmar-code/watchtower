from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from watchtower.config import SourceConfig, FilterRule
from watchtower.engine import notification_entries
from watchtower.sources.aquaculture_certificates import AquacultureCertificatesSource, SORT
from watchtower.sources.common import SourceError
from test_change_sources import poll, response

STAMP = '2026-09-18T12:00:00+00:00'


def certificate(number=1):
    return {'status':{'status':'ISSUED','validFrom':'2026-09-01T00:00:00Z','validUntil':'9999-12-30T00:00:00Z'},
        'installationCertificate':{'uuid':f'00000000-0000-4000-8000-{number:012d}',
            'issueDate':'2026-09-01','expiryDate':'2031-09-01','certificateUpdateDate':None,
            'issueParagraph':'SECTION_37_PARAGRAPH_1','siteNr':10000+number,'siteName':'Example site',
            'productionUnitsMax':10,'productionUnitsPresent':5,
            'licenseHolder':{'orgNr':'123456789','name':'Example Aquaculture AS'},
            'accreditedInspectionBody':{'orgNr':'987654321','name':'Example Inspection AS'},
            'auditLog':[], 'reportTime':'2026-09-01T00:00:00Z',
            'certificateFile':{'uuid':'ignored','classified':True}}}


def page(entries, number=0, total=None):
    total=len(entries) if total is None else total
    return {'content':entries,'page':number,'size':20,'totalElements':total,'totalPages':(total+19)//20,'sort':SORT}


def source(entries=None):
    src=AquacultureCertificatesSource(SourceConfig(id='certificates',kind='aquaculture_certificates',filters=FilterRule(match_all=True)))
    payload=page(entries or [certificate()])
    src.get=Mock(return_value=response(json.dumps(payload).encode()))
    return src


class AquacultureCertificateTests(unittest.TestCase):
    def test_status_timestamp_and_file_metadata_do_not_realert(self):
        src=source();state,alerts=poll(src);self.assertFalse(alerts)
        changed=certificate();changed['installationCertificate']['reportTime']='2026-09-02T00:00:00Z'
        changed['installationCertificate']['certificateFile']['uuid']='new-storage-identifier'
        changed['status']['validFrom']='2026-09-02T00:00:00Z'
        self.assertFalse(poll(source([changed]),state)[1])

    def test_explicit_revocation_explains_status_and_reason_once(self):
        state,_=poll(source());changed=certificate()
        changed['status']['status']='REVOKED'
        changed['status']['revocation']={'reason':'Example component defect',
            'revocationParagraph':'SECTION_37_PARAGRAPH_3','reportTime':'2026-09-17T12:00:00Z',
            'accreditedInspectionBody':{'orgNr':'987654321','name':'Example Inspection AS'}}
        updated,alerts=poll(source([changed]),state)
        self.assertEqual(1,len(alerts));detail=str(alerts[0].item.alert_details)
        self.assertIn('ISSUED → REVOKED',detail);self.assertIn('Example component defect',detail)
        self.assertIsNone(alerts[0].item.published);self.assertFalse(poll(source([changed]),updated)[1])

    def test_complete_pagination_and_duplicate_detection(self):
        entries=[certificate(n) for n in range(1,22)];src=source()
        src.get=Mock(side_effect=[response(page(entries[:20],total=21)),response(page(entries[20:],number=1,total=21))])
        self.assertEqual(21,len(src._poll(STAMP)))
        src.get=Mock(side_effect=[response(page(entries[:20],total=21)),response(page(entries[:1],number=1,total=21))])
        with self.assertRaises(SourceError):src._poll(STAMP)

    def test_expiry_is_a_source_date_without_automatic_revocation(self):
        changed=certificate();changed['installationCertificate']['expiryDate']='2026-09-02'
        record=source()._record(changed,STAMP)
        self.assertEqual('ISSUED',record['fields']['status'])
        self.assertEqual('2026-09-02',record['fields']['expiry_date'])

    def test_source_update_date_can_precede_current_issue_date(self):
        changed=certificate();changed['installationCertificate']['certificateUpdateDate']='2025-02-20'
        record=source()._record(changed,STAMP)
        self.assertEqual('2025-02-20',record['fields']['update_date'])
        self.assertEqual('2026-09-01',record['fields']['issue_date'])

    def test_reorder_audit_is_quiet_and_revised_text_is_explained(self):
        record=certificate();record['installationCertificate']['auditLog']=[
            {'auditNumber':1,'dateOfUpdate':'2026-09-01','description':'Example issuance'},
            {'auditNumber':2,'dateOfUpdate':'2026-09-02','description':'Example correction'}]
        state,_=poll(source([record]));record['installationCertificate']['auditLog'].reverse()
        self.assertFalse(poll(source([record]),state)[1])
        record['installationCertificate']['auditLog'][0]['description']='Example corrected limit'
        _,alerts=poll(source([record]),state)
        self.assertEqual(1,len(alerts));self.assertIn('corrected limit',str(alerts[0].item.alert_details))

    def test_incomplete_or_changed_read_preserves_input_state(self):
        state,_=poll(source());prior=deepcopy(state)
        broken=page([certificate()]);broken['totalElements']=2
        src=source();src.get=Mock(return_value=response(broken))
        with self.assertRaises(SourceError):src.fetch_with_state(state)
        self.assertEqual(prior,state)
        changed=certificate();changed['installationCertificate']['productionUnitsMax']=12
        src=source();src.get=Mock(side_effect=[response(page([certificate()])),response(page([changed]))])
        with self.assertRaises(SourceError):src.fetch_with_state(state)
        self.assertEqual(prior,state)

    def test_status_outside_snapshot_or_invalid_report_fails(self):
        for modify in [lambda r:r['status'].update(validFrom='2027-01-01T00:00:00Z'),
                       lambda r:r['status'].update(status='REVOKED'),
                       lambda r:r['installationCertificate'].update(uuid='unknown'),
                       lambda r:r['installationCertificate'].update(expiryDate='2025-13-01')]:
            row=certificate();modify(row)
            with self.assertRaises(SourceError):source()._record(row,STAMP)

    def test_inconsistent_source_dates_are_preserved_and_explicit(self):
        changed=certificate();changed['installationCertificate'].update(issueDate='2031-03-13',expiryDate='2029-03-19')
        src=source();record=src._record(changed,STAMP)
        self.assertEqual('2031-03-13',record['fields']['issue_date'])
        self.assertEqual('2029-03-19',record['fields']['expiry_date'])
        self.assertEqual('ISSUED',record['fields']['status'])
        item=src._item(record,'added',('Ny registrering',),False)
        self.assertIn('utløp før utstedelse',str(item.alert_details))
        self.assertIn('fremtidig utstedelses',str(item.alert_details))

    def test_many_changes_keep_date_warning_and_scope_in_delivered_notification(self):
        state,_=poll(source());changed=certificate()
        changed['status']['status']='REVOKED'
        changed['status']['revocation']={'reason':'Example component defect',
            'revocationParagraph':'SECTION_37_PARAGRAPH_3','reportTime':'2026-09-17T12:00:00Z',
            'accreditedInspectionBody':{'orgNr':'987654321','name':'Example Inspection AS'}}
        changed['installationCertificate'].update(issueDate='2031-03-13',expiryDate='2029-03-19',
            certificateUpdateDate='2031-03-14',productionUnitsMax=12,productionUnitsPresent=8,
            siteName='Example renamed site',issueParagraph='SECTION_37_PARAGRAPH_2',
            auditLog=[{'auditNumber':1,'dateOfUpdate':'2031-03-14','description':'Example revision'}])
        changed['installationCertificate']['licenseHolder']['name']='Example revised holder'
        updated,alerts=poll(source([changed]),state)
        details=notification_entries(alerts)[0].details
        self.assertLessEqual(len(details),8)
        self.assertTrue(all(len(line)<=500 for line in details))
        text=' '.join(details)
        for phrase in ['ISSUED → REVOKED','Example component defect','utløp før utstedelse',
                       'fremtidig utstedelses','flere feltendringer','fravær alene','Vedlegg']:
            self.assertIn(phrase,text)
        saved=next(iter(updated['source_state']['records']['rows'].values()))['row']['fields']
        self.assertEqual(12,saved['max_units']);self.assertEqual('Example revised holder',saved['holder']['name'])
        self.assertFalse(poll(source([changed]),updated)[1])


if __name__ == '__main__':unittest.main()
