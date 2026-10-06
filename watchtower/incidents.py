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
    """Bound notification size while retaining the number and category of failures."""
    sources = sorted(set(source_ids))
    if not sources:
        return "ingen"
    accounts = [source for source in sources if source.startswith("account_figures_")]
    remaining = [source for source in sources if not source.startswith("account_figures_")]
    if len(accounts) >= 4:
        parts = [f"BRREG-regnskapstall ({len(accounts)} kilder)", *remaining]
    else:
        parts = [*accounts, *remaining]
    if len(parts) > max_names:
        return ", ".join(parts[:max_names]) + f" + {len(parts) - max_names} andre"
    return ", ".join(parts)
