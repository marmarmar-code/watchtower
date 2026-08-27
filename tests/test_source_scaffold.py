from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.create_source_adapter import _write_new_file, create, main


class SourceScaffoldTests(unittest.TestCase):
    def test_normal_creation_is_deterministic_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "watchtower").mkdir()
            (root / "watchtower" / "engine.py").touch()
            paths = create("example_source", root)
            self.assertEqual(
                [p.relative_to(root).as_posix() for p in paths],
                [
                    "watchtower/sources/example_source.py",
                    "tests/test_source_example_source.py",
                    "docs/sources/example_source.md",
                ],
            )
            snapshots = [p.read_text(encoding="utf-8") for p in paths]
            self.assertIn("class ExampleSource", snapshots[0])
            self.assertIn("self.fail", snapshots[1])
            self.assertIn("unregistered", snapshots[2].lower())
            compile(snapshots[0], paths[0].name, "exec")
            compile(snapshots[1], paths[1].name, "exec")
            self.assertEqual(snapshots, [p.read_text(encoding="utf-8") for p in paths])

    def test_invalid_id_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "watchtower").mkdir()
            (root / "watchtower" / "engine.py").touch()
            with redirect_stderr(StringIO()):
                self.assertEqual(main(["../escape", "--root", directory]), 2)

    def test_wrong_project_root_fails_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "Watchtower project"):
                create("example", root)
            self.assertEqual([], list(root.iterdir()))

    def test_existing_target_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "watchtower").mkdir()
            (root / "watchtower" / "engine.py").touch()
            target = root / "watchtower" / "sources" / "example.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                create("example", root)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_partial_creation_is_cleaned_up_after_io_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "watchtower").mkdir()
            (root / "watchtower" / "engine.py").touch()

            def fail_on_test(path: Path, content: str) -> None:
                if path.name.startswith("test_source_"):
                    raise OSError("synthetic write failure")
                _write_new_file(path, content)

            with patch(
                "scripts.create_source_adapter._write_new_file",
                side_effect=fail_on_test,
            ):
                with self.assertRaisesRegex(OSError, "synthetic write failure"):
                    create("example", root)

            self.assertFalse((root / "watchtower" / "sources" / "example.py").exists())
            self.assertFalse((root / "tests" / "test_source_example.py").exists())
            self.assertFalse((root / "docs" / "sources" / "example.md").exists())


if __name__ == "__main__":
    unittest.main()
