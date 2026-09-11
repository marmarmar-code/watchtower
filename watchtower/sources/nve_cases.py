"""Bounded, explicitly selected NVE concession cases from its public UI API."""
from dataclasses import replace
from datetime import datetime
import json
import math
from urllib.parse import urlencode
from .changes import SnapshotSource, document, integer
from .common import SourceError

DEFAULT_URL = "https://www.nve.no/konsesjon/konsesjonssaker/"
DEFAULT_API_URL = "https://www.nve.no/umbraco/api/license/getall"
TYPES = {"0", "V-1", "V-9", "V-19", "A-1", "A-6", "A-7", "A-5", "A-8", "A-9"}
FIELDS = ("Tittel", "Tiltakshaver", "Fylke", "Kommune", "Stadium", "Status", "Dato",
          "Sakstype", "Hoeringsfrist", "MW", "GWh")
LABELS = {"Tittel": "Prosjekt", "Tiltakshaver": "Tiltakshaver", "Fylke": "Fylke",
          "Kommune": "Kommune", "Stadium": "Saksstadium", "Status": "Status",
          "Dato": "Saksdato (NVE)", "Sakstype": "Sakstype", "Hoeringsfrist": "Høringsfrist",
          "MW": "Planlagt effekt (MW)", "GWh": "Estimert produksjon (GWh)", "Paaklaget": "Påklaget"}


class NveCasesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if self.complete or "removed" in self.events:
            raise ValueError("NVE selection cannot prove removal; removed is unsupported")
        if config.urls and config.urls != (DEFAULT_URL,):
            raise ValueError("nve_cases uses the official NVE concession API")
        o = config.options
        self.page_size = integer(o.get("page_size", 1000), "page_size", 1, 1000)
        license_type = o.get("type", "A-8")
        if license_type not in TYPES:
            raise ValueError("Unknown NVE concession type")
        self.license_type = license_type
        self.params = {"type": license_type, "pageNumber": 1, "pageSize": self.page_size,
                       "sortBy": "name", "sortDir": "ASC"}
        for key, api, default in (("filter_text", "filterText", ""), ("case_type", "caseType", "00"),
                                  ("county", "county", "00"), ("municipality", "municipality", "00")):
            value = o.get(key, default)
            if not isinstance(value, str) or len(value) > 300:
                raise ValueError(f"{key} must be bounded text")
            self.params[api] = value
        self.params["stadium"] = integer(o.get("stadium", 0), "stadium", 0, 30)
        if self.params["stadium"] not in {0, 10, 20, 30}:
            raise ValueError("stadium must be 0, 10, 20 or 30")
        self.params["size"] = integer(o.get("size", 0), "size", 0, 8)
        self.params["progress"] = integer(o.get("progress", 0), "progress", 0, 100)
        self.field_labels = {**LABELS, **self.field_labels}

    def read_records(self):
        try:
            payload = json.loads(document(self, DEFAULT_API_URL + "?" + urlencode(self.params)))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("NVE returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("Licenses"), list):
            raise SourceError("NVE response lacks Licenses array")
        rows, total = payload["Licenses"], payload.get("TotalCount")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise SourceError("NVE response lacks a valid total")
        if len(rows) > self.max_records or total > self.page_size:
            raise SourceError("NVE exceeds max_records/page_size; narrow the selection")
        if len(rows) != total:
            raise SourceError("NVE returned an incomplete selection; previous state preserved")
        records = []
        for row in rows:
            if not isinstance(row, dict) or any(key not in row for key in (*FIELDS, "SoknadId", "Type")):
                raise SourceError("NVE case is missing monitored fields")
            key = row["SoknadId"]
            if isinstance(key, bool) or not isinstance(key, int) or key <= 0:
                raise SourceError("NVE case lacks a stable numeric identity")
            if not isinstance(row["Type"], str) or not row["Type"]:
                raise SourceError("NVE case lacks its concession type")
            if self.license_type != "0" and self.license_type not in row["Type"].split(","):
                raise SourceError("NVE case is outside the requested concession type")
            for name in (*FIELDS, "Paaklaget"):
                value = row.get(name)
                if isinstance(value, (dict, list, bool)) or (value is not None and not isinstance(value, (str, int, float))):
                    raise SourceError("NVE case contains invalid field values")
            if not isinstance(row["Tittel"], str) or not row["Tittel"].strip():
                raise SourceError("NVE case lacks its project title")
            for name in ("Dato", "Hoeringsfrist"):
                value = row[name]
                if value not in (None, ""):
                    try:
                        if not isinstance(value, str):
                            raise ValueError
                        datetime.fromisoformat(value)
                    except ValueError:
                        raise SourceError("NVE case date is invalid") from None
            for name in ("MW", "GWh"):
                value = row[name]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise SourceError("NVE project value is not a finite number")
            values = {name: row[name].strip() if isinstance(row[name], str) else row[name] for name in FIELDS}
            if values["Hoeringsfrist"] == "0001-01-01T00:00:00":
                values["Hoeringsfrist"] = None
            values["Paaklaget"] = row.get("Paaklaget")
            records.append({"key": str(key), "title": values["Tittel"],
                "url": DEFAULT_URL + "konsesjonssak?" + urlencode({"id": key, "type": row["Type"]}),
                "published": None, "fields": values})
        return records

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        label = "Ny konsesjonssak i utvalget" if event == "added" else "Endret konsesjonssak"
        return replace(item, alert_details=(label, *item.alert_details[1:]))
