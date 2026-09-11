import unittest
from unittest.mock import Mock
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.industrial_documents import IndustrialDocumentsSource
from watchtower.sources.common import SourceError

URL='https://www.norskeutslipp.no/no/Diverse/Virksomhet/?CompanyID=5447&ComponentPageID=180'
class IndustrialDocumentsTests(unittest.TestCase):
 def source(self,html,**options):
  c=SourceConfig(id='industrial_documents',kind='industrial_documents',label=options.pop('label','Yara'),urls=options.pop('urls',(URL,)),filters=FilterRule(match_all=True),options=options)
  s=IndustrialDocumentsSource(c);r=Mock(status_code=200,headers={},iter_content=Mock(return_value=[html.encode()]));s.get=Mock(return_value=r);return s
 def test_parses_stable_permit_and_inspection_ids(self):
  h='''<a class="pdf" href="/WebHandlers/PDFDocumentHandler.ashx?documentID=770680&documentType=T&companyID=5447&aar=0&epslanguage=no">Tillatelse</a><a class="pdf" href="/WebHandlers/PDFDocumentHandler.ashx?documentID=909701&documentType=K&companyID=5447&aar=2025&epslanguage=no">Kontroll 2025</a>'''
  rows=self.source(h).read_records();self.assertEqual(['5447:770680','5447:909701'],[x['key'] for x in rows]);self.assertEqual('K',rows[1]['fields']['document_type']);self.assertEqual(2025,rows[1]['fields']['document_year']);self.assertIsNone(rows[0]['fields']['document_year']);self.assertIn('official_pdf_url',rows[0]['fields']);self.assertTrue(rows[0]['title'].startswith('Yara ·'))
 def test_ignores_other_company_and_non_pdf(self):
  h='<a class="pdf" href="/WebHandlers/PDFDocumentHandler.ashx?documentID=1&documentType=T&companyID=18869&aar=0">Other</a><a href="/x">x</a>'
  with self.assertRaises(SourceError): self.source(h).read_records()
 def test_rejects_duplicates_and_removals(self):
  h='<a class="pdf" href="/WebHandlers/PDFDocumentHandler.ashx?documentID=1&documentType=T&companyID=5447&aar=0">A</a><a class="pdf" href="/WebHandlers/PDFDocumentHandler.ashx?documentID=1&documentType=T&companyID=5447&aar=0">B</a>'
  with self.assertRaises(SourceError): self.source(h).read_records()
  with self.assertRaises(ValueError): self.source(h,complete_snapshot=True)
 def test_rejects_multiple_urls_and_bad_handler_or_repeated_identity(self):
  with self.assertRaises(ValueError): self.source('',urls=(URL,URL))
  bad='<a class="pdf" href="https://www.norskeutslipp.no/other/PDFDocumentHandler.ashx?documentID=1&documentType=T&companyID=5447&aar=2025&epslanguage=no">A</a>'
  with self.assertRaises(SourceError): self.source(bad).read_records()
  repeated='<a class="pdf" href="/WebHandlers/PDFDocumentHandler.ashx?documentID=1&documentID=2&documentType=T&companyID=5447&aar=2025&epslanguage=no">A</a>'
  with self.assertRaises(SourceError): self.source(repeated).read_records()
 def test_label_and_year_validation(self):
  h='<a class="pdf" href="/WebHandlers/PDFDocumentHandler.ashx?documentID=1&documentType=T&companyID=5447&aar=1899&epslanguage=no">A</a>'
  with self.assertRaises(SourceError): self.source(h,label='Annet anlegg').read_records()
  h=h.replace('1899','2025')
  self.assertTrue(self.source(h,label='Annet anlegg').read_records()[0]['title'].startswith('Annet anlegg ·'))
if __name__=='__main__': unittest.main()
