import unittest
from unittest.mock import Mock, patch
from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.gdpr_decisions import GdprDecisionsSource, PAGE
from watchtower.sources.common import SourceError
from test_change_sources import response, poll

def card(n, outcome='No violation', lead='no', csa=('se',), day='2026-01-02', pdf=None):
    ident=f'EDPBI:NO:OSS:D:2026:{n}'
    pdf=pdf or f'/system/files/2026-09/decision-no-{n}.pdf'
    cs=''.join(f'<span class="member-country-token__code">{x}</span>' for x in csa)
    return f'''<details class="foss-decision-teaser__details"><summary>
      <div class="foss-decision-foss-decision-teaser__id">{ident}</div>
      <time datetime="{day}T12:00:00Z">2 January 2026</time>
      <div class="foss-decision-foss-decision-teaser__lead-sa"><span class="member-country-token__code">{lead}</span></div>
      <dl class="foss-decision-teaser__properties-list"><dt>Main legal reference</dt><dd>Article 60</dd><dt>CSA</dt><dd>{cs}</dd><dt>Relevant topics</dt><dd><ul><li>Transparency</li></ul></dd><dt>Outcome</dt><dd>{outcome}</dd></dl>
      <a type="application/pdf" href="{pdf}">decision</a></summary></details>'''

