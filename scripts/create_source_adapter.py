#!/usr/bin/env python3
"""Create a safe, unregistered Watchtower source-adapter skeleton."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def class_name(source_id: str) -> str:
    parts = source_id.split("_")
    name = "".join(part.capitalize() for part in parts)
    return name if parts[-1] == "source" else name + "Source"


def render_module(source_id: str, name: str) -> str:
    return f'''from __future__ import annotations

from ..models import Item
from .common import Source


class {name}(Source):
    """Starting point for the {source_id} public-source adapter."""

    def fetch(self) -> list[Item]:
        # Keep network access, parsing, and validation explicit and bounded.
        # Do not add secrets or private data to this adapter.
        raise NotImplementedError("Implement and contract-test {source_id} before registration")
'''


def render_test(source_id: str, name: str) -> str:
    return f'''from __future__ import annotations

import unittest

from watchtower.config import FilterRule, SourceConfig
from watchtower.sources.{source_id} import {name}


class {name}ContractTests(unittest.TestCase):
    def test_replace_placeholder_with_deterministic_contract(self):
        config = SourceConfig(
            id="{source_id}",
            kind="{source_id}",
            label="Synthetic contract fixture",
            filters=FilterRule(include_any=("synthetic",)),
        )
        source = {name}(config)
        self.assertEqual(config.id, "{source_id}")
        self.assertTrue(callable(source.fetch))
        self.fail("Replace this placeholder with a no-network parsing contract")


if __name__ == "__main__":
    unittest.main()
'''


def render_doc(source_id: str, name: str) -> str:
    return f'''# {source_id} source adapter

Generated starting point: `{name}`.

Implement parsing and a deterministic contract test before enabling this source. This
generator leaves the adapter unregistered and does not edit `engine.py`; a fork
owner/developer must register it deliberately, add it to the public source catalog
and assign maintenance responsibility.
'''


def _write_new_file(path: Path, content: str) -> None:
    handle = None
    try:
        handle = path.open("x", encoding="utf-8")
        with handle:
            handle.write(content)
    except OSError:
        if handle is not None:
            path.unlink(missing_ok=True)
        raise


def create(source_id: str, root: Path) -> list[Path]:
    if not 2 <= len(source_id) <= 63 or not SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError(
            "source-id must be 2-63 characters: lowercase letter, digits, underscore"
        )
    if not (root / "watchtower" / "engine.py").is_file():
        raise ValueError("root must be a Watchtower project checkout")
    name = class_name(source_id)
    paths = [
        root / "watchtower" / "sources" / f"{source_id}.py",
        root / "tests" / f"test_source_{source_id}.py",
        root / "docs" / "sources" / f"{source_id}.md",
    ]
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError("refusing to overwrite: " + ", ".join(str(p) for p in existing))
    contents = [
        render_module(source_id, name),
        render_test(source_id, name),
        render_doc(source_id, name),
    ]
    created: list[Path] = []
    try:
        for path, content in zip(paths, contents):
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_new_file(path, content)
            created.append(path)
    except OSError:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_id", help="new source ID (lowercase snake_case)")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Watchtower project root",
    )
    args = parser.parse_args(argv)
    try:
        paths = create(args.source_id, args.root.resolve())
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in paths:
        print(path)
    print(
        "Adapter is not registered: register it deliberately, add it to the source "
        "catalog and assign maintenance responsibility."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
