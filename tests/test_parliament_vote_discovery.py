from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
import unittest

from tests.test_change_sources import poll, response
from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.parliament_vote_discovery import ParliamentVoteDiscoverySource, _votes

NS = 'xmlns="http://data.stortinget.no"'


def config(**options):
    return SourceConfig(id="vote-discovery", kind="parliament_vote_discovery", label="Nye voteringer",
                        urls=(), filters=FilterRule(match_all=True), options={"lookback_days": 30,
                        "max_sessions": 2, "max_meetings": 40, "max_cases": 200,
                        "max_records": 1000, "allow_empty": True, **options})


def source(replies, **options):
    item = ParliamentVoteDiscoverySource(config(**options))
    item.get = Mock(side_effect=[response(value.encode()) for value in replies])
    return item


def dates():
    today = datetime.now(timezone.utc).date()
    return today, today - timedelta(days=1), today - timedelta(days=60)


def sessions():
    today, _, old = dates()
    return f'''<sesjoner_oversikt {NS}><innevaerende_sesjon><id>2026-2027</id>
      <fra>{old.isoformat()}T00:00:00</fra><til>{(today + timedelta(days=300)).isoformat()}T23:59:00</til>
      </innevaerende_sesjon><sesjoner_liste><sesjon><id>2026-2027</id>
      <fra>{old.isoformat()}T00:00:00</fra><til>{(today + timedelta(days=300)).isoformat()}T23:59:00</til>
      </sesjon></sesjoner_liste></sesjoner_oversikt>'''


def meetings(ids=(10,)):
    today, recent, old = dates()
    rows = ''.join(f'<mote><id>{identifier}</id><mote_dato_tid>{recent.isoformat()}T10:00:00</mote_dato_tid></mote>'
                   for identifier in ids)
    rows += f'<mote><id>-1</id><mote_dato_tid>{today.isoformat()}T10:00:00</mote_dato_tid></mote>'
    rows += f'<mote><id>99</id><mote_dato_tid>{old.isoformat()}T10:00:00</mote_dato_tid></mote>'
    return f'<mote_oversikt {NS}><sesjon_id>2026-2027</sesjon_id><moter_liste>{rows}</moter_liste></mote_oversikt>'


def agenda(meeting=10, cases=((100, "Sak A"),)):
    _, recent, _ = dates()
    rows = ''.join(f'<dagsordensak><sak_id>{case}</sak_id><dagsordensak_tekst>{title}</dagsordensak_tekst></dagsordensak>'
                   for case, title in cases)
    return f'''<mote_dagsorden_oversikt {NS}><mote_id>{meeting}</mote_id>
      <mote_dato_tid>{recent.isoformat()}T10:00:00</mote_dato_tid>
      <dagsordensak_liste>{rows}</dagsordensak_liste></mote_dagsorden_oversikt>'''


def votes(case=100, vote_id=700, topic="Forslaget", adopted="true"):
    _, recent, _ = dates()
    row = f'''<sak_votering><sak_id>{case}</sak_id><votering_id>{vote_id}</votering_id>
      <votering_tid>{recent.isoformat()}T12:00:00</votering_tid><votering_tema>{topic}</votering_tema>
      <vedtatt>{adopted}</vedtatt><personlig_votering>true</personlig_votering>
      <antall_for>90</antall_for><antall_mot>70</antall_mot><antall_ikke_tilstede>9</antall_ikke_tilstede>
      <votering_resultat_type>manuell</votering_resultat_type></sak_votering>'''
    return f'<sak_votering_oversikt {NS}><sak_id>{case}</sak_id><sak_votering_liste>{row}</sak_votering_liste></sak_votering_oversikt>'


