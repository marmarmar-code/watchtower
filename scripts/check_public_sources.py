#!/usr/bin/env python3
"""Read-only live check of the public RSS profiles and source recipes."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchtower.config import FilterRule, SourceConfig, load_config
from watchtower.engine import build_source
from watchtower.recipes import load_recipes, selected_sources, source_toml
from watchtower.rss_profiles import load_profiles


def checks(section: str):
    today = date.today()
    rows = []
    if section in {"all", "rss"}:
        for profile in load_profiles():
            age = (today - date.fromisoformat(profile["verified_on"])).days
            if age < 0:
                raise ValueError(f"{profile['id']}: verification metadata is future dated")
            if profile.get("feed_urls"):
                config = SourceConfig(
                    id=f"check_{profile['id']}", kind="rss",
                    urls=tuple(profile["feed_urls"]), filters=FilterRule(match_all=True),
                    options={"allow_empty": profile["id"] == "met_farevarsler"},
                )
                rows.append(("rss", profile["id"], build_source(config)))
    if section in {"all", "recipes"}:
        for recipe in load_recipes():
            age = (today - date.fromisoformat(recipe["verified_on"])).days
            if age < 0:
                raise ValueError(f"{recipe['id']}: verification metadata is future dated")
            source = selected_sources(recipe=recipe["id"], match_all=True)[0]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.toml"
                path.write_text(source_toml(source), encoding="utf-8")
                config = load_config(path).sources[0]
            rows.append(("recipe", recipe["id"], build_source(config)))
    return rows


def run(section: str, workers: int, timeout: float) -> int:
    rows = checks(section)
    checked_on = date.today().isoformat()

    def fetch(row):
        kind, source_id, source = row
        source.timeout = timeout
        source.retry_attempts = 1
        items = source.fetch()
        return kind, source_id, len(items), tuple(source.coverage_warnings)

    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, row): row[:2] for row in rows}
        for future in as_completed(futures):
            kind, source_id = futures[future]
            try:
                _, _, count, warnings = future.result()
                warning = f" warnings={','.join(warnings)}" if warnings else ""
                print(f"OK\t{kind}\t{source_id}\titems={count}{warning}")
            except Exception as exc:
                failed += 1
                print(f"FAIL\t{kind}\t{source_id}\t{type(exc).__name__}: {exc}")
    print(f"PUBLIC SOURCE CHECK; checked_on={checked_on} checked={len(rows)} failed={failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=("all", "rss", "recipes"), default="all")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or args.timeout <= 0:
        parser.error("workers must be 1-8 and timeout must be positive")
    try:
        return run(args.section, args.workers, args.timeout)
    except (OSError, ValueError) as exc:
        print(f"PUBLIC SOURCE CHECK FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
