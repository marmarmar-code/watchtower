from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .config import Config, SourceConfig
from .models import Item
from .notifier import Notifier
from .state import StateStore
from .sources.common import Source
from .sources.regjeringen import RegjeringenSource
from .sources.stortinget import StortingetSource
from .sources.konkurransetilsynet import KonkurransetilsynetSource
from .sources.euronext import EuronextSource
from .sources.doffin import DoffinSource
from .sources.hoyesterett import HoyesterettSource
from .sources.brreg import BrregSource


SOURCE_TYPES: dict[str, type[Source]] = {
    "regjeringen": RegjeringenSource,
    "stortinget": StortingetSource,
    "konkurransetilsynet": KonkurransetilsynetSource,
    "euronext": EuronextSource,
    "doffin": DoffinSource,
    "hoyesterett": HoyesterettSource,
    "brreg": BrregSource,
}

_STATUS_FIELDS = ("checked_sources", "baselined_sources", "alerts", "errors")
_ALERT_AUDIT_SOURCE_ID = "_alert_audit"
_ALERT_AUDIT_LIMIT = 500


@dataclass
class Alert:
    source: SourceConfig
    item: Item
    change: str
    matched_terms: tuple[str, ...]


@dataclass
class RunResult:
    checked_sources: int
    baselined_sources: int
    alerts: int
    errors: dict[str, str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_source(config: SourceConfig) -> Source:
    cls = SOURCE_TYPES.get(config.kind)
    if cls is None:
        raise ValueError(f"unsupported source kind: {config.kind}")
    return cls(config)


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())[:160]
    return type(exc).__name__ if not message else f"{type(exc).__name__}: {message}"


def _should_save_status(previous: dict | None, current: dict) -> bool:
    if previous is None:
        return True
    if any(previous.get(field) != current.get(field) for field in _STATUS_FIELDS):
        return True
    previous_day = str(previous.get("last_run_at") or "")[:10]
    current_day = str(current.get("last_run_at") or "")[:10]
    return not previous_day or previous_day != current_day


def _state_for_evaluation(source: SourceConfig, previous: dict | None) -> dict | None:
    if (
        previous is not None
        and source.options.get("rebaseline_empty_state") is True
        and not previous.get("seen")
    ):
        return None
    return previous


