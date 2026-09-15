from copy import deepcopy
from datetime import datetime,timezone
from html import escape
import tempfile
import unittest
from unittest.mock import Mock,patch

from watchtower.config import Config,SourceConfig,FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.lobbying_finance import LobbyingFinanceSource,OWN,NONCOMMERCIAL
from watchtower.sources.common import SourceError
from test_change_sources import response,poll


def cost(tag='costs',minimum='10000',maximum='24999'):
    return f'<{tag} type="CostRange" currency="€"><range>'+('' if minimum is None else '<min>'+minimum+'</min>')+('' if maximum is None else '<max>'+maximum+'</max>')+f'</range></{tag}>'


def absolute(tag='amount',amount='1000'):
    return f'<{tag} type="AbsoluteCost" currency="€"><absoluteCost>{amount}</absoluteCost></{tag}>'


def grant(amount='1000',name='Example programme'):
    return '<grant>'+absolute(amount=amount)+'<source>'+escape(name)+'</source></grant>'


def financial(ngo=False,new=False,amount=None,grants='',intermediaries='',start='2025-01-01',end='2025-12-01'):
    kind='NGO' if ngo else 'Intermediary'
    if new:closed='<closedYear/>'
    else:
        fields='<fundingSources><fundingSource><source>Public funding</source></fundingSource></fundingSources><contributions/>'+ (absolute('totalBudget','100000') if amount is None else amount) if ngo else '<intermediaries>'+intermediaries+'</intermediaries>'+(cost() if amount is None else amount)
        closed=f'<closedYear type="ClosedYear{kind}FinancialInformation"><startDate>{start}</startDate><endDate>{end}</endDate>'+fields+'<grants>'+grants+'</grants></closedYear>'
    current=f'<currentYear type="CurrentYear{kind}FinancialInformation">'+('' if ngo else '<intermediaries><intermediary><name>Example consultant</name></intermediary></intermediaries>')+'<grants>'+grants+'</grants></currentYear>'
    return '<financialData><newOrganisation>'+str(new).lower()+'</newOrganisation>'+closed+current+'</financialData>'


def actor(ident='123456789-01',country='NORWAY',ngo=False,fin=None,goals='Example goals'):
    return f'<interestRepresentative><identificationCode>{ident}</identificationCode><registrationDate>2026-01-01T00:00:00+00:00</registrationDate><name><originalName>Example {ident}</originalName></name><entityForm>Example entity</entityForm><registrationCategory>Example category</registrationCategory><headOffice><country>{country}</country><address>Unmonitored contact</address></headOffice><interestRepresented>'+escape(NONCOMMERCIAL if ngo else OWN)+'</interestRepresented><goals>'+goals+'</goals>'+ (financial(ngo=ngo) if fin is None else fin)+'</interestRepresentative>'


def export(*actors,count=None,stamp='2026-09-14T20:00:00.071+00:00'):
    actors=actors or (actor(),)
    return ('<?xml version="1.1" encoding="UTF-8"?><ListOfIRPublicDetail xmlns="http://intragate.ec.europa.eu/transparencyregister/odp"><metaData xmlns=""><exportDate>'+stamp+'</exportDate><numberOfIR>'+str(len(actors) if count is None else count)+'</numberOfIR></metaData><resultList xmlns="">'+''.join(actors)+'</resultList></ListOfIRPublicDetail>').encode()


def source(**options):
    return LobbyingFinanceSource(SourceConfig(id='lobby',kind='lobbying_finance',label='Financial declarations',urls=(),filters=FilterRule(match_all=True),options=options))


def install(s,first=None,second=None):
    first=export() if first is None else first;s.get=Mock(side_effect=[response(first),response(first if second is None else second)])


