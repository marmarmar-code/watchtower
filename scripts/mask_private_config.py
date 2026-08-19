from __future__ import annotations

import sys
from pathlib import Path
import tomllib


def escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def walk(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    elif isinstance(value, str):
        value = value.strip()
        if len(value) >= 3:
            yield value


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mask_private_config.py <watchtower.toml>")
    data = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for value in sorted(set(walk(data)), key=len, reverse=True):
        print(f"::add-mask::{escape(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