def page(cards, total=None, page=0, role='lead', date='2026'):
    total=total if total is not None else len(cards)
    checked='lsa[28]' if role=='lead' else 'csa[28]'
    nav=''.join(f'<a href="{PAGE}?date={date}&{checked}=28&page={i}">Page</a>' for i in range((total+10)//11))
    return f'<h1>Register of final one-stop-shop decisions</h1><input name="date" value="{date}"><input id="n" type="checkbox" name="{checked}" value="28" checked><label for="n">Norway</label><div role="status">{total} items</div><nav class="pager">{nav}</nav>'+''.join(cards)

def source(role='lead', **opts):
    return GdprDecisionsSource(SourceConfig(id='gdpr',kind='gdpr_decisions',label='GDPR',urls=(),filters=FilterRule(match_all=True),options={'authority_role':role,**opts}))

class Tests(unittest.TestCase):
    def setUp(self):
        self.patch=patch('watchtower.sources.gdpr_decisions.today', return_value=__import__('datetime').date(2026,9,15));self.patch.start();self.addCleanup(self.patch.stop)
    def install(self,s, first, second=None):
        second=first if second is None else second
        s.get=Mock(side_effect=[response(first.encode()),response(second.encode()),response(first.encode()),response(second.encode())])
    def test_quiet_and_changed_outcome(self):
        a=page([card(1)]); b=page([card(1,'Fine')]); s=source(max_pages=2);s.get=Mock(side_effect=[response(x.encode()) for x in ([a]*4+[b]*4)])
        st,al=poll(s);self.assertFalse(al);st,al=poll(s,st);self.assertFalse(al);st,al=poll(s,st);self.assertEqual(1,len(al));st,al=poll(s,st);self.assertFalse(al)
    def test_two_pages_and_nullable_outcome(self):
        first=page([card(i,outcome='' if i==1 else 'No violation') for i in range(1,12)],12,0)
        second=page([card(12)],12,1);s=source();self.install(s,first,second);rows=s.read_records();self.assertEqual(12,len(rows));self.assertIsNone(next(x for x in rows if x['key'].endswith(':1'))['fields']['outcome'])
    def test_role_and_filter_validation(self):
        with self.assertRaises(ValueError): source(authority_role='bad')
        bad=page([card(1,lead='se')]);s=source();self.install(s,bad)
        with self.assertRaises(SourceError):s.read_records()
        bad=page([card(1,csa=('se',))],role='concerned');s=source('concerned');self.install(s,bad)
        with self.assertRaises(SourceError):s.read_records()
    def test_duplicate_missing_date_pdf_and_filter_errors(self):
        for html in (page([card(1),card(1)]),page([card(1,day='2025-01-02')]),page([card(1,pdf='https://evil.example/x.pdf')])):
            s=source();self.install(s,html)
            with self.assertRaises(SourceError):s.read_records()
    def test_second_pass_mismatch(self):
        a=page([card(1)]);b=page([card(2)]);s=source();self.install(s,a,b)
        with self.assertRaises(SourceError):s.read_records()

    def test_multiple_legal_references_and_duplicate_download_links(self):
        a=page([card(1)]).replace('<dd>Article 60</dd>','<dd>Article 60</dd><dd>Article 17</dd><dd>Article 6</dd>')
        a=a.replace('</summary>', '<a type="application/pdf" href="/system/files/2026-09/decision-no-1.pdf">Download</a></summary>')
        s=source();self.install(s,a);row=s.read_records()[0];f=row['fields']
        self.assertEqual(['Article 60','Article 17','Article 6'],f['legal_reference']);self.assertEqual(1,len(f['documents']));self.assertIsNone(row['published'])
    def test_valid_concerned_role_with_foreign_lead(self):
        a=page([card(1,lead='se',csa=('no','dk'))],role='concerned').replace('EDPBI:NO:','EDPBI:SE:')
        s=source('concerned');self.install(s,a);f=s.read_records()[0]['fields'];self.assertEqual('se',f['lead_authority']);self.assertEqual(['dk','no'],f['concerned_authorities'])
    def test_filter_labels_years_future_and_bounds(self):
        good=page([card(1)])
        for bad in [good.replace('value="2026"','value="2025"'),good.replace('>Norway<','>Sweden<'),good.replace('name="lsa[28]"','name="csa[28]"'),page([card(1,day='2026-12-01')])]:
            s=source();self.install(s,bad)
            with self.assertRaises(SourceError):s.read_records()
        for opt in [{'max_pages':True},{'from_year':2017},{'complete_snapshot':True},{'events':['removed']}]:
            with self.assertRaises(ValueError):source(**opt)
        s=source(max_records=1);self.install(s,page([card(1),card(2)]))
        with self.assertRaises(SourceError):s.read_records()
    def test_multipage_guards(self):
        first=page([card(i) for i in range(1,12)],12)
        second=page([card(12)],12,1)
        for a,b in [(first.replace('page=1','page=0'),second),(first,second.replace('12 items','13 items')),(first,page([card(1)],12,1)),(first.replace('page=1','page=99'),second),(first.replace('&lsa[28]=28&page=1','&csa[28]=28&page=1'),second)]:
            s=source();self.install(s,a,b)
            with self.assertRaises(SourceError):s.read_records()
    def test_failed_read_preserves_saved_state(self):
        import tempfile
        from watchtower.config import Config
        from watchtower.engine import run
        from watchtower.state import StateStore
        s=source();a=page([card(1)]);self.install(s,a);old,_=poll(s)
        self.install(s,a,page([card(1,'Changed')]))
        with tempfile.TemporaryDirectory() as d:
            store=StateStore(d);store.save('gdpr',old);result=run(Config((s.config,)),store,None,source_factory=lambda _:s)
            self.assertIn('gdpr',result.errors);self.assertEqual(old,store.load('gdpr'))
    def test_http_rate_limit_does_not_advance_state(self):
        from unittest.mock import patch
        s=source();a=page([card(1)]);self.install(s,a);old,_=poll(s)
        s.get=Mock(side_effect=SourceError('HTTP 429'))
        import copy
        before=copy.deepcopy(old)
        with self.assertRaisesRegex(SourceError,'429'):poll(s,old)
        self.assertEqual(before,old)
    def test_year_range_rollover_and_absence_not_reversal(self):
        s=source();a=page([card(1),card(2)]);self.install(s,a);old,_=poll(s)
        self.install(s,page([card(1)]));new,alerts=poll(s,old);self.assertEqual([],alerts);self.assertEqual(set(old['seen']),set(new['seen']))
        with patch('watchtower.sources.gdpr_decisions.today',return_value=__import__('datetime').date(2027,1,3)):
            self.install(s,page([card(1)],date='2026-2027'));self.assertEqual(1,len(s.read_records()));self.assertIn('2026-2027',s._url(0))

if __name__=='__main__': unittest.main()
