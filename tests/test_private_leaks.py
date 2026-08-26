from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_private_leaks import collect_protected_values, find_leaks


class PrivateLeakTests(unittest.TestCase):
    def config(self):
        return {
            "privacy": {"protected_values": ["manual-private-term"]},
            "source": [
                {
                    "search_queries": ["query-private-term"],
                    "companies": ["999999999"],
                    "filter": {
                        "include_any": ["include-private-term", "XZ"],
                        "include_all": ["required-private-term"],
                        "exclude_any": ["exclude-private-term"],
                    },
                }
            ],
        }

    def test_collects_manual_filters_queries_and_companies(self):
        values = set(collect_protected_values(self.config()))
        self.assertEqual(
            {
                "manual-private-term",
                "query-private-term",
                "include-private-term",
                "required-private-term",
                "exclude-private-term",
                "999999999",
                "XZ",
            },
            values,
        )

    def test_setup_placeholders_are_not_treated_as_private_values(self):
        config = {
            "privacy": {"protected_values": ["REPLACE_ME_PRIVATE_VALUE"]},
            "source": [
                {
                    "search_queries": ["REPLACE_ME_QUERY_1"],
                    "companies": ["REPLACE_ME_ORGNR_1"],
                    "filter": {
                        "include_any": ["REPLACE_ME_TOPIC_1"],
                        "include_all": [],
                        "exclude_any": [],
                    },
                }
            ],
        }
        self.assertEqual((), collect_protected_values(config))

    def test_clean_public_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "safe.py").write_text("generic public implementation\n", encoding="utf-8")
            self.assertEqual([], find_leaks(self.config(), root))

    def test_private_term_in_public_tree_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "leak.py").write_text("contains include-private-term here\n", encoding="utf-8")
            self.assertEqual([Path("leak.py")], find_leaks(self.config(), root))

    def test_company_identifier_in_public_tree_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "leak.py").write_text("configured company 999999999\n", encoding="utf-8")
            self.assertEqual([Path("leak.py")], find_leaks(self.config(), root))

    def test_short_term_uses_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "short.py"
            path.write_text("prefixXZsuffix\n", encoding="utf-8")
            self.assertEqual([], find_leaks(self.config(), root))
            path.write_text("standalone XZ value\n", encoding="utf-8")
            self.assertEqual([Path("short.py")], find_leaks(self.config(), root))


if __name__ == "__main__":
    unittest.main()
