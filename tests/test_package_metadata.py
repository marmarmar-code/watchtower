from copy import deepcopy
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.package_metadata import API, PackageMetadataSource
from test_change_sources import poll


def response(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return Mock(status_code=200, iter_content=Mock(return_value=[raw]), close=Mock())


def source(**options):
    return PackageMetadataSource(SourceConfig(id='packages', kind='package_metadata', label='Packages',
        urls=(API,), filters=FilterRule(match_all=True), options={'packages':['example-package'], **options}))


def record(target='1.0.0', package='example-package'):
    return {'name':package, 'version':target, 'license':'MIT',
            'dist':{'shasum':'a'*40, 'integrity':'sha512-YWJjZA=='}}


def reads(s, row=None, tags=None, final=None):
    row = record() if row is None else row
    tags = {'latest':row['version']} if tags is None else tags
    final = tags if final is None else final
    s.get = Mock(side_effect=[response(tags), *([response(row)] if any(t in tags for t in s.tags) else []), response(final)])


class PackageMetadataTests(unittest.TestCase):
    def test_quiet_repeat_ignored_person_fields_and_tag_retargeting(self):
        s=source(); row=record(); reads(s,row); state,alerts=poll(s); self.assertEqual([],alerts)
        row.update(maintainers=[{'name':'Synthetic user', 'email':'test@example.invalid'}], description='changed text')
        reads(s,row); same,alerts=poll(s,state); self.assertEqual(state,same); self.assertEqual([],alerts)
        row=record('2.0.0'); reads(s,row); state,alerts=poll(s,state); self.assertEqual(1,len(alerts))
        self.assertIn('1.0.0 → 2.0.0',' '.join(alerts[0].item.alert_details))
        self.assertEqual(['example-package:latest'],list(state['source_state']['records']['rows']))
        # A rollback is a tag change, not a newly published version.
        reads(s,record('1.0.0')); _,alerts=poll(s,state); self.assertEqual(1,len(alerts))
        self.assertIn('2.0.0 → 1.0.0',' '.join(alerts[0].item.alert_details))

    def test_license_deprecation_and_integrity_changes_then_quiet_repeat(self):
        s=source(); row=record(); reads(s,row); state,_=poll(s)
        for key,value in [('license','Apache-2.0'),('deprecated','Use another version')]:
            row[key]=value; reads(s,row); state,alerts=poll(s,state); self.assertEqual(1,len(alerts))
            self.assertIn(value,' '.join(alerts[0].item.alert_details))
            reads(s,row); same,alerts=poll(s,state); self.assertEqual(state,same); self.assertEqual([],alerts)
        row['deprecated']='';row['dist']['shasum']='b'*40
        reads(s,row); _,alerts=poll(s,state); self.assertEqual(1,len(alerts))
        self.assertIn('Use another version → ikke oppgitt',' '.join(alerts[0].item.alert_details))

    def test_optional_metadata_and_legacy_license_normalize_without_false_changes(self):
        s=source();row=record();row['license']={'type':'MIT','url':'https://example.invalid/license'}
        row['dist'].pop('integrity');reads(s,row); state,_=poll(s)
        row['license']='MIT';row['deprecated']='';reads(s,row);same,alerts=poll(s,state)
        self.assertEqual(state,same);self.assertEqual([],alerts)
        row.pop('license');reads(s,row); _,alerts=poll(s,state); self.assertEqual(1,len(alerts))

    def test_selected_tag_absence_reappearance_and_shared_version_fetch(self):
        s=source(tags=['latest','next']);row=record();reads(s,row,tags={'latest':'1.0.0','next':'1.0.0'})
        state,_=poll(s);self.assertEqual(3,s.get.call_count)
        reads(s,row,tags={'latest':'1.0.0'}); state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertFalse(state['source_state']['records']['rows']['example-package:next']['row']['fields']['tag_present'])
        self.assertIn('ikke sletting av pakken',' '.join(alerts[0].item.alert_details))
        reads(s,row,tags={'latest':'1.0.0','next':'1.0.0'});_,alerts=poll(s,state);self.assertEqual(1,len(alerts))

    def test_tag_race_and_version_mismatch_preserve_prior_snapshot(self):
        s=source();reads(s);state,_=poll(s);saved=deepcopy(state);next_before=deepcopy(s._next)
        reads(s,final={'latest':'2.0.0'})
        with self.assertRaisesRegex(SourceError,'during the read'):poll(s,state)
        self.assertEqual(saved,state);self.assertEqual(next_before,s._next)
        for field,value in [('name','different-package'),('version','2.0.0')]:
            row=record();row[field]=value;reads(s,row,tags={'latest':'1.0.0'})
            with self.assertRaisesRegex(SourceError,'identity'):poll(s,state)
            self.assertEqual(saved,state)

    def test_scoped_names_are_encoded_and_unselected_tag_changes_are_quiet(self):
        s=source(packages=['@example/pkg']);row=record(package='@example/pkg')
        reads(s,row,tags={'latest':'1.0.0','next':'2.0.0-beta.1'},final={'latest':'1.0.0','next':'2.0.0-beta.2'})
        self.assertEqual(1,len(s.read_records()))
        urls=[call.args[0] for call in s.get.call_args_list]
        self.assertEqual(API+'/@example%2Fpkg/1.0.0',urls[1])
        self.assertTrue(all(call.kwargs['allow_redirects'] is False for call in s.get.call_args_list))

    def test_invalid_tags_json_limits_and_redirects_fail_closed(self):
        for value in [{}, {'next':'1.0.0'}, {'latest':'../../bad'}, {'latest':True},
                      {'latest':'1.0.0',**{str(i):'1.0.0' for i in range(100)}},
                      b'{"latest":"1.0.0","latest":"2.0.0"}', b'{"latest":NaN}', [], b'{']:
            s=source();s.get=Mock(return_value=response(value))
            with self.subTest(value=str(value)[:40]),self.assertRaises(SourceError):s.read_records()
        s=source(max_bytes=1024);r=response(b'x'*1025);s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        r.close.assert_called_once()
        r=response({});r.status_code=302;s.get=Mock(return_value=r)
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        r.close.assert_called_once()

    def test_invalid_metadata_and_configuration(self):
        cases=[]
        for key,value in [('license',[]),('license',{}),('deprecated',True),('dist',None)]:
            row=record();row[key]=value;cases.append(row)
        for key,value in [('shasum','bad'),('integrity','not-a-digest')]:
            row=record();row['dist'][key]=value;cases.append(row)
        for row in cases:
            s=source();reads(s,row)
            with self.assertRaises(SourceError):s.read_records()
        for options in [{'packages':[]},{'packages':['../bad']},{'packages':['UPPER']}, {'tags':['1.0.0']},
                        {'tags':['one','two','three','four','five','six']}, {'complete_snapshot':True},
                        {'packages':['one','two'],'max_records':1}]:
            with self.subTest(options=options),self.assertRaises(ValueError):source(**options)


if __name__=='__main__':unittest.main()
