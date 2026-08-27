"""Read-only health reporting for a private Watchtower runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Config
from .engine import source_interval_minutes
from .state import StateStore


HEALTHY = "healthy"
LATE = "late"
ERROR = "error"
NOT_STARTED = "not_started"
GRACE_MINUTES = 15


@dataclass(frozen=True)
class SourceHealth:
    source_id: str
    status: str
    last_checked_at: str | None
    interval_minutes: int


@dataclass(frozen=True)
class HealthReport:
    entries: tuple[SourceHealth, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            status: sum(entry.status == status for entry in self.entries)
            for status in (HEALTHY, LATE, ERROR, NOT_STARTED)
        }

    @property
    def okay(self) -> bool:
        counts = self.counts
        return bool(self.entries) and not (
            counts[LATE] or counts[ERROR] or counts[NOT_STARTED]
        )


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def inspect_health(
    config: Config,
    state: StateStore,
    *,
    at: datetime | None = None,
) -> HealthReport:
    """Classify enabled sources without contacting them or changing state."""
    checked_at = at or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)

    status_state = state.load("_status") or {}
    raw_errors = status_state.get("errors", {})
    error_ids = set(raw_errors) if isinstance(raw_errors, dict) else set()
    entries: list[SourceHealth] = []
    for source in config.sources:
        if not source.enabled:
            continue
        interval = source_interval_minutes(source)
        source_state = state.load(source.id) or {}
        raw_last_checked = source_state.get("last_checked_at")
        last_checked = _timestamp(raw_last_checked)
        if source.id in error_ids:
            health = ERROR
        elif last_checked is None:
            health = NOT_STARTED
        elif checked_at > last_checked + timedelta(
            minutes=interval + GRACE_MINUTES
        ):
            health = LATE
        else:
            health = HEALTHY
        entries.append(
            SourceHealth(
                source_id=source.id,
                status=health,
                last_checked_at=(
                    last_checked.isoformat(timespec="seconds")
                    if last_checked is not None
                    else None
                ),
                interval_minutes=interval,
            )
        )
    return HealthReport(tuple(entries))


def render_health(report: HealthReport, *, redacted: bool = False) -> str:
    counts = report.counts
    outcome = "OK" if report.okay else "NEEDS ATTENTION"
    summary = (
        f"WATCHTOWER STATUS {outcome}; enabled_sources={len(report.entries)} "
        f"healthy={counts[HEALTHY]} late={counts[LATE]} "
        f"errors={counts[ERROR]} not_started={counts[NOT_STARTED]}"
    )
    if redacted:
        return summary
    lines = [summary, "ID\tSTATUS\tSIST KONTROLLERT\tINTERVALL"]
    status_labels = {
        HEALTHY: "OK",
        LATE: "FORSINKET",
        ERROR: "FEIL",
        NOT_STARTED: "IKKE STARTET",
    }
    for entry in report.entries:
        lines.append(
            f"{entry.source_id}\t{status_labels[entry.status]}\t"
            f"{entry.last_checked_at or '-'}\t{entry.interval_minutes} min"
        )
    return "\n".join(lines)
