from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from watchtower.cli import main


class CliDistributionTests(unittest.TestCase):
    def test_dry_run_rejects_zero_enabled_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "watchtower.toml"
            state = root / "state"
            state.mkdir()
            config.write_text(
                '[[source]]\n'
                'id="example"\n'
                'kind="regjeringen"\n'
                'enabled=false\n'
                '[source.filter]\n'
                'include_any=["REPLACE_ME_TOPIC"]\n',
                encoding="utf-8",
            )

            argv = [
                "watchtower",
                "dry-run",
                "--config",
                str(config),
                "--state-dir",
                str(state),
            ]
            with patch("sys.argv", argv), patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(1, main())

            self.assertIn("at least one enabled source", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
