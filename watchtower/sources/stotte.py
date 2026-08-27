"""Read-only adapter for Brønnøysundregistrene's public Støtteregister."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any
from urllib.parse import urlparse

from ..models import Item
from .common import Source, SourceError
from .identifiers import valid_orgnr


API_URL = (
    "https://stottetiltak-registerinfo-api.app.brreg.no/"
    "api/v1/soek/stoettetildeling"
)
PUBLIC_SITE = "https://stotte.brreg.no/"


class StotteSource(Source):
    """Monitor allocations selected by explicit company or market scopes."""

    def __init__(self, config, *args, **kwargs) -> None:
        super().__init__(config, *args, **kwargs)
        options = config.options
        if len(config.urls) > 1:
            raise ValueError("Støtteregister accepts at most one API URL")
        endpoint = config.urls[0] if config.urls else options.get("endpoint", API_URL)
        self.endpoint = str(endpoint).strip()
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Støtteregister endpoint must be an HTTPS URL")

        self.recipient_orgnrs = _orgnrs(
            options.get("recipient_orgnrs", options.get("recipients", [])),
            "recipient_orgnrs",
        )
        self.provider_orgnrs = _orgnrs(
            options.get("provider_orgnrs", options.get("providers", [])),
            "provider_orgnrs",
        )
        self.industries = _strings(options.get("industries", []), "industries")
        self.regions = _strings(options.get("regions", []), "regions")
        self.from_date = _optional_date(options.get("from_date"), "from_date")
        self.to_date = _optional_date(options.get("to_date"), "to_date")
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("Støtteregister from_date cannot be after to_date")
        if not any(
            (
                self.recipient_orgnrs,
                self.provider_orgnrs,
                self.industries,
                self.regions,
                self.from_date,
                self.to_date,
            )
        ):
            raise ValueError("Støtteregister requires at least one explicit scope")

        self.page_size = _bounded_int(options.get("page_size", 1000), "page_size", 1, 1000)
        self.max_pages = _bounded_int(options.get("max_pages", 5), "max_pages", 1, 20)

    def fetch(self) -> list[Item]:
        rows: dict[str, dict[str, Any]] = {}
        fingerprints: dict[str, str] = {}
        for query in self._queries():
            for row in self._search(query):
                normalized = _normalize(row)
                allocation_id = normalized["allocation_id"]
                fingerprint = _digest(normalized)
                prior = fingerprints.get(allocation_id)
                if prior is not None and prior != fingerprint:
                    raise SourceError(
                        "Støtteregister returned conflicting versions of one allocation"
                    )
                rows[allocation_id] = normalized
                fingerprints[allocation_id] = fingerprint
        return [
            _item(self.config.id, row, fingerprints[allocation_id])
            for allocation_id, row in sorted(rows.items())
        ]

    def _queries(self) -> list[list[dict[str, Any]]]:
        common: list[dict[str, Any]] = []
        if self.industries:
            common.append(
                {"field": "NAERING", "value": list(self.industries), "matchType": "ONE_OF"}
            )
        if self.regions:
            common.append(
                {"field": "REGION", "value": list(self.regions), "matchType": "ONE_OF"}
            )
        if self.from_date:
            common.append(
                {
                    "field": "TILDELINGSDATO",
                    "value": self.from_date,
                    "matchType": "DATE_GREATER_THAN_OR_EQUAL",
                }
            )
        if self.to_date:
            common.append(
                {
                    "field": "TILDELINGSDATO",
                    "value": self.to_date,
                    "matchType": "DATE_LESS_THAN_OR_EQUAL",
                }
            )

        anchors: list[dict[str, Any]] = []
        anchors.extend(
            {
                "field": "STOETTEMOTTAKER_ORGNR",
                "value": orgnr,
                "matchType": "EXACT_MATCH",
            }
            for orgnr in self.recipient_orgnrs
        )
        anchors.extend(
            {
                "field": "STOETTEGIVER_ORGNR",
                "value": orgnr,
                "matchType": "EXACT_MATCH",
            }
            for orgnr in self.provider_orgnrs
        )
        if not anchors:
            return [common]
        return [[anchor, *common] for anchor in anchors]

    def _search(self, query: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, self.max_pages + 2):
            response = self.post(
                self.endpoint,
                params={
                    "pageSize": self.page_size,
                    "page": page,
                    "sortBy": "ENDRET",
                    "sortDirection": "DESC",
                    "language": "NOB",
                },
                json=query,
                headers={"Accept": "application/json"},
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceError("Støtteregister response was not valid JSON") from exc
            if not isinstance(payload, dict):
                raise SourceError("Støtteregister response had unexpected shape")
            rows = payload.get("stoettetildeling")
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise SourceError("Støtteregister response had invalid allocations")
            if page > self.max_pages:
                if rows:
                    raise SourceError(
                        "Støtteregister search exceeded the configured page limit"
                    )
                break
            result.extend(rows)
            if len(rows) < self.page_size:
                break
        return result


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    allocation_id = _text(row.get("stoettetiltaksnummer"))
    if not allocation_id:
        raise SourceError("Støtteregister result lacked stable allocation id")
    roles = row.get("rolle")
    if not isinstance(roles, list) or not all(isinstance(role, dict) for role in roles):
        raise SourceError("Støtteregister allocation had invalid roles")
    recipient = _business_role(roles, "rolletype.stoettemottaker")
    provider = _business_role(roles, "rolletype.stoettegiver")
    amount = row.get("tildeltBeloep")
    if amount is not None and not isinstance(amount, dict):
        raise SourceError("Støtteregister allocation had invalid amount")
    amount = amount or {}
    return {
        "allocation_id": allocation_id,
        "recipient": recipient,
        "provider": provider,
        "amount": _number(amount.get("beloep")),
        "currency": _text(amount.get("valuta")) or None,
        "award_date": _text(row.get("tildelingsdato")) or None,
        "received_date": _text(row.get("mottattDato")) or None,
        "changed_at": _text(row.get("endret")) or None,
        "status": _text(row.get("status")) or None,
        "industry": _text(row.get("naering")) or None,
        "region": _text(row.get("region")) or None,
        "instrument": _text(row.get("stoetteinstrument")) or None,
        "scheme": _text(row.get("tilknyttetStoetteordning")) or None,
    }


def _business_role(roles: list[dict[str, Any]], role_type: str) -> dict[str, str | None]:
    matches = [role for role in roles if role.get("type") == role_type]
    if not matches:
        return {"orgnr": None, "name": None}
    if len(matches) > 1:
        raise SourceError("Støtteregister allocation had duplicate business roles")
    business = matches[0].get("rolleinnehaverVirksomhet")
    if business is None:
        return {"orgnr": None, "name": None}
    if not isinstance(business, dict):
        raise SourceError("Støtteregister allocation had invalid business role")
    return {
        "orgnr": _text(business.get("organisasjonsnummer")) or None,
        "name": _text(business.get("navn")) or None,
    }


def _item(source_id: str, row: dict[str, Any], fingerprint: str) -> Item:
    recipient = row["recipient"]
    provider = row["provider"]
    recipient_name = recipient.get("name") or recipient.get("orgnr") or row["allocation_id"]
    details = [
        f"Mottaker: {recipient_name}",
        f"Støttegiver: {provider.get('name') or provider.get('orgnr') or 'ukjent'}",
        f"Beløp: {_amount(row.get('amount'), row.get('currency'))}",
    ]
    if row.get("award_date"):
        details.append(f"Tildelingsdato: {row['award_date']}")
    if row.get("industry"):
        details.append(f"Næring: {row['industry']}")
    if row.get("region"):
        details.append(f"Region: {row['region']}")
    return Item(
        source_id=source_id,
        key=f"allocation:{row['allocation_id']}",
        title=f"Støttetildeling: {recipient_name}",
        url=PUBLIC_SITE,
        published=row.get("changed_at") or row.get("award_date"),
        text="\n".join(details),
        metadata={
            "allocation_id": row["allocation_id"],
            "orgnr": recipient.get("orgnr"),
            "provider_orgnr": provider.get("orgnr"),
            "event": "allocation",
        },
        fingerprint=fingerprint,
        alert_details=tuple(details[1:]),
    )


def _orgnrs(value: Any, field: str) -> tuple[str, ...]:
    values = _strings(value, field)
    if any(not valid_orgnr(orgnr.replace(" ", "")) for orgnr in values):
        raise ValueError(f"Støtteregister {field} must contain valid organisation numbers")
    return tuple(dict.fromkeys(orgnr.replace(" ", "") for orgnr in values))


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Støtteregister {field} must be an array of strings")
    cleaned = tuple(item.strip() for item in value)
    if any(not item for item in cleaned):
        raise ValueError(f"Støtteregister {field} cannot contain empty values")
    return tuple(dict.fromkeys(cleaned))


def _optional_date(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Støtteregister {field} must use YYYY-MM-DD") from exc
    return text


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Støtteregister {field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Støtteregister {field} must be an integer") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"Støtteregister {field} must be between {minimum} and {maximum}")
    return number


def _number(value: Any) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SourceError("Støtteregister allocation had invalid amount value")
    return value


def _amount(value: Any, currency: Any) -> str:
    if value is None:
        return "ukjent"
    if isinstance(value, (int, float)):
        rendered = f"{value:,.2f}".replace(",", " ").replace(".00", "")
    else:
        rendered = str(value)
    return f"{rendered} {currency or ''}".strip()


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
