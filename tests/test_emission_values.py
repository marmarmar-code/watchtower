import copy
import unittest
from unittest.mock import Mock

from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.common import SourceError
from watchtower.sources.emission_values import EmissionValuesSource, ORIGIN, PATH, PREFIX, MENU, quantity, factsheet
from test_change_sources import poll, response

URL=ORIGIN+PATH+'?CompanyID=100&ComponentPageID=200'
A='Example substance (A)'
B='Other substance (B)'


def source(**opts):
    return EmissionValuesSource(SourceConfig(id='emissions',kind='emission_values',label='Emissions',urls=(URL,),
        filters=FilterRule(match_all=True),options={'substances':[A,B],'max_bytes':100000,**opts}))


def html(substance=A,air='10,00',water='(I.T.)',years=(2023,2024),unit='tonn per år',target=True):
    body=f'<form method="post" action="{URL}"><input type="hidden" name="__VIEWSTATE" value="transient-state"><h1>Example installation</h1><h2>Utslipp av {substance} (i {unit})</h2>'
    for field,year in [('from',min(years)),('to',max(years))]:
        body+=f'<select name="{PREFIX+field}"><option value="{year}" selected>{year}</option></select>'
    body+='<table><thead><tr><td>År</td><td>Til luft</td><td>Til vann</td></tr></thead><tbody>'
    for year in years:
        body+=f'<tr><td>{year}</td><td>{air}<span class="reference"></span></td><td>{water}</td></tr>'
    body+='</tbody></table><p>(I.T.) = Ikke tilgjengelig (I.R.) = Ikke rapportert</p>'
    if target:
        event=MENU+'rptEmission$ctl01$LinkButtonEmission'
        body+=f'<a id="{event.replace(chr(36),chr(95))}" href="javascript:__doPostBack(\'{event}\',\'\')">{B}</a>'
    else:
        event=MENU+'showAllComponents'
        body+=f'<a id="{event.replace(chr(36),chr(95))}" href="javascript:__doPostBack(\'{event}\',\'\')">Flere stoffer</a>'
    return (body+'</form>').encode()


def load(s,values=None,alter=None,expand=False):
    values=values or {};calls=[]
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        if method=='get':
            if s.session.cookies.get('selected'):
                raise AssertionError('Previous anonymous selection leaked into a fresh factsheet')
            raw=html(A,**values.get(A,{}),target=not expand)
        else:
            assert kwargs['data']['__VIEWSTATE']=='transient-state'
            action=kwargs['data']['__EVENTTARGET']
            if action.endswith('showAllComponents'):
                raw=html(A,**values.get(A,{}))
            else:
                assert action==MENU+'rptEmission$ctl01$LinkButtonEmission'
                raw=html(B,**values.get(B,{}))
            s.session.cookies.set('selected','temporary')
        if alter:raw=alter(raw,method,url,kwargs,len(calls))
        return response(raw)
    s.get=Mock(side_effect=lambda u,**kw:request('get',u,**kw))
    s.post=Mock(side_effect=lambda u,**kw:request('post',u,**kw))
    return calls


