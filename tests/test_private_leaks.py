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
                    "recipient_orgnrs": ["123456785"],
                    "provider_orgnrs": ["974761076"],
                    "industries": ["58.130"],
                    "regions": ["03"],
                    "isins": ["NO0012345678"],
                    "issuers": ["Private Issuer ASA"],
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
                "123456785",
                "974761076",
                "58.130",
                "03",
                "NO0012345678",
                "Private Issuer ASA",
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

    def test_group_terms_are_protected_and_masked_recursively(self):
        from scripts.mask_private_config import walk

        config = self.config()
        groups = [["private-group-actor", "private-group-alias"], ["private-group-event"]]
        config["source"][0]["filter"]["include_any_groups"] = groups
        expected = {term for group in groups for term in group}
        self.assertTrue(expected.issubset(collect_protected_values(config)))
        self.assertTrue(expected.issubset(walk(config)))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "leak.py").write_text("private-group-event", encoding="utf-8")
            self.assertEqual([Path("leak.py")], find_leaks(config, root))

    def test_title_queries_are_protected_masked_and_allow_reviewed_public_topics(self):
        from scripts.mask_private_config import walk

        config = self.config()
        config["source"][0]["title_queries"] = ["private-title-query", "Public Subject"]
        config["privacy"]["public_topic_terms"] = ["PUBLIC SUBJECT"]
        self.assertIn("private-title-query", collect_protected_values(config))
        self.assertNotIn("Public Subject", collect_protected_values(config))
        self.assertTrue({"private-title-query", "Public Subject"}.issubset(walk(config)))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "leak.py").write_text("private-title-query", encoding="utf-8")
            self.assertEqual([Path("leak.py")], find_leaks(config, root))
        config["entity"] = [{"name": "Private Entity", "aliases": ["Public Subject"]}]
        with self.assertRaises(ValueError):
            collect_protected_values(config)

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

    def test_reviewed_public_topic_exempts_only_that_topic(self):
        config = self.config()
        config["privacy"]["public_topic_terms"] = ["PUBLIC SUBJECT"]
        config["source"][0]["filter"]["include_any"].append("Public Subject")
        config["source"][0]["search_queries"].append("public subject")
        values = collect_protected_values(config)
        self.assertNotIn("Public Subject", values)
        self.assertNotIn("public subject", values)
        self.assertIn("include-private-term", values)
        self.assertIn("query-private-term", values)

    def test_public_topic_cannot_exempt_explicit_or_identifier_values(self):
        for term in ("MANUAL-PRIVATE-TERM", "999999999", "Private Issuer ASA"):
            config = self.config()
            config["privacy"]["public_topic_terms"] = [term]
            with self.subTest(term=term), self.assertRaises(ValueError):
                collect_protected_values(config)

    def test_public_topic_cannot_exempt_entity_alias(self):
        config = self.config()
        config["entity"] = [{"name": "Private Entity", "aliases": ["private alias"]}]
        config["privacy"]["public_topic_terms"] = ["PRIVATE ALIAS"]
        with self.assertRaises(ValueError):
            collect_protected_values(config)

    def test_invalid_public_topic_list_fails_closed(self):
        for value in ("topic", [None], [""], ["  "], [7]):
            config = self.config()
            config["privacy"]["public_topic_terms"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                collect_protected_values(config)


if __name__ == "__main__":
    unittest.main()
