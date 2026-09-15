import unittest
from watchtower.sources.pdmr_transactions import parse_attachment
from watchtower.sources.common import SourceError
from pdmr_pdf_fixture import pdf_pages


def mar_cells(*, amount='NOK 0 127,659', date='2026-09-15', kind='Lending of shares', heading='Price(s) and volume(s)', section='4', above=0):
    cells=[]
    rows=[('1','Details of the person discharging managerial responsibilities',''),
          ('a)','Name','Example Holdings AS'),('2','Reason for the notification',''),
          ('a)','Position/status','Closely associated company'),
          ('b)','Initial notification/Amendment','Initial notification'),
          ('3','Details of the issuer',''),('a)','Name','Example ASA'),
          ('b)','LEI','549300XBITM62HH7HW18'),
          (section,'Details of the transaction(s):',''),
          ('a)','Description of the financial|instrument, type of instrument|Identification code','Shares ISIN NO0000000000'),
          ('b)','Nature of the transaction',kind),
          ('c)',heading,'Price(s) Volume(s)|'+amount),
          ('d)','Aggregated information|- Aggregated volume|- Price','127,659|NOK 0'),
          ('e)','Date of the transaction',date),('f)','Place of the transaction','XOFF - Outside trading venue')]
    top=55
    for marker,label,value in rows:
        cells.append((70,top,marker))
        for n,line in enumerate(label.split('|')):cells.append((92,top+n*12,line))
        for n,line in enumerate(value.split('|')):
            if line:cells.append((250,top+n*12-(above if marker=='b)' and value==kind else 0),line))
        top += max(25, 15+12*max(len(label.split('|')),len(value.split('|'))))
    return cells


class MarTests(unittest.TestCase):
    def test_actual_pdf_decoder_retains_zero_lending_and_price_headers(self):
        r=parse_attachment(pdf_pages([mar_cells()]),'form.pdf','Example')['transactions'][0]
        self.assertEqual('Lending of shares',r['transaction_type_text'])
        self.assertEqual('Price(s) Volume(s)\nNOK 0 127,659',r['price_volume_text'])
        self.assertIn('127,659\nNOK 0',r['aggregate_text'])
        self.assertEqual('2026-09-15',r['transaction_date_text'])

    def test_distinct_pages_keep_transactions_separate(self):
        rows=parse_attachment(pdf_pages([mar_cells(),mar_cells(kind='Purchase',amount='NOK 4.80 5,208,333',date='2026-09-03; 21:30 CEST')]),'form.pdf','Example')['transactions']
        self.assertEqual([1,2],[r['section'] for r in rows]);self.assertEqual('Purchase',rows[1]['transaction_type_text'])
        self.assertIn('4.80',rows[1]['price_volume_text']);self.assertEqual('2026-09-03; 21:30 CEST',rows[1]['transaction_date_text'])

    def test_section_4_1_and_value_baseline_offset_do_not_bleed(self):
        r=parse_attachment(pdf_pages([mar_cells(section='4.1',above=2)]),'form.pdf','Example')['transactions'][0]
        self.assertNotIn('Lending',r['instrument_text']);self.assertEqual('Lending of shares',r['transaction_type_text'])

    def test_unknown_label_or_invalid_date_fails(self):
        for kw in [{'heading':'Other prices'},{'date':'2026-02-30'},{'section':'4.2'}]:
            with self.subTest(kw=kw),self.assertRaises(SourceError):parse_attachment(pdf_pages([mar_cells(**kw)]),'form.pdf','Example')

    def test_scanned_unknown_and_announcement_are_explicit(self):
        self.assertEqual('unreadable_scan',parse_attachment(pdf_pages([[]]),'scan.pdf','Example')['status'])
        self.assertEqual('unsupported_format',parse_attachment(pdf_pages([[(50,50,'Unrecognised document with enough digital content.')]]),'x.pdf','Example')['status'])
        raw=pdf_pages([[(50,50,"Example announcement Managers' transaction Attachments")]])
        self.assertEqual('announcement_copy',parse_attachment(raw,'Download announcement as PDF.pdf','Example announcement')['status'])
        self.assertEqual('unsupported_format',parse_attachment(raw,'form.pdf','Example announcement')['status'])

    def test_truncated_pdf_and_page_limit_fail(self):
        raw=pdf_pages([mar_cells()])
        for data in [raw[:-20],b'<html>failure</html>']:
            with self.assertRaises(SourceError):parse_attachment(data,'form.pdf','Example')
        with self.assertRaises(SourceError):parse_attachment(pdf_pages([mar_cells(),mar_cells()]),'form.pdf','Example',1)