class EmissionValueTests(unittest.TestCase):
    def test_full_forms_substance_selection_and_repeat_are_quiet(self):
        s=source();s.session.cookies.set('selected','previous')
        calls=load(s,{B:{'water':'(I.R.)','unit':'1000 tonn per år'}})
        previous,alerts=poll(s);self.assertEqual([],alerts);self.assertEqual(8,len(s._next['rows']))
        self.assertEqual(['get','post','get','post'],[c[0] for c in calls])
        self.assertEqual({'Ikke tilgjengelig','Ikke rapportert','Oppgitt tall'},
            {r['row']['fields']['availability'] for r in s._next['rows'].values()})
        repeat,alerts=poll(s,previous);self.assertEqual([],alerts);self.assertEqual(previous,repeat)
        self.assertEqual({'tonn per år','1000 tonn per år'},
            {r['row']['fields']['unit'] for r in s._next['rows'].values()})

    def test_missing_to_zero_and_quantity_change_are_real_changes(self):
        s=source(substances=[A]);load(s,{A:{'air':'(I.T.)','years':(2023,)}});previous,_=poll(s)
        load(s,{A:{'air':'0,00','years':(2023,)}});previous,alerts=poll(s,previous)
        self.assertEqual(1,len(alerts));self.assertIn('Oppgitt mengde: 0',' '.join(alerts[0].item.alert_details))
        load(s,{A:{'air':'0','years':(2023,)}});repeat,alerts=poll(s,previous)
        self.assertEqual([],alerts);self.assertEqual(previous,repeat)
        load(s,{A:{'air':'100,00','years':(2023,)}});_,alerts=poll(s,previous)
        self.assertEqual(1,len(alerts));self.assertIn('100',' '.join(alerts[0].item.alert_details))
        self.assertEqual(('1234.5','Oppgitt tall'),quantity('1 234,50'))

    def test_new_year_without_numbers_is_silent_until_reported(self):
        s=source(substances=[A]);load(s,{A:{'air':'(I.T.)','years':(2023,)}});previous,_=poll(s)
        load(s,{A:{'air':'(I.T.)','years':(2023,2024)}});previous,alerts=poll(s,previous);self.assertEqual([],alerts)
        load(s,{A:{'air':'(I.T.)','years':(2023,2024)}},lambda raw,m,u,k,n:raw.replace(b'<td>2024</td><td>(I.T.)',b'<td>2024</td><td>2,50'))
        _,alerts=poll(s,previous);self.assertEqual(1,len(alerts))

    def test_missing_markers_remain_distinct_and_unknown_values_fail(self):
        s=source(substances=[A]);load(s,{A:{'air':'(I.T.)','years':(2023,)}});previous,_=poll(s)
        load(s,{A:{'air':'(I.R.)','years':(2023,)}});_,alerts=poll(s,previous);self.assertEqual(1,len(alerts))
        self.assertIn('Ikke rapportert',' '.join(alerts[0].item.alert_details))
        for bad in ['', '(UNKNOWN)', '1.2', '1,2,3', 'NaN']:
            with self.subTest(value=bad),self.assertRaises(SourceError):quantity(bad)

    def test_complete_years_headers_legends_and_selected_substances(self):
        changes=[lambda raw:raw.replace(b'<td>2024</td>',b'<td>2023</td>'),
            lambda raw:raw.replace(b'<td>Til vann</td>',b'<td>Other medium</td>'),
            lambda raw:raw.replace(b'Ikke tilgjengelig',b'Other meaning'),
            lambda raw:raw.replace(b'<h2>Utslipp av',b'<h2>Other heading')]
        for change in changes:
            s=source(substances=[A]);load(s,alter=lambda raw,m,u,k,n:change(raw))
            with self.subTest(change=change),self.assertRaises(SourceError):s.read_records()
        s=source(substances=[A]);load(s,{A:{'years':(2022,2024)}})
        with self.assertRaisesRegex(SourceError,'incomplete'):s.read_records()
        s=source(substances=['Absent substance']);load(s)
        with self.assertRaisesRegex(SourceError,'unavailable'):s.read_records()

    def test_expanded_menu_uses_observed_public_form_action(self):
        s=source();calls=load(s,expand=True);self.assertEqual(8,len(s.read_records()))
        self.assertEqual(2,sum(c[2].get('data',{}).get('__EVENTTARGET','').endswith('showAllComponents') for c in calls))
        s=source();load(s,alter=lambda raw,m,u,k,n:raw.replace(MENU.encode(),b'unrelated$action$'))
        with self.assertRaisesRegex(SourceError,'action'):s.read_records()

    def test_second_sweep_change_preserves_previous_history(self):
        s=source(substances=[A]);load(s);previous,_=poll(s);saved=copy.deepcopy(previous)
        load(s,alter=lambda raw,m,u,k,n:raw.replace(b'10,00',b'11,00') if n==2 else raw)
        with self.assertRaisesRegex(SourceError,'changed during reading'):poll(s,previous)
        self.assertEqual(saved,previous)
        load(s);repeat,alerts=poll(s,previous);self.assertEqual(previous,repeat);self.assertEqual([],alerts)

    def test_observed_company_route_and_query_identity(self):
        url=ORIGIN+'/Templates/NorskeUtslipp/Pages/company.aspx?CompanyID=101'
        self.assertEqual((url,'101'),factsheet(url))
        for bad in [url+'&CompanyID=102',url+'&other=1',url.replace('https:','http:'),url.replace('101','0')]:
            with self.subTest(url=bad),self.assertRaises(ValueError):factsheet(bad)

    def test_transport_form_identity_and_configuration_bounds(self):
        s=source();s.get=Mock(return_value=response(b'',status=302))
        with self.assertRaisesRegex(SourceError,'redirect'):s.read_records()
        s=source(max_bytes=1024);s.get=Mock(return_value=response(b'x'*1025))
        with self.assertRaisesRegex(SourceError,'max_bytes'):s.read_records()
        s=source();load(s,alter=lambda raw,m,u,k,n:raw.replace(URL.encode(),b'https://example.test/'))
        with self.assertRaisesRegex(SourceError,'form identity'):s.read_records()
        for opts in [{'max_years':1},{'max_records':1}]:
            s=source(**opts);load(s)
            with self.subTest(options=opts),self.assertRaises(SourceError):s.read_records()
        for opts in [{'substances':[]},{'complete_snapshot':True},{'events':['removed']}]:
            with self.subTest(options=opts),self.assertRaises(ValueError):source(**opts)


if __name__=='__main__':unittest.main()
