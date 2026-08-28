from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re
import tomllib


MATCH_MODES = {"smart", "substring", "whole_word"}
NOTIFICATION_PROVIDERS = {"slack", "teams"}
PLACEHOLDER_MARKER = "REPLACE_ME"
MIN_SOURCE_INTERVAL_MINUTES = 5


@dataclass(frozen=True)
class FilterRule:
    include_any: tuple[str, ...] = ()
    include_all: tuple[str, ...] = ()
    exclude_any: tuple[str, ...] = ()
    match_mode: str = "smart"
    match_all: bool = False

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
        return self.match_all or bool(self.include_any or self.include_all)


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


def _strings(value: Any, field: str = "filter values") -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{field} must be a string array")
    return tuple(v.strip() for v in value if v.strip())


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _source_id_is_file_safe(value: str) -> bool:
    return bool(value) and all(character.isalnum() or character in "-_" for character in value)


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return PLACEHOLDER_MARKER in value.upper()
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    return False


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    general = raw.get("general", {})
    if not isinstance(general, dict):
        raise ValueError("[general] must be a table")
    max_seen = _integer(general.get("max_seen_per_source", 3000), "general.max_seen_per_source")
    if max_seen < 1:
        raise ValueError("general.max_seen_per_source must be positive")

    notification_row = raw.get("notifications", {}) or {}
    if not isinstance(notification_row, dict):
        raise ValueError("[notifications] must be a table")
    raw_provider = notification_row.get("provider", "slack")
    if not isinstance(raw_provider, str):
        raise ValueError("notification provider must be slack or teams")
    notification_provider = raw_provider.strip().lower()
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
        raw_source_id = row.get("id", "")
        raw_kind = row.get("kind", "")
        if not isinstance(raw_source_id, str) or not isinstance(raw_kind, str):
            raise ValueError("source id and kind are required strings")
        source_id = raw_source_id.strip()
        kind = raw_kind.strip()
        if not source_id or not kind:
            raise ValueError("source id and kind are required")
        if not _source_id_is_file_safe(source_id):
            raise ValueError(
                "source id must contain only letters, digits, hyphen or underscore"
            )
        if source_id in ids:
            raise ValueError("duplicate source id")
        ids.add(source_id)

        enabled = _boolean(row.get("enabled", True), "source.enabled")
        if enabled and _contains_placeholder(row):
            raise ValueError(f"enabled source {source_id} contains placeholder values")

        filter_row = row.get("filter", {}) or {}
        if not isinstance(filter_row, dict):
            raise ValueError("source.filter must be a table")
        raw_match_mode = filter_row.get("match_mode", "smart")
        if not isinstance(raw_match_mode, str):
            raise ValueError("filter match_mode must be smart, substring or whole_word")
        match_mode = raw_match_mode.strip()
        if match_mode not in MATCH_MODES:
            raise ValueError("filter match_mode must be smart, substring or whole_word")
        match_all = filter_row.get("match_all", False)
        if not isinstance(match_all, bool):
            raise ValueError("filter match_all must be true or false")
        filters = FilterRule(
            include_any=_strings(filter_row.get("include_any"), "source.filter.include_any"),
            include_all=_strings(filter_row.get("include_all"), "source.filter.include_all"),
            exclude_any=_strings(filter_row.get("exclude_any"), "source.filter.exclude_any"),
            match_mode=match_mode,
            match_all=match_all,
        )
        if enabled and not (filters.include_any or filters.include_all or filters.match_all):
            raise ValueError(
                f"enabled source {source_id} requires include rules or filter.match_all = true"
            )

        raw_label = row.get("label")
        if raw_label is not None and not isinstance(raw_label, str):
            raise ValueError("source.label must be a string")
        label = (raw_label or source_id).strip()
        if not label:
            label = source_id

        urls = _strings(row.get("urls"), "source.urls")
        alert_on_update = _boolean(
            row.get("alert_on_update", True),
            "source.alert_on_update",
        )
        if "interval_minutes" in row:
            interval = _integer(row["interval_minutes"], "source.interval_minutes")
            if interval < MIN_SOURCE_INTERVAL_MINUTES:
                raise ValueError(
                    f"source.interval_minutes must be at least {MIN_SOURCE_INTERVAL_MINUTES}"
                )
        if "rebaseline_empty_state" in row:
            _boolean(row["rebaseline_empty_state"], "source.rebaseline_empty_state")
        options = {k: v for k, v in row.items() if k not in {
            "id", "kind", "enabled", "label", "urls", "filter", "alert_on_update"
        }}
        sources.append(SourceConfig(
            id=source_id,
            kind=kind,
            enabled=enabled,
            label=label,
            urls=urls,
            filters=filters,
            alert_on_update=alert_on_update,
            options=options,
        ))
    return Config(tuple(sources), max_seen, notifications)
