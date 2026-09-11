"""Reviewed source recipes and local, atomic additions to private configuration."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

from .config import load_config
from .rss_profiles import load_profiles
from .runtime_safety import SECRET_PATTERNS
from .sources.changes import public_url

PATH = Path(__file__).with_name("source_recipes.toml")


def load_recipes(path=PATH):
    rows = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("recipe", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("source recipes must be a non-empty list")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "name", "sector", "description", "official_url", "verified_on", "source"}:
            raise ValueError("invalid source recipe metadata")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", row["id"]) or row["id"] in ids:
            raise ValueError("invalid or duplicate recipe id")
        ids.add(row["id"])
        for field in ("name", "sector", "description"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError("recipe metadata must be non-empty text")
        public_url(row["official_url"])
        date.fromisoformat(row["verified_on"])
        if not isinstance(row["source"], dict) or not row["source"].get("kind"):
            raise ValueError("recipe requires source configuration")
    return rows


def _value(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def source_toml(source):
    lines = ["[[source]]"]
    def table(data, prefix):
        for key, value in data.items():
            if not isinstance(value, dict):
                lines.append(f"{_value(key)} = {_value(value)}")
        for key, value in data.items():
            if isinstance(value, dict):
                parts = [*prefix, key]
                lines.append("[" + ".".join(_value(part) for part in parts) + "]")
                table(value, parts)
    table(source, ["source"])
    return "\n".join(lines) + "\n"


def selected_sources(*, recipe=None, rss_profile=None, source_id=None, topics=(), match_all=False):
    if bool(recipe) == bool(rss_profile):
        raise ValueError("select exactly one recipe or RSS profile")
    if recipe:
        selected = next((row for row in load_recipes() if row["id"] == recipe), None)
        if not selected:
            raise ValueError("unknown source recipe")
        sources = [{"id": source_id or recipe, "label": selected["name"], "enabled": True,
                    "interval_minutes": 1440, "alert_on_update": True, **deepcopy(selected["source"])}]
    else:
        profile = next((row for row in load_profiles() if row["id"] == rss_profile), None)
        if not profile or not profile.get("feed_urls"):
            raise ValueError("unknown or unavailable RSS profile")
        sources = [{"id": f"{source_id or rss_profile}_{i}" if len(profile["feed_urls"]) > 1 else source_id or rss_profile,
                    "kind": "rss", "label": profile["name"], "enabled": True,
                    "interval_minutes": 60, "alert_on_update": False, "urls": [url],
                    "allow_empty": rss_profile == "met_farevarsler"}
                   for i, url in enumerate(profile["feed_urls"], 1)]
    for source in sources:
        exclusions = source.get("filter", {}).get("exclude_any")
        if topics:
            source["filter"] = {"include_any": list(topics), "match_mode": "smart"}
        elif match_all:
            source["filter"] = {"match_all": True}
        elif not any(source.get("filter", {}).get(key) for key in ("include_any", "include_all", "match_all")):
            raise ValueError("add --topic or explicitly select --all for this news/list source")
        if exclusions:
            source["filter"]["exclude_any"] = deepcopy(exclusions)
    return sources


def add_sources(root, sources, *, apply=False):
    from .engine import build_source
    root = Path(root).expanduser().absolute()
    target = root / "config" / "watchtower.toml"
    public_root = Path(__file__).resolve().parents[1]
    if root.resolve().is_relative_to(public_root):
        raise ValueError("runtime must be outside the public code directory")
    if any(path.is_symlink() for path in (root, *root.parents, target.parent, target)):
        raise ValueError("runtime paths must not use symbolic links")
    original = target.read_text(encoding="utf-8")
    block = "\n" + "\n".join(source_toml(source) for source in sources)
    candidate = original.rstrip() + "\n" + block
    if any(pattern.search(candidate) for pattern in SECRET_PATTERNS):
        raise ValueError("configuration contains a credential; use Actions Secrets")
    with tempfile.TemporaryDirectory() as directory:
        trial = Path(directory) / "config.toml"
        trial.write_text(candidate, encoding="utf-8")
        config = load_config(trial)
        for source in config.sources:
            if source.enabled:
                build_source(source)
    if not apply:
        return block
    # Exclusive lock prevents two simultaneous add-source commands losing edits.
    lock = target.parent / ".watchtower-config.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    temporary = None
    try:
        os.close(descriptor)
        if target.read_text(encoding="utf-8") != original:
            raise ValueError("configuration changed during validation; try again")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    return block


def add_source(args):
    try:
        sources = selected_sources(recipe=args.recipe, rss_profile=args.rss_profile,
                                   source_id=args.source_id, topics=args.topic, match_all=args.all)
        block = add_sources(args.runtime, sources, apply=args.apply)
        print("Kilder lagt til i privat konfigurasjon." if args.apply else "Forslag – bruk --apply for å lagre:")
        print(block)
        print("Bruk preview for å se innholdet. Første ordinære kjøring etablerer stille baseline.")
        return 0
    except (ValueError, OSError) as exc:
        print(f"Kildeoppsett kunne ikke fullføres: {exc}")
        return 1
