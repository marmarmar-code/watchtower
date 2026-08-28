from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.common import SourceError
from watchtower.sources.doffin import DEFAULT_URL, DoffinSource, _item, _rows


class DoffinTests(unittest.TestCase):
    def config(self, *, urls=()):
        return SourceConfig(
            id="doffin",
            kind="doffin",
            urls=urls,
            filters=FilterRule(match_all=True),
        )

    def test_custom_api_host_is_rejected_before_credentials_are_used(self):
        with self.assertRaisesRegex(ValueError, "official API URL"):
            DoffinSource(self.config(urls=("https://example.test/search",)))

    def test_explicit_official_api_url_is_accepted(self):
        source = DoffinSource(self.config(urls=(DEFAULT_URL,)))
        self.assertEqual(DEFAULT_URL, source.endpoint)

    def test_malformed_notice_row_fails_closed(self):
        with self.assertRaisesRegex(SourceError, "invalid row"):
            _rows({"hits": [{"id": "one"}, "not a notice"]})

    def test_notice_without_identity_or_title_fails_closed(self):
        with self.assertRaisesRegex(SourceError, "identity or title"):
            _item("doffin", {"id": "one"})

    @patch.dict(os.environ, {"DOFFIN_API_KEY": "test-key"})
    def test_credentialed_request_does_not_follow_redirects(self):
        response = Mock()
        response.json.return_value = {"hits": []}
        source = DoffinSource(self.config())
        source.get = Mock(return_value=response)

        self.assertEqual([], source.fetch())
        self.assertFalse(source.get.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
