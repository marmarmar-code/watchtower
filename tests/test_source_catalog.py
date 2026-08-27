import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from watchtower.cli import main
from watchtower.engine import SOURCE_TYPES
from watchtower.source_catalog import load_catalog


class SourceCatalogTests(unittest.TestCase):
    def test_catalog_covers_all_builtin_sources(self):
        rows = load_catalog()
        self.assertEqual({row["id"] for row in rows}, set(SOURCE_TYPES))
        self.assertEqual(len(rows), len(SOURCE_TYPES))
        by_id = {row["id"]: row for row in rows}
        self.assertTrue(by_id["doffin"]["credential_required"])
        self.assertEqual("fork-owner", by_id["rss"]["maintenance_owner"])
        self.assertEqual("beta", by_id["ssb"]["status"])

    def test_duplicate_ids_are_rejected(self):
        text = """[[source]]\nid='regjeringen'\nname='x'\nstatus='stable'\ncredential_required=false\ninterval_class='hourly'\nmaintenance_owner='fork-owner'\ncoverage='x'\n[[source]]\nid='regjeringen'\nname='x'\nstatus='stable'\ncredential_required=false\ninterval_class='hourly'\nmaintenance_owner='fork-owner'\ncoverage='x'\n"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.toml"
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_catalog(path)

    def test_engine_mismatch_is_rejected(self):
        path = Path(__file__).parents[1] / "watchtower" / "source_catalog.toml"
        text = path.read_text(encoding="utf-8").replace('id = "brreg"', 'id = "unknown"')
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "catalog.toml"
            altered.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differ"):
                load_catalog(altered)

    def test_cli_lists_sources_without_private_configuration(self):
        output = StringIO()
        with patch("sys.argv", ["watchtower", "list-sources"]), redirect_stdout(output):
            self.assertEqual(0, main())
        rendered = output.getvalue()
        self.assertIn("rss\tprøveversjon\toffentlig\tegen fork\tRSS og Atom", rendered)
        self.assertIn("ssb\tprøveversjon\toffentlig\tegen fork", rendered)
        self.assertIn("doffin\tetablert\tkrever nøkkel", rendered)

    def test_cli_lists_ready_rss_profiles(self):
        output = StringIO()
        with patch("sys.argv", ["watchtower", "list-rss-profiles"]), redirect_stdout(output):
            self.assertEqual(0, main())
        rendered = output.getvalue()
        self.assertIn(
            "politiloggen\tklar\t2026-08-27\tPolitiet\tPolitiloggen",
            rendered,
        )
        self.assertIn(
            "norges_bank_pressemeldinger\tklar\t2026-08-27\tNorges Bank",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
