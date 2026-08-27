"""Read-only monitor for Patentstyret's official IPR portfolio API."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import quote

from ..models import Item
from .common import Source, SourceError
from .identifiers import valid_orgnr


API_URL = "https://api.patentstyret.no/register/v1/IprCasesByCompany"
SEARCH_URL = "https://services.patentstyret.no/search-details"
STATE_KEY = "patentstyret"
KIND_NAMES = {
    "patent": "Patent",
    "trademark": "Trademark",
    "varemerke": "Trademark",
    "design": "Design",
}


class PatentstyretSource(Source):
    """Monitor patents, trademarks and designs for selected organisations."""

    def __init__(self, config, *args, **kwargs) -> None:
        super().__init__(config, *args, **kwargs)
        options = config.options
        values = options.get("companies", options.get("organisation_numbers"))
        if not isinstance(values, list) or not values:
            raise ValueError("Patentstyret source requires a non-empty companies array")
        cleaned = tuple(dict.fromkeys(str(value).strip().replace(" ", "") for value in values))
        if any(not valid_orgnr(value) for value in cleaned):
            raise ValueError("Patentstyret companies must contain valid organisation numbers")
        self.companies = cleaned

        if config.urls:
            raise ValueError("Patentstyret does not accept custom API URLs")
        self.endpoint = API_URL

        raw_kinds = options.get("kinds", ["Patent", "Trademark", "Design"])
        if not isinstance(raw_kinds, list) or not raw_kinds:
            raise ValueError("Patentstyret kinds must be a non-empty array")
        kinds: set[str] = set()
        for value in raw_kinds:
            normalized = KIND_NAMES.get(str(value).strip().casefold())
            if normalized is None:
                raise ValueError("Patentstyret kinds must be Patent, Trademark or Design")
            kinds.add(normalized)
        self.kinds = kinds
        self._snapshots: dict[str, dict[str, Any]] = {}

    def fetch(self) -> list[Item]:
        return self.fetch_with_state(None)

    def fetch_with_state(self, previous: dict[str, Any] | None) -> list[Item]:
        api_key = os.environ.get("PATENTSTYRET_API_KEY", "").strip()
        if not api_key:
            raise SourceError("Patentstyret requires PATENTSTYRET_API_KEY")
        old = ((previous or {}).get("source_state") or {}).get(STATE_KEY, {})
        if not isinstance(old, dict):
            old = {}
        headers = {
            "Accept": "application/json",
            "Ocp-Apim-Subscription-Key": api_key,
        }
        merged: dict[str, dict[str, Any]] = {}
        organisations: dict[str, set[str]] = {}
        for orgnr in self.companies:
            response = self.get(
                self.endpoint,
                params={"organizationNumber": orgnr},
                headers=headers,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceError("Patentstyret response was not valid JSON") from exc
            for row in _rows(payload):
                snapshot = _snapshot(row)
                if snapshot["kind"] not in self.kinds:
                    continue
                case_key = f"{snapshot['kind'].casefold()}:{snapshot['application_number']}"
                prior = merged.get(case_key)
                if prior is not None and _digest(prior) != _digest(snapshot):
                    raise SourceError("Patentstyret returned conflicting versions of one case")
                merged[case_key] = snapshot
                organisations.setdefault(case_key, set()).add(orgnr)

        items: list[Item] = []
        snapshots: dict[str, dict[str, Any]] = {}
        for case_key, snapshot in sorted(merged.items()):
            stored = {**snapshot, "organisation_numbers": sorted(organisations[case_key])}
            snapshots[case_key] = stored
            items.append(_item(self.config.id, case_key, stored, _diff(old.get(case_key), stored)))
        self._snapshots = snapshots
        return items

    def augment_state(self, state: dict[str, Any]) -> dict[str, Any]:
        result = dict(state)
        source_state = result.get("source_state")
        source_state = dict(source_state) if isinstance(source_state, dict) else {}
        source_state[STATE_KEY] = self._snapshots
        result["source_state"] = source_state
        return result


def _rows(payload: Any) -> list[dict[str, Any]]:
    rows: Any = payload
    if isinstance(payload, dict):
        for key in ("cases", "iprCases", "items", "results", "data"):
            if key in payload:
                rows = payload[key]
                break
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SourceError("Patentstyret response contained no valid case list")
    return rows


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    number = _first(row, "applicationNumber", "application_number", "caseNumber")
    if not number:
        raise SourceError("Patentstyret case lacked application number")
    raw_kind = _first(row, "caseType", "type", "domain", "category")
    kind = KIND_NAMES.get(raw_kind.casefold())
    if kind is None:
        raise SourceError("Patentstyret case had an unsupported type")
    title = _first(row, "title", "markName", "name", "inventionTitle")
    status = _first(row, "status", "caseStatus", "currentStatus")
    application_date = _first(row, "applicationDate", "filingDate", "date")
    registration_number = _first(row, "registrationNumber", "registration_number")
    case_url = _first(row, "caseUrl", "url")
    return {
        "application_number": number,
        "registration_number": registration_number or None,
        "kind": kind,
        "title": title or None,
        "status": status or None,
        "application_date": application_date or None,
        "case_url": case_url or None,
    }


def _item(
    source_id: str,
    case_key: str,
    snapshot: dict[str, Any],
    details: list[str],
) -> Item:
    number = snapshot["application_number"]
    kind = snapshot["kind"]
    title = snapshot.get("title") or f"{kind} {number}"
    url = snapshot.get("case_url") or (
        f"{SEARCH_URL}/{quote(kind, safe='')}/{quote(number, safe='')}"
    )
    organisations = snapshot["organisation_numbers"]
    text = [
        f"Organisasjonsnummer: {', '.join(organisations)}",
        f"Søknadsnummer: {number}",
    ]
    if snapshot.get("registration_number"):
        text.append(f"Registreringsnummer: {snapshot['registration_number']}")
    if snapshot.get("status"):
        text.append(f"Status: {snapshot['status']}")
    return Item(
        source_id=source_id,
        key=case_key,
        title=f"{kind}: {title}",
        url=url,
        published=snapshot.get("application_date"),
        text="\n".join(text),
        metadata={
            "organisation_numbers": ",".join(organisations),
            "application_number": number,
            "kind": kind,
            "status": snapshot.get("status"),
            "event": "ipr_case",
        },
        fingerprint=_digest(snapshot),
        alert_details=tuple(details),
    )


def _diff(previous: Any, current: dict[str, Any]) -> list[str]:
    if not isinstance(previous, dict):
        return []
    details: list[str] = []
    for field, label in (
        ("status", "Status"),
        ("registration_number", "Registreringsnummer"),
        ("title", "Tittel/navn"),
    ):
        if previous.get(field) != current.get(field):
            details.append(
                f"{label}: {previous.get(field) or 'ikke oppgitt'} → "
                f"{current.get(field) or 'ikke oppgitt'}"
            )
    return details


def _first(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return " ".join(str(value).split())
    return ""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
