from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import Mock

from watchtower.sources.common import SourceError
from watchtower.sources.web_changes import WebChangesSource
from test_change_sources import config, poll, response


def card(key="one", heading="New editor", date="18.9.2026 15:25:00 CEST"):
    return f'<a href="/{key}"><h2>{heading}<small>{date} | Release</small></h2><p>Source summary</p></a>'


def page(source, *cards):
    source.get = Mock(return_value=response(("<main>" + "".join(cards) + "</main>").encode()))


def dated(source):
    return WebChangesSource(replace(source.config, options={
        **source.config.options, "published_selector": "h2 small", "published_text_separator": "|",
    }))


class WebPublicationTests(unittest.TestCase):
    def test_presentation_upgrade_keeps_scope_history_and_observed_date(self):
        old = WebChangesSource(config("web_links", selector="main a"))
        page(old, card())
        state, _ = poll(old)
        untouched = deepcopy(state)
        source = dated(old)
        page(source, card())
        self.assertEqual(old.scope, source.scope)
        updated, alerts = poll(source, state)
        self.assertEqual([], alerts)
        self.assertEqual(untouched, state)
        self.assertEqual(set(state["seen"]), set(updated["seen"]))
        item = source.fetch_with_state(updated)[0]
        self.assertEqual("18.9.2026 15:25:00 CEST", item.published)
        self.assertEqual("New editor Source summary", item.title)
        row = updated["source_state"]["records"]["rows"]["https://example.test/one"]["row"]
        self.assertIn("18.9.2026", row["raw_title"])
        self.assertNotIn("18.9.2026", row["fields"]["title"])

    def test_date_only_and_order_changes_are_silent_but_headline_changes_alert(self):
        source = dated(WebChangesSource(config("web_links", selector="main a", title_selector="h2")))
        page(source, card(), card("two", "New office"))
        state, _ = poll(source)
        prior = {i.key: i.content_hash() for i in source.fetch_with_state(state)}
        page(source, card("two", "New office", "19.9.2026 12:00:00 CEST"),
             card(date="19.9.2026 12:01:00 CEST"))
        updated, alerts = poll(source, state)
        self.assertEqual([], alerts)
        self.assertEqual(prior, {i.key: i.content_hash() for i in source.fetch_with_state(updated)})
        page(source, card("one", "New manager editor", "19.9.2026 12:01:00 CEST"),
             card("two", "New office", "19.9.2026 12:00:00 CEST"))
        _, alerts = poll(source, updated)
        self.assertEqual(1, len(alerts))
        self.assertIn("New editor → New manager editor", " ".join(alerts[0].item.alert_details))
        self.assertNotIn("2026", " ".join(alerts[0].item.alert_details))

    def test_attribute_date_preserves_source_timezone_and_does_not_change_hash(self):
        source = WebChangesSource(config("web_links", selector="main a", title_selector="h2",
                                         published_selector="time", published_attribute="datetime"))
        page(source, '<a href="/one"><h2>Headline</h2><time datetime="2026-09-18T10:00:00+02:00">Today</time></a>')
        state, _ = poll(source)
        original = source.fetch_with_state(state)[0]
        self.assertEqual("2026-09-18T10:00:00+02:00", original.published)
        page(source, '<a href="/one"><h2>Headline</h2><time datetime="2026-09-19T10:00:00+02:00">Tomorrow</time></a>')
        changed = source.fetch_with_state(state)[0]
        self.assertEqual(original.content_hash(), changed.content_hash())
        self.assertEqual([], poll(source, state)[1])

    def test_missing_ambiguous_empty_and_drifted_dates_fail_closed(self):
        source = dated(WebChangesSource(config("web_links", selector="main a")))
        page(source, card())
        state, _ = poll(source)
        unchanged = deepcopy(state)
        for content in ('<a href="/one"><h2>No date</h2></a>',
                        '<a href="/one"><h2>A<small>one</small><small>two</small></h2></a>',
                        '<a href="/one"><h2>A<small></small></h2></a>',
                        '<a href="/one"><h2>A<small>Date without separator</small></h2></a>',
                        '<a href="/one"><h2>A<small>| Release</small></h2></a>'):
            page(source, content)
            with self.subTest(content=content), self.assertRaises(SourceError):
                source.fetch_with_state(state)
            self.assertEqual(unchanged, state)
        source = WebChangesSource(config("web_links", selector="main a", published_selector="time",
                                         published_attribute="datetime"))
        page(source, '<a href="/one">Headline<time>Date</time></a>')
        with self.assertRaisesRegex(SourceError, "attribute is missing"):
            source.fetch()

    def test_upgrade_preserves_concurrent_headline_change_when_date_is_known(self):
        old = WebChangesSource(config("web_links", selector="main a", title_selector="h2"))
        page(old, card())
        state, _ = poll(old)
        source = dated(old)
        page(source, card(heading="New manager editor"))
        _, alerts = poll(source, state)
        self.assertEqual(1, len(alerts))
        self.assertIn("New editor → New manager editor", " ".join(alerts[0].item.alert_details))

    def test_upgrade_does_not_guess_unobserved_historical_date(self):
        old = WebChangesSource(config("web_links", selector="main a"))
        page(old, card())
        state, _ = poll(old)
        unchanged = deepcopy(state)
        source = dated(old)
        page(source, card(date="19.9.2026 15:25:00 CEST"))
        with self.assertRaisesRegex(SourceError, "comparable prior date"):
            source.fetch_with_state(state)
        self.assertEqual(unchanged, state)

    def test_publication_options_require_a_link_selector_and_valid_values(self):
        for options in ({"published_selector": ""}, {"published_selector": 7},
                        {"published_attribute": "datetime"}, {"published_text_separator": "|"},
                        {"published_selector": "time", "published_attribute": ""},
                        {"published_selector": "time", "published_attribute": "invalid name"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                WebChangesSource(config("web_links", selector="main a", **options))
        with self.assertRaisesRegex(ValueError, "require web_links"):
            WebChangesSource(config("web_page", selector="main", published_selector="time"))


if __name__ == "__main__":
    unittest.main()
