"""Selected industrial installations from Miljødirektoratet's public ArcGIS layer."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, urlencode, urlparse

from .changes import SnapshotSource, document, integer
from .common import SourceError

QUERY_URL = "https://kart3.miljodirektoratet.no/arcgis/rest/services/industri/MapServer/0/query"
FIELDS = ("anlegg_id", "navn", "driftsstatus", "forurensningsmyndighet",
          "siste_rapportering_aar", "har_utslipp_luft", "har_utslipp_vann",
          "har_krav_til_overvaking", "faktaark")
FIELD_TYPES = {"anlegg_id": "esriFieldTypeInteger", "navn": "esriFieldTypeString",
               "driftsstatus": "esriFieldTypeString", "forurensningsmyndighet": "esriFieldTypeString",
               "siste_rapportering_aar": "esriFieldTypeInteger",
               "har_utslipp_luft": "esriFieldTypeSmallInteger",
               "har_utslipp_vann": "esriFieldTypeSmallInteger",
               "har_krav_til_overvaking": "esriFieldTypeSmallInteger", "faktaark": "esriFieldTypeString"}
STATUSES = {"Aktiv", "Etterdrift", "Nedlagt"}


class IndustrialEnvironmentSource(SnapshotSource):
    """Monitor status and reporting metadata for explicitly selected installations."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls:
            raise ValueError("industrial_environment uses a fixed official ArcGIS endpoint")
        if self.complete or "removed" in self.events:
            raise ValueError("Selected ArcGIS records cannot confirm removals")
        selected = config.options.get("installation_ids")
        if (not isinstance(selected, list) or not selected or len(selected) > 50
                or any(type(value) is not int or value <= 0 for value in selected)
                or len(selected) != len(set(selected))):
            raise ValueError("installation_ids must be 1–50 unique positive integers")
        self.installation_ids = tuple(sorted(selected))
        self.max_records = integer(config.options.get("max_records", 50), "max_records", 1, 50)
        if len(self.installation_ids) > self.max_records:
            raise ValueError("installation_ids exceeds max_records")
        self.allow_empty = False
        self.field_labels = {"name": "Anlegg", "status": "Driftsstatus",
                             "authority": "Forurensningsmyndighet",
                             "reporting_year": "Siste rapporteringsår",
                             "emissions_air": "Har rapporterte utslipp til luft",
                             "emissions_water": "Har rapporterte utslipp til vann",
                             "monitoring_required": "Har krav til overvåking", **self.field_labels}

    def read_records(self):
        ids = ",".join(str(value) for value in self.installation_ids)
        query = {"where": f"anlegg_id IN ({ids})", "outFields": ",".join(FIELDS),
                 "returnGeometry": "false", "resultRecordCount": len(self.installation_ids), "f": "json"}
        try:
            payload = json.loads(document(self, QUERY_URL + "?" + urlencode(query)))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("Miljødirektoratet returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("exceededTransferLimit") not in (None, False):
            raise SourceError("Miljødirektoratet ArcGIS result is incomplete")
        features, schema = payload.get("features"), payload.get("fields")
        if (not isinstance(features, list) or any(not isinstance(feature, dict) for feature in features)
                or not isinstance(schema, list)):
            raise SourceError("Miljødirektoratet ArcGIS schema changed")
        observed_schema = {}
        for field in schema:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                raise SourceError("Miljødirektoratet ArcGIS field schema is invalid")
            if field["name"] in observed_schema:
                raise SourceError("Miljødirektoratet ArcGIS repeated a field definition")
            observed_schema[field["name"]] = field.get("type")
        if observed_schema != FIELD_TYPES:
            raise SourceError("Miljødirektoratet ArcGIS selected fields changed")
        rows = [_record(feature) for feature in features]
        if {row["key"] for row in rows} != {str(value) for value in self.installation_ids} or len(rows) != len(self.installation_ids):
            raise SourceError("Miljødirektoratet did not return exactly the selected installations")
        return rows

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            details = ("Nytt observert industrianlegg", f"Anlegg: {fields['name']}",
                       f"Driftsstatus: {fields['status']}",
                       f"Forurensningsmyndighet: {fields['authority']}",
                       f"Siste rapporteringsår i kilden: {fields['reporting_year']}")
        else:
            details = ("Endret drifts- eller rapporteringsmetadata", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _record(feature):
    attributes = feature.get("attributes")
    if not isinstance(attributes, dict) or set(attributes) != set(FIELDS):
        raise SourceError("Miljødirektoratet installation attributes changed")
    identifier = attributes["anlegg_id"]
    if type(identifier) is not int or identifier <= 0:
        raise SourceError("Miljødirektoratet installation ID is invalid")
    name = _text(attributes["navn"], "name", 300)
    status = _text(attributes["driftsstatus"], "status", 50)
    if status not in STATUSES:
        raise SourceError("Miljødirektoratet installation status is unknown")
    authority = _text(attributes["forurensningsmyndighet"], "authority", 100)
    year = attributes["siste_rapportering_aar"]
    if type(year) is not int or not 1900 <= year <= datetime.now(timezone.utc).year:
        raise SourceError("Miljødirektoratet reporting year is invalid")
    flags = []
    for key in ("har_utslipp_luft", "har_utslipp_vann", "har_krav_til_overvaking"):
        value = attributes[key]
        if value not in (None, 0, 1) or type(value) not in (int, type(None)):
            raise SourceError(f"Miljødirektoratet {key} flag is invalid")
        flags.append(None if value is None else bool(value))
    facts = _facts_url(attributes["faktaark"], identifier)
    fields = {"name": name, "status": status, "authority": authority, "reporting_year": year,
              "emissions_air": flags[0], "emissions_water": flags[1], "monitoring_required": flags[2]}
    return {"key": str(identifier), "title": f"{name} · Norske utslipp", "url": facts,
            "published": None, "fields": fields}


def _text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SourceError(f"Miljødirektoratet installation {label} is invalid")
    return value.strip()


def _facts_url(value, identifier):
    if not isinstance(value, str):
        raise SourceError("Norske utslipp facts link is invalid")
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.hostname != "www.norskeutslipp.no"
            or parsed.path != "/Templates/NorskeUtslipp/Pages/company.aspx"
            or query != {"CompanyID": [str(identifier)]} or parsed.fragment):
        raise SourceError("Norske utslipp facts link does not match the installation ID")
    return value
