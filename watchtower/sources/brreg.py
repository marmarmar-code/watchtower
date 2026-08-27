from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..models import Item
from .common import Source, SourceError
from .identifiers import valid_orgnr


ENTITY_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"
ROLES_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller"
ACCOUNTS_URL = "https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}"
GROUP_URL = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{orgnr}"
UPDATES_URL = "https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter"
ANNUAL_REPORT_URL = (
    "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{orgnr}/{year}"
)

ROLE_CODES = {
    "DAGL": "Daglig leder",
    "LEDE": "Styreleder",
    "NEST": "Nestleder",
    "MEDL": "Styremedlem",
}
UPDATE_FIELD_LABELS = {
    "aktivitet": "Aktivitet",
    "antallAnsatte": "Antall ansatte",
    "forretningsadresse": "Forretningsadresse",
    "konkurs": "Konkurs",
    "navn": "Navn",
    "naeringskode1": "Næringskode",
    "organisasjonsform": "Organisasjonsform",
    "slettedato": "Slettedato",
    "stiftelsesdato": "Stiftelsesdato",
    "underAvvikling": "Under avvikling",
    "underTvangsavviklingEllerTvangsopplosning": "Under tvangsavvikling",
    "vedtektsfestetFormaal": "Vedtektsfestet formål",
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
            if not valid_orgnr(orgnr):
                raise ValueError(
                    "BRREG companies must contain valid 9-digit organisation numbers"
                )
            companies.append(orgnr)
        self.companies = tuple(dict.fromkeys(companies))

        raw_events = config.options.get("events", ["annual_accounts", "company", "roles"])
        if not isinstance(raw_events, list):
            raise ValueError("BRREG events must be an array")
        allowed = {
            "annual_accounts",
            "company",
            "roles",
            "group_structure",
            "registry_updates",
        }
        events = {str(value).strip() for value in raw_events}
        unknown = events - allowed
        if unknown:
            raise ValueError(f"unsupported BRREG events: {', '.join(sorted(unknown))}")
        self.events = events
        self.max_group_relations = _bounded_int(
            config.options.get("max_group_relations", 1000),
            "max_group_relations",
            1,
            5000,
        )
        self.registry_update_limit = _bounded_int(
            config.options.get("registry_update_limit", 100),
            "registry_update_limit",
            1,
            1000,
        )
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

            if "group_structure" in self.events:
                group = self._group_structure(orgnr)
                current["group_structure"] = group
                items.append(
                    self._group_item(
                        orgnr,
                        company_name,
                        old.get("group_structure"),
                        group,
                    )
                )

            if "registry_updates" in self.events:
                update_state = old.get("registry_updates")
                updates = self._registry_updates(orgnr)
                initialized = isinstance(update_state, dict)
                previous_newest = (
                    update_state.get("newest_id") if initialized else None
                )
                update_ids = [int(update["id"]) for update in updates]
                if (
                    previous_newest is not None
                    and update_ids
                    and int(previous_newest) != update_ids[0]
                    and int(previous_newest) not in update_ids
                ):
                    raise SourceError(
                        "BRREG registry update window no longer contains the previous cursor"
                    )
                current["registry_updates"] = {
                    "newest_id": update_ids[0] if update_ids else previous_newest,
                }
                items.extend(
                    self._registry_update_item(
                        orgnr,
                        company_name,
                        update,
                        suppress_alert=not initialized,
                    )
                    for update in updates
                )

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
        if response.status_code == 404:
            raise SourceError("BRREG entity was not found")
        if response.status_code == 410:
            return {
                "name": None,
                "organisation_form": {"code": None, "description": None},
                "industry": {"code": None, "description": None},
                "bankrupt": False,
                "liquidating": False,
                "forced_liquidation": False,
                "deleted": False,
                "removed": True,
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

    def _group_structure(self, orgnr: str) -> list[dict[str, Any]]:
        response = self.get(GROUP_URL.format(orgnr=orgnr), accepted_statuses=(404,))
        if response.status_code == 404:
            return []
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("BRREG group response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise SourceError("BRREG group response had unexpected shape")
        children = payload.get("children", [])
        if not isinstance(children, list):
            raise SourceError("BRREG group response had invalid children")
        relations: list[dict[str, Any]] = []
        _flatten_group(children, relations, self.max_group_relations)
        relations.sort(key=_group_relation_key)
        return relations

    def _registry_updates(self, orgnr: str) -> list[dict[str, Any]]:
        response = self.get(
            UPDATES_URL,
            params={
                "organisasjonsnummer": orgnr,
                "includeChanges": "true",
                "page": 0,
                "size": self.registry_update_limit,
                "sort": "id,DESC",
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("BRREG registry update response was not valid JSON") from exc
        embedded = payload.get("_embedded") if isinstance(payload, dict) else None
        rows = embedded.get("oppdaterteEnheter") if isinstance(embedded, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SourceError("BRREG registry update response had invalid rows")
        updates = [_normalize_registry_update(row, orgnr) for row in rows]
        updates.sort(key=lambda row: int(row["id"]), reverse=True)
        return updates

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

    def _group_item(
        self,
        orgnr: str,
        company_name: str,
        previous: Any,
        current: list[dict[str, Any]],
    ) -> Item:
        changes = _diff_group(previous, current)
        summary = f"{len(current)} konsernforhold"
        text = [company_name, orgnr, summary, *changes[:20]]
        return Item(
            source_id=self.config.id,
            key=f"group:{orgnr}",
            title=(
                f"Konsernendring: {company_name}"
                if changes
                else f"Konsernstruktur: {company_name}"
            ),
            url=GROUP_URL.format(orgnr=orgnr),
            text="\n".join(text),
            metadata={"orgnr": orgnr, "event": "group_structure"},
            fingerprint=_digest(current),
            suppress_alert=not changes,
            alert_details=tuple(changes[:8]),
        )

    def _registry_update_item(
        self,
        orgnr: str,
        company_name: str,
        update: dict[str, Any],
        *,
        suppress_alert: bool,
    ) -> Item:
        details = tuple(_format_registry_change(change) for change in update["changes"])
        visible = list(details[:20])
        if len(details) > 20:
            visible.append(f"I tillegg: {len(details) - 20} felt")
        return Item(
            source_id=self.config.id,
            key=f"registry-update:{orgnr}:{update['id']}",
            title=f"Registerendring: {company_name}",
            url=ENTITY_URL.format(orgnr=orgnr),
            published=update["date"],
            text="\n".join(
                [company_name, orgnr, update["change_type"], *visible]
            ),
            metadata={
                "orgnr": orgnr,
                "event": "registry_updates",
                "update_id": update["id"],
            },
            fingerprint=_digest(update),
            suppress_alert=suppress_alert,
            alert_details=details[:8],
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

    old_form = old.get("organisation_form")
    new_form = current.get("organisation_form")
    if (
        isinstance(old_form, dict)
        and isinstance(new_form, dict)
        and old_form.get("code")
        and new_form.get("code")
        and old_form != new_form
    ):
        changes.append(
            f"Organisasjonsform: {_coded_display(old_form)} → {_coded_display(new_form)}"
        )

    old_industry = old.get("industry")
    new_industry = current.get("industry")
    if (
        isinstance(old_industry, dict)
        and isinstance(new_industry, dict)
        and old_industry.get("code")
        and new_industry.get("code")
        and old_industry != new_industry
    ):
        changes.append(
            f"Næringskode: {_coded_display(old_industry)} → {_coded_display(new_industry)}"
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


def _flatten_group(
    rows: list[Any],
    result: list[dict[str, Any]],
    maximum: int,
) -> None:
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("BRREG group response contained an invalid relation")
        if len(result) >= maximum:
            raise SourceError("BRREG group structure exceeded the configured safety limit")
        child_orgnr = _clean(row.get("organisasjonsnummer"))
        parent_orgnr = _clean(row.get("parentOrganisasjonsnummer"))
        if not child_orgnr or not parent_orgnr:
            raise SourceError("BRREG group relation lacked a stable organisation number")
        connection = row.get("knytningsform")
        form = row.get("organisasjonsform")
        relation = {
            "child_orgnr": child_orgnr,
            "child_name": _clean(row.get("navn")),
            "parent_orgnr": parent_orgnr,
            "parent_name": _clean(row.get("parentNavn")),
            "connection": _coded(connection),
            "basis": _clean(row.get("grunnlag")),
            "date": _clean(row.get("dato")),
            "organisation_form": _coded(form),
            "level": row.get("nivaa") if isinstance(row.get("nivaa"), int) else None,
        }
        result.append(relation)
        children = row.get("children", [])
        if not isinstance(children, list):
            raise SourceError("BRREG group relation had invalid children")
        _flatten_group(children, result, maximum)


def _group_relation_key(row: dict[str, Any]) -> tuple[str, str, str]:
    connection = row.get("connection")
    code = connection.get("code") if isinstance(connection, dict) else ""
    return (
        str(row.get("parent_orgnr") or ""),
        str(row.get("child_orgnr") or ""),
        str(code or ""),
    )


def _diff_group(previous: Any, current: list[dict[str, Any]]) -> list[str]:
    if not isinstance(previous, list):
        return []
    old = {_group_relation_key(row): row for row in previous if isinstance(row, dict)}
    new = {_group_relation_key(row): row for row in current}
    changes: list[str] = []
    for key in sorted(old.keys() - new.keys()):
        relation = old[key]
        changes.append(f"Ut av konsernet: {_group_display(relation)}")
    for key in sorted(new.keys() - old.keys()):
        relation = new[key]
        changes.append(f"Inn i konsernet: {_group_display(relation)}")
    for key in sorted(old.keys() & new.keys()):
        if old[key] != new[key]:
            changes.append(f"Konsernforhold endret: {_group_display(new[key])}")
    return changes


def _group_display(row: dict[str, Any]) -> str:
    child = row.get("child_name") or row.get("child_orgnr") or "ukjent"
    child_orgnr = row.get("child_orgnr")
    parent = row.get("parent_name") or row.get("parent_orgnr") or "ukjent"
    basis = row.get("basis")
    value = f"{child} ({child_orgnr}) under {parent}" if child_orgnr else f"{child} under {parent}"
    return f"{value}, {basis}" if basis else value


def _normalize_registry_update(row: dict[str, Any], orgnr: str) -> dict[str, Any]:
    update_id = row.get("oppdateringsid")
    if isinstance(update_id, bool) or not isinstance(update_id, int):
        raise SourceError("BRREG registry update lacked a stable id")
    if row.get("organisasjonsnummer") != orgnr:
        raise SourceError("BRREG registry update had unexpected identity")
    changed_at = _clean(row.get("dato"))
    change_type = _clean(row.get("endringstype"))
    changes = row.get("endringer")
    if not changed_at or not change_type or not isinstance(changes, list):
        raise SourceError("BRREG registry update was incomplete")
    normalized_changes: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            raise SourceError("BRREG registry update contained an invalid field change")
        operation = _clean(change.get("op"))
        path = _clean(change.get("path"))
        if not operation or not path:
            raise SourceError("BRREG registry field change was incomplete")
        try:
            json.dumps(change.get("value"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SourceError("BRREG registry field value was not serializable") from exc
        normalized_changes.append(
            {"operation": operation, "path": path, "value": change.get("value")}
        )
    return {
        "id": update_id,
        "date": changed_at,
        "change_type": change_type,
        "changes": normalized_changes,
    }


def _format_registry_change(change: dict[str, Any]) -> str:
    field = str(change.get("path") or "").strip("/").split("/")[-1]
    label = UPDATE_FIELD_LABELS.get(field) or _humanize_field(field)
    operation = change.get("operation")
    if operation == "remove":
        return f"{label}: fjernet"
    value = _display_value(change.get("value"))
    if operation in {"add", "replace"}:
        return f"{label}: {value}"
    return f"{label} ({operation}): {value}"


def _humanize_field(value: str) -> str:
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", value).replace("_", " ").strip()
    return spaced[:1].upper() + spaced[1:] if spaced else "Felt"


def _display_value(value: Any) -> str:
    if isinstance(value, bool):
        rendered = "ja" if value else "nei"
    elif isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif value is None:
        rendered = "ikke oppgitt"
    else:
        rendered = " ".join(str(value).split())
    return rendered[:220]


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


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"BRREG {field} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"BRREG {field} must be an integer") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"BRREG {field} must be between {minimum} and {maximum}")
    return number


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
