from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATH_PARTS = {"runtime", "private"}
SECRET_PATTERNS = [
    re.compile(r"https://hooks\.slack\.com/services/[A-Z0-9]+/[A-Z0-9]+/[A-Za-z0-9]+", re.I),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
]
ALLOWED_DOTENV = {".gitignore"}


def main() -> int:
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PATH_PARTS for part in rel.parts):
            problems.append(f"forbidden path: {rel}")
        if path.name.startswith(".env") and path.name not in ALLOWED_DOTENV:
            problems.append(f"dotenv file: {rel}")
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
            problems.append(f"key material path: {rel}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                problems.append(f"secret-like content: {rel}")
    if problems:
        print("PUBLIC SAFETY CHECK FAILED")
        for problem in sorted(set(problems)):
            print(f"- {problem}")
        return 1
    print("PUBLIC SAFETY CHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
