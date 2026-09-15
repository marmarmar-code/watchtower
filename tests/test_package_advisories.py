from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.package_advisories import PackageAdvisoriesSource, API
from test_change_sources import poll


def advisory(ident='GHSA-aaaa-bbbb-cccc'):
    day=datetime.now(timezone.utc).date().isoformat()
    return {'ghsa_id':ident,'cve_id':'CVE-2026-12345','html_url':f'https://github.com/advisories/{ident}',
            'type':'reviewed','summary':'Package vulnerability','severity':'high','published_at':day+'T00:00:00Z',
            'updated_at':day+'T00:01:00Z','github_reviewed_at':day+'T00:00:00Z','withdrawn_at':None,
            'vulnerabilities':[{'package':{'ecosystem':'pip','name':'example-package'},'vulnerable_version_range':'< 2.0','first_patched_version':'2.0'}]}


def response(value, link=''):
    raw=value if isinstance(value,bytes) else json.dumps(value).encode()
    return Mock(status_code=200,headers={'Link':link},iter_content=Mock(return_value=[raw]),close=Mock())


def source(**options):
    return PackageAdvisoriesSource(SourceConfig(id='advisories',kind='package_advisories',label='Package advisories',
                                  urls=(API,),filters=FilterRule(match_all=True),options=options))


def cursor(size=1, **changes):
    today=datetime.now(timezone.utc).date();query={'type':'reviewed','updated':f'{today-timedelta(days=3)}..{today}',
        'sort':'updated','direction':'desc','per_page':str(size),'after':'cursor-token'};query.update(changes)
    return '<'+API+'?'+urlencode(query)+'>; rel="next"'


class PackageAdvisoriesTests(unittest.TestCase):
    def test_baseline_noise_patch_and_withdrawal_changes(self):
        s=source();row=advisory();s.get=Mock(return_value=response([row]))
        state,alerts=poll(s);self.assertEqual([],alerts)
        row.update(updated_at=row['updated_at'].replace('00:01','00:02'),description='New prose',epss={'percentage':.9})
        s.get.return_value=response([row]);state,alerts=poll(s,state);self.assertEqual([],alerts)
        row['vulnerabilities'][0]['first_patched_version']='2.0.1'
        s.get.return_value=response([row]);state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertIn('2.0.1',' '.join(alerts[0].item.alert_details))
        row['withdrawn_at']=row['updated_at'];s.get.return_value=response([row])
        state,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertIn('Tilbaketrukket',' '.join(alerts[0].item.alert_details))
        row['withdrawn_at']=None;row['severity']='medium';s.get.return_value=response([row])
        _,alerts=poll(s,state);self.assertEqual(1,len(alerts));self.assertIn('Høy → Middels',' '.join(alerts[0].item.alert_details))

    def test_cursor_traversal_duplicates_partial_page_and_cap(self):
        first,second=advisory(),advisory('GHSA-dddd-eeee-ffff')
        s=source(page_size=1,max_pages=2);s.get=Mock(side_effect=[response([first],cursor()),response([second])])
        self.assertEqual(2,len(s.read_records()));self.assertIn('after=cursor-token',s.get.call_args.args[0])
        for options,replies in [({'page_size':1,'max_pages':1},[response([first],cursor())]),
                                ({'page_size':1},[response([first],cursor()),response([first])]),
                                ({'page_size':2},[response([first],cursor(2))]),
                                ({'page_size':1,'max_records':1},[response([first],cursor()),response([second])]),
                                ({'page_size':1},[response([first],cursor()),response([])])]:
            s=source(**options);s.get=Mock(side_effect=replies)
            with self.assertRaises(SourceError):s.read_records()
        for link in [cursor(type='unreviewed'),cursor(updated='2000-01-01..2000-01-02'),cursor().replace('api.github.com','other.example'),
                     cursor(after=''), 'broken link',cursor()+', '+cursor()]:
            s=source(page_size=1);s.get=Mock(return_value=response([first],link))
            with self.assertRaises(SourceError):s.read_records()

    def test_package_order_null_fix_and_unknown_cve_are_preserved(self):
        s=source();row=advisory();other=deepcopy(row['vulnerabilities'][0]);other['package']['name']='second';other['first_patched_version']=None
        row['vulnerabilities'].append(other);row['cve_id']=None;s.get=Mock(return_value=response([row]));state,_=poll(s)
        row['vulnerabilities'].reverse();s.get.return_value=response([row]);after,alerts=poll(s,state)
        self.assertEqual(state,after);self.assertEqual([],alerts)

    def test_invalid_record_fields_fail_closed(self):
        mutations=[('type','malware'),('ghsa_id','invalid'),('html_url','https://other.example/'),('severity','severe'),
                   ('cve_id',True),('updated_at','2000-01-01T00:00:00Z'),('github_reviewed_at',None),('withdrawn_at','tomorrow'),('vulnerabilities',[])]
        for key,value in mutations:
            row=advisory();row[key]=value;s=source();s.get=Mock(return_value=response([row]))
            with self.subTest(field=key),self.assertRaises(SourceError):s.read_records()
        for change in ('missing_patch','duplicate','missing_range'):
            row=advisory()
            if change=='missing_patch':row['vulnerabilities'][0].pop('first_patched_version')
            if change=='missing_range':row['vulnerabilities'][0].pop('vulnerable_version_range')
            if change=='duplicate':row['vulnerabilities']*=2
            s=source();s.get=Mock(return_value=response([row]))
            with self.assertRaises(SourceError):s.read_records()

    def test_empty_window_limits_and_redirects(self):
        s=source(allow_empty=True);s.get=Mock(return_value=response([]));self.assertEqual([],s.read_records())
        s=source(max_bytes=1024);r=response(b'x'*1025);s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        r.close.assert_called_once()
        r=response([]);r.status_code=302;s.get.return_value=r
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        for options in ({'events':['removed']},{'complete_snapshot':True},{'updated_days':0},{'max_pages':6}):
            with self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
