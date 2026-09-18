from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.common import SourceError
from watchtower.sources.dmp_quality_letters import DmpQualityLettersSource, TITLE, CAPTION, DIRECTORY, periods, pdf_text
from test_change_sources import poll, response


def index(period='Mars 2026', product='Example', file='example.pdf', duplicate=False):
    row=f'<tr><td><a href="{DIRECTORY}{file}">{product}</a></td><td>Example substance</td><td>{period}</td></tr>'
    return (f'<h1>{TITLE}</h1><table><caption>{CAPTION}</caption><thead><tr><th>Preparatnavn</th>'
            f'<th>Virkestoff</th><th>Dato</th></tr></thead><tbody>{row}{row if duplicate else ""}</tbody></table>').encode()


def source(page=None, body='Example product; batch 100 is included.', second=None):
    src=DmpQualityLettersSource(SourceConfig(id='quality',kind='dmp_quality_letters',label='Kvalitetsbrev',filters=FilterRule(match_all=True)))
    page=page or index()
    src.get=Mock(side_effect=[response(page),response(body.encode()),response(page),response((second or body).encode())])
    return src


class DmpQualityLettersTests(unittest.TestCase):
    def test_natural_identity_survives_document_and_revision_period_changes(self):
        src=source()
        first=src._index(index())[0]
        revised=src._index(index('Mars 2026, oppdatert mai 2026',file='revised.pdf'))[0]
        self.assertEqual(first['key'],revised['key'])
        self.assertEqual('2026-03',revised['fields']['issued_period'])
        self.assertEqual('2026-05',revised['fields']['updated_period'])
        self.assertIsNone(revised['published'])

    @patch('watchtower.sources.dmp_quality_letters.pdf_text', side_effect=lambda raw,*_:raw.decode())
    def test_pdf_content_revision_alerts_with_known_before_and_after(self, _extract):
        prefix='Example product; unchanged introductory information. '*150
        state,alerts=poll(source(body=prefix+'batch 100 is included.')); self.assertEqual([],alerts)
        changed=source(body=prefix+'batch 200 is included.')
        updated,alerts=poll(changed,state)
        self.assertEqual(1,len(alerts))
        self.assertIn('batch 100',' '.join(alerts[0].item.alert_details))
        self.assertIn('batch 200',' '.join(alerts[0].item.alert_details))
        excerpt=next(detail for detail in alerts[0].item.alert_details if detail.startswith('Brevtekst'))
        self.assertLess(len(excerpt),500)
        self.assertEqual([],poll(source(body=prefix+'batch 200 is included.'),updated)[1])

    @patch('watchtower.sources.dmp_quality_letters.pdf_text', side_effect=lambda raw,*_:raw.decode())
    def test_only_document_filename_changes_are_not_editorial_changes(self, _extract):
        state,_=poll(source())
        _,alerts=poll(source(page=index(file='renamed.pdf')),state)
        self.assertEqual([],alerts)

    @patch('watchtower.sources.dmp_quality_letters.pdf_text', side_effect=lambda raw,*_:raw.decode())
    def test_second_read_drift_and_product_mismatch_preserve_state(self, _extract):
        state,_=poll(source()); prior=deepcopy(state)
        for src in (source(second='Example product; different batch'), source(body='Other product')):
            with self.assertRaises(SourceError): src.fetch_with_state(state)
            self.assertEqual(prior,state)

    def test_ambiguous_identity_date_schema_and_offsite_document_fail(self):
        for page in (index(duplicate=True),index(period='unknown'),index().replace(DIRECTORY.encode(),b'https://example.org/'),index().replace(b'Virkestoff',b'Changed')):
            with self.subTest(page=page),self.assertRaises(SourceError): source()._index(page)
        with self.assertRaises(SourceError): periods('Mai 2026, oppdatert mars 2026')
        with self.assertRaises(SourceError): pdf_text(b'not a PDF',8)

    def test_pdf_extraction_normalizes_whitespace_and_enforces_bounds(self):
        page=Mock(width=600,height=800,chars=[{}]*100,extract_text=Mock(return_value='Example quality letter.\n'+('Batch 100 information. '*20)))
        document=Mock(pages=[page]);document.__enter__=Mock(return_value=document);document.__exit__=Mock(return_value=False)
        with patch('pdfplumber.open',return_value=document):
            self.assertNotIn('\n',pdf_text(b'%PDF-example',8))
            page.chars=[{}]*40001
            with self.assertRaises(SourceError):pdf_text(b'%PDF-example',8)


if __name__=='__main__':unittest.main()
