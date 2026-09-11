import json
from unittest import TestCase
from unittest.mock import Mock
from watchtower.config import SourceConfig,FilterRule
from watchtower.sources.building_cases import BuildingCasesSource
from watchtower.sources.common import SourceError
from test_change_sources import poll
def cfg(**o): return SourceConfig(id='building_cases',kind='building_cases',label='PBE',urls=(),filters=FilterRule(match_all=True),options={'query':'Sinsenveien 5',**o})
def html(status='Siste dok. 03.09.2026'):
 return '<input id="text" name="text" value="Sinsenveien 5"><table><tr><th>x</th></tr><tr onclick="document.location = \'casedet.asp?mode=all&caseno=202550619\';"><td>202550619</td><td>Prosjekt</td><td>'+status+'</td></tr></table>'
def source(h):
 s=BuildingCasesSource(cfg()); raw=h.encode(); s.get=Mock(return_value=Mock(status_code=200,headers={},iter_content=Mock(return_value=[raw]))); return s
class BuildingTests(TestCase):
    def test_identity_status_and_latest_date(self):
     r=source(html()).read_records()[0]; assert r['key']=='202550619' and r['published'] is None and r['fields']['latest_document_date']=='03.09.2026'
    def test_initial_repeat_quiet_and_status_change(self):
     s=source(html()); st,a=poll(s); assert not a; s.get.return_value=Mock(status_code=200,headers={},iter_content=Mock(return_value=[html('Avsluttet').encode()])); _,a=poll(s,st); assert len(a)==1
    def test_missing_rows_and_duplicate_fail_closed(self):
     with TestCase().assertRaises(SourceError): source('<html>tom</html>').read_records()
     with TestCase().assertRaises(SourceError): source(html()+html()).read_records()
    def test_complete_removed_rejected(self):
     with TestCase().assertRaises(ValueError): BuildingCasesSource(cfg(events=['added','removed'],complete_snapshot=True))
