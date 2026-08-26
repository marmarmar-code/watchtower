from __future__ import annotations

import hashlib
import json
from typing import Any

from ..models import Item
from .common import Source, SourceError


ENTITY_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"
ROLES_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller"
ACCOUNTS_URL = "https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}"
ANNUAL_REPORT_URL = (
    "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{orgnr}/{year}"
)

ROLE_CODES = {
    "DAGL": "Daglig leder",
    "LEDE": "Styreleder",
    "NEST": "Nestleder",
    "MEDL": "Styremedlem",
}


class BrregSource(Source):
    """Monitor selected Norwegian companies through BRREG public APIs."""

    def __init__(self, config, *args, **kwargs) -> None:
        super().__init__(config, *args, **kwargs)
        raw_companies = config.options.get("companies", [])
        if not isinstance(raw_companies, list) or not raw_companies:
            raise ValueError("BRREG source requires a non-empty companies array")
        companies = []
        for value in raw_companies:
            orgnr = str(value).strip().replace(" ", "")
            if len(orgnr) != 9 or not orgnr.isdigit():
                raise ValueError("BRREG companies must contain 9-digit organisation numbers")
            companies.append(orgnr)
        self.companies = tuple(dict.fromkeys(companies))

        raw_events = config.options.get("events", ["annual_accounts", "company", "roles"])
        if not isinstance(raw_events, list):
            raise ValueError("BRREG events must be an array")
        allowed = {"annual_accounts", "company", "roles"}
        events = {str(value).strip() for value in raw_events}
        unknown = events - allowed
        if unknown:
            raise ValueError(f"unsupported BRREG events: {', '.join(sorted(unknown))}")
        self.events = events
        self._snapshots: dict[str, Any] = {}

    def fetch(self) -> list[Item]:
        return self.fetch_with_state(None)

    def fetch_with_state(self, previous: dict[str, Any] | None) -> list[Item]:
        previous_snapshots = (
            previous.get("source_state", {}).get("brreg", {})
            if isinstance(previous, dict)
            and isinstance(previous.get("source_state"), dict)
            and isinstance(previous.get("source_state", {}).get("brreg"), dict)
            else {}
        )
        items: list[Item] = []
        next_snapshots: dict[str, Any] = {}

        for orgnr in self.companies:
            entity = self._entity(orgnr)
            company_name = str(entity.get("name") or orgnr)
            current: dict[str, Any] = {"entity": entity}
            old = previous_snapshots.get(orgnr, {})
            if not isinstance(old, dict):
                old = {}

            if "company" in self.events:
                items.append(self._entity_item(orgnr, company_name, old.get("entity"), entity))

            if "roles" in self.events:
                roles = self._roles(orgnr)
                current["roles"] = roles
                items.append(self._roles_item(orgnr, company_name, old.get("roles"), roles))

            if "annual_accounts" in self.events:
                account = self._latest_account(orgnr)
                current["annual_account"] = account
                if account is not None:
                    items.append(self._account_item(orgnr, company_name, account))

            next_snapshots[orgnr] = current

        self._snapshots = next_snapshots
        return items

    def augment_state(self, state: dict[str, Any]) -> dict[str, Any]:
        result = dict(state)
        source_state = result.get("source_state")
        source_state = dict(source_state) if isinstance(source_state, dict) else {}
        source_state["brreg"] = self._snapshots
        result["source_state"] = source_state
        return result

    def _entity(self, orgnr: str) -> dict[str, Any]:
        response = self.get(ENTITY_URL.format(orgnr=orgnr), accepted_statuses=(404, 410))
        if response.status_code in {404, 410}:
            return {
                "name": None,
                "organisation_form": {"code": None, "description": None},
                "industry": {"code": None, "description": None},
                "bankrupt": False,
                "liquidating": False,
                "forced_liquidation": False,
                "deleted": False,
                "removed": response.status_code == 410,
                "unknown": response.status_code == 404,
            }
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("BRREG entity response was not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("organisasjonsnummer") != orgnr:
            raise SourceError("BRREG entity response had unexpected identity")
        return {
            "name": _clean(payload.get("navn")),
            "organisation_form": _coded(payload.get("organisasjonsform")),
            "industry": _coded(payload.get("naeringskode1")),
            "bankrupt": bool(payload.get("konkurs")),
            "liquidating": bool(payload.get("underAvvikling")),
            "forced_liquidation": bool(payload.get("underTvangsavviklingEllerTvangsopplosning")),
            "deleted": bool(payload.get("slettedato") or payload.get("erSlettet") is True),
            "removed": False,
            "unknown": False,
        }

    def _roles(self, orgnr: str) -> dict[str, list[str]]:
        response = self.get(ROLES_URL.format(orgnr=orgnr), accepted_statuses=(404, 410))
        if response.status_code in {404, 410}:
            return {code: [] for code in ROLE_CODES}
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("BRREG roles response was not valid JSON") from exc
        groups = payload.get("rollegrupper", []) if isinstance(payload, dict) else []
        if not isinstance(groups, list):
            raise SourceError("BRREG roles response had invalid rollegrupper")
        result: dict[str, set[str]] = {code: set() for code in ROLE_CODES}
        for group in groups:
            roles = group.get("roller", []) if isinstance(group, dict) else []
            if not isinstance(roles, list):
                continue
            for role in roles:
                if not isinstance(role, dict) or role.get("avregistrert") is True:
                    continue
                role_type = role.get("type")
                code = role_type.get("kode") if isinstance(role_type, dict) else None
                if code not in result:
                    continue
                holder = _role_holder(role)
                if holder:
                    result[code].add(holder)
        return {code: sorted(values, key=str.casefold) for code, values in result.items()}

    def _latest_account(self, orgnr: str) -> dict[str, Any] | None:
        response = self.get(ACCOUNTS_URL.format(orgnr=orgnr), accepted_statuses=(404,))
        if response.status_code == 404:
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("BRREG accounts response was not valid JSON") from exc
        if not isinstance(payload, list):
            raise SourceError("BRREG accounts response was not a list")
        candidates = [row for row in payload if isinstance(row, dict) and isinstance(row.get("id"), int)]
        if not candidates:
            return None
        latest = max(candidates, key=lambda row: int(row["id"]))
        period = latest.get("regnskapsperiode")
        period_to = period.get("tilDato") if isinstance(period, dict) else None
        return {
            "id": int(latest["id"]),
            "period_to": _clean(period_to),
            "report_type": _clean(latest.get("regnskapstype")),
            "journal_number": _clean(latest.get("journalnr")),
        }

    def _entity_item(
        self,
        orgnr: str,
        company_name: str,
        previous: Any,
        current: dict[str, Any],
    ) -> Item:
        changes = _diff_entity(previous, current)
        detail = "; ".join(changes) if changes else "Selskapsdata uendret"
        return Item(
            source_id=self.config.id,
            key=f"company:{orgnr}",
            title=(
                f"Selskapsendring: {company_name}"
                if changes
                else f"Selskapsstatus: {company_name}"
            ),
            url=ENTITY_URL.format(orgnr=orgnr),
            text=f"{company_name}\n{orgnr}\n{detail}",
            metadata={"orgnr": orgnr, "event": "company"},
            fingerprint=_digest(current),
            suppress_alert=not changes,
            alert_details=tuple(changes),
        )

    def _roles_item(
        self,
        orgnr: str,
        company_name: str,
        previous: Any,
        current: dict[str, list[str]],
    ) -> Item:
        changes = _diff_roles(previous, current)
        detail = "; ".join(changes) if changes else "Roller uendret"
        return Item(
            source_id=self.config.id,
            key=f"roles:{orgnr}",
            title=f"Rolleendring: {company_name}" if changes else f"Roller: {company_name}",
            url=ROLES_URL.format(orgnr=orgnr),
            text=f"{company_name}\n{orgnr}\n{detail}",
            metadata={"orgnr": orgnr, "event": "roles"},
            fingerprint=_digest(current),
            suppress_alert=not changes,
            alert_details=tuple(changes),
        )

    def _account_item(self, orgnr: str, company_name: str, account: dict[str, Any]) -> Item:
        report_id = int(account["id"])
        period_to = str(account.get("period_to") or "")
        year = period_to[:4] if len(period_to) >= 4 and period_to[:4].isdigit() else ""
        url = (
            ANNUAL_REPORT_URL.format(orgnr=orgnr, year=year)
            if year
            else ACCOUNTS_URL.format(orgnr=orgnr)
        )
        return Item(
            source_id=self.config.id,
            key=f"annual:{orgnr}:{report_id}",
            title=f"Nytt årsregnskap: {company_name}",
            url=url,
            published=period_to or None,
            text=(
                f"{company_name}\n{orgnr}\nNytt årsregnskap\n"
                f"Periode til: {period_to or 'ukjent'}\nBRREG-ID: {report_id}"
            ),
            metadata={
                "orgnr": orgnr,
                "event": "annual_accounts",
                "report_id": report_id,
            },
            alert_details=(
                f"Periode til: {period_to or 'ukjent'}",
                f"BRREG-ID: {report_id}",
            ),
        )


def _diff_entity(previous: Any, current: dict[str, Any]) -> list[str]:
    old = previous if isinstance(previous, dict) else {}
    if not old:
        return []
    changes: list[str] = []
    if old.get("name") and current.get("name") and old.get("name") != current.get("name"):
        changes.append(f"Navn: {old['name']} → {current['name']}")
    for key, label in (
        ("bankrupt", "Konkurs"),
        ("liquidating", "Avvikling"),
        ("forced_liquidation", "Tvangsavvikling/tvangsoppløsning"),
        ("deleted", "Slettet"),
        ("removed", "Fjernet fra BRREG Åpne Data"),
    ):
        if bool(old.get(key)) != bool(current.get(key)):
            changes.append(f"{label}: {'ja' if current.get(key) else 'nei'}")
    if old.get("organisation_form") and old.get("organisation_form") != current.get("organisation_form"):
        changes.append(
            f"Organisasjonsform: {_coded_display(old.get('organisation_form'))} → "
            f"{_coded_display(current.get('organisation_form'))}"
        )
    if old.get("industry") and old.get("industry") != current.get("industry"):
        changes.append(
            f"Næringskode: {_coded_display(old.get('industry'))} → "
            f"{_coded_display(current.get('industry'))}"
        )
    return changes


def _diff_roles(previous: Any, current: dict[str, list[str]]) -> list[str]:
    old = previous if isinstance(previous, dict) else {}
    if not old:
        return []
    changes: list[str] = []
    for code, label in ROLE_CODES.items():
        old_people = set(str(v) for v in old.get(code, []) if v)
        new_people = set(str(v) for v in current.get(code, []) if v)
        removed = sorted(old_people - new_people, key=str.casefold)
        added = sorted(new_people - old_people, key=str.casefold)
        if len(removed) == 1 and len(added) == 1:
            changes.append(f"{label}: {removed[0]} → {added[0]}")
        else:
            if removed:
                changes.append(f"{label} ut: {', '.join(removed)}")
            if added:
                changes.append(f"{label} inn: {', '.join(added)}")
    return changes


def _role_holder(role: dict[str, Any]) -> str | None:
    person = role.get("person")
    if isinstance(person, dict):
        name = person.get("navn")
        if isinstance(name, dict):
            parts = [
                _clean(name.get("fornavn")),
                _clean(name.get("mellomnavn")),
                _clean(name.get("etternavn")),
            ]
            value = " ".join(part for part in parts if part)
            return value or None
    entity = role.get("enhet")
    if isinstance(entity, dict):
        name = entity.get("navn")
        value = (
            " ".join(str(part).strip() for part in name if str(part).strip())
            if isinstance(name, list)
            else _clean(name)
        )
        orgnr = _clean(entity.get("organisasjonsnummer"))
        if value and orgnr:
            return f"{value} ({orgnr})"
        return value or orgnr
    return None


def _coded(value: Any) -> dict[str, str | None]:
    if not isinstance(value, dict):
        return {"code": None, "description": None}
    return {
        "code": _clean(value.get("kode")),
        "description": _clean(value.get("beskrivelse")),
    }


def _coded_display(value: Any) -> str:
    if not isinstance(value, dict):
        return "ukjent"
    code = value.get("code") or "ukjent"
    description = value.get("description")
    return f"{code} ({description})" if description else str(code)


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