class ParliamentVoteDiscoveryTests(unittest.TestCase):
    def test_discovers_and_aggregates_one_vote_across_cases(self):
        item = source([sessions(), meetings(), agenda(cases=((100, "Sak A"), (101, "Sak B"))),
                       votes(100), votes(101)])
        rows = item.read_records()
        self.assertEqual(1, len(rows)); self.assertEqual("700", rows[0]["key"])
        self.assertEqual([100, 101], rows[0]["case_ids"])
        self.assertEqual("Sak A | Sak B", rows[0]["case_titles"])
        self.assertIsNone(rows[0]["published"])
        self.assertEqual(5, item.get.call_count)

    def test_baseline_and_repeat_are_quiet(self):
        replies = [sessions(), meetings(), agenda(), votes()]
        state, alerts = poll(source(replies)); self.assertEqual([], alerts)
        repeated, alerts = poll(source(replies), state)
        self.assertEqual([], alerts); self.assertEqual(state["source_state"], repeated["source_state"])

    def test_case_context_can_shrink_quietly_but_outcome_change_alerts(self):
        first = [sessions(), meetings(), agenda(cases=((100, "Sak A"), (101, "Sak B"))),
                 votes(100), votes(101)]
        state, _ = poll(source(first))
        one_case = [sessions(), meetings(), agenda(cases=((100, "Sak A"),)), votes(100)]
        state, alerts = poll(source(one_case), state)
        self.assertEqual([], alerts)
        self.assertEqual([100], state["source_state"]["records"]["rows"]["700"]["row"]["case_ids"])
        changed = [sessions(), meetings(), agenda(cases=((100, "Sak A"),)), votes(100, adopted="false")]
        _, alerts = poll(source(changed), state)
        self.assertEqual(1, len(alerts)); self.assertIn("Ja → Nei", " ".join(alerts[0].item.alert_details))

    def test_conflicting_shared_vote_fails(self):
        item = source([sessions(), meetings(), agenda(cases=((100, "A"), (101, "B"))),
                       votes(100), votes(101, adopted="false")])
        with self.assertRaisesRegex(SourceError, "conflicting fields"):
            item.read_records()

    def test_historical_official_vote_fixture_is_positive(self):
        raw = (Path(__file__).parent / "fixtures" / "event_sources" /
               "parliament-votes-200314.xml").read_bytes()
        item = ParliamentVoteDiscoverySource(config())
        item.get = Mock(return_value=response(raw))
        rows = _votes(item, 200314)
        self.assertEqual(31, len(rows))
        self.assertEqual(("28470", "28500"), (rows[0][0], rows[-1][0]))

    def test_empty_vote_list_is_valid(self):
        empty = f'<sak_votering_oversikt {NS}><sak_id>100</sak_id><sak_votering_liste /></sak_votering_oversikt>'
        self.assertEqual([], source([sessions(), meetings(), agenda(), empty]).read_records())

    def test_echo_schema_dates_and_bounds_fail_closed(self):
        wrong_session = meetings().replace("2026-2027", "2025-2026")
        with self.assertRaises(SourceError): source([sessions(), wrong_session]).read_records()
        wrong_meeting = agenda().replace("<mote_id>10", "<mote_id>11")
        with self.assertRaises(SourceError): source([sessions(), meetings(), wrong_meeting]).read_records()
        bad_date = meetings().replace("T10:00:00", "", 1)
        with self.assertRaises(SourceError): source([sessions(), bad_date]).read_records()
        with self.assertRaisesRegex(SourceError, "max_meetings"):
            source([sessions(), meetings((10, 11))], max_meetings=1).read_records()
        many_cases = tuple((value, str(value)) for value in range(100, 102))
        with self.assertRaisesRegex(SourceError, "max_cases"):
            source([sessions(), meetings(), agenda(cases=many_cases)], max_cases=1).read_records()

    def test_sessions_must_cover_the_whole_window_and_have_valid_ranges(self):
        today, _, old = dates()
        gap = f'''<sesjoner_oversikt {NS}><innevaerende_sesjon><id>2026-2027</id>
          <fra>{old.isoformat()}T00:00:00</fra><til>{old.isoformat()}T23:59:00</til>
          </innevaerende_sesjon><sesjoner_liste /></sesjoner_oversikt>'''
        with self.assertRaisesRegex(SourceError, "cover"):
            source([gap]).read_records()
        reversed_range = sessions().replace(
            f"<til>{(today + timedelta(days=300)).isoformat()}T23:59:00</til>",
            f"<til>{(old - timedelta(days=1)).isoformat()}T23:59:00</til>")
        with self.assertRaisesRegex(SourceError, "cover"):
            source([reversed_range]).read_records()

    def test_duplicate_and_invalid_vote_identity_fail(self):
        base = votes()
        row = base.split("<sak_votering_liste>", 1)[1].split("</sak_votering_liste>", 1)[0]
        duplicate = base.replace("</sak_votering_liste>", row + "</sak_votering_liste>")
        with self.assertRaises(SourceError):
            source([sessions(), meetings(), agenda(), duplicate]).read_records()
        invalid = votes(vote_id=0)
        with self.assertRaises(SourceError): source([sessions(), meetings(), agenda(), invalid]).read_records()

    def test_configuration_rejects_urls_types_and_removals(self):
        invalid = [config(lookback_days=True), config(max_sessions=4), config(events=["removed"]),
                   config(complete_snapshot=True), SourceConfig(id="v", kind="parliament_vote_discovery",
                   label="V", urls=("https://example.test",), filters=FilterRule(match_all=True),
                   options={"lookback_days": 30})]
        for candidate in invalid:
            with self.assertRaises(ValueError): ParliamentVoteDiscoverySource(candidate)


if __name__ == "__main__": unittest.main()
