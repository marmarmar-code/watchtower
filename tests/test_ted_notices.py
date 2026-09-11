import unittest
from unittest.mock import Mock
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.ted_notices import TedNoticesSource, _record
from test_change_sources import poll, response


def notice(key="123-2026", winner=None):
    return {"publication-number":key,"publication-date":"2026-09-11+02:00",
            "notice-type":"can-standard","notice-title":{"eng":"Synthetic contract result"},
            "winner-name":{"eng":winner or ["Synthetic winner"]},
            "total-value":100,"total-value-cur":["NOK"]}


def payload(rows, **extra):
    return {"notices":rows,"totalNoticeCount":len(rows),"timedOut":False,**extra}


class TedTests(unittest.TestCase):
    def source(self, **options):
        return TedNoticesSource(SourceConfig("test","ted_notices",filters=FilterRule(match_all=True),options=options))
    def test_stable_winner_order_and_value_change(self):
        s=self.source();s.post=Mock(return_value=response(payload([notice(winner=["B","A"])])))
        state,alerts=poll(s);self.assertEqual([],alerts)
        s.post.return_value=response(payload([notice(winner=["A","B"])]))
        state2,alerts=poll(s,state);self.assertEqual([],alerts);self.assertEqual(state,state2)
        changed=notice(winner=["A","B"]);changed["total-value"]=200
        s.post.return_value=response(payload([changed]))
        _,alerts=poll(s,state);self.assertEqual(1,len(alerts))
        self.assertIn("100 → 200", " ".join(alerts[0].item.alert_details))
        self.assertTrue(s.post.call_args.kwargs["stream"])
        self.assertFalse(s.post.call_args.kwargs["allow_redirects"])
    def test_partial_timeout_duplicate_and_invalid_data_fail(self):
        for value in (payload([notice()],timedOut=True),payload([notice()],totalNoticeCount=2),
                      payload([notice(),notice()]),payload([notice()],totalNoticeCount=True)):
            s=self.source(max_pages=1);s.post=Mock(return_value=response(value))
            with self.assertRaises(SourceError):s.fetch()
        for update in ({"total-value":True},{"total-value-cur":["bad"]},{"notice-type":"cn-standard"},
                       {"publication-number":"invalid"},{"notice-title":[] }):
            with self.assertRaises(SourceError):_record({**notice(),**update},("can-standard",))
    def test_multiple_pages_preserve_all_notices(self):
        s=self.source(limit=1,max_pages=2)
        s.post=Mock(side_effect=[response(payload([notice()],totalNoticeCount=2)),
                                response(payload([notice("124-2026")],totalNoticeCount=2))])
        self.assertEqual(2,len(s.fetch()))
    def test_valid_empty_is_quiet_only_with_explicit_allow_empty(self):
        s=self.source(allow_empty=True);s.post=Mock(return_value=response(payload([])))
        _,alerts=poll(s);self.assertEqual([],alerts)
    def test_query_escape_and_removal_are_rejected(self):
        for options in ({"keywords":['quote" OR buyer-country=FRA']},{"keywords":["back\\slash"]},
                        {"cpv":"123"},{"limit":True},{"complete_snapshot":True},
                        {"events":["removed"]},{"query":"anything"}):
            with self.assertRaises(ValueError):self.source(**options)
