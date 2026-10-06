from __future__ import annotations

from collections.abc import Mapping

DEFAULT_FAILURE_THRESHOLD = 2


def reportable_failures(
    status: Mapping[str, object],
    *,
    threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> tuple[str, ...]:
    """Return source IDs that have just crossed the operational alert threshold."""
    if threshold < 1:
        raise ValueError("failure threshold must be positive")
    errors = status.get("errors", {})
    streaks = status.get("error_streaks", {})
    if not isinstance(errors, dict) or not isinstance(streaks, dict):
        return ()
    return tuple(sorted(
        source_id
        for source_id in errors
        if streaks.get(source_id) == threshold
    ))


def recovered_failures(
    before: Mapping[str, object],
    current: Mapping[str, object],
    *,
    threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> tuple[str, ...]:
    """Return previously reportable incidents that are now healthy."""
    if threshold < 1:
        raise ValueError("failure threshold must be positive")
    previous_errors = before.get("errors", {})
    current_errors = current.get("errors", {})
    previous_streaks = before.get("error_streaks", {})
    if (
        not isinstance(previous_errors, dict)
        or not isinstance(current_errors, dict)
        or not isinstance(previous_streaks, dict)
    ):
        return ()
    return tuple(sorted(
        source_id
        for source_id in set(previous_errors) - set(current_errors)
        if previous_streaks.get(source_id, 0) >= threshold
    ))


def summarize_sources(source_ids, *, max_names: int = 5) -> str:
    """Group repeated source families without conflating unrelated errors."""
    import re
    sources = sorted(set(source_ids))
    if not sources:
        return "ingen"
    families = {}
    individuals = []
    for source in sources:
        match = re.fullmatch(r"(.+)_([0-9]{9})_v([0-9]+)", source)
        if match:
            families.setdefault((match.group(1), match.group(3)), []).append(source)
        else:
            individuals.append(source)
    parts = []
    for (prefix, version), members in sorted(families.items()):
        if len(members) >= 4:
            label = "BRREG-regnskapstall" if prefix == "account_figures" else prefix.replace("_", " ")
            parts.append(f"{label} ({len(members)} kilder)")
        else:
            individuals.extend(members)
    parts.extend(sorted(individuals))
    if len(parts) > max_names:
        return ", ".join(parts[:max_names]) + f" + {len(parts) - max_names} andre"
    return ", ".join(parts)


def coverage_transitions(
    before: Mapping[str, object],
    current: Mapping[str, object],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """New and resolved limitations, not routine repeats of known warnings."""
    previous = before.get("warnings", {})
    latest = current.get("warnings", {})
    if not isinstance(previous, dict) or not isinstance(latest, dict):
        return {}, {}
    def normalise(mapping):
        return {
            key: set(values)
            for key, values in mapping.items()
            if isinstance(key, str) and isinstance(values, list)
            and all(isinstance(value, str) for value in values)
        }
    old = normalise(previous)
    new = normalise(latest)
    added, cleared = {}, {}
    for key in sorted(set(old) | set(new)):
        if delta := new.get(key, set()) - old.get(key, set()):
            added[key] = tuple(sorted(delta))
        if delta := old.get(key, set()) - new.get(key, set()):
            cleared[key] = tuple(sorted(delta))
    return added, cleared


def reportable_escalations(
    before: Mapping[str, object],
    current: Mapping[str, object],
    *,
    now: str,
    after_hours: int = 24,
) -> tuple[str, ...]:
    """Escalate a long-running outage once, when it first crosses the age threshold."""
    from datetime import datetime, timedelta, timezone

    errors = current.get("errors", {})
    since = current.get("error_since", {})
    previous_time = before.get("last_run_at")
    if not isinstance(errors, dict) or not isinstance(since, dict):
        return ()
    try:
        at = datetime.fromisoformat(now.replace("Z", "+00:00"))
        older = datetime.fromisoformat(str(previous_time).replace("Z", "+00:00"))
        if at.tzinfo is None or older.tzinfo is None:
            return ()
        at, older = at.astimezone(timezone.utc), older.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return ()
    cutoff = timedelta(hours=after_hours)
    result = []
    for source in errors:
        stamp = since.get(source)
        try:
            started = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if started.tzinfo is None:
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        if older - started < cutoff <= at - started:
            result.append(source)
    return tuple(sorted(result))
