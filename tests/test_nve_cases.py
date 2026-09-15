import unittest
from unittest.mock import Mock
from watchtower.config import SourceConfig, FilterRule
from watchtower.sources.nve_cases import NveCasesSource, FIELDS
from watchtower.sources.common import SourceError
from test_change_sources import poll, response

def record(stadium="Melding"):
    row = {k:"" for k in FIELDS}
    return {**row,"SoknadId":123,"Type":"A-8","Tittel":"Synthetic solar project",
            "Stadium":stadium,"MW":20.0,"GWh":25.0,"Hoeringsfrist":"0001-01-01T00:00:00"}

class NveTests(unittest.TestCase):
    def source(self, **options):
        return NveCasesSource(SourceConfig("test","nve_cases",filters=FilterRule(match_all=True),
                             options={"max_records":1000,**options}))
    def test_stable_project_id_and_substantive_changes(self):
        s=self.source();s.get=Mock(return_value=response({"TotalCount":1,"Licenses":[record()]}))
        state,alerts=poll(s);self.assertEqual([],alerts)
        next_state,alerts=poll(s,state);self.assertEqual([],alerts)
        self.assertEqual(state,next_state)
        s.get.return_value=response({"TotalCount":1,"Licenses":[record("Klage mottatt")]})
        _,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertIn("Saksstadium: Melding → Klage mottatt",alerts[0].item.alert_details)
        self.assertIsNone(alerts[0].item.published)
    def test_incomplete_and_invalid_totals_fail(self):
        for total in (2,True,-1,None,1001):
            s=self.source();s.get=Mock(return_value=response({"TotalCount":total,"Licenses":[record()]}))
            with self.subTest(total=total),self.assertRaises(SourceError):s.fetch()
    def test_missing_fields_or_duplicate_id_fail(self):
        bad=record();bad.pop("Status")
        for rows in ([bad],[record(),record()]):
            s=self.source();s.get=Mock(return_value=response({"TotalCount":len(rows),"Licenses":rows}))
            with self.assertRaises(SourceError):s.fetch()
    def test_wrong_type_invalid_dates_or_numeric_values_fail(self):
        for changes in ({'Type':'A-6'}, {'Dato':'not-a-date'}, {'MW':'unknown'}):
            s=self.source();s.get=Mock(return_value=response({'TotalCount':1,'Licenses':[{**record(),**changes}]}))
            with self.assertRaises(SourceError):s.fetch()
    def test_selection_and_removal_validation(self):
        for o in ({"complete_snapshot":True},{"events":["removed"]},{"page_size":True},{"type":"bad"}):
            with self.assertRaises(ValueError):self.source(**o)
