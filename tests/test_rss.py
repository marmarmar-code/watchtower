from __future__ import annotations

import unittest

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.rss import RssSource


class Response:
    def __init__(self, content: bytes) -> None:
        self.content = content


class RssSourceTests(unittest.TestCase):
    def config(self, *urls: str) -> SourceConfig:
        return SourceConfig(
            id="example-feed",
            kind="rss",
            label="Example feed",
            urls=tuple(urls),
            filters=FilterRule(include_any=("example",)),
        )

    def profile_config(self, *profiles: str) -> SourceConfig:
        return SourceConfig(
            id="profile-feed",
            kind="rss",
            label="Profile feed",
            filters=FilterRule(include_any=("example",)),
            options={"profiles": list(profiles)},
        )

    def test_rss_item_is_normalized(self):
        source = RssSource(self.config("https://example.test/feed.xml"))
        source.get = lambda *_args, **_kwargs: Response(
            b"""
            <rss><channel><item>
              <title>Example release</title>
              <link>https://example.test/releases/1</link>
              <guid>release-1</guid>
              <pubDate>Thu, 27 Aug 2026 08:00:00 GMT</pubDate>
              <description><![CDATA[<p>Example <b>description</b></p>]]></description>
              <category>News</category>
            </item></channel></rss>
            """
        )

        items = source.fetch()

        self.assertEqual(1, len(items))
        self.assertEqual("https://example.test/releases/1", items[0].key)
        self.assertEqual("Example release", items[0].title)
        self.assertEqual("Example description", items[0].text)
        self.assertIn("News", items[0].searchable_text())

    def test_linkless_rss_items_use_distinct_guid_keys(self):
        source = RssSource(self.config("https://example.test/feed.xml"))
        source.get = lambda *_args, **_kwargs: Response(
            b"<rss><channel>"
            b"<item><title>Example one</title><guid>one</guid></item>"
            b"<item><title>Example two</title><guid>two</guid></item>"
            b"</channel></rss>"
        )

        items = source.fetch()

        self.assertEqual(2, len(items))
        self.assertEqual(
            {
                "https://example.test/feed.xml#guid=one",
                "https://example.test/feed.xml#guid=two",
            },
            {item.key for item in items},
        )
        self.assertEqual({"https://example.test/feed.xml"}, {item.url for item in items})

    def test_atom_entry_is_normalized(self):
        source = RssSource(self.config("https://example.test/atom.xml"))
        source.get = lambda *_args, **_kwargs: Response(
            b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>urn:example:2</id>
                <title>Example decision</title>
                <link rel="alternate" href="https://example.test/decisions/2" />
                <updated>2026-08-27T08:00:00Z</updated>
                <summary>Example summary</summary>
                <category term="Decisions" />
              </entry>
            </feed>
            """
        )

        items = source.fetch()

        self.assertEqual(1, len(items))
        self.assertEqual("urn:example:2", items[0].key)
        self.assertEqual("https://example.test/decisions/2", items[0].url)
        self.assertEqual("2026-08-27T08:00:00Z", items[0].published)
        self.assertIn("Decisions", items[0].searchable_text())

    def test_atom_entry_without_id_or_link_is_not_usable(self):
        source = RssSource(self.config("https://example.test/atom.xml"))
        source.get = lambda *_args, **_kwargs: Response(
            b'<feed xmlns="http://www.w3.org/2005/Atom">'
            b"<entry><title>Example without identity</title></entry></feed>"
        )

        with self.assertRaisesRegex(SourceError, "contained no usable items"):
            source.fetch()

    def test_duplicate_item_across_feeds_is_returned_once(self):
        source = RssSource(
            self.config("https://example.test/one.xml", "https://example.test/two.xml")
        )
        source.get = lambda *_args, **_kwargs: Response(
            b"<rss><channel><item><title>Example</title>"
            b"<link>https://example.test/items/same</link><guid>same</guid>"
            b"</item></channel></rss>"
        )

        self.assertEqual(1, len(source.fetch()))

    def test_feed_urls_are_required(self):
        with self.assertRaisesRegex(SourceError, "requires at least one feed URL"):
            RssSource(self.config()).fetch()

    def test_bundled_profile_resolves_without_copying_a_url(self):
        source = RssSource(self.profile_config("politiloggen"))
        self.assertEqual(
            ("https://api.politiloggen.politiet.no/feeds/rss",),
            source.feed_urls,
        )

    def test_unknown_bundled_profile_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown RSS profile"):
            RssSource(self.profile_config("missing"))

    def test_empty_feed_fails_closed(self):
        source = RssSource(self.config("https://example.test/feed.xml"))
        source.get = lambda *_args, **_kwargs: Response(b"<rss><channel /></rss>")

        with self.assertRaisesRegex(SourceError, "contained no usable items"):
            source.fetch()

    def test_one_empty_feed_makes_multi_feed_source_fail_closed(self):
        source = RssSource(
            self.config("https://example.test/one.xml", "https://example.test/two.xml")
        )
        responses = iter([
            Response(
                b"<rss><channel><item><title>Example</title>"
                b"<link>https://example.test/items/1</link></item></channel></rss>"
            ),
            Response(b"<rss><channel /></rss>"),
        ])
        source.get = lambda *_args, **_kwargs: next(responses)

        with self.assertRaisesRegex(SourceError, "contained no usable items"):
            source.fetch()

    def test_unrelated_xml_with_item_nodes_is_rejected(self):
        source = RssSource(self.config("https://example.test/not-a-feed.xml"))
        source.get = lambda *_args, **_kwargs: Response(
            b"<document><item><title>Example</title><link>/1</link></item></document>"
        )

        with self.assertRaisesRegex(SourceError, "unsupported"):
            source.fetch()


if __name__ == "__main__":
    unittest.main()
