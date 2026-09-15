from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import Mock

from bs4 import BeautifulSoup

from watchtower.config import FilterRule, SourceConfig
from watchtower.engine import evaluate
from watchtower.models import Item
from watchtower.sources.common import SourceError
from watchtower.sources.euronext import EuronextSource, _company_page_items


COMPANY_URL = "https://example.test/nb/product/equities/NO0010000000-XOSL/company-information"
COMPANY_HTML = """
<html>
  <head><title>EXAMPLE INDUSTRIES | NO0010000000 | Company information</title></head>
  <body>
    <section class="issuer-news">
      <a href="/nb/listview/company-press-release/152145">Se alle</a>
      <div><span>14/08/2026</span><span>Second-quarter results show continued growth</span></div>
      <div><span>07/08/2026</span><span>Example Industries ASA (EXI): Invitation to results webcast</span></div>
      <div class="modal"><a href="/nb/products/equities/company-news/2026-08-14-quarter">Open in new window</a></div>
      <div class="modal"><a href="/nb/products/equities/company-news/2026-08-07-invitation">Open in new window</a></div>
    </section>
  </body>
</html>
"""


class EuronextTests(unittest.TestCase):
    def test_expanded_archive_backfill_is_quiet_but_recent_news_and_known_revisions_survive(self):
        source, _, _ = self.expanded_source()
        rows = [Item('extended', key, title, 'https://example.test/'+key, published=date)
                for key, title, date in [('old', 'Old archive row', '20 Nov 2025 15:26 CET'),
                                         ('recent', 'Recent news', '11 Sep 2026 06:00 CEST'),
                                         ('grace', 'Previous date', '10 Sep 2026 23:30 CEST'),
                                         ('known', 'Known corrected title', '19 Nov 2025 12:00 CET')]]
        previous, _, _ = evaluate(source.config, [replace(rows[-1], title='Earlier title')], None, max_seen=100)
        previous['last_checked_at'] = '2026-09-11T00:30:42+00:00'
        source._fetch_issuer = Mock(return_value=rows)
        items = source.fetch_with_state(previous)
        self.assertEqual([True, False, False, False], [x.suppress_alert for x in items])
        self.assertEqual([(x.key,x.content_hash()) for x in rows], [(x.key,x.content_hash()) for x in items])
        state, alerts, baseline = evaluate(source.config, items, previous, max_seen=100)
        self.assertFalse(baseline)
        self.assertEqual({'recent','grace','known'}, {a.item.key for a in alerts})
        self.assertEqual(4, len(state['seen']))
        self.assertFalse(source.fetch()[0].suppress_alert)  # No leaked prior-run context.
        # An outage does not move the last successful check forward.
        previous['last_checked_at'] = '2025-11-19T00:00:00+00:00'
        self.assertFalse(source.fetch_with_state(previous)[0].suppress_alert)
        legacy = EuronextSource(replace(source.config, options={}))
        legacy._fetch_issuer = source._fetch_issuer
        previous['last_checked_at'] = '2026-09-11T00:30:42+00:00'
        self.assertFalse(legacy.fetch_with_state(previous)[0].suppress_alert)

    def test_expanded_backfill_skips_detail_lookup_and_rejects_uncertain_dates(self):
        url, rows = self.node_items(date='20/11/2025')
        source = EuronextSource(SourceConfig(id='euronext',kind='euronext',urls=(url,),options={'include_listview':True}))
        source._fetch_issuer = Mock(return_value=rows)
        source.get = Mock()
        previous = {'seen':{}, 'last_checked_at':'2026-09-11T00:30:42+00:00'}
        items = source.fetch_with_state(previous)
        self.assertTrue(items[0].suppress_alert)
        self.assertEqual(rows[0].content_hash(), items[0].content_hash())
        source.get.assert_not_called()
        source._fetch_issuer.return_value = [replace(rows[0],published='unavailable')]
        with self.assertRaisesRegex(SourceError,'usable date'):
            source.fetch_with_state(previous)
        for checked in ('', 'invalid', '2026-09-11', 123):
            with self.subTest(checked=checked), self.assertRaisesRegex(SourceError,'check time'):
                source.fetch_with_state({**previous,'last_checked_at':checked})

    def node_items(self, *, node_id="12345", extra="", date="14/08/2026"):
        url = COMPANY_URL.replace("example.test", "live.euronext.com")
        html = f'''<html><title>EXAMPLE INDUSTRIES | Company information</title>
          <section><a href="/nb/listview/company-press-release/1">Se alle</a>
          <table><tr><td>{date}</td><td><a href="" data-node-nid="{node_id}">
          Example quarterly results</a></td></tr>{extra}</table></section></html>'''
        return url, _company_page_items("euronext", BeautifulSoup(html, "html.parser"), url)

    def test_node_link_preserves_legacy_hashes_and_does_not_resend(self):
        url, items = self.node_items()
        item = items[0]
        legacy = Item(**{key: value for key, value in vars(item).items() if key not in {"identity_url", "issuer_url"}} | {"url": url})
        self.assertEqual("https://live.euronext.com/nb/node/12345", item.url)
        self.assertTrue(item.key.startswith("euronext:"))
        self.assertEqual(legacy.compatible_content_hashes(), item.compatible_content_hashes())
        config = SourceConfig(id="euronext", kind="euronext", filters=FilterRule(match_all=True))
        previous, _, _ = evaluate(config, [legacy], None, max_seen=100)
        current, alerts, baseline = evaluate(config, items, previous, max_seen=100)
        self.assertFalse(baseline)
        self.assertEqual([], alerts)
        self.assertEqual(previous["seen"], current["seen"])
        _, alerts, _ = evaluate(config, [replace(item, text="Changed issuer content")], current, max_seen=100)
        self.assertEqual(1, len(alerts))

    def test_ambiguous_or_invalid_node_id_keeps_issuer_link(self):
        extra = '<tr><td>14/08/2026</td><td><a href="" data-node-nid="67890">Example quarterly results</a></td></tr>'
        for options in ({"node_id": "../123"}, {"extra": extra}):
            with self.subTest(options=options):
                url, items = self.node_items(**options)
                self.assertEqual(url, items[0].url)

    def test_title_date_pair_selects_its_own_node(self):
        extra = '<tr><td>13/08/2026</td><td><a href="" data-node-nid="67890">Example quarterly results</a></td></tr>'
        _, items = self.node_items(extra=extra)
        self.assertEqual(["https://live.euronext.com/nb/node/12345", "https://live.euronext.com/nb/node/67890"], [item.url for item in items])

    def test_only_unseen_items_resolve_verified_issuer_notice(self):
        url, items = self.node_items()
        source = self.source()
        html = '''<div data-node-path="/node/12345" data-isin="NO0010000000">
          <h1>Example quarterly results</h1><a href="https://newsweb.oslobors.no/message/123">Read notice</a></div>'''
        source.get = Mock(return_value=Mock(text=html))
        linked = source._notification_links(items)
        self.assertEqual("https://newsweb.oslobors.no/message/123", linked[0].url)
        self.assertEqual(items[0].compatible_content_hashes(), linked[0].compatible_content_hashes())
        source.get.assert_called_once()
        source.get.reset_mock()
        source._previous_seen = {items[0].key: items[0].content_hash()}
        source._notification_links(items)
        source.get.assert_not_called()

    def test_failed_or_mismatched_detail_keeps_safe_issuer_link(self):
        url, items = self.node_items()
        for result in (SourceError("HTTP 503"), Mock(text='''<div data-node-path="/node/12345" data-isin="NO0020000000">
          <h1>Example quarterly results</h1><a href="https://newsweb.oslobors.no/message/123">Read notice</a></div>''')):
            with self.subTest(result=type(result).__name__):
                source = self.source()
                source.get = Mock(side_effect=[result])
                linked = source._notification_links(items)
                self.assertEqual(url, linked[0].url)
                self.assertEqual(items[0].content_hash(), linked[0].content_hash())

    def test_persisted_items_skip_detail_requests_and_state_context_is_cleared(self):
        _, items = self.node_items()
        source = self.source()
        source._fetch_issuer = Mock(return_value=items)
        source.get = Mock(side_effect=SourceError("optional detail unavailable"))
        source.fetch_with_state({"seen": {items[0].key: items[0].content_hash()}})
        source.get.assert_not_called()
        source.fetch()
        source.get.assert_called_once()

    def test_canonical_detail_path_and_multiple_instruments_require_matching_date(self):
        url, items = self.node_items()
        for date, expected in (("2026-08-14", "https://newsweb.oslobors.no/message/123"), ("2026-08-13", url)):
            with self.subTest(date=date):
                html = f'''<div data-node-path="/products/equities/company-news/{date}-results"
                  data-isin="NO0020000000, NO0010000000">
                  <h1>Example quarterly results</h1><a href="https://newsweb.oslobors.no/message/123">Read notice</a></div>'''
                source = self.source()
                source.get = Mock(return_value=Mock(text=html))
                self.assertEqual(expected, source._notification_links(items)[0].url)

    def expanded_source(self, **options):
        url = COMPANY_URL.replace("example.test", "live.euronext.com")
        source = EuronextSource(SourceConfig(id="extended", kind="euronext", urls=(url,),
            options={"include_listview": True, **options}, filters=FilterRule(match_all=True)))
        company = '<title>EXAMPLE</title><a href="/nb/listview/company-press-release/1">All</a><table><tr><td>14/08/2026</td><td>Notice 0</td></tr></table>'
        rows = ''.join(f'<tr><td>14 Aug 2026 12:00 CEST</td><td>EXAMPLE</td><td><a href="" data-node-nid="{100+i}">Notice {i}</a></td></tr>' for i in range(50))
        listing = '<table><tr><th>Time</th><th>Company</th><th>Title</th></tr>' + rows + '</table>'
        source.get = Mock(side_effect=[Mock(text=company), Mock(text=listing)])
        return source, company, listing

    def test_expanded_baseline_is_silent_and_skips_fifty_detail_requests(self):
        source, _, _ = self.expanded_source()
        items = source.fetch_with_state(None)
        self.assertEqual(50, len(items))
        self.assertEqual(50, len({item.key for item in items}))
        self.assertEqual(2, source.get.call_count)
        state, alerts, baseline = evaluate(source.config, items, None, max_seen=100)
        self.assertTrue(baseline)
        self.assertEqual([], alerts)
        source, _, _ = self.expanded_source()
        second = source.fetch_with_state(state)
        self.assertEqual([i.content_hash() for i in items], [i.content_hash() for i in second])
        self.assertEqual(2, source.get.call_count)
        self.assertEqual([], source.coverage_warnings)

    def test_expanded_never_silently_falls_back_to_short_or_foreign_list(self):
        for mode in ('missing', 'foreign', 'stale', 'duplicate'):
            source, company, listing = self.expanded_source()
            if mode == 'missing': company = company.replace('/nb/listview/company-press-release/1', '/other')
            if mode == 'foreign': company = company.replace('/nb/listview/', 'https://other.test/nb/listview/')
            if mode == 'stale': listing = listing.replace('Notice 0', 'Old notice')
            if mode == 'duplicate': listing = listing.replace('Notice 1</a>', 'Notice 0</a>')
            source.get = Mock(side_effect=[Mock(text=company), Mock(text=listing)])
            with self.subTest(mode=mode), self.assertRaises(SourceError):
                source.fetch_with_state(None)

    def test_expanded_limit_and_missing_overlap_warning(self):
        source, _, _ = self.expanded_source(max_items=10)
        source._notification_links = lambda items: items
        self.assertEqual(10, len(source.fetch_with_state({'seen': {'gone': 'hash'}})))
        self.assertEqual(1, len(source.coverage_warnings))
        for options in ({'max_items': 0}, {'max_items': 51}, {'max_items': True}, {'include_listview': 'yes'}):
            source, _, _ = self.expanded_source(**options)
            with self.subTest(options=options), self.assertRaises(SourceError): source.fetch()
            source.get.assert_not_called()

    def test_expanded_links_validate_issuer_and_full_timestamp(self):
        source, company, listing = self.expanded_source(max_items=1)
        detail = '<div data-node-path="/products/equities/company-news/2026-08-14-notice" data-isin="NO0010000000"><h1>Notice 0</h1><a href="https://newsweb.oslobors.no/message/100">Read</a></div>'
        source.get = Mock(side_effect=[Mock(text=company), Mock(text=listing), Mock(text=detail)])
        items = source.fetch_with_state({'seen': {}})
        self.assertEqual('https://newsweb.oslobors.no/message/100', items[0].url)
        self.assertIn('/listview/', items[0].identity_url)

    def source(self) -> EuronextSource:
        return EuronextSource(SourceConfig(
            id="euronext",
            kind="euronext",
            urls=(COMPANY_URL,),
        ))

    def test_company_page_news_is_normalized_with_issuer_context(self):
        soup = BeautifulSoup(COMPANY_HTML, "html.parser")

        items = _company_page_items("euronext", soup, COMPANY_URL)

        self.assertEqual(2, len(items))
        self.assertEqual("14/08/2026", items[0].published)
        self.assertEqual(
            "Second-quarter results show continued growth",
            items[0].title,
        )
        self.assertEqual(
            "https://example.test/nb/products/equities/company-news/2026-08-14-quarter",
            items[0].url,
        )
        self.assertIn("EXAMPLE INDUSTRIES", items[0].searchable_text())

    def test_fetch_does_not_open_stale_listview_when_company_page_has_news(self):
        source = self.source()
        source.get = Mock(return_value=Mock(text=COMPANY_HTML))

        items = source.fetch()

        self.assertEqual(2, len(items))
        self.assertEqual(1, source.get.call_count)
        source.get.assert_called_once_with(COMPANY_URL)

    def test_empty_issuer_page_retries_then_returns_news(self):
        source = self.source()
        source.retry_attempts = 2
        source.sleep = Mock()
        source.get = Mock(side_effect=[Mock(text="<html></html>"), Mock(text=COMPANY_HTML)])

        items = source.fetch()

        self.assertEqual(2, len(items))
        self.assertEqual(2, source.get.call_count)
        source.sleep.assert_called_once_with(1.0)

    def test_http_source_error_is_not_retried_as_empty_page(self):
        source = self.source()
        source.retry_attempts = 3
        source.sleep = Mock()
        source.get = Mock(side_effect=SourceError("network failure"))

        with self.assertRaisesRegex(SourceError, "network failure"):
            source.fetch()

        source.get.assert_called_once_with(COMPANY_URL)
        source.sleep.assert_not_called()

    def test_three_empty_issuer_pages_raise_source_error(self):
        source = self.source()
        source.retry_attempts = 3
        source.sleep = Mock()
        source.get = Mock(return_value=Mock(text="<html></html>"))

        with self.assertRaisesRegex(SourceError, "no company news"):
            source.fetch()

        self.assertEqual(3, source.get.call_count)
        self.assertEqual(2, source.sleep.call_count)

    def test_empty_listview_fallback_retries_then_returns_news(self):
        empty_company = """<html><body><a href="/nb/listview/company-press-release/152145">Se alle</a></body></html>"""
        empty_list = "<html><body><table></table></body></html>"
        valid_list = """<table><tr><th>Tid</th><th>Selskap</th><th>Tittel</th></tr><tr><td colspan="3">14 Aug 2026</td></tr><tr><td>07:00 CEST</td><td>EXAMPLE CORP</td><td><a href="/nb/products/equities/company-news/2026-1">Quarter report</a></td></tr></table>"""
        source = self.source()
        source.retry_attempts = 2
        source.sleep = Mock()
        source.get = Mock(side_effect=[Mock(text=empty_company), Mock(text=empty_list), Mock(text=empty_company), Mock(text=valid_list)])

        items = source.fetch()

        self.assertEqual(1, len(items))
        self.assertEqual(4, source.get.call_count)
        self.assertEqual(1, source.sleep.call_count)

    def test_later_listview_url_can_succeed_after_first_http_error(self):
        company_html = """<html><body>
          <a href="/nb/listview/company-press-release/first">First</a>
          <a href="/nb/listview/company-press-release/second">Second</a>
        </body></html>"""
        valid_list = """<table><tr><th>Tid</th><th>Selskap</th><th>Tittel</th></tr>
          <tr><td colspan="3">14 Aug 2026</td></tr>
          <tr><td>07:00 CEST</td><td>EXAMPLE CORP</td><td>
          <a href="/nb/products/equities/company-news/2026-2">Quarter report</a></td></tr>
        </table>"""
        source = self.source()
        source.retry_attempts = 1
        source.get = Mock(side_effect=[Mock(text=company_html), SourceError("first list unavailable"), Mock(text=valid_list)])

        items = source.fetch()

        self.assertEqual(1, len(items))
        self.assertEqual("Quarter report", items[0].title)
        self.assertEqual(3, source.get.call_count)

    def test_multiple_issuers_fail_if_one_issuer_remains_empty(self):
        second_url = COMPANY_URL.replace("NO0010000000", "NO0020000000")
        source = EuronextSource(SourceConfig(id="euronext", kind="euronext", urls=(COMPANY_URL, second_url)))
        source.retry_attempts = 1
        source.sleep = Mock()
        source.get = Mock(side_effect=[Mock(text=COMPANY_HTML), Mock(text="<html></html>")])

        with self.assertRaises(SourceError):
            source.fetch()

        self.assertEqual(2, source.get.call_count)

    def test_listview_remains_a_fallback_when_company_page_has_no_news(self):
        company_html = """
        <html><body>
          <a href="/nb/listview/company-press-release/152145">Se alle</a>
        </body></html>
        """
        list_html = """
        <table>
          <thead><tr>
            <th>Tid</th><th>Selskap</th><th>Tittel</th><th>Sektor</th><th>Kategori</th>
          </tr></thead>
          <tbody>
            <tr><td colspan="5">14 Aug 2026</td></tr>
            <tr>
              <td>07:00 CEST</td><td>EXAMPLE CORP</td>
              <td><a href="/nb/products/equities/company-news/2026-1">Quarter report</a></td>
              <td>Publishing</td><td>Half-year report</td>
            </tr>
          </tbody>
        </table>
        """
        source = self.source()
        source.get = Mock(side_effect=[Mock(text=company_html), Mock(text=list_html)])

        items = source.fetch()

        self.assertEqual(1, len(items))
        self.assertEqual("Quarter report", items[0].title)
        self.assertEqual(2, source.get.call_count)


if __name__ == "__main__":
    unittest.main()
