from copy import deepcopy
from datetime import date
import tempfile
import unittest
from unittest.mock import Mock,patch

from watchtower.config import Config,SourceConfig,FilterRule
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.bankruptcy_notices import BankruptcyNoticesSource
from watchtower.sources.common import SourceError
from test_change_sources import poll,response

ORG='999999991'
KID='20260000000001'


def page(body):
    return ('<html><head><title>Kunngjøringer - Brønnøysundregistrene</title></head><body>'+body+'</body></html>').encode('latin1')


def result(kid=KID,org=ORG,published='15.09.2026',name='Example AS',kind='Konkursåpning'):
    return {'kid':kid,'org':org,'published':published,'name':name,'kind':kind}


def index(rows=None,start='13.09.2026',end='15.09.2026',count=None):
    rows=[result()] if rows is None else rows
    body='<table><tr><td>Dato</td><td>'+start+' til '+end+'</td></tr><tr><td>Sted</td><td>Hele landet</td></tr><tr><td>Kunngjøringstype</td><td>Konkurs/tvangsavvikling</td></tr>'
    if rows or count is not None:body+=f'<tr><td>Antall treff</td><td>{len(rows) if count is None else count}</td></tr>'
    if not rows:rows=[dict(kid='',org='',published='',name='',kind='')]
    for row in rows:
        link=f'<a href="hent_en.jsp?kid={row["kid"]}&sokeverdi={row["org"]}&spraak=nb">{row["kind"]}</a>'
        cells=['',row['name'],'',row['org'],'',row['published'],'',link,'']
        body+='<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>'
    return page(body+'</table>')


def detail(org=ORG,published='15.09.2026',opening='14.09.2026',claim='15.10.2026',frist='12.09.2026',basis='Oppbud',meeting='20.10.2026',time='10:00',trustee='Adv. Example Trustee'):
    rows=[('Navn/foretaksnavn :','Example AS'),('Adresse:','Ignored private address'),('Organisasjonsnummer:',org),
          ('Konkurs åpnet:',opening),('Saksnr:','26-1KON-TOSL')]
    if basis is not None:rows.append(('Åpnet etter:',basis))
    body='<h3>Konkurs - åpning</h3>Ved OSLO TINGRETT er det åpnet konkurs i boet til:'
    body+='<table>'+''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k,v in rows)+'</table>'
    body+=(f'Krav i boet meldes bostyrer, {trustee}, Ignored contact address, e-post example@example.test innen {claim} . '
           f'Fristdagen er {frist} . Første skiftesamling blir holdt {meeting} kl. {time} i Oslo tingrett, Fjernmøte . '
           f'Alle henvendelser om konkursen rettes til bostyrer {trustee} . Konkursregisteret {published}')
    # Match the provider's nested standalone announcement document.
    return page('<table><tr><td>'+page(body).decode('latin1')+'</td></tr></table>')


def source(**options):
    return BankruptcyNoticesSource(SourceConfig(id='bankruptcy',kind='bankruptcy_notices',label='Corporate bankruptcies',urls=(),
        filters=FilterRule(match_all=True),options={'allow_empty':True,**options}))


def install(src,idx=None,details=None,second_index=None,second_details=None):
    idx=index() if idx is None else idx;details=[detail()] if details is None else details
    second_index=idx if second_index is None else second_index
    second_details=details if second_details is None else second_details
    src.get=Mock(side_effect=[response(x) for x in [idx,*details,second_index,*second_details]])


