from __future__ import annotations

import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup

from watchtower.config import SourceConfig
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
