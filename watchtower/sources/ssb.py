"""Fail-closed monitoring of explicitly selected SSB Statbank tables."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlencode

from ..models import Item
from .common import Source, SourceError


DEFAULT_BASE_URL = "https://data.ssb.no/api/pxwebapi/v2"
TABLE_NUMBER_RE = re.compile(r"^[0-9]{5}$")
LANGUAGES = {"no", "nb", "nn", "en"}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot(table: str, metadata: dict[str, Any]) -> dict[str, Any]:
    label = metadata.get("label")
    last_period = metadata.get("lastPeriod")
    variable_names = metadata.get("variableNames")
    if not isinstance(label, str) or not label.strip():
        raise SourceError(f"SSB table {table} has no label")
    if not isinstance(last_period, str) or not last_period.strip():
        raise SourceError(f"SSB table {table} has no last period")
    if not isinstance(variable_names, list) or not all(
        isinstance(name, str) for name in variable_names
    ):
        raise SourceError(f"SSB table {table} has invalid variable metadata")

    # Only monitoring-relevant fields belong in the fingerprint. Transport
    # metadata and future unrelated response fields must not create alerts.
    relevant = {
        "label": label.strip(),
        "first_period": str(metadata.get("firstPeriod") or "").strip(),
        "last_period": last_period.strip(),
        "variables": [name.strip() for name in variable_names],
        "discontinued": bool(metadata.get("discontinued", False)),
    }
    return {**relevant, "fingerprint": _digest(relevant)}


class SsbSource(Source):
    """Monitor table periods and structure without downloading statistics."""

    def __init__(self, config, *args, **kwargs) -> None:
        super().__init__(config, *args, **kwargs)
        raw = config.options.get("tables", config.options.get("table_ids"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("SSB source requires a non-empty tables array")
        tables: list[str] = []
        for value in raw:
            table = str(value).strip()
            if not TABLE_NUMBER_RE.fullmatch(table):
                raise ValueError("SSB tables must contain five-digit table numbers")
            tables.append(table)
        self.tables = tuple(dict.fromkeys(tables))
        self.base_url = str(config.options.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.lang = str(config.options.get("lang", "no")).strip().casefold() or "no"
        if self.lang not in LANGUAGES:
            raise ValueError("SSB lang must be no, nb, nn or en")
        self._snapshots: dict[str, dict[str, Any]] = {}

    def fetch(self) -> list[Item]:
        return self.fetch_with_state(None)

    def fetch_with_state(self, previous: dict[str, Any] | None) -> list[Item]:
        source_state = previous.get("source_state", {}) if isinstance(previous, dict) else {}
        old_all = source_state.get("ssb", {}) if isinstance(source_state, dict) else {}
        if not isinstance(old_all, dict):
            old_all = {}

        items: list[Item] = []
        snapshots: dict[str, dict[str, Any]] = {}
        for table in self.tables:
            api_url = f"{self.base_url}/tables/{table}?{urlencode({'lang': self.lang})}"
            response = self.get(api_url)
            try:
                metadata = response.json()
            except ValueError as exc:
                raise SourceError("SSB table response was not valid JSON") from exc
            if not isinstance(metadata, dict):
                raise SourceError("SSB table response had unexpected shape")

            current = _snapshot(table, metadata)
            snapshots[table] = current
            old = old_all.get(table)
            changes: list[str] = []
            if isinstance(old, dict):
                if old.get("last_period") != current["last_period"]:
                    changes.append(f"Ny periode: {current['last_period']}")
                if old.get("first_period") != current["first_period"]:
                    changes.append("Første periode i tidsserien endret")
                if old.get("label") != current["label"]:
                    changes.append("Tabelltittel endret")
                if old.get("variables") != current["variables"]:
                    changes.append("Tabellstruktur endret")
                if old.get("discontinued") != current["discontinued"]:
                    changes.append(
                        "Tabellen er avsluttet"
                        if current["discontinued"]
                        else "Tabellen er aktiv igjen"
                    )

            items.append(
                Item(
                    source_id=self.config.id,
                    key=f"table:{table}",
                    title=f"SSB: {current['label']}",
                    url=f"https://www.ssb.no/statbank/table/{table}",
                    published=current["last_period"],
                    text=(
                        f"Tabell {table} | Siste periode {current['last_period']} | "
                        + " | ".join(current["variables"])
                    ),
                    metadata={
                        "table": table,
                        "event": "table_metadata",
                        "latest_period": current["last_period"],
                    },
                    fingerprint=current["fingerprint"],
                    suppress_alert=not bool(changes),
                    alert_details=tuple(changes),
                )
            )

        self._snapshots = snapshots
        return items

    def augment_state(self, state: dict[str, Any]) -> dict[str, Any]:
        result = dict(state)
        current = result.get("source_state")
        source_state = dict(current) if isinstance(current, dict) else {}
        source_state["ssb"] = self._snapshots
        result["source_state"] = source_state
        return result