class LobbyingFinanceTests(unittest.TestCase):
    def setUp(self):
        p=patch('watchtower.sources.lobbying_finance.now',return_value=datetime(2026,9,15,12,tzinfo=timezone.utc));p.start();self.addCleanup(p.stop)

    def test_baseline_repeat_cost_range_and_literal_period(self):
        s=source();install(s);old,alerts=poll(s);self.assertEqual([],alerts);r=old['source_state']['records']['rows']['123456789-01']['row']
        self.assertIsNone(r['published']);closed=r['fields']['financial_declaration']['closed_year'];self.assertEqual('2025-12-01',closed['period_end'])
        self.assertEqual({'type':'CostRange','currency':'€','minimum':'10000','maximum':'24999'},closed['estimated_activity_costs'])
        install(s);new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_ngo_total_budget_is_separate_from_activity_costs(self):
        s=source();install(s,export(actor(ngo=True)));state,_=poll(s);f=state['source_state']['records']['rows']['123456789-01']['row']['fields']['financial_declaration']
        self.assertEqual('100000',f['closed_year']['total_budget']['amount']);self.assertNotIn('estimated_activity_costs',f['closed_year'])
        self.assertNotIn('period_start',f['current_year_declaration']);self.assertEqual([],f['current_year_declaration']['grants'])

    def test_missing_range_bound_is_not_zero(self):
        s=source();install(s,export(actor(fin=financial(amount=cost(minimum=None,maximum='10000')))));old,_=poll(s)
        f=old['source_state']['records']['rows']['123456789-01']['row']['fields']['financial_declaration']['closed_year']['estimated_activity_costs']
        self.assertIsNone(f['minimum']);self.assertEqual('10000',f['maximum'])
        install(s,export(actor(fin=financial(amount=cost(minimum='0',maximum='10000')))));_,alerts=poll(s,old);self.assertEqual(1,len(alerts))

    def test_new_organisation_without_closed_year_can_transition(self):
        s=source();install(s,export(actor(fin=financial(new=True))));old,alerts=poll(s);self.assertEqual([],alerts)
        f=old['source_state']['records']['rows']['123456789-01']['row']['fields']['financial_declaration'];self.assertTrue(f['new_organisation']);self.assertIsNone(f['closed_year'])
        install(s);new,alerts=poll(s,old);self.assertEqual(1,len(alerts));install(s);self.assertEqual([],poll(s,new)[1])

    def test_cost_and_intermediary_change_alert_once(self):
        for f in [financial(amount=cost(minimum='25000',maximum='49999')),financial(intermediaries='<intermediary><name>Example adviser</name>'+cost('representationCosts')+'</intermediary>')]:
            s=source();install(s);old,_=poll(s);body=export(actor(fin=f));install(s,body);new,alerts=poll(s,old)
            self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event']);install(s,body);self.assertEqual([],poll(s,new)[1])

    def test_repeated_grants_preserve_multiplicity_and_ignore_order(self):
        s=source();a=grant();b=grant('2000','Other programme');install(s,export(actor(fin=financial(grants=a+a+b))));old,_=poll(s)
        f=old['source_state']['records']['rows']['123456789-01']['row']['fields']['financial_declaration'];self.assertEqual(3,len(f['closed_year']['grants']))
        install(s,export(actor(fin=financial(grants=b+a+a))));new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)

    def test_export_stamp_and_ignored_contact_updates_do_not_alert(self):
        s=source();install(s);old,_=poll(s)
        body=export(actor(goals='Other goals'),stamp='2026-09-15T06:00:00+00:00').replace(b'Unmonitored contact',b'Changed contact')
        install(s,body);new,alerts=poll(s,old);self.assertEqual([],alerts);self.assertNotEqual(old['source_state']['records']['exported_at'],new['source_state']['records']['exported_at'])

    def test_country_selection_and_new_registration_without_removal(self):
        s=source();a=actor();b=actor('123456789-02');install(s,export(a));old,_=poll(s)
        install(s,export(a,b));new,alerts=poll(s,old);self.assertEqual(1,len(alerts))
        install(s,export(a));missing,alerts=poll(s,new);self.assertEqual([],alerts);self.assertEqual(new['seen'],missing['seen'])
        install(s,export(a,b));self.assertEqual([],poll(s,missing)[1])
        install(s,export(a,actor('123456789-03',country='SWEDEN',fin='<financialData/>')));self.assertEqual(1,len(s.read_records()))
        s=source(countries=['DENMARK']);install(s)
        with self.assertRaisesRegex(SourceError,'country is absent'):s.read_records()

    def test_xml11_compatibility_only_in_unmonitored_fields(self):
        s=source();install(s,export(actor(goals='Text &#x2; text &#11;')));state,_=poll(s);self.assertEqual(2,s.compatibility_references)
        self.assertNotIn('\ufffd',str(state))
        for body in [export().replace(b'Example entity',b'Example &#x2; entity'),export().replace(b'10000',b'10&#x2;000'),export(actor(goals='&#0;')),
                     export(actor(goals='&#x2;')).replace(b'version="1.1"',b'version="1.0"')]:
            s=source();install(s,body)
            with self.assertRaises(SourceError):s.read_records()

    def test_regression_mid_read_and_bad_finance_preserve_saved_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old)
        for first,second in [(export(stamp='2026-09-14T19:00:00+00:00'),None),(export(),export(actor(fin=financial(amount=cost(minimum=None))))),
                             (export().replace('currency="€"'.encode(),b'currency="USD"'),None)]:
            install(s,first,second)
            with tempfile.TemporaryDirectory() as directory:
                store=StateStore(directory);store.save('lobby',old);outcome=run(Config((s.config,)),store,None,source_factory=lambda _:s)
                self.assertIn('lobby',outcome.errors);self.assertEqual(saved,store.load('lobby'))

    def test_export_total_identity_namespace_and_freshness_guards(self):
        bads=[export(count=2),export(actor(),actor()),export().replace(b'123456789-01',b'invalid'),export().replace(b'numberOfIR>1',b'numberOfIR>0'),
              export(stamp='2026-09-01T20:00:00+00:00'),export(stamp='2026-09-16T20:00:00+00:00'),export(stamp='2026-09-14T20:00:00'),
              export().replace(b'/transparencyregister/odp',b'/other/odp'),export().replace(b'<identificationCode>',b'<identificationCode>123456789-02</identificationCode><identificationCode>')]
        for body in bads:
            s=source();install(s,body)
            with self.subTest(body=body[:40]),self.assertRaises(SourceError):s.read_records()

    def test_finance_bounds_types_dates_and_required_fields(self):
        bads=[financial(amount=cost(minimum=None,maximum=None)),financial(amount=cost(minimum='50000',maximum='10000')),
              financial(amount=cost(minimum='-1')),financial(amount=cost(minimum='1e100')),financial(start='2025-02-30'),financial(start='2026-01-01'),
              financial().replace('type="CostRange"','type="Unknown"'),financial().replace('<currentYear type="CurrentYearIntermediaryFinancialInformation">','<currentYear type="CurrentYearNGOFinancialInformation">'),
              financial().replace('<newOrganisation>false','<newOrganisation>true'),financial().replace('<costs ','<unknown ').replace('</costs>','</unknown>')]
        for f in bads:
            s=source();install(s,export(actor(fin=f)))
            with self.subTest(fin=f[:40]),self.assertRaises(SourceError):s.read_records()

    def test_transport_and_record_limits(self):
        for status,body,options in [(302,export(),{}),(200,export()[:-30],{}),(200,b'x'*2048,{'max_download_bytes':1024}),
                                    (200,export().replace(b'Example entity',b'\xff'),{}),(200,export().replace(b'<ListOfIRPublicDetail',b'<!DOCTYPE x><ListOfIRPublicDetail'),{})]:
            s=source(**options);r=response(body,status=status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()
        for options in [{'max_records':1},{'max_export_records':1}]:
            s=source(**options);install(s,export(actor(),actor('123456789-02')))
            with self.assertRaises(SourceError):s.read_records()

    def test_configuration_rejects_unsafe_scope_and_removals(self):
        for options in [{'countries':[]},{'countries':['Norway']},{'max_download_bytes':True},{'max_export_age_days':0},{'max_export_records':0},{'complete_snapshot':True},{'events':['removed']}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
