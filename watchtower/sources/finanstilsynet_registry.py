"""Scoped snapshots of active permissions in Finanstilsynet's Registry API v2."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from ..models import Item
from .common import Source, SourceError
from .identifiers import valid_orgnr

API = "https://api.finanstilsynet.no/registry/v2/legal-entities"
STATE_KEY = "finanstilsynet_registry"


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted((_canonical(row) for row in value), key=_json)
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _label(value):
    if not isinstance(value, dict):
        return ""
    return str(value.get("norwegian") or value.get("english") or "")


def _licence(value):
    if not isinstance(value, dict):
        raise SourceError("Registry returned an invalid licence")
    holder, kind = value.get("licensedEntity"), value.get("licenceType")
    if (not isinstance(holder, dict) or not _int(holder.get("legalEntityId"))
            or not isinstance(kind, dict) or not isinstance(kind.get("code"), str)
            or not kind["code"] or not isinstance(value.get("serviceProviderType"), str)):
        raise SourceError("Registry returned an incomplete licence identity")
    required = ("licenceClassification", "registeredDate", "services", "remarks", "hasSecurity")
    if any(field not in value for field in required):
        raise SourceError("Registry omitted a monitored licence field")
    services = value["services"]
    if services is not None and (not isinstance(services, list) or any(
        not isinstance(row, dict) or not _int(row.get("serviceId")) for row in services
    )):
        raise SourceError("Registry returned invalid services")
    return _canonical({
        "holder": {"id": holder["legalEntityId"], "name": holder.get("legalEntityName")},
        "code": kind["code"], "name": kind.get("name"),
        "role": value["serviceProviderType"],
        **{field: value[field] for field in required if field != "services"},
        "services": None if services is None else [
            {field: row.get(field) for field in (
                "serviceId", "serviceCode", "serviceName", "serviceInstruments"
            )} for row in services
        ],
    })


def _key(licence):
    return _json([licence["code"], licence["holder"]["id"], licence["role"]])


def _description(licence):
    name = _label(licence["name"]) or licence["code"]
    holder = licence["holder"]["name"] or str(licence["holder"]["id"])
    return f"{name}; rolle: {licence['role']}; innehaver: {holder}"


def _value(value):
    if value is None:
        return "ikke oppgitt"
    if isinstance(value, bool):
        return "ja" if value else "nei"
    if isinstance(value, dict) and ("norwegian" in value or "english" in value):
        return _label(value) or "tom"
    return str(value) if isinstance(value, str) else _json(value)


def _service_names(services):
    return ", ".join(
        _label(row["serviceName"]) or str(row["serviceId"]) for row in services
    ) if services else "ingen oppgitt"


def _snapshot(entity):
    if not isinstance(entity.get("name"), str) or not entity["name"]:
        raise SourceError("Registry entity lacks a name")
    # null is not treated as an empty list: uncertain coverage must not look like
    # every active permission disappeared.
    if not isinstance(entity.get("licences"), list) or "remarks" not in entity:
        raise SourceError("Registry entity lacks a complete licence list")
    licences = [_licence(value) for value in entity["licences"]]
    if len({_key(row) for row in licences}) != len(licences):
        raise SourceError("Registry returned ambiguous duplicate licences")
    return _canonical({
        "present": True, "id": entity["legalEntityId"], "name": entity["name"],
        "orgnr": entity["organisationNumber"], "licences": licences,
        "remarks": entity["remarks"],
    })


def _diff(old, new):
    if not old:
        return []
    if not new["present"] and old["present"]:
        return ["Virksomheten ble ikke funnet i et fullført registersøk. Dette dokumenterer ikke at en tillatelse er tilbakekalt."]
    if new["present"] and not old["present"]:
        return ["Virksomheten er nå funnet i registeret."]
    if not new["present"]:
        return []
    details = []
    if new["id"] != old["id"]:
        details.append(f"Register-ID endret: {old['id']} → {new['id']}")
    if new["name"] != old["name"]:
        details.append(f"Navn: {old['name']} → {new['name']}")
    if new["remarks"] != old["remarks"]:
        details.append("Registerets merknad om virksomheten er endret.")
    before, after = ({_key(row): row for row in snapshot["licences"]} for snapshot in (old, new))
    for key in sorted(before.keys() | after.keys()):
        if key not in before:
            details.append("Ny i aktiv tillatelsesliste: " + _description(after[key]))
        elif key not in after:
            details.append("Ikke lenger i aktiv tillatelsesliste: " + _description(before[key]) + ". Kontroller årsaken hos kilden.")
        elif before[key] != after[key]:
            fields = [field for field in after[key] if after[key][field] != before[key][field]]
            labels = {"services": "tjenester/instrumenter", "hasSecurity": "sikkerhetsstillelse",
                      "remarks": "merknader", "registeredDate": "registreringsdato",
                      "licenceClassification": "klassifisering", "holder": "innehavernavn", "name": "navn"}
            changed = ", ".join(labels.get(field, field) for field in fields)
            details.append(f"Endret {changed}: {_description(after[key])}")
            for field in fields:
                if field in {"hasSecurity", "remarks", "registeredDate"}:
                    details.append(f"{labels[field]}: {_value(before[key][field])} → {_value(after[key][field])}")
                elif field == "services":
                    details.append(f"Tjenester før: {_service_names(before[key][field])}; nå: {_service_names(after[key][field])}. Instrumentdetaljer finnes i registersvaret.")
    return details


class FinanstilsynetRegistrySource(Source):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls:
            raise ValueError("Registry uses the official API; urls must be empty")
        companies = config.options.get("companies", [])
        if (not isinstance(companies, list) or not 1 <= len(companies) <= 100
                or any(not isinstance(org, str) or not valid_orgnr(org) for org in companies)):
            raise ValueError("Registry requires 1–100 valid organisation numbers in companies")
        self.companies = tuple(dict.fromkeys(companies))
        self.max_pages = config.options.get("max_pages", 3)
        if not _int(self.max_pages) or not 1 <= self.max_pages <= 20:
            raise ValueError("Registry max_pages must be an integer from 1 to 20")
        self._snapshots = {}

    def _company(self, orgnr):
        total, rows, ids = None, [], set()
        for page in range(1, self.max_pages + 1):
            try:
                payload = self.get(API + "/filter", params={
                    "query": orgnr, "pageSize": 50, "page": page,
                }, headers={"Accept": "application/json"}).json()
            except ValueError as exc:
                raise SourceError("Registry returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise SourceError("Registry returned an unexpected document")
            batch = payload.get("legalEntities")
            count = payload.get("total")
            if (not isinstance(batch, list) or not _int(count) or count < 0
                    or not _int(payload.get("page")) or payload["page"] != page
                    or not _int(payload.get("hitsReturned")) or payload["hitsReturned"] != len(batch)
                    or len(batch) > 50 or (total is not None and count != total)):
                raise SourceError("Registry returned inconsistent pagination")
            total = count
            for row in batch:
                if (not isinstance(row, dict) or not _int(row.get("legalEntityId"))
                        or row["legalEntityId"] in ids or "organisationNumber" not in row):
                    raise SourceError("Registry returned incomplete or duplicate entities")
                ids.add(row["legalEntityId"])
                rows.append(row)
            if len(rows) > total:
                raise SourceError("Registry returned more entities than its total")
            if len(rows) == total:
                matches = [row for row in rows if row["organisationNumber"] == orgnr]
                if len(matches) > 1:
                    raise SourceError("Registry organisation number matched multiple entities")
                return _snapshot(matches[0]) if matches else {"present": False, "orgnr": orgnr}
            if not batch:
                raise SourceError("Registry returned an empty page before completion")
        raise SourceError("Registry result exceeds max_pages; previous state is preserved")

    def fetch(self):
        return self.fetch_with_state(None)

    def fetch_with_state(self, previous):
        old = ((previous or {}).get("source_state") or {}).get(STATE_KEY, {})
        if not isinstance(old, dict):
            raise SourceError("Invalid private registry snapshots")
        snapshots, items = {}, []
        for orgnr in self.companies:
            snapshot = self._company(orgnr)
            snapshots[orgnr] = snapshot
            before = old.get(orgnr)
            details = _diff(before, snapshot)
            name = snapshot.get("name") or (before or {}).get("name") or orgnr
            url = (API + "?" + urlencode({"ids": snapshot["id"]}) if snapshot["present"]
                   else API + "/filter?" + urlencode({"query": orgnr, "pageSize": 50, "page": 1}))
            items.append(Item(
                self.config.id, f"org:{orgnr}", f"Virksomhetsregister: {name}", url,
                text=_json(snapshot), metadata={"orgnr": orgnr},
                fingerprint=_json(snapshot), alert_details=tuple(details),
                # Adding a selection to an existing source establishes its own
                # baseline; historical permissions must not appear as new grants.
                suppress_alert=before is None,
            ))
        self._snapshots = snapshots
        return items

    def augment_state(self, state):
        return {**state, "source_state": {
            **state.get("source_state", {}), STATE_KEY: self._snapshots,
        }}
