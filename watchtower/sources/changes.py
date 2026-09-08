"""Shared event detection for explicitly selected, bounded record snapshots."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
from urllib.parse import urljoin, urlparse

from ..models import Item
from .common import Source, SourceError


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def integer(value, name, minimum=1, maximum=5000):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def strings(value, name, *, empty=False):
    if not isinstance(value, list) or (not value and not empty) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{name} must be a non-empty string array")
    return tuple(dict.fromkeys(value))


def public_url(url):
    if not isinstance(url, str):
        raise ValueError("source URL must be text")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source URL must be HTTPS without embedded credentials")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"} or parsed.hostname.endswith(".local"):
        raise ValueError("source URL must identify a public host")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("source URL must identify a public host")
    return url


def document(source, url):
    """Bound decompressed bytes and validate redirects before following them."""
    for _ in range(6):
        public_url(url)
        response = source.get(url, stream=True, allow_redirects=False,
                              accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                location = response.headers.get("Location")
                if not location:
                    raise SourceError("Source redirect lacks a destination")
                url = urljoin(url, location)
                continue
            chunks, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > source.max_bytes:
                    raise SourceError("Source exceeds max_bytes; narrow the selection")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()
    raise SourceError("Source returned too many redirects")


def field(row, path):
    if isinstance(row, dict) and path in row:
        return row[path]
    value = row
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise SourceError("A configured record field is absent")
    return value


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def significant(before, after, rule):
    if before == after:
        return False
    if not rule:
        return True
    old, new = number(before), number(after)
    if old is None or new is None:
        return True  # Missing/nonnumeric status transitions are meaningful.
    delta = abs(new - old)
    if "absolute" in rule and delta < Decimal(str(rule["absolute"])):
        return False
    if "percent" in rule and old != 0 and delta / abs(old) * 100 < Decimal(str(rule["percent"])):
        return False
    return True


def shown(value):
    if value is None:
        return "ikke oppgitt"
    return str(value) if isinstance(value, (str, int, float)) else canonical(value)


class SnapshotSource(Source):
    """Subclasses return rows with stable keys and explicitly monitored fields."""
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        options = config.options
        self.events = strings(options.get("events", ["added", "changed"]), "events")
        if set(self.events) - {"added", "changed", "removed"}:
            raise ValueError("events must contain added, changed or removed")
        self.complete = options.get("complete_snapshot", False)
        self.allow_empty = options.get("allow_empty", False)
        if not isinstance(self.complete, bool) or not isinstance(self.allow_empty, bool):
            raise ValueError("complete_snapshot and allow_empty must be boolean")
        if "removed" in self.events and not self.complete:
            raise ValueError("removed events require complete_snapshot = true")
        self.confirmations = integer(options.get("removal_confirmations", 2), "removal_confirmations", 2, 10)
        self.change_confirmations = integer(options.get("change_confirmations", 2 if config.kind == "web_page" else 1), "change_confirmations", 1, 5)
        self.max_records = integer(options.get("max_records", 1000), "max_records", 1, 10000)
        self.max_bytes = integer(options.get("max_bytes", 4000000), "max_bytes", 1024, 10000000)
        self.thresholds = options.get("thresholds", {})
        if not isinstance(self.thresholds, dict):
            raise ValueError("thresholds must be a table of field rules")
        for name, rule in self.thresholds.items():
            if not isinstance(rule, dict) or not rule or set(rule) - {"absolute", "percent"}:
                raise ValueError("threshold rules support absolute and percent")
            if any(number(value) is None or number(value) < 0 for value in rule.values()):
                raise ValueError("thresholds must be finite non-negative numbers")
        self._next = {}
        self.field_labels = options.get("field_labels", {})
        if not isinstance(self.field_labels, dict) or any(not isinstance(v, str) for v in self.field_labels.values()):
            raise ValueError("field_labels must map field names to text")
        # A changed selection has its own quiet baseline, rather than a storm.
        self.scope = digest({"kind": config.kind, "urls": config.urls, "options": {
            k: v for k, v in options.items() if k not in {"interval_minutes"}
        }})

    def read_records(self):
        raise NotImplementedError

    def fetch(self):
        return self.fetch_with_state(None)

    def fetch_with_state(self, previous):
        stored = ((previous or {}).get("source_state") or {}).get("records", {})
        if not isinstance(stored, dict):
            raise SourceError("Invalid private record snapshots")
        old = stored.get("rows", {}) if stored.get("scope") == self.scope else {}
        baseline = stored.get("scope") != self.scope
        if not isinstance(old, dict):
            raise SourceError("Invalid private record rows")
        rows = self.read_records()
        if not rows and not self.allow_empty:
            raise SourceError("Source returned no selected records; previous state preserved")
        if len(rows) > self.max_records:
            raise SourceError("Source exceeds max_records; narrow the selection")
        keys = [row["key"] for row in rows]
        if len(keys) != len(set(keys)):
            raise SourceError("Source returned duplicate record identities")
        current, items = {}, []
        for row in rows:
            key = row["key"]
            before = old.get(key)
            accepted = dict(row["fields"])
            changed = []
            event = "added"
            if before and before.get("present"):
                event = "changed"
                if set(before["row"]["fields"]) != set(accepted):
                    raise SourceError("Monitored field schema changed; inspect source configuration")
                for name, value in row["fields"].items():
                    prior = before["row"]["fields"][name]
                    if significant(prior, value, self.thresholds.get(name)):
                        changed.append(self.describe_change(name, prior, value))
                    else:
                        accepted[name] = prior  # Thresholds accumulate from last accepted value.
            else:
                changed = [f"{self.field_labels.get(name, name)}: {shown(value)}" for name, value in row["fields"].items()]
            accepted_row = {**row, "fields": accepted}
            current[key] = {"present": True, "row": accepted_row, "missing": 0}
            waiting = False
            if before and before.get("present") and changed and self.change_confirmations > 1:
                candidate = digest(accepted)
                count = before.get("candidate_count", 0) + 1 if before.get("candidate") == candidate else 1
                if count < self.change_confirmations:
                    waiting = True
                    accepted_row = {**row, "fields": before["row"]["fields"]}
                    current[key] = {"present": True, "row": accepted_row, "missing": 0,
                                    "candidate": candidate, "candidate_count": count}
            labels = {"added": "Ny registrering", "changed": "Endret registrering"}
            details = (labels[event], *changed)
            items.append(self._item(accepted_row, event, details,
                                   baseline or event not in self.events or not changed or waiting))
        if self.complete:
            for key, before in old.items():
                if key in current or not before.get("present"):
                    continue
                missing = before.get("missing", 0) + 1
                current[key] = {**before, "missing": missing}
                if missing >= self.confirmations:
                    current[key]["present"] = False
                    items.append(self._item(before["row"], "removed", (
                        f"Ikke funnet i {self.confirmations} fullførte uttrekk på rad.",
                        "Dette er en observasjon av kildens utvalg, ikke dokumentasjon på sletting eller tilbakekall.",
                    ), baseline or "removed" not in self.events))
        if len(current) > self.max_records * 2:
            raise SourceError("Too many records awaiting disappearance confirmation")
        self._next = {"scope": self.scope, "rows": current}
        return items

    def _item(self, row, event, details, suppress):
        return Item(self.config.id, "record:" + digest(row["key"]), row["title"], row["url"],
                    published=row.get("published"), text=canonical(row["fields"]),
                    metadata={"event": event}, alert_details=tuple(details),
                    fingerprint=digest({"fields": row["fields"], "present": event != "removed"}),
                    suppress_alert=suppress)

    def describe_change(self, name, before, after):
        return f"{self.field_labels.get(name, name)}: {shown(before)} → {shown(after)}"

    def augment_state(self, state):
        return {**state, "source_state": {**state.get("source_state", {}), "records": self._next}}
