from __future__ import annotations

import unittest
from dataclasses import replace
from watchtower.engine import evaluate

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.rss import RssSource


class Response:
    def __init__(self, content: bytes) -> None:
        self.content = content


class RssSourceTests(unittest.TestCase):
    def test_category_exclusion_uses_exact_source_labels_and_preserves_history(self):
        categories = ['<category>Calendar</category>', '<category>Other</category><category>calendar</category>',
                      '<category>Other | Calendar</category>', '<category>Calendar news</category>', '']
        raw = ('<rss><channel>' + ''.join(f'<item><title>Example Calendar news {i}</title><link>https://example.test/{i}</link>{cat}</item>' for i,cat in enumerate(categories)) + '</channel></rss>').encode()
        cfg = self.config('https://example.test/feed')
        old = RssSource(cfg); old.get = lambda *_: Response(raw)
        new = RssSource(replace(cfg, options={'exclude_categories':['CALENDAR']})); new.get = old.get
        before, after = old.fetch(), new.fetch()
        self.assertEqual([True, True, False, False, False], [i.suppress_alert for i in after])
        self.assertEqual([(i.key,i.content_hash(),i.metadata) for i in before], [(i.key,i.content_hash(),i.metadata) for i in after])
        previous,_,_ = evaluate(cfg,[],None,max_seen=100)
        before_state,_,_ = evaluate(cfg,before,previous,max_seen=100)
        after_state,alerts,_ = evaluate(cfg,after,previous,max_seen=100)
        self.assertEqual(before_state,after_state)
        self.assertEqual(3,len(alerts))

    def test_atom_category_terms_and_text_are_supported_without_splitting_labels(self):
        cfg = replace(self.config('https://example.test/atom'),options={'exclude_categories':['Calendar']})
        source = RssSource(cfg)
        source.get = lambda *_: Response(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>a</id><title>Example</title><category term="calendar"/></entry><entry><id>b</id><title>Example</title><category>Calendar</category></entry><entry><id>c</id><title>Example</title><category term="Other | Calendar"/></entry></feed>')
        self.assertEqual([True,True,False],[i.suppress_alert for i in source.fetch()])

    def test_category_rules_and_filtered_broken_feeds_fail_visibly(self):
        for rules in ('Calendar',[None],[''],[' Calendar'],['Calendar ']):
            with self.subTest(rules=rules),self.assertRaisesRegex(ValueError,'exclude_categories'):
                RssSource(replace(self.config(),options={'exclude_categories':rules}))
        source = RssSource(replace(self.config('https://example.test/rss'),options={'exclude_categories':['Calendar']}))
        source.get = lambda *_: Response(b'<rss><channel><item><title>Example</title><link>https://example.test/1</link><category>Calendar</category></item></channel></rss>')
        self.assertTrue(source.fetch()[0].suppress_alert)
        source.get = lambda *_: Response(b'<rss><channel><item><title>Broken</title><category>Calendar</category></item></channel></rss>')
        with self.assertRaises(SourceError): source.fetch()

    def test_exact_path_segments_keep_news_mentions_and_all_history(self):
        urls = ['https://example.test/region/events/2026/one',
                'https://example.test/articles/events-in-business',
                'https://example.test/news?tag=events',
                'https://example.test/other-events/one',
                'https://jobs.example.test/job/1']
        raw = ('<rss><channel>' + ''.join(f'<item><title>Example events in business</title><link>{url}</link></item>' for url in urls) + '</channel></rss>').encode()
        config = replace(self.config('https://example.test/rss'), options={'exclude_url_hosts': ['jobs.example.test']})
        original = RssSource(config); original.get = lambda *_: Response(raw)
        filtered = RssSource(replace(config, options={**config.options, 'exclude_url_path_segments': ['events']}))
        filtered.get = original.get
        before, after = original.fetch(), filtered.fetch()
        self.assertEqual([(i.key, i.content_hash()) for i in before], [(i.key, i.content_hash()) for i in after])
        self.assertEqual([True, False, False, False, True], [i.suppress_alert for i in after])
        previous, _, _ = evaluate(config, [], None, max_seen=100)
        old_state, _, _ = evaluate(config, before, previous, max_seen=100)
        new_state, alerts, _ = evaluate(filtered.config, after, previous, max_seen=100)
        self.assertEqual(old_state, new_state)
        self.assertEqual(urls[1:4], [a.item.url for a in alerts])

    def test_path_filtered_atom_is_valid_and_bad_rules_are_rejected(self):
        source = RssSource(replace(self.config('https://example.test/atom'), options={'exclude_url_path_segments': ['events']}))
        source.get = lambda *_: Response(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>event1</id><title>Example event</title><link href="https://example.test/events/one"/></entry></feed>')
        self.assertTrue(source.fetch()[0].suppress_alert)
        source.get = lambda *_: Response(b'<rss><channel><item><title>Broken</title></item></channel></rss>')
        with self.assertRaises(SourceError):
            source.fetch()
        for segments in ('events', [''], [None], ['/events/'], ['events*'], ['events?x=1'], [' events']):
            with self.subTest(segments=segments), self.assertRaisesRegex(ValueError, 'exclude_url_path_segments'):
                RssSource(replace(self.config(), options={'exclude_url_path_segments': segments}))

    def test_path_segment_prefix_exclusion_is_bounded_and_preserves_state(self):
        urls = [
            'https://example.test/flaggeplikt-vedtak-om-overtredelsesgebyr/1',
            'https://example.test/flaggeplikt-vedtak-om-overtredelsesgebyr-old/2',
            'https://example.test/other/flaggeplikt-vedtak-om-overtredelsesgebyr/3',
            'https://example.test/flaggeplikt/4?next=flaggeplikt-vedtak-om-overtredelsesgebyr',
            'https://flaggeplikt-vedtak-om-overtredelsesgebyr.example.test/5',
        ]
        raw = ('<rss><channel>' + ''.join(
            f'<item><title>Example {i}</title><link>{url}</link></item>'
            for i, url in enumerate(urls)
        ) + '</channel></rss>').encode()
        config = self.config('https://example.test/rss')
        original = RssSource(config); original.get = lambda *_: Response(raw)
        filtered = RssSource(replace(config, options={
            'exclude_url_path_segment_prefixes': ['flaggeplikt-vedtak-om-overtredelsesgebyr'],
        })); filtered.get = original.get
        before, after = original.fetch(), filtered.fetch()
        self.assertEqual([(i.key, i.content_hash()) for i in before], [(i.key, i.content_hash()) for i in after])
        self.assertEqual([True, True, True, False, False], [i.suppress_alert for i in after])
        previous, _, _ = evaluate(config, [], None, max_seen=100)
        old_state, _, _ = evaluate(config, before, previous, max_seen=100)
        new_state, alerts, _ = evaluate(filtered.config, after, previous, max_seen=100)
        self.assertEqual(old_state, new_state)
        self.assertEqual([urls[3], urls[4]], [entry.item.url for entry in alerts])

    def test_path_segment_prefix_configuration_rejects_broad_rules(self):
        for prefixes in ('flaggeplikt', [''], [None], ['/flaggeplikt'], ['flaggeplikt*'],
                         ['flaggeplikt?x=1'], [' flaggeplikt']):
            with self.subTest(prefixes=prefixes), self.assertRaisesRegex(
                ValueError, 'exclude_url_path_segment_prefixes'
            ):
                RssSource(replace(self.config(), options={
                    'exclude_url_path_segment_prefixes': prefixes,
                }))

    def config(self, *urls: str) -> SourceConfig:
        return SourceConfig(
            id="example-feed",
            kind="rss",
            label="Example feed",
            urls=tuple(urls),
            filters=FilterRule(include_any=("example",)),
        )

    def test_exact_host_exclusion_preserves_identity_and_director_news(self):
        urls = ["https://jobs.example.test/jobs/1", "https://JOBS.EXAMPLE.TEST./jobs/2",
                "https://news.example.test/new-director", "https://jobs.example.test.other.test/news",
                "https://news.example.test/?next=jobs.example.test"]
        raw = ('<rss><channel>' + ''.join(f'<item><title>Example new director</title><link>{url}</link></item>' for url in urls) + '</channel></rss>').encode()
        config = self.config("https://example.test/rss")
        old = RssSource(config); old.get = lambda *_: Response(raw)
        source = RssSource(replace(config, options={"exclude_url_hosts": ["jobs.example.test"]}))
        source.get = old.get
        original, items = old.fetch(), source.fetch()
        self.assertEqual([(i.key, i.compatible_content_hashes()) for i in original], [(i.key, i.compatible_content_hashes()) for i in items])
        self.assertEqual([True, True, False, False, False], [i.suppress_alert for i in items])
        previous, _, _ = evaluate(config, [], None, max_seen=100)
        current, alerts, _ = evaluate(config, items, previous, max_seen=100)
        self.assertEqual(urls[2:], [a.item.url for a in alerts])
        self.assertEqual(5, len(current['seen']))
        _, repeated, _ = evaluate(config, source.fetch(), current, max_seen=100)
        self.assertEqual([], repeated)

    def test_excluded_atom_only_feed_is_valid_and_silent(self):
        source = RssSource(replace(self.config("https://example.test/atom"), options={"exclude_url_hosts": ["jobs.example.test"]}))
        source.get = lambda *_: Response(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>job1</id><title>Example job</title><link href="https://jobs.example.test/1"/></entry></feed>')
        items = source.fetch()
        self.assertEqual(1, len(items))
        self.assertTrue(items[0].suppress_alert)

    def test_host_configuration_rejects_broad_or_malformed_rules(self):
        for hosts in ('jobs.example.test', [None], ['*.example.test'], ['https://example.test'], ['example.test:443'], ['example.test/path'], [' example.test'], ['']):
            with self.subTest(hosts=hosts), self.assertRaisesRegex(ValueError, 'exclude_url_hosts'):
                RssSource(replace(self.config(), options={'exclude_url_hosts': hosts}))

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
