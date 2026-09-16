from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET

from watchtower.config import Config, FilterRule, SourceConfig, source_seen_limit
from watchtower.engine import evaluate, run
from watchtower.sources.common import SourceError
from watchtower.sources.stortinget import StortingetSource
from watchtower.state import StateStore


NS = 'xmlns="http://data.stortinget.no"'


def question(identity="123", *, status="til_behandling", answered="", timestamp="2026-08-01T12:00:00"):
    return f"""<sporsmal>
      <respons_dato_tid>{timestamp}</respons_dato_tid><versjon>1.6</versjon>
      <besvart_av><id>ABC</id><respons_dato_tid>{timestamp}</respons_dato_tid></besvart_av>
      <emne_liste><emne><id>17</id><tittel>Nested topic title</tittel></emne></emne_liste>
      <id>{identity}</id><tittel>Example policy question {identity}</tittel>
      <sendt_dato>2026-08-01</sendt_dato><status>{status}</status>
      <besvart_dato>{answered}</besvart_dato>
      <sporsmal_til_minister_tittel>Example minister</sporsmal_til_minister_tittel>
    </sporsmal>"""


def root(records):
    return ET.fromstring(f"<root {NS}>{records}</root>")


def config(*datasets):
    return SourceConfig(
        id="parliament", kind="stortinget", label="Example parliament",
        filters=FilterRule(include_any=("example",)), alert_on_update=True,
        options={"datasets": list(datasets or ["skriftligesporsmal"])},
    )


