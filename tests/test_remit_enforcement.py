import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.remit_enforcement import RemitEnforcementSource, routing, model_contract, queries
from watchtower.sources.remit_table import PROPERTIES, table, participant_count
from test_change_sources import poll


def response(rows=None):
    schema = [{"N": "G" + str(i), "T": 4 if i == 6 else 1, **({"DN": "D" + str(i)} if i != 6 else {})} for i in range(8)]
    values = ["Article 5", "Authority (XX)", "Example Energy", "EUR 100", "AppealPossible", "Link", 2026, "https://example.org/decision/1"]
    dictionaries = {"D" + str(i): [v] for i, v in enumerate(values) if i != 6}
    encoded = [0 if i != 6 else 2026 for i in range(8)]
    descriptors = [{"Kind": 1, "Depth": 0, "Value": "G" + str(i), "GroupKeys": [{"Source": {"Entity": "FINES", "Property": p}, "Calc": "G" + str(i), "IsSameAsSelect": True}], "Name": "FINES." + p} for i, p in enumerate(PROPERTIES)]
    ds = {"N": "DS0", "PH": [{"DM0": rows or [{"S": schema, "C": encoded}]}], "IC": True, "HAD": True, "ValueDicts": dictionaries}
    return {"jobIds": ["test"], "results": [{"jobId": "test", "result": {"data": {"fromCache": False, "descriptor": {"Version": 2, "Select": descriptors}, "dsr": {"Version": 2, "MinorVersion": 1, "DS": [ds]}}}}]}


def data(value):
    return value["results"][0]["result"]["data"]


def count_response(value=1):
    r = response()
    d = data(r)
    d["descriptor"]["Select"] = [{"Kind": 2, "Value": "M0", "Name": "CountNonNull"}]
    ds = d["dsr"]["DS"][0]
    del ds["ValueDicts"]
    ds["PH"][0]["DM0"] = [{"S": [{"N": "M0", "T": 4}], "M0": value}]
    ds["Msg"] = [{"Code": "IgnoredDataReductionAlgorithm", "Severity": "Warning", "Message": "test"}]
    return r


def source(**options):
    return RemitEnforcementSource(SourceConfig(id="remit", kind="remit_enforcement", label="REMIT", filters=FilterRule(match_all=True), options=options))


def raw(amount="EUR 100", status="AppealPossible", year=2026):
    return ["Article 5", "Authority (XX)", "Example Energy", amount, status, "Link", year, "https://example.org/decision/1"]


class DecoderTests(unittest.TestCase):
    def test_observed_dictionary_repeat_and_inline(self):
        r = response()
        rows = data(r)["dsr"]["DS"][0]["PH"][0]["DM0"]
        rows.append({"R": 247, "C": ["EUR 200"]})
        self.assertEqual(["EUR 100", "EUR 200"], [x[3] for x in table(r)])
        data(r)["fromCache"] = True
        self.assertEqual(2, len(table(r)))

    def test_count_contract(self):
        self.assertEqual(148, participant_count(count_response(148)))
        for value in (True, -1, "148"):
            with self.assertRaises(SourceError):
                participant_count(count_response(value))
        r = count_response();data(r)["descriptor"]["Select"][0]["Name"] = "Count"
        with self.assertRaises(SourceError):
            participant_count(r)

    def test_corrupt_envelopes_and_masks_fail(self):
        mutations = [
            lambda d: d["dsr"]["DS"][0].update(IC=False),
            lambda d: d["dsr"]["DS"][0].update(HAD=False),
            lambda d: d["dsr"]["DS"][0].update(RT="next"),
            lambda d: d["dsr"]["DS"][0].update(Msg=[{"Code": "PartialData", "Severity": "Warning"}]),
            lambda d: d["dsr"]["DS"][0]["PH"][0]["DM0"][0].update(N=1),
            lambda d: d["dsr"]["DS"][0]["PH"][0]["DM0"].append({"R": 256, "C": []}),
            lambda d: d["dsr"]["DS"][0]["PH"][0]["DM0"].append({"R": True, "C": []}),
            lambda d: d["dsr"]["DS"][0]["PH"][0]["DM0"][0]["C"].__setitem__(0, 9),
            lambda d: d["descriptor"]["Select"][0]["GroupKeys"][0]["Source"].update(Property="wrong"),
            lambda d: d["descriptor"]["Select"][0].update(Value="M0"),
        ]
        for mutation in mutations:
            r = response();mutation(data(r))
            with self.subTest(mutation=mutation), self.assertRaises(SourceError):
                table(r)

    def test_unexpected_nulls_extra_cells_and_exact_duplicates_fail(self):
        for cells in ([None] + [0] * 5 + [2026, 0], [0] * 6 + [2026, 0, 9]):
            r=response();data(r)["dsr"]["DS"][0]["PH"][0]["DM0"][0]["C"]=cells
            with self.assertRaises(SourceError):table(r)
        r=response();data(r)["dsr"]["DS"][0]["PH"][0]["DM0"].append({"R":255,"C":[]})
        with self.assertRaises(SourceError):table(r)


