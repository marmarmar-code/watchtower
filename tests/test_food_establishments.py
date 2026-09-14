import csv
import io
import unittest
from copy import deepcopy
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.food_establishments import FoodEstablishmentsSource, PROFILES, APPROVAL_HEADERS, MEAT_HEADERS
from test_change_sources import poll


def source(profile='dairy', **options):
    return FoodEstablishmentsSource(SourceConfig(id='approvals', kind='food_establishments', label='Food approvals',
        filters=FilterRule(match_all=True), options={'list_type':profile, **options}))


def row(profile='dairy'):
    if profile == 'meat':
        r = dict.fromkeys(MEAT_HEADERS, '')
        r.update(GODKJENNINGSNUMMER='123', VIRKSOMHETSNAVN='Example plant', POSTSTED='Example town', PRODUKSJONSFORM='SH', ART='B, O', SEKSJON='Section 1 - Meat of domestic ungulates')
    else:
        r = dict.fromkeys(APPROVAL_HEADERS, '')
        r.update(APPROVALID='M123', ORGANIZATIONNAME='Example plant', ORGANIZATIONTOWN='Example town', CATEGORY='PP, RW', LISTCATEGORYID='1011', LISTCATEGORY='Approved establishments',
            LISTTYPEID=PROFILES[profile][2], LISTTYPE='Section 9 - '+PROFILES[profile][3], ACTIVITIES='Food processing', APPROVALFROMDATE='2020-01-01')
    return r


def csv_bytes(rows):
    s=io.StringIO(newline=''); writer=csv.DictWriter(s, fieldnames=sorted(rows[0]));writer.writeheader();writer.writerows(rows)
    return s.getvalue().encode()


def replies(s, rows):
    ident='GODKJENNINGSNUMMER' if s.profile=='meat' else 'APPROVALID'
    url='https://mattilsynet-xp7prod.enonic.cloud/_/attachment/inline/11111111-1111-1111-1111-111111111111:'+'a'*40+'/'+s.filename
    html=f'<h1>{s.section}</h1><a href="{url}">Last ned (CSV)</a><table><thead><tr><th>{ident}</th></tr></thead><tbody>'
    html+=''.join('<tr><td>'+r[ident]+'</td></tr>' for r in rows)+'</tbody></table>'
    return [response(html.encode()),response(csv_bytes(rows))]


def response(raw, status=200):
    return Mock(status_code=status, iter_content=Mock(return_value=[raw]), close=Mock())


class FoodEstablishmentsTests(unittest.TestCase):
    def test_duplicate_entries_reorder_codes_and_date_changes(self):
        s=source();r=row();second={**r,'CATEGORY':'CC', 'APPROVALFROMDATE':'2022-01-01'};rows=[r,r,second]
        s.get=Mock(side_effect=replies(s,rows));state,alerts=poll(s);self.assertEqual([],alerts)
        self.assertEqual(2,len(s._next['rows']['M123']['row']['fields']['entries']))
        r['CATEGORY']='RW, PP';s.get.side_effect=replies(s,list(reversed(rows)))
        after,alerts=poll(s,state);self.assertEqual([],alerts);self.assertEqual(state,after)
        second['APPROVALTODATE']='2026-12-31';s.get.side_effect=replies(s,rows)
        after,alerts=poll(s,after);self.assertEqual(1,len(alerts));self.assertIn('2026-12-31',' '.join(alerts[0].item.alert_details))
        new={**r,'APPROVALID':'M999'};s.get.side_effect=replies(s,[*rows,new])
        after,alerts=poll(s,after);self.assertEqual(1,len(alerts));self.assertIn('Nyobservert',' '.join(alerts[0].item.alert_details))
        s.get.side_effect=replies(s,[new]);_,alerts=poll(s,after);self.assertEqual([],alerts)

    def test_meat_groups_species_and_forms(self):
        s=source('meat');r=row('meat');other={**r,'PRODUKSJONSFORM':'CP'}
        s.get=Mock(side_effect=replies(s,[r,other]));state,alerts=poll(s);self.assertEqual([],alerts)
        r['ART']='O, B';s.get.side_effect=replies(s,[other,r]);after,alerts=poll(s,state);self.assertEqual([],alerts);self.assertEqual(state,after)
        r['ART']='O';s.get.side_effect=replies(s,[other,r]);_,alerts=poll(s,state);self.assertEqual(1,len(alerts))

    def test_csv_identity_completeness_and_schema(self):
        s=source();r=row()
        for raw,ids in [(csv_bytes([r]),['OTHER']), (csv_bytes([r]),['M123','M456']),
                        (b'APPROVALID\nM123\n',['M123']), (csv_bytes([r])+b'extra,row\n',['M123','extra'])]:
            with self.subTest(ids=ids),self.assertRaises(SourceError):s._records(raw,ids)
        for field,value in [('APPROVALID',''),('LISTTYPEID','other'),('APPROVALFROMDATE','2026-02-30'),('APPROVALTODATE','2019-12-31'),('ORGANIZATIONNAME','')]:
            bad={**r,field:value}
            with self.subTest(field=field),self.assertRaises(SourceError):s._records(csv_bytes([bad]),[bad['APPROVALID']])

    def test_eggs_scope_and_blank_dates(self):
        s=source('eggs');r=row('eggs');r['APPROVALFROMDATE']=''
        records=s._records(csv_bytes([r]),['M123']);self.assertIsNone(records[0]['fields']['entries'][0]['valid_to'])
        self.assertIsNone(records[0]['fields']['entries'][0]['valid_from'])
        with self.assertRaisesRegex(SourceError,'section'):source()._records(csv_bytes([r]),['M123'])

    def test_redirect_attachment_limit_and_table_guards(self):
        s=source();r=response(b'',302);s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        r.close.assert_called_once();self.assertFalse(s.get.call_args.kwargs['allow_redirects'])
        s.max_bytes=1024;r=response(b'x'*1025);s.get.return_value=r
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        r.close.assert_called_once()
        s=source();valid=replies(s,[row()])[0].iter_content()[0]
        for raw in [valid.replace(b'mattilsynet-xp7prod.enonic.cloud',b'user@mattilsynet-xp7prod.enonic.cloud'),valid.replace(b'.csv"',b'.csv?x=1"'),valid.replace(b'Approval',b'Other') if b'Approval' in valid else b'<h1>Wrong page</h1>']:
            s.get=Mock(return_value=response(raw))
            with self.assertRaises(SourceError):s.read_records()
        for options in ({'complete_snapshot':True},{'events':['removed']},{'list_type':'unknown'}):
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
