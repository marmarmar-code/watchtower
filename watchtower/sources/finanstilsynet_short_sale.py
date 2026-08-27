"""Read-only adapter for Finanstilsynet's public Short Sale Register."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

from ..models import Item
from .common import Source, SourceError


API_URL = "https://ssr.finanstilsynet.no/api/v2/instruments"
PUBLIC_SITE = "https://ssr.finanstilsynet.no/"
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
STATE_KEY = "finanstilsynet_short_sale"


class FinanstilsynetShortSaleSource(Source):
    """Monitor the latest aggregate short position for selected issuers."""

    def __init__(self, config, *args, **kwargs) -> None:
        super().__init__(config, *args, **kwargs)
        if len(config.urls) > 1:
            raise ValueError("Short-sale source accepts at most one API URL")
        self.endpoint = config.urls[0] if config.urls else API_URL
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Short-sale endpoint must be an HTTPS URL")
        options = config.options
        self.isins = _strings(options.get("isins", options.get("allowed_isins", [])), "isins")
        self.issuers = _strings(
            options.get("issuers", options.get("allowed_issuers", [])), "issuers"
        )
        if any(not ISIN_RE.fullmatch(value.upper()) for value in self.isins):
            raise ValueError("Short-sale isins must contain valid 12-character ISINs")
        self.isins = tuple(value.upper() for value in self.isins)
        if not self.isins and not self.issuers:
            raise ValueError("Short-sale source requires isins or issuers")
        self._snapshots: dict[str, dict[str, Any]] = {}

    def fetch(self) -> list[Item]:
        return self.fetch_with_state(None)

    def fetch_with_state(self, previous: dict[str, Any] | None) -> list[Item]:
        old = ((previous or {}).get("source_state") or {}).get(STATE_KEY, {})
        if not isinstance(old, dict):
            old = {}
        try:
            payload = self.get(self.endpoint, headers={"Accept": "application/json"}).json()
        except ValueError as exc:
            raise SourceError("Short-sale API returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise SourceError("Short-sale API returned an unexpected document")

        requested_isins = set(self.isins)
        requested_issuers = {value.casefold() for value in self.issuers}
        found_isins: set[str] = set()
        found_issuers: set[str] = set()
        snapshots: dict[str, dict[str, Any]] = {}
        items: list[Item] = []
        seen_instruments: set[str] = set()

        for instrument in payload:
            if not isinstance(instrument, dict):
                raise SourceError("Short-sale API contained an invalid instrument")
            isin = _text(instrument.get("isin")).upper()
            issuer = _text(instrument.get("issuerName"))
            if not isin or not issuer:
                raise SourceError("Short-sale API contained an incomplete instrument")
            issuer_key = issuer.casefold()
            selected = isin in requested_isins or issuer_key in requested_issuers
            if not selected:
                continue
            if isin in seen_instruments:
                raise SourceError("Short-sale API returned a duplicate instrument")
            seen_instruments.add(isin)
            if isin in requested_isins:
                found_isins.add(isin)
            if issuer_key in requested_issuers:
                found_issuers.add(issuer_key)

            events = instrument.get("events")
            if not isinstance(events, list) or not events:
                raise SourceError("Short-sale instrument had no valid events")
            latest = max((_event(event) for event in events), key=lambda event: event["date"])
            snapshot = {"isin": isin, "issuer": issuer, **latest}
            snapshots[isin] = snapshot
            details = _diff(old.get(isin), snapshot)
            items.append(_item(self.config.id, snapshot, details))

        missing_isins = requested_isins - found_isins
        missing_issuers = requested_issuers - found_issuers
        if missing_isins or missing_issuers:
            raise SourceError("Short-sale selection did not match every configured issuer")
        self._snapshots = snapshots
        return sorted(items, key=lambda item: item.key)

    def augment_state(self, state: dict[str, Any]) -> dict[str, Any]:
        result = dict(state)
        source_state = result.get("source_state")
        source_state = dict(source_state) if isinstance(source_state, dict) else {}
        source_state[STATE_KEY] = self._snapshots
        result["source_state"] = source_state
        return result


def _event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceError("Short-sale API contained an invalid event")
    event_date = _text(value.get("date"))
    if not event_date:
        raise SourceError("Short-sale event lacked a date")
    short_percent = _decimal(value.get("shortPercent"), "shortPercent")
    shares = _integer(value.get("shares"), "shares")
    positions = value.get("activePositions")
    if not isinstance(positions, list):
        raise SourceError("Short-sale event had invalid activePositions")
    normalized_positions = [_position(position) for position in positions]
    normalized_positions.sort(key=lambda row: (row["holder"].casefold(), row["date"]))
    return {
        "date": event_date,
        "short_percent": short_percent,
        "shares": shares,
        "active_positions": normalized_positions,
    }


def _position(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceError("Short-sale event contained an invalid active position")
    holder = _text(value.get("positionHolder"))
    event_date = _text(value.get("date"))
    if not holder or not event_date:
        raise SourceError("Short-sale active position was incomplete")
    return {
        "holder": holder,
        "date": event_date,
        "short_percent": _decimal(value.get("shortPercent"), "shortPercent"),
        "shares": _integer(value.get("shares"), "shares"),
    }


def _item(source_id: str, snapshot: dict[str, Any], details: list[str]) -> Item:
    percent = _percent(snapshot["short_percent"])
    status = "lukket" if snapshot["short_percent"] == "0" else "aktiv"
    holder_lines = [
        f"{position['holder']}: {_percent(position['short_percent'])} %"
        for position in snapshot["active_positions"]
    ]
    text = [
        snapshot["issuer"],
        snapshot["isin"],
        f"Samlet shortandel: {percent} %",
        f"Rapporterte aksjer: {snapshot['shares']}",
        f"Status: {status}",
        *holder_lines,
    ]
    return Item(
        source_id=source_id,
        key=f"short:{snapshot['isin']}",
        title=f"Shortsalg: {snapshot['issuer']} – {percent} %",
        url=PUBLIC_SITE,
        published=snapshot["date"],
        text="\n".join(text),
        metadata={
            "isin": snapshot["isin"],
            "issuer": snapshot["issuer"],
            "position": snapshot["short_percent"],
            "status": status,
            "event": "short_position",
        },
        fingerprint=_digest(snapshot),
        alert_details=tuple(details),
    )


def _diff(previous: Any, current: dict[str, Any]) -> list[str]:
    if not isinstance(previous, dict):
        return []
    details: list[str] = []
    if previous.get("short_percent") != current.get("short_percent"):
        details.append(
            "Samlet shortandel: "
            f"{_percent(previous.get('short_percent'))} % → "
            f"{_percent(current.get('short_percent'))} %"
        )
    if previous.get("shares") != current.get("shares"):
        details.append(f"Rapporterte aksjer: {previous.get('shares')} → {current.get('shares')}")
    old_positions = _position_map(previous.get("active_positions"))
    new_positions = _position_map(current.get("active_positions"))
    for holder in sorted(old_positions.keys() - new_positions.keys(), key=str.casefold):
        details.append(f"Posisjon lukket: {holder}")
    for holder in sorted(new_positions.keys() - old_positions.keys(), key=str.casefold):
        details.append(
            f"Ny posisjon: {holder} ({_percent(new_positions[holder]['short_percent'])} %)"
        )
    for holder in sorted(old_positions.keys() & new_positions.keys(), key=str.casefold):
        old_percent = old_positions[holder].get("short_percent")
        new_percent = new_positions[holder].get("short_percent")
        if old_percent != new_percent:
            details.append(
                f"{holder}: {_percent(old_percent)} % → {_percent(new_percent)} %"
            )
    return details[:8]


def _position_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    return {
        str(row.get("holder")): row
        for row in value
        if isinstance(row, dict) and row.get("holder")
    }


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Short-sale {field} must be an array of strings")
    cleaned = tuple(item.strip() for item in value)
    if any(not item for item in cleaned):
        raise ValueError(f"Short-sale {field} cannot contain empty values")
    return tuple(dict.fromkeys(cleaned))


def _decimal(value: Any, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SourceError(f"Short-sale event had invalid {field}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise SourceError(f"Short-sale event had invalid {field}") from exc
    if not number.is_finite() or number < 0:
        raise SourceError(f"Short-sale event had invalid {field}")
    rendered = format(number.normalize(), "f")
    return "0" if rendered in {"-0", ""} else rendered


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SourceError(f"Short-sale event had invalid {field}")
    return value


def _percent(value: Any) -> str:
    return str(value if value not in (None, "") else "ukjent").replace(".", ",")


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
