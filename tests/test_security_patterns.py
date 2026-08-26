from __future__ import annotations

import unittest

from scripts.check_public_safety import SECRET_PATTERNS as PUBLIC_SECRET_PATTERNS
from watchtower.runtime_safety import SECRET_PATTERNS as RUNTIME_SECRET_PATTERNS


class SecurityPatternTests(unittest.TestCase):
    def test_root_outlook_connector_webhook_is_detected(self):
        value = (
            "https://"
            + "outlook.office.com/"
            + "webhook/example/IncomingWebhook/secret"
        )
        self.assertTrue(any(pattern.search(value) for pattern in PUBLIC_SECRET_PATTERNS))
        self.assertTrue(any(pattern.search(value) for pattern in RUNTIME_SECRET_PATTERNS))


if __name__ == "__main__":
    unittest.main()
