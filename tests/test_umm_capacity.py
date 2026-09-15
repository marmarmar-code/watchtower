from copy import deepcopy
import tempfile
import unittest
from unittest.mock import Mock
from xml.sax.saxutils import escape

from watchtower.config import SourceConfig, FilterRule, Config
from watchtower.engine import run
from watchtower.state import StateStore
from watchtower.sources.umm_capacity import UmmCapacitySource
from watchtower.sources.common import SourceError
from test_change_sources import poll, response

GUID = '12345678-1234-1234-1234-123456789abc'
OTHER = '22345678-1234-1234-1234-123456789abc'
BASE = ['Unit Name','Area','Installed Capacity','Available Capacity','Unavailable Capacity','From','To']


def table(headers=None, rows=None):
    headers = BASE if headers is None else headers
    rows = [['Example unit','NO1','100 MW','90 MW','10 MW','15.09.2026 10:00','15.09.2026 12:00']] if rows is None else rows
    return '<table><tr>'+''.join('<th>'+escape(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</table>'


def item(rev=1, guid=GUID, capacity=None, general=False, status='Active'):
    metadata = [('Published:','15.09.2026 09:23:56'),('Publisher:','Example publisher'),('Status:',status)]
    if not general: metadata += [('Type of Unavailability:','Planned'),('Reason Code:','Other')]
    description = '<table>'+''.join('<tr><th>'+k+'</th><td>'+v+'</td></tr>' for k,v in metadata)+'</table>'
    if not general: description += table() if capacity is None else capacity
    return f'<item><guid>{guid}_{rev}</guid><link>https://umm.nordpoolgroup.com/#/messages/{guid}/{rev}</link><title>Example capacity message</title><pubDate>Tue, 15 Sep 2026 07:23:56 Z</pubDate><description>'+escape(description)+'</description></item>'


def feed(*items):
    return ('<rss version="2.0"><channel><title>Nord Pool UMM message RSS Feed</title><description>Urgent Market Messages</description>'+''.join(items or [item()])+'</channel></rss>').encode()


def src(**options):
    return UmmCapacitySource(SourceConfig(id='umm',kind='umm_capacity',label='Capacity messages',urls=(),
        filters=FilterRule(match_all=True),options={'allow_empty':True,**options}))


def install(source, first=None, second=None):
    first = feed() if first is None else first
    source.get = Mock(side_effect=[response(first),response(first if second is None else second)])


class UmmTests(unittest.TestCase):
    def test_baseline_repeat_and_message_reorder_are_quiet(self):
        s=src();install(s,feed(item(),item(guid=OTHER)));old,alerts=poll(s);self.assertEqual([],alerts)
        install(s,feed(item(guid=OTHER),item()));new,alerts=poll(s,old)
        self.assertEqual(old,new);self.assertEqual([],alerts)
        fields=new['source_state']['records']['rows'][GUID]['row']['fields']
        self.assertEqual('15.09.2026 09:23:56',fields['published_local'])
        self.assertEqual('2026-09-15T07:23:56+00:00',fields['rss_publication'])

    def test_revision_capacity_and_status_changes_deliver_once(self):
        s=src();install(s);old,_=poll(s)
        changed=feed(item(rev=2,status='Dismissed',capacity=table().replace('90 MW','80 MW')))
        install(s,changed);new,alerts=poll(s,old)
        self.assertEqual(1,len(alerts));self.assertEqual('changed',alerts[0].item.metadata['event'])
        self.assertIn('Revisjon: 1 → 2',' '.join(alerts[0].item.alert_details))
        self.assertIn('Kapasitetstabellen er endret',' '.join(alerts[0].item.alert_details))
        install(s,changed);self.assertEqual([],poll(s,new)[1])

    def test_parent_child_and_continuation_blanks_are_preserved(self):
        headers=['Unit Name','Unit EIC','Area','Installed Capacity','Available Capacity','Unavailable Capacity','Fuel Type','Power Feed-In','From','To']
        rows=[['Plant','EXAMPLE-PLANT','NO4','500 MW','','','Hydro Water Reservoir','','',''],
              ['Unit 1','EXAMPLE-UNIT','','100 MW','80 MW','20 MW','','','15.09.2026 10:00','15.09.2026 11:00'],
              ['','','','','70 MW','30 MW','','','15.09.2026 11:00','15.09.2026 12:00']]
        s=src();install(s,feed(item(capacity=table(headers,rows))));state,_=poll(s)
        actual=state['source_state']['records']['rows'][GUID]['row']['fields']['tables'][0]
        self.assertEqual([dict(zip(headers,r)) for r in rows],actual)
        self.assertEqual('',actual[1]['Area']);self.assertEqual('',actual[2]['Installed Capacity'])

    def test_eight_column_transmission_schema_and_exact_area_selection(self):
        headers=['Unit Name','Unit EIC',*BASE[1:]]
        for area,count in [('DE-LU - NO2',1),('NO2 - DE-LU',1),('NO20',0),('SE1',0)]:
            rows=[['Link','EXAMPLE-EIC',area,'100.25 MW','90 MW','10.25 MW','15.09.2026 10:00','15.09.2026 12:00']]
            s=src(areas=['NO2']);install(s,feed(item(capacity=table(headers,rows))))
            with self.subTest(area=area):self.assertEqual(count,len(s.read_records()))

    def test_general_messages_excluded_and_missing_capacity_table_rejected(self):
        s=src();install(s,feed(item(general=True)));self.assertEqual([],s.read_records())
        install(s,feed(item(capacity='')))
        with self.assertRaisesRegex(SourceError,'lost its table'):s.read_records()

    def test_absence_is_quiet_and_unchanged_reappearance_does_not_duplicate(self):
        s=src();install(s);old,_=poll(s)
        install(s,feed(item(guid=OTHER,general=True)));empty,alerts=poll(s,old)
        self.assertEqual([],alerts);self.assertEqual({},empty['source_state']['records']['rows']);self.assertEqual(old['seen'],empty['seen'])
        install(s);self.assertEqual([],poll(s,empty)[1])

    def test_regression_and_mid_read_drift_preserve_persisted_state(self):
        s=src();install(s,feed(item(rev=2)));old,_=poll(s);saved=deepcopy(old)
        for first,second in [(feed(),feed()),(feed(item(rev=2)),feed(item(rev=3)))]:
            install(s,first,second)
            with tempfile.TemporaryDirectory() as directory:
                store=StateStore(directory);store.save('umm',old)
                outcome=run(Config((s.config,)),store,None,source_factory=lambda _:s)
                self.assertIn('umm',outcome.errors);self.assertEqual(saved,store.load('umm'))

    def test_guid_link_duplicates_and_channel_fail_closed(self):
        bads=[feed(item(),item()),feed(item(),item(rev=2)),feed().replace(b'/1</link>',b'/2</link>'),
              feed().replace((GUID+'_1').encode(),b'bad'),feed().replace(b'Urgent Market Messages',b'Other'),
              feed().replace(b'<rss version="2.0">',b'<rss version="1.0">'),feed()[:-10],
              b'<!DOCTYPE rss [<!ENTITY x "bad">]>'+feed(),
              feed().replace(b'07:23:56 Z',b'07:23:56'),feed().replace(b'07:23:56 Z',b'bad')]
        for bad in bads:
            s=src();install(s,bad)
            with self.subTest(bad=bad[:50]),self.assertRaises(SourceError):s.read_records()

    def test_schema_numeric_and_interval_validation(self):
        bads=[table().replace('100 MW','NaN MW'),table().replace('100 MW','-1 MW'),table().replace('100 MW','100 kW'),
              table().replace('15.09.2026 10:00','31.02.2026 10:00'),table().replace('15.09.2026 12:00','15.09.2026 09:00'),
              table().replace('15.09.2026 12:00',''),table().replace('<td>Example unit</td>',''),
              table().replace('<td>Example unit</td>','<td rowspan="2">Example unit</td>'),
              table().replace('<th>Area</th>','<th>Region</th>'),table(rows=[])]
        for bad in bads:
            s=src();install(s,feed(item(capacity=bad)))
            with self.subTest(bad=bad[:50]),self.assertRaises(SourceError):s.read_records()

    def test_metadata_and_resource_bounds(self):
        bads=[feed().replace(b'Published:',b'Unknown:'),feed().replace(b'15.09.2026 09:23:56',b'15.09.2026 25:23:56'),
              feed().replace(b'Planned',b'Unknown')]
        for bad in bads:
            s=src();install(s,bad)
            with self.assertRaises(SourceError):s.read_records()
        cases=[({'max_items':1},feed(item(),item(guid=OTHER))),({'max_records':1},feed(item(),item(guid=OTHER))),
               ({'max_table_rows':1},feed(item(capacity=table(rows=[['A','NO1','1 MW','1 MW','0 MW','',''],['B','NO1','1 MW','1 MW','0 MW','','']]))))]
        for options,body in cases:
            s=src(**options);install(s,body)
            with self.subTest(options=options),self.assertRaises(SourceError):poll(s)
        for status,body,options in [(302,feed(),{}),(200,b'x'*2048,{'max_bytes':1024})]:
            s=src(**options);r=response(body,status=status);s.get=Mock(return_value=r)
            with self.assertRaises(SourceError):s.read_records()
            r.close.assert_called_once()

    def test_configuration_limits_and_no_removal(self):
        for options in [{'areas':[]},{'areas':['NO20']},{'areas':['NO1','NO1']},{'max_items':True},{'max_table_rows':0},{'events':['removed']},{'complete_snapshot':True}]:
            with self.subTest(options=options),self.assertRaises(ValueError):src(**options)


if __name__=='__main__':unittest.main()
