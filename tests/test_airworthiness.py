import copy
from datetime import date
import unittest
from unittest.mock import Mock
from xml.sax.saxutils import escape

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.airworthiness import AirworthinessSource, BASE, ORIGIN, EMPTY
from watchtower.sources.common import SourceError
from test_change_sources import poll, response


def source(**options):
    config = SourceConfig(id='directives', kind='airworthiness', label='Directives',
        filters=FilterRule(match_all=True), options={'issuers':['EU'], 'allow_empty':True, **options})
    result = AirworthinessSource(config)
    result._window = lambda:(date(2026,1,1), date(2026,1,3))
    return result


def unit(index=1, **fields):
    return {'id':f'2026-{index:04d}', 'issuer':'EU', 'subject':'Example component inspection',
        'issue':'2026-01-01', 'effective':'2026-01-02', 'revision':'Not applicable', **fields}


def listing(rows, page=1):
    title = '<title>EASA Safety Publications Tool</title>'
    if not rows:
        return (title+EMPTY).encode()
    current = rows[(page-1)*20:page*20]
    heading = f'<div id="toolbar"><h2>List of Mandatory Continuing Airworthiness Information from 2026-01-01 to 2026-01-03</h2><h3>Displaying records {(page-1)*20+1} to {min(page*20,len(rows))} out of a total of {len(rows)} publications.</h3></div>'
    table = '<table class="ad-list"><tr><th>Number</th></tr>'
    for r in current:
        table += f'<tr><td><a href="{ORIGIN}/ad/{r["id"]}">{r["id"]}</a></td><td><img alt="{r["issuer"]}" title="{r["issuer"]}"></td><td>{r["issue"]}</td><td>{escape(r["subject"])}</td><td>Example type</td><td>{r["effective"]}</td><td></td></tr>'
    return (title+heading+table+'</table>').encode()


def export(rows):
    result = '<list>'
    for r in rows:
        fields = {'ad_class':'AD','ad_number':r['id'],'issued_by':r['issuer'],'subject':r['subject'],
            'issue_date':r['issue'],'effective_date':r['effective'],'type_designation':'Example type'}
        result += '<item>'+''.join(f'<{k}>{escape(v)}</{k}>' for k,v in fields.items())+'<attachments/></item>'
    return (result+'</list>').encode()


def detail(row, banner='Unrelated publication notice'):
    fields = {'Number':row['id'],'Issued by':f'<img src="{ORIGIN}/static/img/flags/{row["issuer"].lower()}.gif">',
        'Issue date':row['issue'],'Effective date':row['effective'],'Revision':row['revision'],
        'Correction':'Not applicable','Supersedure':'None','ATA Chapter':'10'}
    return ('<h1>'+banner+'</h1><table class="table-detail">'+''.join(f'<tr><td>{k}</td><td>{v}</td></tr>' for k,v in fields.items())+'</table>').encode()


def load(s, rows, alter=None):
    calls = []
    def request(method, url, **kwargs):
        calls.append((method,url,kwargs))
        if method == 'get':
            raw = detail(next(r for r in rows if r['id'] == url.rsplit('/',1)[1]))
        elif kwargs.get('params',{}).get('format') == 'xml':
            raw = export(rows)
        else:
            page = int(url.rsplit('page-',1)[1]) if 'page-' in url else 1
            raw = listing(rows,page)
        if alter:
            raw = alter(raw, method, url, kwargs, len(calls))
        return response(raw)
    s.get = Mock(side_effect=lambda url,**kw:request('get',url,**kw))
    s.post = Mock(side_effect=lambda url,**kw:request('post',url,**kw))
    return calls


