from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re
import tomllib


MATCH_MODES = {"smart", "substring", "whole_word"}
NOTIFICATION_PROVIDERS = {"slack", "teams"}


@dataclass(frozen=True)
class FilterRule:
    include_any: tuple[str, ...] = ()
    include_all: tuple[str, ...] = ()
    exclude_any: tuple[str, ...] = ()
    match_mode: str = "smart"

    def matches_term(self, text: str, term: str) -> bool:
        needle = term.strip()
        if not needle:
            return False
        mode = self.match_mode
        if mode == "smart":
            mode = "whole_word" if len(needle) <= 3 else "substring"
        if mode == "substring":
            return needle.casefold() in text.casefold()
        if mode == "whole_word":
            pattern = rf"(?<!\w){re.escape(needle)}(?!\w)"
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        raise ValueError(f"unsupported filter match_mode: {self.match_mode}")

    def matches(self, text: str) -> bool:
        if self.exclude_any and any(self.matches_term(text, term) for term in self.exclude_any):
            return False
        if self.include_all and not all(self.matches_term(text, term) for term in self.include_all):
            return False
        if self.include_any and not any(self.matches_term(text, term) for term in self.include_any):
            return False
        return bool(self.include_any or self.include_all)


@dataclass(frozen=True)
class SourceConfig:
    id: str
    kind: str
    enabled: bool = True
    label: str = ""
    urls: tuple[str, ...] = ()
    filters: FilterRule = field(default_factory=FilterRule)
    alert_on_update: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationConfig:
    provider: str = "slack"


@dataclass(frozen=True)
class Config:
    sources: tuple[SourceConfig, ...]
    max_seen_per_source: int = 3000
    notifications: NotificationConfig = field(default_factory=NotificationConfig)


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("filter values must be string arrays")
    return tuple(v.strip() for v in value if v.strip())


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    general = raw.get("general", {})
    max_seen = int(general.get("max_seen_per_source", 3000))

    notification_row = raw.get("notifications", {}) or {}
    if not isinstance(notification_row, dict):
        raise ValueError("[notifications] must be a table")
    notification_provider = str(notification_row.get("provider", "slack")).strip().lower()
    if notification_provider not in NOTIFICATION_PROVIDERS:
        raise ValueError("notification provider must be slack or teams")
    notifications = NotificationConfig(provider=notification_provider)

    source_rows = raw.get("source", [])
    if not isinstance(source_rows, list):
        raise ValueError("[[source]] entries are required")
    sources: list[SourceConfig] = []
    ids: set[str] = set()
    for row in source_rows:
        if not isinstance(row, dict):
            raise ValueError("source entry must be a table")
        source_id = str(row.get("id", "")).strip()
        kind = str(row.get("kind", "")).strip()
        if not source_id or not kind:
            raise ValueError("source id and kind are required")
        if source_id in ids:
            raise ValueError(f"duplicate source id: {source_id}")
        ids.add(source_id)
        filter_row = row.get("filter", {}) or {}
        match_mode = str(filter_row.get("match_mode", "smart")).strip()
        if match_mode not in MATCH_MODES:
            raise ValueError("filter match_mode must be smart, substring or whole_word")
        filters = FilterRule(
            include_any=_strings(filter_row.get("include_any")),
            include_all=_strings(filter_row.get("include_all")),
            exclude_any=_strings(filter_row.get("exclude_any")),
            match_mode=match_mode,
        )
        urls = _strings(row.get("urls"))
        options = {k: v for k, v in row.items() if k not in {
            "id", "kind", "enabled", "label", "urls", "filter", "alert_on_update"
        }}
        sources.append(SourceConfig(
            id=source_id,
            kind=kind,
            enabled=bool(row.get("enabled", True)),
            label=str(row.get("label") or source_id).strip(),
            urls=urls,
            filters=filters,
            alert_on_update=bool(row.get("alert_on_update", True)),
            options=options,
        ))
    return Config(tuple(sources), max_seen, notifications)
