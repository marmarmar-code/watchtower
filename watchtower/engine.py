from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable
from hashlib import sha256

from .config import Config, MIN_SOURCE_INTERVAL_MINUTES, SourceConfig
from .models import Item, NotificationEntry
from .notifier import Notifier, format_slack_entries
from .state import StateStore
from .delivery import pending, prepare, deliver, record_history
from .sources.common import Source
from .sources.regjeringen import RegjeringenSource
from .sources.stortinget import StortingetSource
from .sources.konkurransetilsynet import KonkurransetilsynetSource
from .sources.euronext import EuronextSource
from .sources.doffin import DoffinSource
from .sources.hoyesterett import HoyesterettSource
from .sources.brreg import BrregSource
from .sources.rss import RssSource
from .sources.ssb import SsbSource
from .sources.stotte import StotteSource
from .sources.finanstilsynet_short_sale import FinanstilsynetShortSaleSource
from .sources.finanstilsynet_registry import FinanstilsynetRegistrySource
from .sources.patentstyret import PatentstyretSource
from .sources.structured import StructuredSource
from .sources.web_changes import WebChangesSource
from .sources.ssb_data import SsbDataSource
from .sources.food_recalls import FoodRecallsSource
from .sources.nve_cases import NveCasesSource
from .sources.medicine import MedicineSource
from .sources.public_cases import PublicCasesSource
from .sources.journals import JournalsSource
from .sources.ted_notices import TedNoticesSource
from .sources.building_cases import BuildingCasesSource
from .sources.financial_decisions import FinancialDecisionsSource
from .sources.aquaculture import AquacultureSource
from .sources.research_awards import ResearchAwardsSource
from .sources.account_documents import AccountDocumentsSource
from .sources.press_cases import PressCasesSource
from .sources.clinical_trials import ClinicalTrialsSource
from .sources.food_inspections import FoodInspectionsSource
from .sources.ema_medicines import EmaMedicinesSource
from .sources.pesticides import PesticidesSource
from .sources.methods_decisions import MethodsDecisionsSource
from .sources.industrial_environment import IndustrialEnvironmentSource
from .sources.industrial_documents import IndustrialDocumentsSource
from .sources.company_discovery import CompanyDiscoverySource
from .sources.parliament_votes import ParliamentVotesSource


SOURCE_TYPES: dict[str, type[Source]] = {
    "industrial_environment": IndustrialEnvironmentSource,
    "industrial_documents": IndustrialDocumentsSource,
    "company_discovery": CompanyDiscoverySource,
    "parliament_votes": ParliamentVotesSource,
    "pesticides": PesticidesSource,
    "methods_decisions": MethodsDecisionsSource,
    "ema_medicines": EmaMedicinesSource,
    "food_inspections": FoodInspectionsSource,
    "clinical_trials": ClinicalTrialsSource,
    "account_documents": AccountDocumentsSource,
    "press_cases": PressCasesSource,
    "aquaculture": AquacultureSource,
    "research_awards": ResearchAwardsSource,
    "food_recalls": FoodRecallsSource,
    "nve_cases": NveCasesSource,
    "medicine": MedicineSource,
    "public_cases": PublicCasesSource,
    "journals": JournalsSource,
    "ted_notices": TedNoticesSource,
    "building_cases": BuildingCasesSource,
    "financial_decisions": FinancialDecisionsSource,
    "json_records": StructuredSource,
    "csv_records": StructuredSource,
    "web_page": WebChangesSource,
    "web_links": WebChangesSource,
    "ssb_data": SsbDataSource,
    "regjeringen": RegjeringenSource,
    "stortinget": StortingetSource,
    "konkurransetilsynet": KonkurransetilsynetSource,
    "euronext": EuronextSource,
    "doffin": DoffinSource,
    "hoyesterett": HoyesterettSource,
    "brreg": BrregSource,
    "rss": RssSource,
    "ssb": SsbSource,
    "stotte": StotteSource,
    "finanstilsynet_short_sale": FinanstilsynetShortSaleSource,
    "finanstilsynet_registry": FinanstilsynetRegistrySource,
    "patentstyret": PatentstyretSource,
}

_STATUS_FIELDS = ("checked_sources", "baselined_sources", "alerts", "errors", "warnings")
_LAST_CHECKED_FIELD = "last_checked_at"
DEFAULT_SOURCE_INTERVAL_MINUTES = 60
MAX_DETAILED_ALERTS_PER_RUN = 32


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
    warnings: dict[str, list[str]] = field(default_factory=dict)


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
    if previous is not None:
        _validate_source_state(previous)
    return previous


