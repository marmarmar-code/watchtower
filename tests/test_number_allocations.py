import csv
import io
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.number_allocations import NumberAllocationsSource, PAGE_URL, DATA_URL, TITLE, VISIBLE_HEADERS
from test_change_sources import poll, response

HEADERS=['Fra','Til','Tilbyder','Status','Kommentar','Antall','Kategori','Punktkode']

def row(start='21 00 00 00', end='21 00 00 99', holder='', status='Ledig', category='Mobilnummer'):
    return {'Fra':start,'Til':end,'Tilbyder':holder,'Status':status,'Kommentar':'','Antall':str(int(end.replace(' ',''))-int(start.replace(' ',''))+1),'Kategori':category,'Punktkode':''}


def csv_bytes(rows, headers=HEADERS):
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=headers);writer.writeheader();writer.writerows(rows)
    return buffer.getvalue().encode('utf-8-sig')


def html(rows):
    return ('<h1>'+TITLE+'</h1><a href="'+DATA_URL+'">E164.csv</a><table><thead><tr>'
            +''.join('<th>'+h+'</th>' for h in VISIBLE_HEADERS)+'</tr></thead><tbody>'
            +''.join('<tr>'+''.join('<td>'+r[h]+'</td>' for h in VISIBLE_HEADERS)+'</tr>' for r in rows)
            +'</tbody></table>').encode()


class NumberAllocationTests(unittest.TestCase):
    def source(self, **options):
        return NumberAllocationsSource(SourceConfig(id='numbers',kind='number_allocations',label='Nkom',urls=(PAGE_URL,),
            filters=FilterRule(match_all=True),options=options),timeout=1,retry_attempts=1)

    def load(self, source, rows, visible=None):
        page,data=html(visible if visible is not None else rows),csv_bytes(rows)
        source.get=Mock(side_effect=lambda url,**kw:response(page if url==PAGE_URL else data))

    def test_baseline_reorder_and_number_format_are_quiet(self):
        a,b=row(),row('22 00 00 00','22 00 00 99')
        s=self.source();self.load(s,[a,b]);first,alerts=poll(s);self.assertEqual([],alerts)
        a['Fra']='21000000';a['Til']='21000099'
        self.load(s,[b,a]);second,alerts=poll(s,first);self.assertEqual([],alerts);self.assertEqual(first,second)

    def test_assignment_category_and_point_code_change_use_same_key(self):
        s=self.source();r=row();self.load(s,[r]);state,_=poll(s)
        r.update(Tilbyder='Example Company',Status='Tildelt',Kategori='Other category',Punktkode='AB',Kommentar='Example comment')
        self.load(s,[r]);state,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertEqual(['21000000-21000099'],list(s._next['rows']))
        self.assertIn('Example Company',' '.join(alerts[0].item.alert_details));_,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_selection_applies_after_full_register_and_visible_row_validation(self):
        a,b=row(category='A'),row('22 00 00 00','22 00 00 99',category='B')
        s=self.source(categories=['B']);self.load(s,[a,b],visible=[a]);self.assertEqual(['22000000-22000099'],[r['key'] for r in s.read_records()])
        b['Antall']='99';self.load(s,[a,b],visible=[a])
        with self.assertRaises(SourceError):s.read_records()
        s=self.source(categories=['missing']);self.load(s,[a])
        with self.assertRaises(SourceError):s.read_records()

    def test_eight_and_twelve_digits_preserve_leading_zeros(self):
        s=self.source();r=row('01 00 00 00','01 00 00 99');self.load(s,[r,row('010000000000','010000000099')]);rows=s.read_records()
        self.assertEqual({'01000000-01000099','010000000000-010000000099'},{r['key'] for r in rows})

    def test_duplicate_overlapping_ranges_and_bad_arithmetic_rejected(self):
        cases=[[row(),row()],[row(),row('21 00 00 50','21 00 01 00',category='Other')]]
        for edit in [{'Antall':'99'},{'Antall':'1,00'},{'Fra':'2100000'},{'Til':'210000000099'},{'Fra':'21000100'},{'Status':'Unknown'},{'Status':'Tildelt'},{'Kategori':''}]:
            r=row();r.update(edit);cases.append([r])
        for rows in cases:
            s=self.source();self.load(s,rows)
            with self.subTest(rows=rows),self.assertRaises(SourceError):s.read_records()

    def test_html_mismatch_download_host_and_visible_duplicates_rejected(self):
        a=row();b=row();b['Status']='Blokkert'
        s=self.source();self.load(s,[a],visible=[b])
        with self.assertRaises(SourceError):s.read_records()
        s=self.source();self.load(s,[a],visible=[a,a])
        with self.assertRaises(SourceError):s.read_records()
        for page in [html([a]).replace(DATA_URL.encode(),b'https://example.test/E164.csv'),html([a]).replace(TITLE.encode(),b'Other title'),b'<h1>Unavailable</h1>']:
            s=self.source();s.get=Mock(return_value=response(page))
            with self.assertRaises(SourceError):s.read_records()
            self.assertEqual(1,s.get.call_count)

    def test_malformed_csv_header_and_missing_cells_fail(self):
        for data in [b'Fra,Til\n1,2\n',csv_bytes([row()]).replace(b'Kategori',b'Fra'),csv_bytes([row()])+b'1,2\n',b'\xff\xff']:
            s=self.source();s.get=Mock(side_effect=[response(html([row()])),response(data)])
            with self.assertRaises(SourceError):s.read_records()

    def test_csv_column_order_thousand_separator_and_quoted_holder(self):
        r=row('21000000','21000999',holder='Example, Company',status='Tildelt');r['Antall']='1,000'
        s=self.source();s.get=Mock(side_effect=[response(html([r])),response(csv_bytes([r],list(reversed(HEADERS))))]);fields=s.read_records()[0]['fields']
        self.assertEqual(1000,fields['quantity']);self.assertEqual('Example, Company',fields['holder'])

    def test_record_byte_bounds_and_redirects(self):
        for options in [{'max_records':1},{'max_register_records':1}]:
            s=self.source(**options);self.load(s,[row(),row('22000000','22000099')])
            with self.assertRaises(SourceError):s.read_records()
        for reply in [response(b'x'*1025),response(b'',status=302,headers={'Location':DATA_URL})]:
            s=self.source(max_bytes=1024);s.get=Mock(return_value=reply)
            with self.assertRaises(SourceError):s.read_records()
            reply.close.assert_called_once();self.assertFalse(s.get.call_args.kwargs['allow_redirects'])

    def test_no_removal_claim_and_empty_register_rejected(self):
        s=self.source();a,b=row(),row('22000000','22000099');self.load(s,[a,b]);state,_=poll(s)
        self.load(s,[a]);_,alerts=poll(s,state);self.assertEqual([],alerts)
        self.load(s,[])
        with self.assertRaises(SourceError):poll(s,state)
        for options in [{'complete_snapshot':True},{'events':['removed']},{'categories':[]},{'categories':'Mobilnummer'}]:
            with self.subTest(options=options),self.assertRaises(ValueError):self.source(**options)


if __name__=='__main__':
    unittest.main()
