from __future__ import annotations

import unittest

from watchtower.config import SourceConfig
from watchtower.sources.euronext import EuronextSource


class LiveEuronextVerification(unittest.TestCase):
    def test_current_issuer_page_returns_dated_company_news(self):
        source = EuronextSource(SourceConfig(
            id="euronext-live-verification",
            kind="euronext",
            urls=(
                "https://live.euronext.com/en/product/equities/"
                "NO0013144014-MERK/company-information",
            ),
        ))

        items = source.fetch()

        self.assertGreater(len(items), 0)
        self.assertTrue(any(item.published and "2026" in item.published for item in items))
        self.assertTrue(all(item.title.strip() for item in items))


if __name__ == "__main__":
    unittest.main()
