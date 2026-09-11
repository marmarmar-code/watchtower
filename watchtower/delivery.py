"""A private, resumable delivery journal. Webhooks remain at-least-once."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from .models import NotificationEntry
from .notifier import format_slack_entries, notification_batches
from .state import StateStore


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pending(state: StateStore) -> dict | None:
    journal = state.load("_outbox")
    if journal is None:
        return None
    if journal.get("version") != 1 or not isinstance(journal.get("pending"), bool):
        raise ValueError("invalid private delivery journal")
    if not journal["pending"]:
        return None
    if not isinstance(journal.get("batches"), list) or not journal["batches"]:
        raise ValueError("invalid private delivery batches")
    for batch in journal["batches"]:
        if not isinstance(batch, dict) or not isinstance(batch.get("rows"), list):
            raise ValueError("invalid private delivery batch")
        if not isinstance(batch.get("entries"), list) or not isinstance(batch.get("sent_at"), (str, type(None))):
            raise ValueError("invalid private delivery batch")
        if not all(isinstance(row, dict) for row in batch["rows"]):
            raise ValueError("invalid private delivery batch")
    if (
        not isinstance(journal.get("provider"), str)
        or not journal["provider"]
        or not isinstance(journal.get("staged"), dict)
        or not all(isinstance(value, dict) for value in journal["staged"].values())
        or not isinstance(journal.get("status"), dict)
    ):
        raise ValueError("invalid private delivery state")
    return journal


def prepare(state, *, provider, entries, rows, staged, status, summary=None):
    if pending(state):
        raise ValueError("finish pending delivery before preparing new alerts")
    transaction = uuid4().hex
    rows = [{**row, "delivery_id": f"{transaction}:{index}"} for index, row in enumerate(rows)]
    batches = []
    if summary:
        batches.append({"text": summary, "entries": [], "rows": rows, "sent_at": None})
    else:
        offset = 0
        for batch in notification_batches(entries):
            batches.append({
                "entries": [asdict(entry) for entry in batch],
                "rows": rows[offset:offset + len(batch)], "sent_at": None,
            })
            offset += len(batch)
    journal = {
        "version": 1, "pending": True, "provider": provider,
        "created_at": timestamp(), "batches": batches, "staged": staged, "status": status,
    }
    state.save("_outbox", journal)
    return journal


def record_history(state: StateStore, rows: list[dict]) -> None:
    old = state.load("_alert_audit") or {}
    existing = old.get("entries", [])
    if not isinstance(existing, list) or not all(isinstance(row, dict) for row in existing):
        raise ValueError("invalid private alert audit")
    # Finalization can repeat after a crash; keep each delivery receipt once.
    incoming = {row.get("delivery_id") for row in rows if row.get("delivery_id")}
    kept = [row for row in existing if row.get("delivery_id") not in incoming]
    state.save("_latest_alerts", {"entries": rows})
    state.save("_alert_audit", {"entries": (kept + rows)[-500:]})


def deliver(state: StateStore, notifier, *, provider: str) -> dict:
    journal = pending(state)
    if journal is None:
        raise ValueError("no pending delivery")
    if journal["provider"] != provider:
        raise ValueError("pending delivery uses another provider; restore the previous provider first")
    if notifier is None:
        raise RuntimeError("pending delivery requires a notifier")
    # Validate all stored entries and destinations before sending anything.
    decoded = []
    for batch in journal["batches"]:
        decoded.append(tuple(NotificationEntry(**entry) for entry in batch["entries"]))
    for source_id in journal["staged"]:
        state.path_for(source_id)
        if source_id.startswith("_"):
            raise ValueError("reserved source id in delivery journal")
    for batch, entries in zip(journal["batches"], decoded):
        if batch["sent_at"]:
            continue
        if "text" in batch:
            if callable(getattr(type(notifier), "send_text", None)):
                notifier.send_text(batch["text"])
            else:
                notifier.send(batch["text"])
        elif callable(getattr(type(notifier), "send_alerts", None)):
            notifier.send_alerts(entries)
        else:
            notifier.send(format_slack_entries(entries))
        batch["sent_at"] = timestamp()
        # Persist progress after every provider message, including partial runs.
        state.save("_outbox", journal)
    for source_id, value in journal["staged"].items():
        state.save(source_id, value)
    rows = [
        {**row, "sent_at": batch["sent_at"]}
        for batch in journal["batches"] for row in batch["rows"]
    ]
    record_history(state, rows)
    status = {**journal["status"], "last_run_at": timestamp()}
    state.save("_status", status)
    state.save("_outbox", {"version": 1, "pending": False})
    return status
