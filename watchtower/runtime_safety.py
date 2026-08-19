from __future__ import annotations

import json
import re
from pathlib import Path
import tomllib

SECRET_PATTERNS = [
    re.compile(r"https://hooks\.slack\.com/services/[A-Z0-9]+/[A-Z0-9]+/[A-Za-z0-9]+", re.I),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
]


def validate_runtime(root: str | Path) -> list[str]:
    root = Path(root)
    problems: list[str] = []
    allowed_top = {"README.md", ".gitignore", "config", "state", ".git"}
    for child in root.iterdir():
        if child.name not in allowed_top:
            problems.append(f"unexpected top-level path: {child.name}")
    config = root / "config" / "watchtower.toml"
    if not config.exists():
        problems.append("missing config/watchtower.toml")
    else:
        try:
            data = tomllib.loads(config.read_text(encoding="utf-8"))
            if not isinstance(data.get("source", []), list):
                problems.append("invalid watchtower.toml")
        except Exception:
            problems.append("invalid watchtower.toml")
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(root)
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"} or path.name.startswith(".env"):
            problems.append(f"secret file type: {rel}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"secret-like content: {rel}")
        if rel.parts and rel.parts[0] == "state" and path.suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError:
                problems.append(f"invalid state JSON: {rel}")
    return sorted(set(problems))
