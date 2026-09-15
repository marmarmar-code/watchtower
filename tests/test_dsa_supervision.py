from copy import deepcopy
import tempfile
import unittest
from unittest.mock import Mock
from xml.sax.saxutils import escape

from watchtower.config import Config, SourceConfig, FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.dsa_supervision import DsaSupervisionSource
from watchtower.sources.common import SourceError
from test_change_sources import response, poll


def action(day='15.09.2026', description='preliminary findings', suffix='example'):
    return '<li>'+day+': '+escape(description)+' (<a href="https://digital-strategy.ec.europa.eu/en/news/'+suffix+'">press release</a>)</li>'


def service(name='Example service', provider='Example provider', anchor='example', actions=None, users='50', kind='Very large online platform'):
    labels=['Main establishment of the provider in the EU' if provider else '', 'Designated service', 'Type of service under DSA',
            'Average monthly active users in millions*','Digital Services Coordinator as of 17 February 2024','DSA enforcement actions']
    values=[f'<h2 id="ecl-inpage-{anchor}">{provider}</h2>' if provider else '',name,kind,users,'Ireland',
            '<ul>'+(''.join(actions) if actions is not None else action())+'</ul>']
    return '<div class="ecl-container">'+''.join('<div class="ecl-row"><div>'+label+'</div><div>'+value+'</div></div>' for label,value in zip(labels,values))+'</div>'


def page(*services, stamp='15 September 2026', before='', after=''):
    return ('<html><head><title>Example</title></head><body><h1>Supervision of the designated very large online platforms and search engines under DSA</h1>'+before+
            '<div class="ecl"><pre>Information updated on '+stamp+'.</pre>'+''.join(services or [service()])+'<h2 id="ecl-inpage-notes">Notes</h2></div>'+after+'</body></html>').encode()


def source(**options):
    return DsaSupervisionSource(SourceConfig(id='dsa',kind='dsa_supervision',label='DSA supervision',urls=(),filters=FilterRule(match_all=True),options=options))


def install(s, first=None, second=None):
    first=page() if first is None else first
    s.get=Mock(side_effect=[response(first),response(first if second is None else second)])


