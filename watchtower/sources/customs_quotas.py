"""Bounded official customs quotas with explicit remaining-capacity bands."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
import re

from .changes import SnapshotSource, document, integer
from .common import SourceError

DATA_URL = "https://data.toll.no/dataset/c78ae30b-230f-4680-9e31-4a76a37f6c51/resource/fab81ea1-7f2f-4110-8700-cf203edfc3fe/download/tollkvote.json"
DATASET_URL = "https://data.toll.no/no/dataset/tollkvote"
UNITS = {"K": "Kg", "V": "Verdi"}
STATES = {"available": "Over grensen for lav restkvote", "low": "Lav restkvote", "exhausted": "Oppbrukt eller overtrukket"}


class CustomsQuotasSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (DATA_URL,)):
            raise ValueError("customs_quotas accepts only the official quota resource")
        if self.complete or "removed" in self.events:
            raise ValueError("Customs quota snapshots cannot confirm removals")
        self.low_percent = integer(config.options.get("low_remaining_percent", 10), "low_remaining_percent", 1, 50)
        self.field_labels = {"capacity": "Maksimal kvote", "unit": "Kildens enhet", "country_group": "Landgruppekode",
                             "commodity_codes": "Varenumre", "valid_from": "Gyldig fra", "valid_to": "Gyldig til",
                             "usage_state": "Beregnet reststatus", **self.field_labels}

    def read_records(self):
        try:
            payload = json.loads(document(self, DATA_URL))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("Customs quota response is invalid JSON") from exc
        if (not isinstance(payload, dict) or set(payload) != {"versjon", "tollkvoter"}
                or payload["versjon"] != "1.0" or not isinstance(payload["tollkvoter"], list)):
            raise SourceError("Customs quota envelope changed")
        raw = payload["tollkvoter"]
        if not raw or len(raw) > self.max_records:
            raise SourceError("Customs quota response is empty or exceeds max_records")
        rows, seen = [], set()
        for value in raw:
            row = _record(value, self.low_percent)
            if row["key"] in seen:
                raise SourceError("Customs quota identifiers repeat")
            seen.add(row["key"])
            rows.append(row)
        return sorted(rows, key=lambda row: row["key"])

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields, observed = row["fields"], row["observed"]
        head = "Nyobservert tollkvote" if event == "added" else "Endret tollkvote"
        changes = details[1:] if event != "added" else (f"Maksimal kvote: {fields['capacity']}",
                   "Varenumre: " + ", ".join(fields["commodity_codes"]), "Beregnet reststatus: " + STATES[fields["usage_state"]])
        info = (head, *changes, f"Avskrevet: {observed['used']} · Beregnet rest: {observed['remaining']} · Enhet: {fields['unit']}",
                "Beregnet rest = maksimal kvote minus kildens avskrevne mengde",
                f"Lav restkvote betyr høyst {self.low_percent} % av maksimal kvote",
                f"Gyldig: {fields['valid_from']}–{fields['valid_to']}",
                "Registerstatus; ingen garanti for at en konkret import får kvote")
        if fields['unit'] == 'Verdi':
            info += ("Kilden oppgir enheten Verdi uten valuta",)
        return replace(item, alert_details=info)

    def describe_change(self, name, before, after):
        if name == "usage_state":
            return f"Beregnet reststatus: {STATES[before]} → {STATES[after]}"
        return super().describe_change(name, before, after)


def _amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,16}(?:\.[0-9]{1,6})?", value):
        raise SourceError("Customs quota amount is invalid")
    return Decimal(value)


def _shown(value):
    return format(value.normalize(), "f")


def _record(value, low_percent):
    required = {"dokumentnummer", "landgruppekode", "maksMengde", "avskrevetMengde", "mengdeenhet",
                "mengdeenhetBeskrivelse", "varenumre", "fomdato", "tomdato"}
    if not isinstance(value, dict) or not required <= set(value):
        raise SourceError("Customs quota record schema changed")
    ident, group = value["dokumentnummer"], value["landgruppekode"]
    if not isinstance(ident, str) or not re.fullmatch(r"T[0-9]{6}[A-Z]", ident):
        raise SourceError("Customs quota document number is invalid")
    if not isinstance(group, str) or not re.fullmatch(r"[A-Z0-9]{1,12}", group):
        raise SourceError("Customs quota country group is invalid")
    unit = value["mengdeenhet"]
    if not isinstance(unit, str) or unit not in UNITS or value["mengdeenhetBeskrivelse"] != UNITS[unit]:
        raise SourceError("Customs quota unit is unknown or inconsistent")
    codes = value["varenumre"]
    if (not isinstance(codes, list) or not codes or len(codes) > 500
            or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{8}", v) for v in codes)
            or len(codes) != len(set(codes))):
        raise SourceError("Customs quota commodity codes are invalid")
    first, last = value["fomdato"], value["tomdato"]
    try:
        if any(not isinstance(v, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v) for v in (first, last)):
            raise ValueError
        if date.fromisoformat(first) > date.fromisoformat(last):
            raise ValueError
    except ValueError:
        raise SourceError("Customs quota validity dates are invalid") from None
    capacity, used = _amount(value["maksMengde"]), _amount(value["avskrevetMengde"])
    if capacity <= 0:
        raise SourceError("Customs quota capacity must be positive")
    remaining = capacity - used
    state = "exhausted" if remaining <= 0 else "low" if remaining * 100 <= capacity * low_percent else "available"
    fields = {"capacity": _shown(capacity), "unit": UNITS[unit], "country_group": group,
              "commodity_codes": sorted(codes), "valid_from": first, "valid_to": last, "usage_state": state}
    return {"key": ident, "title": f"Tollkvote {ident} · {group}", "url": DATASET_URL,
            "published": None, "fields": fields, "observed": {"used": _shown(used), "remaining": _shown(remaining)}}