class StortingetTests(unittest.TestCase):
    def source(self, records, *, cfg=None):
        source = StortingetSource(cfg or config())
        source._xml = lambda *_args, **_kwargs: root(records)
        return source

    def test_nested_person_and_topic_ids_do_not_merge_questions(self):
        items = self.source(question("123") + question("124")).fetch()
        self.assertEqual(["sporsmal:123", "sporsmal:124"], [item.key for item in items])
        self.assertEqual("Example policy question 123", items[0].title)
        self.assertTrue(items[1].url.endswith("NSporsmalId=124"))

    def test_case_fields_are_direct_and_short_title_has_explicit_priority(self):
        records = """<sak><emne_liste><emne><id>17</id><tittel>Wrong</tittel></emne></emne_liste>
          <id>321</id><tittel>Example long title</tittel><korttittel>Example short title</korttittel>
          <status>til_behandling</status></sak>"""
        item = self.source(records, cfg=config("saker")).fetch()[0]
        self.assertEqual("sak:321", item.key)
        self.assertEqual("Example short title", item.title)
        self.assertTrue(item.url.endswith("sakid=321"))

    def test_hearing_uses_own_id_all_case_titles_and_direct_link(self):
        records = """<horing><komite><id>COM</id></komite>
          <horing_sak_info_liste>
            <horing_sak_info><sak_id>321</sak_id><sak_korttittel>Example first case</sak_korttittel></horing_sak_info>
            <horing_sak_info><sak_id>322</sak_id><sak_korttittel>Example second case</sak_korttittel></horing_sak_info>
          </horing_sak_info_liste><id>456</id><horing_status>Planlagt</horing_status>
          <start_dato>2026-09-10</start_dato><innspillsfrist>2026-09-01</innspillsfrist></horing>"""
        item = self.source(records, cfg=config("horinger")).fetch()[0]
        self.assertEqual("horing:456", item.key)
        self.assertEqual("Example first case / Example second case", item.title)
        self.assertTrue(item.url.endswith("horing/?h=456"))
        self.assertEqual("2026-09-10", item.published)
        self.assertIn("Frist: 2026-09-01", item.alert_details)

    def test_missing_direct_id_never_falls_back_to_nested_id_or_content(self):
        for dataset, record in [
            ("saker", "<sak><person><id>17</id></person><tittel>Example</tittel></sak>"),
            ("skriftligesporsmal", question().replace("<id>123</id>", "")),
            ("horinger", "<horing><komite><id>17</id></komite><tittel>Example</tittel></horing>"),
        ]:
            with self.subTest(dataset=dataset), self.assertRaisesRegex(SourceError, "direct Stortinget"):
                self.source(record, cfg=config(dataset)).fetch()

    def test_duplicate_direct_ids_abort_before_engine_can_overwrite(self):
        with self.assertRaisesRegex(SourceError, "duplicate Stortinget record"):
            self.source(question() + question()).fetch()

    def test_response_timestamps_are_not_updates_but_answer_is(self):
        source = self.source(question())
        first = source.fetch()
        previous, _, _ = evaluate(source.config, first, None, max_seen=3000)
        source._xml = lambda *_a, **_kw: root(question(timestamp="2026-08-02T12:00:00"))
        second = source.fetch()
        self.assertEqual(first[0].content_hash(), second[0].content_hash())
        _, alerts, _ = evaluate(source.config, second, previous, max_seen=3000)
        self.assertEqual([], alerts)
        source._xml = lambda *_a, **_kw: root(question(status="besvart", answered="2026-08-02"))
        updated, alerts, _ = evaluate(source.config, source.fetch(), previous, max_seen=3000)
        self.assertEqual(1, len(alerts))
        self.assertEqual("updated", alerts[0].change)
        self.assertIn("Besvart: 2026-08-02", alerts[0].item.alert_details)
        _, alerts, _ = evaluate(source.config, source.fetch(), updated, max_seen=3000)
        self.assertEqual([], alerts)

    def test_zero_answer_date_is_not_presented_as_answered(self):
        item = self.source(question(answered="0001-01-01T00:00:00Z")).fetch()[0]
        self.assertEqual("", item.metadata["answered_at"])
        self.assertFalse(any(detail.startswith("Besvart:") for detail in item.alert_details))

    def test_zero_hearing_date_allows_valid_fallback(self):
        records = """<horing><id>456</id><tittel>Example hearing</tittel>
          <start_dato>0001-01-01T00:00:00Z</start_dato><horing_dato_tid>2026-09-10</horing_dato_tid>
          <innspillsfrist>0001-01-01T00:00:00Z</innspillsfrist>
          <anmodningsfrist_dato_tid>2026-09-01</anmodningsfrist_dato_tid></horing>"""
        item = self.source(records, cfg=config("horinger")).fetch()[0]
        self.assertEqual("2026-09-10", item.published)
        self.assertIn("Frist: 2026-09-01", item.alert_details)

    def test_legacy_migration_preserves_old_keys_and_future_item_alerts(self):
        source = self.source(question())
        previous = {"initialized": True, "seen": {"sporsmal:ABC": "old-digest"}, "order": ["sporsmal:ABC"]}
        saved = deepcopy(previous)
        items = source.fetch_with_state(previous)
        self.assertTrue(all(item.suppress_alert for item in items))
        migrated, alerts, _ = evaluate(source.config, items, previous, max_seen=3000)
        migrated = source.augment_state(migrated)
        self.assertEqual(saved, previous)
        self.assertEqual([], alerts)
        self.assertEqual("old-digest", migrated["seen"]["sporsmal:ABC"])
        self.assertIn("sporsmal:123", migrated["seen"])
        self.assertEqual(2, migrated["stortinget_identity_version"])
        _, alerts, _ = evaluate(source.config, source.fetch_with_state(migrated), migrated, max_seen=3000)
        self.assertEqual([], alerts)
        source._xml = lambda *_a, **_kw: root(question() + question("124"))
        _, alerts, _ = evaluate(source.config, source.fetch_with_state(migrated), migrated, max_seen=3000)
        self.assertEqual(["sporsmal:124"], [alert.item.key for alert in alerts])

    def test_empty_feed_does_not_mark_legacy_state_migrated(self):
        previous = {"initialized": True, "seen": {"sporsmal:ABC": "old"}, "order": ["sporsmal:ABC"]}
        with self.assertRaisesRegex(SourceError, "empty feed"):
            self.source("").fetch_with_state(previous)

    def test_partial_empty_feed_does_not_commit_migration(self):
        cfg = config("saker", "skriftligesporsmal")
        source = self.source(question(), cfg=cfg)
        previous = {"initialized": True, "seen": {"sak:17": "old-case", "sporsmal:ABC": "old-question"},
                    "order": ["sak:17", "sporsmal:ABC"]}
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory))
            state.save(cfg.id, previous)
            result = run(Config((cfg,)), state, Mock(), source_factory=lambda _: source)
            self.assertIn("empty saker", result.errors[cfg.id])
            self.assertEqual(previous, state.load(cfg.id))

    def test_xml_schema_and_nonempty_dataset_selection_are_required(self):
        source = StortingetSource(config())
        for content in [b'<error/>', b'<sporsmal_oversikt xmlns="http://data.stortinget.no"/>',
                        b'<sporsmal_oversikt><sporsmal_liste/></sporsmal_oversikt>']:
            source.get = Mock(return_value=Mock(content=content))
            with self.subTest(content=content), self.assertRaisesRegex(SourceError, "XML structure"):
                source._xml("skriftligesporsmal")
        source = StortingetSource(replace(config(), options={"datasets": []}))
        with self.assertRaisesRegex(SourceError, "non-empty"):
            source.fetch()

    def test_full_session_is_retained_after_repair(self):
        source = self.source("".join(question(str(index)) for index in range(4800)))
        legacy = {"initialized": True, "seen": {"sporsmal:ABC": "old-digest"}, "order": ["sporsmal:ABC"]}
        items = source.fetch_with_state(legacy)
        previous, alerts, _ = evaluate(source.config, items, legacy, max_seen=3000)
        previous = source.augment_state(previous)
        self.assertEqual([], alerts)
        self.assertEqual(4801, len(previous["seen"]))
        self.assertEqual("old-digest", previous["seen"]["sporsmal:ABC"])
        _, alerts, _ = evaluate(source.config, source.fetch_with_state(previous), previous, max_seen=3000)
        self.assertEqual([], alerts)

    def test_full_session_retention_floor_preserves_higher_and_unlimited_limits(self):
        cfg = config()
        self.assertEqual(20000, source_seen_limit(cfg, 3000))
        self.assertEqual(30000, source_seen_limit(cfg, 30000))
        self.assertEqual(0, source_seen_limit(cfg, 0))
        self.assertEqual(20000, source_seen_limit(replace(cfg, options={"max_seen_per_source": 3000}), 3000))
        self.assertEqual(40000, source_seen_limit(replace(cfg, options={"max_seen_per_source": 40000}), 3000))
        self.assertEqual(3000, source_seen_limit(replace(cfg, kind="rss"), 3000))

    def test_migration_marker_does_not_commit_after_partial_fetch_failure(self):
        cfg = config("skriftligesporsmal", "horinger")
        source = self.source(question(), cfg=cfg)
        def xml(endpoint, params=None):
            if endpoint == "horinger":
                raise SourceError("example unavailable hearing endpoint")
            return root(question())
        source._xml = xml
        previous = {"initialized": True, "seen": {"sporsmal:ABC": "old-digest"}, "order": ["sporsmal:ABC"]}
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory))
            state.save(cfg.id, previous)
            result = run(Config((cfg,)), state, Mock(), source_factory=lambda _: source)
            self.assertIn(cfg.id, result.errors)
            self.assertEqual(previous, state.load(cfg.id))


if __name__ == "__main__":
    unittest.main()