class DsaTests(unittest.TestCase):
    def test_baseline_repeat_and_action_reorder_quiet(self):
        s=source();a=action();b=action('14.09.2026','request for information','request')
        install(s,page(service(actions=[a,b])));old,alerts=poll(s);self.assertEqual([],alerts)
        install(s,page(service(actions=[b,a])));new,alerts=poll(s,old);self.assertEqual(old,new);self.assertEqual([],alerts)
        self.assertEqual('2026-09-15',new['source_state']['records']['overview_date'])

    def test_new_action_once_preserves_preliminary_description(self):
        s=source();install(s);old,_=poll(s)
        body=page(service(actions=[action(),action('16.09.2026','decision accepting binding commitments','decision')]),stamp='16 September 2026')
        install(s,body);new,alerts=poll(s,old);self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        self.assertIn('decision accepting binding commitments',' '.join(alerts[0].item.alert_details))
        actions=new['source_state']['records']['rows']['example service']['row']['fields']['actions']
        self.assertTrue(any('preliminary findings' in a['description'] for a in actions))
        install(s,body);self.assertEqual([],poll(s,new)[1])

    def test_same_date_different_actions_and_description_correction(self):
        s=source();install(s,page(service(actions=[action(),action(description='opening of proceedings',suffix='opening')])));old,_=poll(s)
        install(s,page(service(actions=[action(description='corrected preliminary findings'),action(description='opening of proceedings',suffix='opening')])));new,alerts=poll(s,old)
        self.assertEqual(1,len(alerts));self.assertEqual(2,len(new['source_state']['records']['rows']['example service']['row']['fields']['actions']))
        self.assertIn('fastslår ikke',' '.join(alerts[0].item.alert_details))

    def test_document_link_change_is_monitored(self):
        s=source();install(s);old,_=poll(s)
        install(s,page(service(actions=[action(suffix='corrected-document')])));_,alerts=poll(s,old);self.assertEqual(1,len(alerts))

    def test_source_termination_text_is_preserved_literally(self):
        kind='As of 15 September 2026, designation was terminated by a Commission decision.'
        s=source();install(s,page(service(kind=kind)));state,alerts=poll(s);self.assertEqual([],alerts)
        self.assertEqual(kind,state['source_state']['records']['rows']['example service']['row']['fields']['service_type'])

    def test_provider_continuations_and_multiple_services(self):
        s=source();install(s,page(service(),service(name='Other service',provider='')));state,_=poll(s)
        rows=state['source_state']['records']['rows'];self.assertEqual(2,len(rows))
        self.assertEqual('Example provider',rows['other service']['row']['fields']['provider'])
        self.assertTrue(rows['other service']['row']['url'].endswith('#ecl-inpage-example'))
        install(s,page(service(provider='')))
        with self.assertRaisesRegex(SourceError,'preceding provider'):s.read_records()

    def test_users_footer_and_revision_stamp_do_not_create_alerts(self):
        s=source();install(s);old,_=poll(s)
        body=page(service(users='60'),stamp='16 September 2026',after='<p>Changed unrelated footer</p>')
        install(s,body);new,alerts=poll(s,old);self.assertEqual([],alerts)
        self.assertNotIn('users',new['source_state']['records']['rows']['example service']['row']['fields'])
        self.assertEqual('2026-09-16',new['source_state']['records']['overview_date'])

    def test_exact_selection_and_missing_selection(self):
        s=source(services=['Other service']);install(s,page(service(),service(name='Other service',provider='')))
        self.assertEqual(['Other service'],[r['title'] for r in s.read_records()])
        install(s,page())
        with self.assertRaisesRegex(SourceError,'selected DSA service is absent'):s.read_records()
        s=source(services=['example service']);install(s)
        with self.assertRaises(SourceError):s.read_records()

    def test_absence_and_reappearance_quiet_without_closure(self):
        s=source();install(s,page(service(),service(name='Other service',provider='')));old,_=poll(s)
        install(s,page());missing,alerts=poll(s,old);self.assertEqual([],alerts)
        self.assertEqual(old['seen'],missing['seen'])
        install(s,page(service(),service(name='Other service',provider='')));self.assertEqual([],poll(s,missing)[1])

    def test_regression_and_mid_read_drift_preserve_persisted_state(self):
        s=source();install(s);old,_=poll(s);saved=deepcopy(old)
        for a,b in [(page(stamp='14 September 2026'),None),(page(),page(service(actions=[action(description='changed')])) )]:
            install(s,a,b)
            with tempfile.TemporaryDirectory() as directory:
                store=StateStore(directory);store.save('dsa',old)
                outcome=run(Config((s.config,)),store,None,source_factory=lambda _:s)
                self.assertIn('dsa',outcome.errors);self.assertEqual(saved,store.load('dsa'))

    def test_invalid_labels_dates_identity_and_duplicate_entries(self):
        bads=[page().replace(b'Designated service',b'Other field'),page().replace(b'id="ecl-inpage-notes"',b'id="missing"'),
              page().replace(b'<h1>Supervision',b'<h1>Other'),page(stamp='31 February 2026'),page(service(actions=[action('31.02.2026')])),
              page(service(actions=[action(),action()])),page(service(),service()),page(service(),service(name='Other service')),
              page(service(actions=[])),page(service(actions=[action().replace('15.09.2026: ','')])),page(service(name=''))]
        for bad in bads:
            s=source();install(s,bad)
            with self.subTest(bad=bad[:40]),self.assertRaises(SourceError):s.read_records()

    def test_unsafe_or_missing_links_fail_closed_even_in_unselected_service(self):
        for replacement in ['https://example.test/doc','javascript:alert(1)','http://ec.europa.eu/doc']:
            bad=page(service(actions=[action().replace('https://digital-strategy.ec.europa.eu/en/news/example',replacement)]),service(name='Other service',provider=''))
            s=source(services=['Other service']);install(s,bad)
            with self.assertRaises(SourceError):s.read_records()
        s=source();install(s,page(service(actions=['<li>15.09.2026: preliminary findings</li>'])))
        with self.assertRaises(SourceError):s.read_records()

    def test_transport_bounds_and_record_limits(self):
        for status,body,options in [(302,page(),{}),(200,page()[:-20],{}),(200,b'x'*2048,{'max_bytes':1024}),(200,page().replace(b'Example</title>',b'\xff</title>'),{})]:
            s=source(**options);r=response(body,status=status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()
        for options,body in [({'max_services':1},page(service(),service(name='Other',provider=''))),
                             ({'max_records':1},page(service(),service(name='Other',provider=''))),
                             ({'max_actions_per_service':1},page(service(actions=[action(),action(suffix='other')])) )]:
            s=source(**options);install(s,body)
            with self.subTest(options=options),self.assertRaises(SourceError):poll(s)

    def test_configuration_disallows_removal_and_invalid_limits(self):
        for options in [{'services':[]},{'max_services':True},{'max_actions_per_service':0},{'events':['removed']},{'complete_snapshot':True}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
