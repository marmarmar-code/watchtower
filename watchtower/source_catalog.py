"""Public catalog of the source adapters shipped with Watchtower."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib

from .engine import SOURCE_TYPES

CATALOG_PATH = Path(__file__).with_name("source_catalog.toml")
REQUIRED_FIELDS = {
    "id",
    "name",
    "status",
    "credential_required",
    "interval_class",
    "maintenance_owner",
    "coverage",
}
STATUSES = {"stable", "beta", "maintenance", "deprecated"}
INTERVAL_CLASSES = {"hourly", "daily", "weekly", "custom"}
SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def load_catalog(path: str | Path = CATALOG_PATH) -> list[dict[str, object]]:
    """Load and validate the public catalog, returning its source records."""
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)
    rows = raw.get("source")
    if not isinstance(rows, list):
        raise ValueError("catalog must contain [[source]] entries")
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"source entry {index} must be a table")
        missing = REQUIRED_FIELDS - set(row)
        if missing:
            fields = ", ".join(sorted(missing))
            raise ValueError(f"source entry {index} missing fields: {fields}")
        unknown = set(row) - REQUIRED_FIELDS
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise ValueError(f"source entry {index} has unknown fields: {fields}")
        source_id = row["id"]
        if not isinstance(source_id, str) or not SOURCE_ID_RE.fullmatch(source_id):
            raise ValueError(f"source entry {index} id must be lowercase snake_case")
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        if not isinstance(row["name"], str) or not row["name"].strip():
            raise ValueError(f"source {source_id} name must be a non-empty string")
        status = row["status"]
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError(f"source {source_id} has unknown status: {status}")
        if not isinstance(row["credential_required"], bool):
            raise ValueError(f"source {source_id} credential_required must be boolean")
        interval = row["interval_class"]
        if not isinstance(interval, str) or interval not in INTERVAL_CLASSES:
            raise ValueError(f"source {source_id} has unknown interval_class: {interval}")
        for field in ("maintenance_owner", "coverage"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"source {source_id} {field} must be a non-empty string")
        result.append(dict(row))
    expected = set(SOURCE_TYPES)
    if seen != expected:
        missing = ", ".join(sorted(expected - seen)) or "-"
        extra = ", ".join(sorted(seen - expected)) or "-"
        raise ValueError(
            "catalog ids differ from engine.SOURCE_TYPES "
            f"(missing: {missing}; extra: {extra})"
        )
    return result


def validate_catalog(path: str | Path = CATALOG_PATH) -> None:
    load_catalog(path)
