from __future__ import annotations

import sys
from pathlib import Path
import tomllib


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_private_leaks.py <watchtower.toml> <public-root>")
    config_path = Path(sys.argv[1])
    public_root = Path(sys.argv[2])
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    protected = data.get("privacy", {}).get("protected_values", [])
    if not isinstance(protected, list):
        raise SystemExit("privacy.protected_values must be an array")
    public_texts: list[str] = []
    for path in public_root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            public_texts.append(path.read_text(encoding="utf-8").casefold())
        except UnicodeDecodeError:
            pass
    haystack = "\n".join(public_texts)
    leaked = [str(v) for v in protected if len(str(v).strip()) >= 4 and str(v).casefold() in haystack]
    if leaked:
        print("Private protected values found in public source", file=sys.stderr)
        return 1
    print("PRIVATE/PUBLIC LEAK CHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
