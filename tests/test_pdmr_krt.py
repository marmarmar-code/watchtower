import unittest
from watchtower.sources.pdmr_transactions import parse_attachment
from watchtower.sources.pdmr_krt import LABELS, NOTE
from watchtower.sources.common import SourceError
from pdmr_pdf_fixture import pdf_pages


def krt_pdf(*, related=False, correction=False, extra=None, date='05.06.2026', price='0', comment='Du har ikke lagt inn informasjon her'):
    values={'1.3.1':'Nærstående person' if related else 'Primærinnsider',
            '2.1':'Korrigering' if correction else 'Ny melding',
            '2.2.1':'549300XBITM62HH7HW18','2.2.2':'Example ASA',
            '2.3.1':'Aksje','2.3.2':'NO0000000000','2.3.2.1':'Example ASA',
            '2.4.1':'Salg','2.5.1':'Nei','2.6.1':'NOK','2.8.1':price,'2.8.2':'3 250','2.8.3':'0',
            '2.9.1':date,'2.10.1':'XOFF - Utenfor en handelsplass','2.11':comment}
    if related:values.update({'1.5.2':'Example Actor','1.7.1':'Example Insider','1.7.2':'CTO'})
    else:values.update({'1.4.2':'Example Actor','1.4.4':'Director'})
    if correction:values['2.1.1']='Corrected volume in an earlier report.'
    if extra:values.update(extra)
    pages=[[(40,30,'KRT-1500 Skjema for melding om transaksjoner utført av personer med ledelsesansvar'),(40,50,'Referansenummer: abcdef123456'),(40,70,'1.2.1 Rapportøres Navn'),(40,84,'REPORTER MUST NOT BE ACTOR')],[]]
    tops=[110,40]
    for number in sorted(values,key=lambda n:tuple(map(int,n.split('.')))):
        n=0 if number.startswith('1.') else 1;top=tops[n]
        label=LABELS.get(number,'Unknown field :'); val=values[number]
        if label.endswith(' :'):
            pages[n].append((40,top,number+' '+label+' '+val));top+=15
        else:
            pages[n].append((40,top,number+' '+label));pages[n].append((40,top+14,val));top+=30
        if number=='2.10.1':
            # Wrap the form note without changing its literal words.
            a=NOTE.rsplit(' så ',1);pages[n].append((40,top,a[0]));pages[n].append((40,top+14,'så '+a[1]));top+=30
        tops[n]=top
    return pdf_pages(pages)


class KrtTests(unittest.TestCase):
    def parse(self,**kw):return parse_attachment(krt_pdf(**kw),'form.pdf','Example')['transactions'][0]
    def test_actual_decoder_keeps_source_numbers_currency_and_actor(self):
        r=self.parse(price='159,288');self.assertEqual('Example Actor',r['actor_text']);self.assertNotIn('REPORTER',str(r))
        self.assertIn('159,288',r['price_volume_text']);self.assertIn('NOK',r['aggregate_text']);self.assertIn('3 250',r['aggregate_text'])
        self.assertEqual('05.06.2026',r['transaction_date_text']);self.assertIsNone(r['comment_text'])
    def test_related_actor_and_insider_are_separate(self):
        r=self.parse(related=True,comment='Delayed report.');self.assertEqual('Example Actor',r['actor_text']);self.assertEqual('Example Insider',r['related_insider_text']);self.assertIn('CTO',r['capacity_text']);self.assertEqual('Delayed report.',r['comment_text'])
    def test_correction_description_and_zero_remain_explicit(self):
        r=self.parse(correction=True);self.assertEqual('Korrigering',r['notification_text']);self.assertIn('Corrected volume',r['correction_text']);self.assertIn('enhet: 0;',r['price_volume_text'])
    def test_unknown_financial_field_or_bad_date_fails(self):
        for kw in [{'extra':{'2.7.1':'10'}},{'date':'30.02.2026'},{'extra':{'2.1.1':'Unlinked correction'}}]:
            with self.subTest(kw=kw),self.assertRaises(SourceError):self.parse(**kw)
