import unittest
from copy import deepcopy
from unittest.mock import Mock
from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.pesticides import PesticidesSource
from watchtower.sources.common import SourceError
from test_change_sources import poll, response

PRODUCT={'id':1,'navn':'Product','status':'Godkjent','type':'Type','virksommeStoffer':[{'id':2,'virksomtStoff':'Substance'}],'godkjentTil':'2026-12-31T00:00:00.000Z'}
PERMIT={'bruksbetingelser':'1_42','bruksbetingelserURL':'/etikett/bruksbetingelser/1_42','godkjenningsType':'Midlertidlig tillatelse','behandlet':'2026-01-01T00:00:00Z','godkjentTil':'2026-09-17T00:00:00Z'}

def source(mode='products'):
    return PesticidesSource(SourceConfig(id='pesticides',kind='pesticides',label='Pesticides',urls=(),filters=FilterRule(match_all=True),options={'mode':mode}))

class PesticideTests(unittest.TestCase):
    def test_missing_optional_fields_and_substance_order_are_quiet(self):
        s=source();p=deepcopy(PRODUCT);s.get=Mock(return_value=response({'preparater':[p]}));state,alerts=poll(s)
        self.assertEqual([],alerts);self.assertIsNone(s._next['rows']['1']['row']['published'])
        p['virksommeStoffer'][0]['id']=99;s.get.return_value=response({'preparater':[p]});_,alerts=poll(s,state);self.assertEqual([],alerts)
        p['godkjentTil']='2027-12-31T00:00:00Z';s.get.return_value=response({'preparater':[p]});_,alerts=poll(s,state);self.assertEqual(1,len(alerts))

    def test_temporary_permission_has_separate_identity(self):
        p={**PRODUCT,'midlertidigeTillatelser':[PERMIT]};s=source('temporary_permits');s.get=Mock(return_value=response({'preparater':[p]}));r=s.read_records()[0]
        self.assertEqual('1_42',r['key']);self.assertTrue(r['url'].endswith('/1_42'))
        p['midlertidigeTillatelser']=[{**PERMIT,'bruksbetingelser':'2_42'}];s.get.return_value=response({'preparater':[p]})
        with self.assertRaises(SourceError):s.read_records()

    def test_invalid_identity_duplicate_and_date_fail_closed(self):
        for products in ([{**PRODUCT,'id':True}],[PRODUCT,PRODUCT],
                         [{**PRODUCT,'godkjentTil':'2026-02-31T00:00:00Z'}],
                         [{**PRODUCT,'status':'Utgått'}], [{**PRODUCT,'virksommeStoffer':[]}],
                         [{**PRODUCT,'behandlet':'2099-01-01T00:00:00Z'}]):
            s=source();s.get=Mock(return_value=response({'preparater':products}))
            with self.assertRaises(SourceError):s.fetch_with_state(None)
