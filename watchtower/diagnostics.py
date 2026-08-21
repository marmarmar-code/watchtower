from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any


_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_.-]+")
_HTTP_STATUS = re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE)


def _safe_token(value: Any, fallback: str) -> str:
    cleaned = _SAFE_TOKEN.sub("-", str(value)).strip("-.")[:60]
    return cleaned or fallback


def failure_detail(status: dict[str, Any]) -> str:
    errors = status.get("errors")
    if not isinstance(errors, dict):
        return ""

    parts: list[str] = []
    for source_id, raw_error in sorted(errors.items(), key=lambda pair: str(pair[0])):
        source = _safe_token(source_id, "ukjent-kilde")
        message = " ".join(str(raw_error).split())
        http = _HTTP_STATUS.search(message)
        if http:
            label = f"HTTP {http.group(1)}"
        else:
            label = _safe_token(message.split(":", 1)[0], "kildefeil")
        parts.append(f"{source} ({label})")
    return ", ".join(parts)[:300]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m watchtower.diagnostics <status.json>")
    path = Path(sys.argv[1])
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if isinstance(status, dict):
        print(failure_detail(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
