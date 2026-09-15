import copy
import json
import unittest
from pathlib import Path
import tempfile
from unittest.mock import Mock

from watchtower.config import FilterRule,SourceConfig,load_config
from watchtower.engine import evaluate
from watchtower.sources.common import SourceError
from watchtower.sources.staffing_register import StaffingRegisterSource,PAGE,STATUS
from test_change_sources import poll,response

ORGS = ['923609016','976967631','925336637']


def unit(index=0,status=1,main=True,name=True):
    v={'organisasjonsnummer':ORGS[index],'erHovedenhet':main,'status':status,'erGodkjent':status==1}
    if name:v['navn']='Example Unit '+str(index)
    return {'virksomhet':v,'location':{'lat':1,'lng':2},'fylkeKommune':{'fylke':'03','kommune':'0301'}}


def page(rows,number=1,size=2):
    return {'data':rows[(number-1)*size:number*size],
            'pagination':{'pageNumber':number,'pageSize':size,'totalItems':len(rows),'totalPages':(len(rows)+size-1)//size},
            'registerInfo':{'statusKodeMapping':STATUS}}


class StaffingRegisterTests(unittest.TestCase):
    def source(self,**options):
        return StaffingRegisterSource(SourceConfig(id='staffing',kind='staffing_register',label='Staffing register',urls=(PAGE,),
            filters=FilterRule(match_all=True),options={'page_size':2,**options}),timeout=1,retry_attempts=1)

    def load(self,s,rows,alter=None):
        calls=[]
        def request(url,**kw):
            number=kw['params']['page'];calls.append(number)
            data=page(rows,number,s.page_size)
            if alter:data=alter(copy.deepcopy(data),len(calls))
            return response(json.dumps(data).encode())
        s.post=Mock(side_effect=request)
        return calls

    def test_full_export_baseline_main_filter_and_repeat_are_quiet(self):
        s=self.source();rows=[unit(),unit(1,main=False),unit(2,status=2)]
        calls=self.load(s,rows);first,alerts=poll(s)
        self.assertEqual([1,2,1,2],calls);self.assertEqual([],alerts)
        self.assertEqual({ORGS[0],ORGS[2]},set(s._next['rows']))
        rows[0]['location']['lat']=99;self.load(s,list(reversed(rows)))
        second,alerts=poll(s,first);self.assertEqual([],alerts);self.assertEqual(first,second)

    def test_status_changes_and_missing_name_are_not_lost(self):
        s=self.source();self.load(s,[unit(name=False)]);state,_=poll(s)
        record=s._next['rows'][ORGS[0]]['row'];self.assertIsNone(record['fields']['name'])
        self.assertEqual('Enhet '+ORGS[0],record['title'])
        self.load(s,[unit(status=2)]);state,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertIn('Ikke godkjent',' '.join(alerts[0].item.alert_details))
        self.assertIn('uten vedtaksdato',' '.join(alerts[0].item.alert_details))
        _,alerts=poll(s,state);self.assertEqual([],alerts)
        self.load(s,[unit(status=0)]);_,alerts=poll(s,state);self.assertEqual(1,len(alerts))

    def test_explicit_subunit_selection_and_absence_do_not_imply_withdrawal(self):
        s=self.source(include_subunits=True,orgnrs=[ORGS[1]])
        self.load(s,[unit(),unit(1,main=False)]);records=s.read_records()
        self.assertEqual([ORGS[1]],[r['key'] for r in records]);self.assertEqual('Underenhet',records[0]['fields']['unit_type'])
        s=self.source(orgnrs=[ORGS[1]]);self.load(s,[unit(),unit(1,main=False)])
        with self.assertRaisesRegex(SourceError,'absent or excluded'):s.read_records()
        s=self.source();self.load(s,[unit(),unit(1)]);state,_=poll(s)
        self.load(s,[unit()]);_,alerts=poll(s,state);self.assertEqual([],alerts)

    def test_bad_pagination_partial_pages_and_cross_page_duplicates_fail(self):
        rows=[unit(),unit(1),unit(2)]
        changes=[lambda d,n:{**d,'pagination':{**d['pagination'],'pageNumber':9}},
                 lambda d,n:{**d,'pagination':{**d['pagination'],'totalPages':3}},
                 lambda d,n:{**d,'pagination':{**d['pagination'],'totalItems':4}} if n==2 else d,
                 lambda d,n:{**d,'data':[]} if n==2 else d,
                 lambda d,n:{**d,'data':[unit()]} if n==2 else d]
        for change in changes:
            s=self.source();self.load(s,rows,change)
            with self.subTest(change=change),self.assertRaises(SourceError):s.read_records()

    def test_middle_page_change_in_second_export_is_rejected(self):
        # Three pages of one unit ensure the changed page is neither endpoint.
        s=self.source(page_size=1);rows=[unit(),unit(1),unit(2)]
        self.load(s,rows);state,_=poll(s)
        def change(data,call):
            if call==5:data['data'][0]['virksomhet'].update(status=2,erGodkjent=False)
            return data
        self.load(s,rows,change)
        with self.assertRaisesRegex(SourceError,'changed during reading'):poll(s,state)
        self.load(s,rows);repeat,alerts=poll(s,state);self.assertEqual(state,repeat);self.assertEqual([],alerts)

    def test_bad_status_identity_and_excluded_rows_fail(self):
        for field,value in [('status',True),('status',3),('erGodkjent',False),('erHovedenhet','true'),('organisasjonsnummer','123456789'),('navn',42)]:
            rows=[unit(),unit(1,main=False)];rows[1]['virksomhet'][field]=value
            s=self.source();self.load(s,rows)
            with self.subTest(field=field,value=value),self.assertRaises(SourceError):s.read_records()
        s=self.source();self.load(s,[unit()],lambda d,n:{**d,'registerInfo':{'statusKodeMapping':{'1':'Other'}}})
        with self.assertRaises(SourceError):s.read_records()

    def test_bounds_redirect_and_duplicate_json_fail(self):
        for options in [{'max_records':1},{'max_export_records':1},{'max_pages':1}]:
            s=self.source(**options);self.load(s,[unit(),unit(1),unit(2)])
            with self.subTest(options=options),self.assertRaises(SourceError):s.read_records()
        s=self.source();s.post=Mock(return_value=response(b'',status=302,headers={'Location':PAGE}))
        with self.assertRaises(SourceError):s.read_records()
        s=self.source();s.post=Mock(return_value=response(b'{"data":[],"data":[]}'))
        with self.assertRaisesRegex(SourceError,'invalid JSON'):s.read_records()
        for options in [{'include_subunits':1},{'page_size':501},{'complete_snapshot':True},{'events':['removed']},{'orgnrs':['123456789']}]:
            with self.assertRaises(ValueError):self.source(**options)

    def test_source_retention_override_keeps_all_keys_and_preserves_scope(self):
        s=self.source(max_seen_per_source=4);self.load(s,[unit(),unit(1),unit(2)])
        items=s.fetch_with_state(None);state,alerts,_=evaluate(s.config,items,None,max_seen=2)
        state=s.augment_state(state);self.assertEqual(3,len(state['seen']));self.assertEqual([],alerts)
        items=s.fetch_with_state(state);repeat,alerts,_=evaluate(s.config,items,state,max_seen=2)
        self.assertEqual(state,s.augment_state(repeat));self.assertEqual([],alerts)
        ordinary=self.source();unchanged,_,_=evaluate(ordinary.config,items,None,max_seen=2)
        self.assertEqual(2,len(unchanged['seen']))
        larger=self.source(max_seen_per_source=5);self.assertEqual(s.scope,larger.scope)
        self.load(larger,[unit(status=2),unit(1),unit(2)])
        _,alerts=poll(larger,state);self.assertEqual(1,len(alerts))

    def test_retention_override_is_validated_when_loading_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml'
            for value in ['0','true','50001','"5000"']:
                path.write_text('[[source]]\nid="units"\nkind="staffing_register"\nmax_seen_per_source='+value+'\n[source.filter]\nmatch_all=true\n')
                with self.subTest(value=value),self.assertRaisesRegex(ValueError,'source.max_seen_per_source'):load_config(path)


if __name__=='__main__':unittest.main()