def _validate_source_state(previous: dict) -> None:
    seen = previous.get("seen")
    order = previous.get("order")
    if previous.get("initialized") is not True:
        raise ValueError("invalid source state schema")
    if not isinstance(seen, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in seen.items()
    ):
        raise ValueError("invalid source state schema")
    if not isinstance(order, list) or any(not isinstance(key, str) for key in order):
        raise ValueError("invalid source state schema")


def source_interval_minutes(source: SourceConfig) -> int:
    raw = source.options.get("interval_minutes", DEFAULT_SOURCE_INTERVAL_MINUTES)
    if isinstance(raw, bool):
        raise ValueError("interval_minutes must be an integer")
    try:
        interval = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval_minutes must be an integer") from exc
    if interval < MIN_SOURCE_INTERVAL_MINUTES:
        raise ValueError(
            f"interval_minutes must be at least {MIN_SOURCE_INTERVAL_MINUTES}"
        )
    return interval


def _source_is_due(
    source: SourceConfig,
    previous: dict | None,
    *,
    at: datetime,
) -> bool:
    if previous is None:
        return True
    last_value = previous.get(_LAST_CHECKED_FIELD)
    if not last_value:
        return True
    try:
        last_checked = datetime.fromisoformat(str(last_value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=timezone.utc)
    return at >= last_checked + timedelta(minutes=source_interval_minutes(source))


def _audit_rows(alerts: list[Alert], *, detected_at: str) -> list[dict]:
    return [
        {
            "detected_at": detected_at,
            "source_id": alert.source.id,
            "item_key": alert.item.key,
            "change": alert.change,
            "alert_id": sha256("\0".join((
                alert.source.id, alert.item.key, alert.item.content_hash(), alert.change,
            )).encode()).hexdigest(),
            "title": alert.item.title[:1000], "url": alert.item.url,
            "published": alert.item.published, "matched_terms": list(alert.matched_terms),
            "details": list(_bounded_details(alert.item)),
            "delivery": "summary" if len(alerts) > MAX_DETAILED_ALERTS_PER_RUN else "detail",
        }
        for alert in alerts
    ]


def _save_alert_audit(state: StateStore, alerts: list[Alert], *, sent_at: str) -> None:
    record_history(state, [
        {**row, "sent_at": sent_at} for row in _audit_rows(alerts, detected_at=sent_at)
    ])


def _unique_web_link_alerts(alerts: list[Alert]) -> list[Alert]:
    """Send an identical new link once when monitored lists overlap in a run.

    Source histories remain independent. Different content, revisions and other
    adapters are deliberately preserved; this is not cross-run deduplication.
    """
    seen = set()
    result = []
    for alert in alerts:
        if alert.source.kind == "web_links" and alert.change == "new":
            # Hash without the per-list source ID, without changing stored items.
            identity = (alert.item.url, alert.item.key,
                        replace(alert.item, source_id="").content_hash())
            if identity in seen:
                continue
            seen.add(identity)
        result.append(alert)
    return result


def run(
    config: Config,
    state: StateStore,
    notifier: Notifier | None,
    *,
    dry_run: bool = False,
    respect_intervals: bool = False,
    run_at: datetime | None = None,
    source_factory: Callable[[SourceConfig], Source] = build_source,
) -> RunResult:
    if not dry_run and pending(state):
        restored = deliver(state, notifier, provider=config.notifications.provider)
        return RunResult(restored["checked_sources"], restored["baselined_sources"],
                         restored["alerts"], restored["errors"], restored.get("warnings", {}))
    checked = 0
    baselined = 0
    alerts: list[Alert] = []
    previous_status = state.load("_status") or {}
    enabled_ids = {source.id for source in config.sources if source.enabled}
    prior_errors = previous_status.get("errors", {})
    if not isinstance(prior_errors, dict):
        raise ValueError("invalid private status errors")
    errors = {
        key: value for key, value in prior_errors.items()
        if key in enabled_ids
    }
    warnings: dict[str, list[str]] = {}
    staged: dict[str, dict] = {}
    started_at = run_at or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    checked_at = started_at.astimezone(timezone.utc).isoformat(timespec="seconds")

    for source_config in config.sources:
        if not source_config.enabled:
            continue
        try:
            old_state = state.load(source_config.id)
            evaluation_state = _state_for_evaluation(source_config, old_state)
            if old_state and old_state.get("coverage_warnings"):
                warnings[source_config.id] = old_state["coverage_warnings"]
            if respect_intervals and not _source_is_due(
                source_config,
                old_state,
                at=started_at,
            ):
                continue
            source = source_factory(source_config)
            items = source.fetch_with_state(old_state)
            source_warnings = list(getattr(source, "coverage_warnings", []))
            checked += 1
            next_state, source_alerts, was_baseline = evaluate(
                source_config,
                items,
                evaluation_state,
                max_seen=config.max_seen_per_source,
            )
            augment_state = getattr(source, "augment_state", None)
            next_state = augment_state(next_state) if callable(augment_state) else next_state
            next_state = dict(next_state)
            next_state[_LAST_CHECKED_FIELD] = checked_at
            next_state["last_item_count"] = len(items)
            next_state["coverage_warnings"] = source_warnings
            errors.pop(source_config.id, None)
            warnings.pop(source_config.id, None)
            if source_warnings:
                warnings[source_config.id] = source_warnings
            staged[source_config.id] = next_state
            alerts.extend(source_alerts)
            if was_baseline:
                baselined += 1
        except Exception as exc:
            errors[source_config.id] = _safe_error(exc)

    alerts = _unique_web_link_alerts(alerts)
    if dry_run:
        return RunResult(checked, baselined, len(alerts), errors, warnings)

    status = {
        "last_run_at": now_iso(), "checked_sources": checked,
        "baselined_sources": baselined, "alerts": len(alerts),
        "errors": errors, "warnings": warnings,
    }
    if alerts:
        if notifier is None:
            raise RuntimeError("alerts pending but notifier is not configured")
        prepare(
            state, provider=config.notifications.provider,
            entries=notification_entries(alerts), rows=_audit_rows(alerts, detected_at=checked_at),
            staged=staged, status=status,
            summary=format_alert_surge(alerts) if len(alerts) > MAX_DETAILED_ALERTS_PER_RUN else None,
        )
        deliver(state, notifier, provider=config.notifications.provider)
        return RunResult(checked, baselined, len(alerts), errors, warnings)
    for source_id, next_state in staged.items():
        state.save(source_id, next_state)
    if _should_save_status(state.load("_status"), status):
        state.save("_status", status)
    return RunResult(checked, baselined, len(alerts), errors, warnings)


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
        compatible_digests = item.compatible_content_hashes()
        digest = compatible_digests[0]
        old_digest = seen.get(item.key)
        if old_digest is None:
            change = "new"
        elif old_digest in compatible_digests:
            change = "unchanged"
        else:
            change = "updated"
        candidate = change == "new" or (change == "updated" and source.alert_on_update)
        if (
            not baseline
            and not item.suppress_alert
            and candidate
            and source.filters.matches(item.searchable_text())
        ):
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


def _bounded_details(item: Item) -> tuple[str, ...]:
    details: list[str] = []
    for value in item.alert_details:
        cleaned = " ".join(str(value).split())
        if cleaned:
            details.append(cleaned[:500])
        if len(details) >= 8:
            break
    return tuple(details)


def notification_entries(alerts: list[Alert]) -> tuple[NotificationEntry, ...]:
    return tuple(
        NotificationEntry(
            source_label=alert.source.label,
            status="NY" if alert.change == "new" else "OPPDATERT",
            title=alert.item.title,
            url=alert.item.url,
            published=alert.item.published,
            matched_terms=alert.matched_terms,
            details=_bounded_details(alert.item),
        )
        for alert in alerts
    )


def format_alert_surge(alerts: list[Alert]) -> str:
    counts: dict[tuple[str, str], int] = {}
    for alert in alerts:
        status = "NY" if alert.change == "new" else "OPPDATERT"
        key = (alert.source.label, status)
        counts[key] = counts.get(key, 0) + 1

    lines = [
        "WATCHTOWER · SIKKERHETSSTOPP",
        f"{len(alerts)} varsler ble registrert i én kjøring.",
        (
            "Detaljutsendingen ble erstattet med denne oppsummeringen "
            f"fordi sikkerhetsgrensen er {MAX_DETAILED_ALERTS_PER_RUN} varsler."
        ),
        "Fordeling:",
    ]
    for (source_label, status), count in sorted(
        counts.items(),
        key=lambda entry: (entry[0][0].casefold(), entry[0][1]),
    ):
        lines.append(f"• {source_label} · {status}: {count}")
    lines.append(
        "Hele trefflisten lagres i privat state/_latest_alerts.json etter vellykket utsending. "
        "Se den private driftsveiledningen for å lese trefflisten."
    )
    return "\n".join(lines)


def format_slack(alerts: list[Alert]) -> str:
    """Compatibility formatter used by tests and custom integrations."""
    return format_slack_entries(notification_entries(alerts))