class AirworthinessTests(unittest.TestCase):
    def test_complete_two_page_baseline_repeat_and_reordering(self):
        s = source(); rows = [unit(i) for i in range(1,22)]
        calls = load(s,rows); first,alerts = poll(s)
        self.assertEqual([],alerts); self.assertEqual(21,len(s._next['rows']))
        self.assertEqual(2,sum('page-2' in c[1] for c in calls))
        self.assertTrue(all(c[2]['data']['fi_adclass[]']=='AD' for c in calls if c[0]=='post'))
        self.assertEqual(42,sum(c[0]=='get' for c in calls))
        load(s,list(reversed(rows))); repeat,alerts=poll(s,first)
        self.assertEqual([],alerts); self.assertEqual(first,repeat)

    def test_revision_date_and_banner_changes_have_distinct_effects(self):
        s=source(); rows=[unit()];load(s,rows);previous,_=poll(s)
        load(s,rows,lambda raw,m,u,k,n:raw.replace(b'Unrelated publication notice',b'Another notice'))
        unchanged,alerts=poll(s,previous);self.assertEqual(previous,unchanged);self.assertEqual([],alerts)
        rows[0]['revision']='This AD revises an earlier directive.';load(s,rows)
        previous,alerts=poll(s,previous);self.assertEqual(1,len(alerts));self.assertIn('revises',' '.join(alerts[0].item.alert_details))
        rows[0]['effective']='2026-01-03';load(s,rows)
        previous,alerts=poll(s,previous);self.assertEqual(1,len(alerts));self.assertIn('2026-01-03',' '.join(alerts[0].item.alert_details))
        rows.append(unit(2,id='2026-0001R1'));load(s,rows)
        _,alerts=poll(s,previous);self.assertEqual(1,len(alerts));self.assertEqual('2026-0001R1',alerts[0].item.url.rsplit('/',1)[1])

    def test_issuer_filter_and_empty_window_without_removal(self):
        s=source();rows=[unit(),unit(2,issuer='US',id='US-2026-01-02')];load(s,rows)
        previous,_=poll(s);self.assertEqual({'2026-0001'},set(s._next['rows']))
        calls=load(s,[]);_,alerts=poll(s,previous);self.assertEqual([],alerts);self.assertEqual(2,len(calls))
        self.assertTrue(all(c[0]=='post' and not c[2].get('params') for c in calls))

    def test_partial_second_page_duplicates_and_capacity_fail(self):
        rows=[unit(i) for i in range(1,22)]
        for changed in [rows[:-1]+[rows[0]],rows]:
            s=source();load(s,changed,lambda raw,m,u,k,n:raw.replace(b'21 to 21',b'21 to 22') if changed is rows and 'page-2' in u else raw)
            with self.assertRaises(SourceError):s.read_records()
        for options in [{'max_pages':1},{'max_records':20}]:
            s=source(**options);load(s,rows)
            with self.assertRaises(SourceError):s.read_records()

    def test_xml_identity_class_dates_and_duplicate_fields_fail(self):
        changes=[lambda b:b.replace(b'<ad_class>AD',b'<ad_class>SIB'),
            lambda b:b.replace(b'<effective_date>2026-01-02',b'<effective_date>2026-01-03'),
            lambda b:b.replace(b'<subject>Example',b'<subject>Changed'),
            lambda b:b.replace(b'2026-0001</ad_number>',b'2026-9999</ad_number>'),
            lambda b:b.replace(b'</item>',b'<ad_number>2026-0001</ad_number></item>'),
            lambda b:b'<!DOCTYPE list [<!ENTITY x "data">]>'+b]
        for change in changes:
            s=source();load(s,[unit()],lambda raw,m,u,k,n:change(raw) if k.get('params',{}).get('format')=='xml' else raw)
            with self.subTest(change=change),self.assertRaises(SourceError):s.read_records()

    def test_detail_identity_missing_field_and_dates_fail(self):
        changes=[lambda b:b.replace(b'2026-0001',b'2026-9999'),
            lambda b:b.replace(b'<td>Revision</td>',b'<td>Different</td>'),
            lambda b:b.replace(b'2026-01-02',b'2026-01-03'),
            lambda b:b.replace(b'/flags/eu.gif',b'/flags/us.gif')]
        for change in changes:
            s=source();load(s,[unit()],lambda raw,m,u,k,n:change(raw) if m=='get' else raw)
            with self.subTest(change=change),self.assertRaises(SourceError):s.read_records()

    def test_second_sweep_detail_change_preserves_prior_state(self):
        s=source();load(s,[unit()]);previous,_=poll(s);saved=copy.deepcopy(previous)
        load(s,[unit()],lambda raw,m,u,k,n:raw.replace(b'Not applicable',b'Changed') if n==6 else raw)
        with self.assertRaisesRegex(SourceError,'changed during reading'):poll(s,previous)
        self.assertEqual(saved,previous)
        load(s,[unit()]);repeat,alerts=poll(s,previous);self.assertEqual(previous,repeat);self.assertEqual([],alerts)

    def test_redirect_oversize_hostile_links_and_configuration(self):
        s=source();s.post=Mock(return_value=response(b'',status=302,headers={'Location':'https://example.test'}))
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        s=source(max_bytes=1024);s.post=Mock(return_value=response(b'x'*1025))
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        s=source();load(s,[unit()],lambda raw,m,u,k,n:raw.replace(ORIGIN.encode(),b'https://example.test') if m=='post' and not k.get('params') else raw)
        with self.assertRaisesRegex(SourceError,'host or path'):s.read_records()
        for options in [{'complete_snapshot':True},{'events':['removed']},{'lookback_days':91},{'max_pages':26},{'ad_numbers':['2026-0001']}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