class BankruptcyTests(unittest.TestCase):
    def setUp(self):
        clock=patch('watchtower.sources.bankruptcy_notices.today',return_value=date(2026,9,15));self.clock=clock.start();self.addCleanup(clock.stop)

    def test_baseline_repeat_separate_dates_and_no_contact_data(self):
        src=source();install(src);previous,alerts=poll(src);self.assertEqual([],alerts)
        f=previous['source_state']['records']['rows'][KID]['row']['fields']
        self.assertEqual(['2026-09-15','2026-09-14','2026-10-15','2026-09-12','2026-10-20'],[f[k] for k in ['announced_date','opening_date','claim_deadline','fristdag','meeting_date']])
        self.assertEqual('Adv. Example Trustee',f['trustee'])
        self.assertNotIn('example@example.test',str(previous));self.assertNotIn('Ignored',str(previous))
        install(src);repeat,alerts=poll(src,previous);self.assertEqual([],alerts);self.assertEqual(previous,repeat)

    def test_changed_claim_deadline_once_with_before_and_after(self):
        src=source();install(src);old,_=poll(src)
        install(src,details=[detail(claim='16.10.2026')]);new,alerts=poll(src,old)
        self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        self.assertIn('2026-10-15 → 2026-10-16',' '.join(alerts[0].item.alert_details))
        install(src,details=[detail(claim='16.10.2026')]);self.assertEqual([],poll(src,new)[1])

    def test_optional_opening_basis_is_not_invented(self):
        src=source();install(src,details=[detail(basis=None)]);state,_=poll(src)
        self.assertIsNone(state['source_state']['records']['rows'][KID]['row']['fields']['opening_basis'])
        install(src);_,alerts=poll(src,state);self.assertEqual(1,len(alerts))

    def test_personal_and_other_types_are_counted_but_not_selected_for_details(self):
        rows=[result(),result(kid='20260000000002',org='010190',name='Synthetic person'),result(kid='20260000000003',kind='Innstilling av bobehandling')]
        src=source();install(src,idx=index(rows));self.assertEqual(1,len(src.read_records()));self.assertEqual(4,src.get.call_count)
        src=source(orgnrs=['999999992']);install(src,details=[]);self.assertEqual([],src.read_records());self.assertEqual(2,src.get.call_count)

    def test_observed_empty_result_is_quiet_and_reappearance_is_not_duplicated(self):
        src=source();install(src);old,_=poll(src)
        install(src,idx=index([]),details=[]);empty,alerts=poll(src,old)
        self.assertEqual([],alerts);self.assertEqual(old['seen'],empty['seen'])
        install(src);self.assertEqual([],poll(src,empty)[1])
        initial=source();install(initial,idx=index([]),details=[]);state,alerts=poll(initial)
        self.assertEqual([],alerts);self.assertEqual({},state['source_state']['records']['rows'])

    def test_window_regression_preserves_state(self):
        src=source();install(src);old,_=poll(src);saved=deepcopy(old)
        self.clock.return_value=date(2026,9,14)
        install(src,idx=index([],start='12.09.2026',end='14.09.2026'),details=[])
        with self.assertRaisesRegex(SourceError,'regressed'):poll(src,old)
        self.assertEqual(saved,old)

    def test_second_sweep_drift_does_not_replace_persisted_history(self):
        src=source();install(src);old,_=poll(src)
        install(src,second_details=[detail(claim='16.10.2026')])
        with tempfile.TemporaryDirectory() as directory:
            store=StateStore(directory);store.save(src.config.id,old)
            outcome=run(Config((src.config,)),store,None,source_factory=lambda _:src)
            self.assertIn(src.config.id,outcome.errors);self.assertEqual(old,store.load(src.config.id))
        install(src,second_index=index([result(name='Changed index name')]))
        with self.assertRaisesRegex(SourceError,'between complete reads'):src.read_records()

    def test_search_filters_count_identity_and_dates_fail_closed(self):
        bads=[index(start='12.09.2026'),index(count=2),index([result(),result()]),index([result(published='12.09.2026')]),
              index([result(org='bad')]),index().replace(ORG.encode(),b'999999992',1),
              index().replace(b'Hele landet',b'Other place'),index().replace(b'Konkurs/tvangsavvikling',b'Other type'),
              index().replace(b'hent_en.jsp?',b'https://example.test/hent_en.jsp?')]
        for bad in bads:
            src=source();install(src,idx=bad)
            with self.subTest(bad=bad[:40]),self.assertRaises(SourceError):src.read_records()

    def test_invalid_empty_response_is_not_accepted(self):
        for bad in [page(''),index([]).replace(b'kid=&sokeverdi=',b'kid=x&sokeverdi=')]:
            src=source();install(src,idx=bad,details=[])
            with self.assertRaises(SourceError):src.read_records()

    def test_detail_org_publication_required_dates_and_clock_validation(self):
        bads=[detail(org='999999992'),detail(published='14.09.2026'),detail(claim='31.02.2026'),detail(time='25:00'),
              detail().replace(b'Krav i boet',b'Other deadline'),detail().replace(b'Saksnr:',b'Unknown:'),
              detail().replace(b'<h3>Konkurs - ',b'<h3>Other - ')]
        for bad in bads:
            src=source();install(src,details=[bad])
            with self.subTest(bad=bad[:40]),self.assertRaises(SourceError):src.read_records()

    def test_duplicate_detail_label_is_rejected(self):
        bad=detail().replace(b'</table>',b'<tr><td>Saksnr:</td><td>duplicate</td></tr></table>',1)
        src=source();install(src,details=[bad])
        with self.assertRaises(SourceError):src.read_records()

    def test_bounds_and_transport_errors_close_responses(self):
        for body,status,limit in [(b'x'*2048,200,1024),(index(),302,100000),(index()[:-20],200,100000)]:
            src=source(max_bytes=limit);r=response(body,status=status);src.get=Mock(return_value=r)
            with self.assertRaises(SourceError):src.read_records()
            r.close.assert_called_once()
        records=[result(),result(kid='20260000000002')]
        for options in ({'max_results':1},{'max_details':1},{'max_records':1}):
            src=source(**options);install(src,idx=index(records),details=[])
            with self.subTest(options=options),self.assertRaises(SourceError):src.read_records()

    def test_configuration_disallows_unbounded_window_and_removal(self):
        for options in ({'window_days':8},{'window_days':True},{'max_results':5000},{'orgnrs':['bad']},{'events':['removed']},{'complete_snapshot':True}):
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
