from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any
import tomllib


FILTER_KEYS = ("include_any", "include_all", "exclude_any")
SOURCE_KEYS = ("search_queries", "companies")


def _strings(value: Any):
    if isinstance(value, str):
        value = value.strip()
        if value:
            yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def collect_protected_values(data: dict[str, Any]) -> tuple[str, ...]:
    values: set[str] = set()

    privacy = data.get("privacy", {})
    if not isinstance(privacy, dict):
        raise ValueError("privacy must be a table")
    protected = privacy.get("protected_values", [])
    if not isinstance(protected, list):
        raise ValueError("privacy.protected_values must be an array")
    values.update(_strings(protected))

    sources = data.get("source", [])
    if not isinstance(sources, list):
        raise ValueError("source must be an array")
    for source in sources:
        if not isinstance(source, dict):
            continue
        rules = source.get("filter", {})
        if rules is not None and not isinstance(rules, dict):
            raise ValueError("source.filter must be a table")
        if isinstance(rules, dict):
            for key in FILTER_KEYS:
                values.update(_strings(rules.get(key, [])))
        for key in SOURCE_KEYS:
            values.update(_strings(source.get(key, [])))

    return tuple(sorted(values, key=lambda value: (value.casefold(), value)))


def _contains_protected(text: str, value: str) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)", re.IGNORECASE)
    return bool(pattern.search(text))


def find_leaks(data: dict[str, Any], public_root: Path) -> list[Path]:
    protected = collect_protected_values(data)
    if not protected:
        return []

    leaked_files: list[Path] = []
    for path in public_root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(_contains_protected(text, value) for value in protected):
            leaked_files.append(path.relative_to(public_root))
    return leaked_files


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_private_leaks.py <watchtower.toml> <public-root>")
    config_path = Path(sys.argv[1])
    public_root = Path(sys.argv[2])
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    try:
        leaked_files = find_leaks(data, public_root)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if leaked_files:
        print("Private runtime terms found in public source", file=sys.stderr)
        for path in leaked_files:
            print(f"- {path}", file=sys.stderr)
        return 1
    print("PRIVATE/PUBLIC LEAK CHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
