from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config, load_config


SECRET_PATTERNS = [
    re.compile(r"https://hooks\.slack(?:-gov)?\.com/services/[A-Z0-9]+/[A-Z0-9]+/[A-Za-z0-9]+", re.I),
    re.compile(
        r"https://[A-Za-z0-9.-]+\.logic\.azure\.com(?::\d+)?/"
        r"[^\s\"']*(?:workflows|triggers/manual|[?&]sig=)[^\s\"']*",
        re.I,
    ),
    re.compile(
        r"https://[A-Za-z0-9.-]+\.api\.powerplatform\.com(?::\d+)?/"
        r"[^\s\"']*(?:workflows|automations|triggers/manual|[?&]sig=)[^\s\"']*",
        re.I,
    ),
    re.compile(
        r"https://(?:[A-Za-z0-9.-]+\.)?(?:webhook\.office\.com|outlook\.office\.com)/"
        r"[^\s\"']*webhook[^\s\"']*",
        re.I,
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
]


def validate_runtime(root: str | Path) -> list[str]:
    root = Path(root)
    problems: list[str] = []
    if not root.is_dir():
        return ["runtime path is missing or is not a directory"]
    if root.is_symlink():
        return ["runtime path must not be a symbolic link"]

    allowed_top = {"README.md", ".gitignore", "config", "state", ".git"}
    for child in root.iterdir():
        if child.is_symlink():
            problems.append(f"symbolic link is not allowed: {child.name}")
        if child.name not in allowed_top:
            problems.append(f"unexpected top-level path: {child.name}")

    for directory_name in ("config", "state"):
        directory = root / directory_name
        if not directory.is_dir() or directory.is_symlink():
            problems.append(f"missing or invalid {directory_name} directory")

    parsed: Config | None = None
    config = root / "config" / "watchtower.toml"
    if not config.is_file() or config.is_symlink():
        problems.append("missing config/watchtower.toml")
    else:
        try:
            parsed = load_config(config)
        except Exception as exc:
            message = " ".join(str(exc).split())[:160]
            problems.append(
                "invalid watchtower.toml"
                if not message
                else f"invalid watchtower.toml: {message}"
            )

    if parsed is not None:
        from .engine import build_source

        for source in parsed.sources:
            if not source.enabled:
                continue
            try:
                build_source(source)
            except Exception as exc:
                problems.append(
                    f"invalid source configuration: {source.id} ({type(exc).__name__})"
                )

    for path in root.rglob("*"):
        if ".git" in path.parts:
            continue
        rel = path.relative_to(root)
        if path.is_symlink():
            problems.append(f"symbolic link is not allowed: {rel}")
            continue
        if not path.is_file():
            continue
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