class SourceTests(unittest.TestCase):
    def install(self, s, rows=None):
        s._poll = Mock(return_value=(rows or [raw()], ["* Amount note", "** Currency note", "*** Reimbursement note"], "2026-09-18T08:00:00+00:00"))

    def test_quiet_initial_repeat_and_reordering(self):
        s=source();self.install(s,[raw(),raw("EUR 200")]);state,alerts=poll(s)
        self.assertFalse(alerts);self.install(s,[raw("EUR 200"),raw()]);again,alerts=poll(s,state)
        self.assertFalse(alerts);self.assertEqual(state,again)
        self.assertEqual(2,len(next(iter(again["source_state"]["records"]["rows"].values()))["row"]["fields"]["entries"]))

    def test_amount_status_before_after_once_and_year_not_date(self):
        s=source();self.install(s);state,_=poll(s);self.install(s,[raw("EUR 150","Final")]);changed,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertIn("EUR 100 → EUR 150",str(alerts[0].item.alert_details));self.assertIn("AppealPossible → Final",str(alerts[0].item.alert_details));self.assertIsNone(alerts[0].item.published)
        self.assertFalse(poll(s,changed)[1])

    def test_nested_subposts_preserved_and_change_is_group_change(self):
        s=source();self.install(s,[raw(),raw("EUR 200")]);state,_=poll(s)
        self.install(s,[raw(),raw("EUR 300")]);_,alerts=poll(s,state)
        self.assertEqual(1,len(alerts));self.assertIn("Rapportens underposter før",str(alerts[0].item.alert_details));self.assertIn("EUR 200",str(alerts[0].item.alert_details));self.assertIn("EUR 300",str(alerts[0].item.alert_details))

    def test_selection_footnotes_and_formatting(self):
        s=source(authorities=["Authority (XX)"],participants=["Example Energy"],from_year=2024)
        records=s._records([raw(),raw(year=2020)],["footnote"]);self.assertEqual(1,len(records));self.assertEqual(["footnote"],records[0]["source_notes"])
        a=raw();a[2]=" Example\u00a0 Energy ";self.assertEqual(records,s._records([a],["footnote"]))

    def test_complete_or_removal_is_rejected(self):
        for opts in ({"complete_snapshot":True},{"complete_snapshot":True,"events":["removed"]}):
            with self.assertRaises(ValueError):source(**opts)

    def test_global_note_and_model_refresh_change_do_not_alert(self):
        s=source();self.install(s);state,_=poll(s)
        s._poll=Mock(return_value=([raw()],["Reworded general note"],"2026-09-18T09:00:00+00:00"))
        _,alerts=poll(s,state);self.assertFalse(alerts)

    def test_missing_group_keeps_prior_values_for_later_revision(self):
        s=source();second=raw();second[2]="Second Energy";self.install(s,[raw(),second]);state,_=poll(s)
        self.install(s,[second]);missing,alerts=poll(s,state);self.assertFalse(alerts);self.assertEqual(2,len(missing["source_state"]["records"]["rows"]))
        self.install(s,[raw("EUR 150"),second]);_,alerts=poll(s,missing)
        self.assertEqual(1,len(alerts));self.assertIn("EUR 100 → EUR 150",str(alerts[0].item.alert_details))

    def test_partial_read_and_refresh_regression_preserve_input(self):
        s=source();self.install(s);state,_=poll(s);old=deepcopy(state)
        s._poll=Mock(side_effect=[([raw()],["note"],"2026-09-18T08:00:00+00:00"),([raw("EUR 1")],["note"],"2026-09-18T08:00:00+00:00")])
        with self.assertRaises(SourceError):s.fetch_with_state(state)
        self.assertEqual(old,state)
        s._poll=Mock(return_value=([raw()],["note"],"2026-09-17T08:00:00+00:00"))
        with self.assertRaises(SourceError):s.fetch_with_state(state)
        self.assertEqual(old,state)

    def test_routing_does_not_follow_arbitrary_host(self):
        key=base64.b64encode(json.dumps({"k":"11111111-1111-4111-8111-111111111111","t":"22222222-2222-4222-8222-222222222222"}).encode()).decode()
        page='<h1>Enforcement decisions</h1><iframe src="https://app.powerbi.com/view?r='+key+'"></iframe><h6>* A</h6><h6>** B</h6><h6>*** C</h6>'
        self.assertEqual("https://wabi-region-primary-api.analysis.windows.net",routing(page,"var resolvedClusterUri = 'https://wabi-region-primary-redirect.analysis.windows.net/';")[3])
        for host in ("https://localhost/","https://evil.example/","https://wabi-region-redirect.analysis.windows.net.evil.example/"):
            with self.assertRaises(SourceError):routing(page,"var resolvedClusterUri = '"+host+"';")

    def test_model_freshness_and_observed_query_contract(self):
        columns=[{"Column":{"Expression":{"SourceRef":{"Source":"a1"}},"Property":p},"Name":"FINES."+p} for p in PROPERTIES]
        command={"Query":{"Version":2,"From":[{"Name":"a1","Entity":"FINES","Type":0}],"Select":columns},"Binding":{"Primary":{"Groupings":[{"Projections":list(range(8))}]},"DataReduction":{"Primary":{"Window":{"Count":500}}}}}
        observed={"Commands":[{"SemanticQueryDataShapeCommand":command}]}
        now=int(datetime.now(timezone.utc).timestamp()*1000)
        model={"models":[{"id":1,"displayName":"Enforcement Decisions - official","lastRefreshStatus":0,"lastRefreshTime":"/Date("+str(now)+")/"}],"exploration":{"sections":[{"visualContainers":[{"query":json.dumps(observed)}]}]}}
        ident,refreshed,query=model_contract(model,45);self.assertEqual(1,ident)
        table_q,count_q=queries(query,300)
        self.assertEqual(300,table_q["Commands"][0]["SemanticQueryDataShapeCommand"]["Binding"]["DataReduction"]["Primary"]["Window"]["Count"])
        self.assertEqual(5,count_q["Commands"][0]["SemanticQueryDataShapeCommand"]["Query"]["Select"][0]["Aggregation"]["Function"])
        self.assertEqual(observed,query)
        stale=deepcopy(model);stale["models"][0]["lastRefreshTime"]="/Date(1000000000000)/"
        with self.assertRaises(SourceError):model_contract(stale,45)
        bad=deepcopy(model);command["Query"]["Where"]=[{"unexpected":"filter"}];bad["exploration"]["sections"][0]["visualContainers"][0]["query"]=json.dumps(observed)
        with self.assertRaises(SourceError):model_contract(bad,45)

    def test_independent_count_mismatch_and_full_window_fail(self):
        from unittest.mock import patch
        s=source(max_report_rows=10);s._download=Mock(return_value=b"page");s._json=Mock(side_effect=[{},response(),count_response(2)])
        with patch("watchtower.sources.remit_enforcement.routing",return_value=("https://app.powerbi.com/view","key",["note"],"https://wabi-region-api.analysis.windows.net")), patch("watchtower.sources.remit_enforcement.model_contract",return_value=(1,"refresh",{})), patch("watchtower.sources.remit_enforcement.queries",return_value=({},{})):
            with self.assertRaises(SourceError):s._poll()
            s._json=Mock(side_effect=[{},response(),count_response(10)])
            with patch("watchtower.sources.remit_enforcement.table",return_value=[raw()]*10), self.assertRaises(SourceError):s._poll()


if __name__ == "__main__":
    unittest.main()