def _save_alert_audit(
    state: StateStore,
    alerts: list[Alert],
    *,
    sent_at: str,
) -> None:
    previous = state.load(_ALERT_AUDIT_SOURCE_ID) or {}
    entries = previous.get("entries", [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("invalid private alert audit")
    entries = list(entries)
    entries.extend(
        {
            "sent_at": sent_at,
            "source_id": alert.source.id,
            "item_key": alert.item.key,
            "change": alert.change,
        }
        for alert in alerts
    )
    state.save(_ALERT_AUDIT_SOURCE_ID, {"entries": entries[-_ALERT_AUDIT_LIMIT:]})


def run(
    config: Config,
    state: StateStore,
    notifier: Notifier | None,
    *,
    dry_run: bool = False,
    source_factory: Callable[[SourceConfig], Source] = build_source,
) -> RunResult:
    checked = 0
    baselined = 0
    alerts: list[Alert] = []
    errors: dict[str, str] = {}
    staged: dict[str, dict] = {}

    for source_config in config.sources:
        if not source_config.enabled:
            continue
        try:
            source = source_factory(source_config)
            old_state = state.load(source_config.id)
            items = source.fetch_with_state(old_state)
            checked += 1
            next_state, source_alerts, was_baseline = evaluate(
                source_config,
                items,
                _state_for_evaluation(source_config, old_state),
                max_seen=config.max_seen_per_source,
            )
            augment_state = getattr(source, "augment_state", None)
            staged[source_config.id] = (
                augment_state(next_state) if callable(augment_state) else next_state
            )
            alerts.extend(source_alerts)
            if was_baseline:
                baselined += 1
        except Exception as exc:
            errors[source_config.id] = _safe_error(exc)

    if dry_run:
        return RunResult(checked, baselined, len(alerts), errors)

    if alerts:
        if notifier is None:
            raise RuntimeError("alerts pending but notifier is not configured")
        notifier.send(format_slack(alerts))

    for source_id, next_state in staged.items():
        state.save(source_id, next_state)

    completed_at = now_iso()
    if alerts:
        _save_alert_audit(state, alerts, sent_at=completed_at)

    status = {
        "last_run_at": completed_at,
        "checked_sources": checked,
        "baselined_sources": baselined,
        "alerts": len(alerts),
        "errors": errors,
    }
    if _should_save_status(state.load("_status"), status):
        state.save("_status", status)
    return RunResult(checked, baselined, len(alerts), errors)


def evaluate(
    source: SourceConfig,
    items: list[Item],
    previous: dict | None,
    *,
    max_seen: int,
) -> tuple[dict, list[Alert], bool]:
    seen = dict(previous.get("seen", {})) if previous else {}
    previous_order = list(previous.get("order", [])) if previous else []
    baseline = previous is None
    alerts: list[Alert] = []

    order: list[str] = []
    order_keys: set[str] = set()
    for key in previous_order:
        if key in seen and key not in order_keys:
            order.append(key)
            order_keys.add(key)
    for key in seen:
        if key not in order_keys:
            order.append(key)
            order_keys.add(key)

    latest: dict[str, tuple[int, Item]] = {}
    for index, item in enumerate(items):
        latest[item.key] = (index, item)
    unique_items = [item for _, item in sorted(latest.values(), key=lambda pair: pair[0])]

    move_to_end: list[str] = []
    moved: set[str] = set()
    for item in unique_items:
        digest = item.content_hash()
        old_digest = seen.get(item.key)
        change = "new" if old_digest is None else ("updated" if old_digest != digest else "unchanged")
        candidate = change == "new" or (change == "updated" and source.alert_on_update)
        if not baseline and candidate and source.filters.matches(item.searchable_text()):
            alerts.append(Alert(source, item, change, _matched_terms(source, item.searchable_text())))
        seen[item.key] = digest

        if change != "unchanged" or item.key not in order_keys:
            if item.key not in moved:
                move_to_end.append(item.key)
                moved.add(item.key)

    if moved:
        order = [key for key in order if key not in moved]
        order.extend(move_to_end)

    if max_seen > 0 and len(order) > max_seen:
        drop = order[:-max_seen]
        order = order[-max_seen:]
        for key in drop:
            seen.pop(key, None)

    if (
        previous is not None
        and previous.get("initialized") is True
        and previous.get("seen") == seen
        and previous.get("order") == order
    ):
        return dict(previous), alerts, baseline

    next_state = {
        "initialized": True,
        "updated_at": now_iso(),
        "seen": seen,
        "order": order,
    }
    return next_state, alerts, baseline


def _matched_terms(source: SourceConfig, text: str) -> tuple[str, ...]:
    terms = [*source.filters.include_any, *source.filters.include_all]
    return tuple(term for term in terms if source.filters.matches_term(text, term))[:8]


def format_slack(alerts: list[Alert]) -> str:
    blocks: list[str] = []
    for alert in alerts:
        kind = "NY" if alert.change == "new" else "OPPDATERT"
        lines = [
            f"*WATCHTOWER · {alert.source.label.upper()} · {kind}*",
            f"*{_escape(alert.item.title)}*",
        ]
        if alert.item.published:
            lines.append(f"Publisert: {_escape(alert.item.published)}")
        if alert.matched_terms:
            lines.append("Treff: " + _escape(", ".join(alert.matched_terms)))
        lines.append(f"<{alert.item.url}|Åpne kilden>")
        blocks.append("\n".join(lines))
    return "\n\n——————————\n\n".join(blocks)


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
