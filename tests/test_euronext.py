from __future__ import annotations

import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup

from watchtower.config import SourceConfig
from watchtower.sources.euronext import EuronextSource, _company_page_items


COMPANY_URL = "https://example.test/nb/product/equities/NO0010000000-XOSL/company-information"
COMPANY_HTML = """
<html>
  <head><title>NORTHSTAR GROUP | NO0010000000 | Company information</title></head>
  <body>
    <section class="issuer-news">
      <a href="/nb/listview/company-press-release/152145">See all</a>
      <div><span>14/08/2026</span><span>Second quarter 2026: Strong result growth for Northstar Group</span></div>
      <div><span>07/08/2026</span><span>Northstar Group ASA (NST): Invitation to results presentation</span></div>
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
            "Second quarter 2026: Strong result growth for Northstar Group",
            items[0].title,
        )
        self.assertEqual(
            "https://example.test/nb/products/equities/company-news/2026-08-14-quarter",
            items[0].url,
        )
        self.assertIn("NORTHSTAR GROUP", items[0].searchable_text())

    def test_fetch_does_not_open_stale_listview_when_company_page_has_news(self):
        source = self.source()
        source.get = Mock(return_value=Mock(text=COMPANY_HTML))

        items = source.fetch()

        self.assertEqual(2, len(items))
        self.assertEqual(1, source.get.call_count)
        source.get.assert_called_once_with(COMPANY_URL)

    def test_listview_remains_a_fallback_when_company_page_has_no_news(self):
        company_html = """
        <html><body>
          <a href="/nb/listview/company-press-release/152145">See all</a>
        </body></html>
        """
        list_html = """
        <table>
          <thead><tr>
            <th>Time</th><th>Company</th><th>Title</th><th>Sector</th><th>Category</th>
          </tr></thead>
          <tbody>
            <tr><td colspan="5">14 Aug 2026</td></tr>
            <tr>
              <td>07:00 CEST</td><td>EXAMPLE CORP</td>
              <td><a href="/nb/products/equities/company-news/2026-1">Quarter report</a></td>
              <td>Technology</td><td>Half-year report</td>
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
