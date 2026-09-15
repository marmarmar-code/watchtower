"""One-site public Fiskeridirektoratet aquaculture-register lookup."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import math
import re
from urllib.parse import urlencode

from .changes import SnapshotSource
from .changes import document
from .common import SourceError

SITES = "https://api.fiskeridir.no/pub-aqua/api/v1/sites"


class AquacultureSource(SnapshotSource):
    """Monitor one explicitly selected aquaculture site."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if self.complete or "removed" in self.events:
            raise ValueError("aquaculture site lookup cannot confirm removals")
        if config.options.get("mode", "sites") != "sites":
            raise ValueError("licenses mode is not approved; use sites")
        if config.urls and config.urls != (SITES,):
            raise ValueError("aquaculture uses the official sites endpoint")
        number = config.options.get("nr")
        if not isinstance(number, str) or not re.fullmatch(r"\d+", number):
            raise ValueError("sites mode requires one numeric nr")
        self.number = number
        self.endpoint = SITES
        self.field_labels = {
            "name": "Lokalitetsnavn", "capacity": "Kapasitet",
            "temporary_capacity": "Midlertidig kapasitet", "unit": "Kapasitetsenhet",
            "water": "Vannmiljø", "production_area": "Produksjonsområde",
            "municipality": "Kommune", "county": "Fylke",
            "first_clearance": "Først klarert i registeret", **self.field_labels,
        }

    def read_records(self):
        url = self.endpoint + "?" + urlencode({"nr": self.number, "range": "0-9"})
        payload = _json(document(self, url))
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise SourceError("sites response is invalid")
        if len(payload) != 1:
            raise SourceError("site number did not resolve to exactly one record")
        record = _site(payload[0])
        if record["key"] != self.number:
            raise SourceError("sites response escaped the requested number")
        return [record]

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        label = "Ny akvakulturlokalitet" if event == "added" else "Endret akvakulturlokalitet"
        return replace(item, alert_details=(label, *item.alert_details[1:]))


def _site(row):
    required = {"siteNr", "name", "capacity", "tempCapacity", "capacityUnitType",
                "waterTypeValue", "firstClearanceTime", "placement"}
    if not required <= set(row):
        raise SourceError("site row lacks monitored fields")
    number = row["siteNr"]
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise SourceError("site row lacks stable siteNr")
    name, unit, water, placement = row["name"], row["capacityUnitType"], row["waterTypeValue"], row["placement"]
    if any(not isinstance(value, str) or not value.strip() for value in (name, unit, water)):
        raise SourceError("site descriptive fields are invalid")
    if not isinstance(placement, dict):
        raise SourceError("site placement is invalid")
    for field in ("capacity", "tempCapacity"):
        value = row[field]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or value < 0):
            raise SourceError("site capacity is invalid")
    _datetime(row["firstClearanceTime"])
    for field in ("prodAreaName", "municipalityName", "countyName"):
        if field not in placement or (placement[field] is not None and not isinstance(placement[field], str)):
            raise SourceError("site placement is invalid")
    fields = {
        "name": name.strip(), "capacity": row["capacity"], "temporary_capacity": row["tempCapacity"],
        "unit": unit, "water": water, "production_area": placement["prodAreaName"],
        "municipality": placement["municipalityName"], "county": placement["countyName"],
        "first_clearance": row["firstClearanceTime"],
    }
    return {"key": str(number), "title": f"Akvakulturlokalitet {number} · {name.strip()}",
            "url": f"{SITES}/{number}", "published": None, "fields": fields}


def _datetime(value):
    try:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SourceError("site firstClearanceTime is invalid") from None


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise SourceError("aquaculture response is invalid JSON") from exc
