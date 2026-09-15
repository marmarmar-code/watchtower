#!/usr/bin/env python3
"""Create and exercise the isolated public catalog test runtime."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchtower.config import load_config
from watchtower.engine import build_source, run
from watchtower.recipes import load_recipes, selected_sources, source_toml
from watchtower.rss_profiles import load_profiles
from watchtower.state import StateStore


TEST_REPOSITORY = "example-owner/watchtower-test-runtime"
TEST_REF = "main"
MARKER = "WATCHTOWER CATALOG TEST RUNTIME"


def expected_ids() -> set[str]:
    return {row["id"] for row in load_profiles()} | {row["id"] for row in load_recipes()}


def config_text() -> str:
    blocks = [
        "# Generated isolated catalog test configuration. Do not use as production runtime.",
        "[general]\nconfig_version = 1\nmax_seen_per_source = 3000\n",
        '[notifications]\nprovider = "slack"\n',
    ]
    for profile in load_profiles():
        source = {
            "id": profile["id"], "kind": "rss", "label": profile["name"],
            "enabled": True, "interval_minutes": 60, "alert_on_update": False,
            "profiles": [profile["id"]], "allow_empty": profile["id"] == "met_farevarsler",
            "filter": {"match_all": True},
        }
        blocks.append(source_toml(source))
    for recipe in load_recipes():
        blocks.append(source_toml(selected_sources(recipe=recipe["id"], match_all=True)[0]))
    return "\n".join(blocks)


def guard(root: Path, runtime_ref: str, runtime_repository: str) -> tuple[Path, Path]:
    if runtime_repository != TEST_REPOSITORY:
        raise ValueError("catalog runtime operations require the fixed test repository")
    if runtime_ref != TEST_REF:
        raise ValueError("catalog runtime operations require the test repository main ref")
    root = root.expanduser().absolute()
    marker = root / "README.md"
    if not marker.is_file() or MARKER not in marker.read_text(encoding="utf-8"):
        raise ValueError("runtime is missing the catalog-test marker")
    config_path, state_path = root / "config" / "watchtower.toml", root / "state"
    config = load_config(config_path)
    actual = {source.id for source in config.sources if source.enabled}
    expected = expected_ids()
    if actual != expected or len(config.sources) != len(expected):
        raise ValueError(
            f"catalog-test config does not contain exactly the {len(expected)} catalog setup ids"
        )
    if not state_path.is_dir() or state_path.is_symlink():
        raise ValueError("catalog-test state directory is missing or unsafe")
    return config_path, state_path


def generate(root: Path, runtime_ref: str, runtime_repository: str) -> None:
    if runtime_repository != TEST_REPOSITORY:
        raise ValueError("repository must be the dedicated Watchtower test runtime")
    if runtime_ref != TEST_REF:
        raise ValueError("ref must be main")
    root = root.expanduser().absolute()
    if root.is_symlink() or (root.exists() and any(root.iterdir())):
        raise ValueError("generate requires a new empty non-symlink directory")
    (root / "config").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "README.md").write_text(
        f"# {MARKER}\n\nDedicated state for `{TEST_REPOSITORY}`. Never copy production runtime data here.\n",
        encoding="utf-8",
    )
    (root / "state" / ".gitkeep").touch()
    target = root / "config" / "watchtower.toml"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(config_text())
    temporary.replace(target)
    guard(root, runtime_ref, runtime_repository)
    print(f"CATALOG TEST RUNTIME READY; setups={len(expected_ids())}")


def preview(root: Path, runtime_ref: str, runtime_repository: str) -> int:
    config_path, _ = guard(root, runtime_ref, runtime_repository)
    config = load_config(config_path)
    failures = 0
    for row in config.sources:
        try:
            count = len(build_source(row).fetch())
            print(f"OK\t{row.id}\titems={count}")
        except Exception as exc:
            failures += 1
            print(f"FAIL\t{row.id}\t{type(exc).__name__}")
    print(f"CATALOG PREVIEW; setups={len(config.sources)} failed={failures}")
    return 1 if failures else 0


def baseline(root: Path, runtime_ref: str, runtime_repository: str) -> int:
    config_path, state_path = guard(root, runtime_ref, runtime_repository)
    existing = [path for path in state_path.iterdir() if path.name != ".gitkeep"]
    if existing:
        raise ValueError("baseline requires empty catalog-test state")
    config = load_config(config_path)
    result = run(config, StateStore(state_path), None)
    if result.alerts:
        raise RuntimeError("fresh catalog baseline unexpectedly produced alerts")
    print(
        f"CATALOG BASELINE; checked={result.checked_sources} "
        f"baselines={result.baselined_sources} errors={len(result.errors)}"
    )
    return 2 if result.errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("generate", "guard", "preview", "baseline"))
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--runtime-ref", required=True)
    parser.add_argument("--runtime-repository", required=True)
    args = parser.parse_args()
    try:
        if args.operation == "generate":
            generate(args.runtime, args.runtime_ref, args.runtime_repository)
            return 0
        guard(args.runtime, args.runtime_ref, args.runtime_repository)
        if args.operation == "preview":
            return preview(args.runtime, args.runtime_ref, args.runtime_repository)
        if args.operation == "baseline":
            return baseline(args.runtime, args.runtime_ref, args.runtime_repository)
        print(f"CATALOG TEST RUNTIME OK; setups={len(expected_ids())}")
        return 0
    except Exception as exc:
        print(f"CATALOG TEST RUNTIME FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
