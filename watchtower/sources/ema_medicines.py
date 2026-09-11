"""Human medicine product data from EMA's official bounded XLSX export."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from io import BytesIO
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .changes import SnapshotSource, document, integer
from .common import SourceError

DOWNLOAD_URL = "https://www.ema.europa.eu/en/medicines/download-medicine-data"
XLSX_URL = "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines-report_en.xlsx"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HEADERS = (
    "Category", "Name of medicine", "EMA product number", "Medicine status", "Opinion status",
    "Latest procedure affecting product information", "International non-proprietary name (INN) / common name",
    "Active substance", "Therapeutic area (MeSH)", "Species\n(veterinary)", "Patient safety",
    "ATC code (human)", "ATCvet code (veterinary)", "Pharmacotherapeutic group\n(human)",
    "Pharmacotherapeutic group\n(veterinary)", "Therapeutic indication", "Accelerated assessment",
    "Additional monitoring", "Advanced therapy", "Biosimilar", "Conditional approval",
    "Exceptional circumstances", "Generic", "Orphan medicine", "PRIME: priority medicine",
    "Marketing authorisation developer / applicant / holder", "European Commission decision date",
    "Start of rolling review date", "Start of evaluation date", "Opinion adopted date",
    "Withdrawal of application date", "Marketing authorisation date",
    "Refusal of marketing authorisation date",
    "Withdrawal / expiry / revocation / lapse of marketing authorisation date",
    "Suspension of marketing authorisation date", "Revision number", "First published date",
    "Last updated date", "Medicine URL",
)
STATUSES = {"Authorised": "Godkjent", "Withdrawn": "Tilbaketrukket",
            "Application withdrawn": "Søknad trukket", "Refused": "Avslått", "Lapsed": "Utløpt",
            "Opinion": "Uttalelse", "Expired": "Utgått", "Revoked": "Tilbakekalt",
            "Opinion under re-examination": "Uttalelse under ny vurdering",
            "Withdrawn from rolling review": "Trukket fra løpende vurdering", "Suspended": "Suspendert"}
OPINIONS = {"": "", "Positive": "Positiv", "Negative": "Negativ"}
PRODUCT_ID = re.compile(r"EMEA/H/[A-Z]/\d{6}")


class EmaMedicinesSource(SnapshotSource):
    """Monitor substantive changes to EMA human-medicine product records."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (XLSX_URL,):
            raise ValueError("ema_medicines accepts only EMA's official XLSX URL")
        if self.complete or "removed" in self.events:
            raise ValueError("EMA adapter does not claim product removals")
        self.max_records = integer(config.options.get("max_records", 3000), "max_records", 1, 5000)
        self.max_unpacked_bytes = integer(config.options.get("max_unpacked_bytes", 8000000),
                                          "max_unpacked_bytes", 1024, 20000000)
        self.allow_empty = False
        self.field_labels = {"name": "Legemiddel", "medicine_status": "Produktstatus",
                             "opinion_status": "Uttalelsesstatus", "inn": "INN/fellesnavn",
                             "active_substance": "Virkestoff", "therapeutic_area": "Terapiområde",
                             "indication": "Indikasjon", "mah": "Innehaver/søker",
                             "authorisation_date": "Markedsføringstillatelse", **self.field_labels}

    def read_records(self):
        rows = _workbook_rows(document(self, XLSX_URL), self.max_unpacked_bytes, 5000)
        if not rows or tuple(rows[0]) != HEADERS:
            raise SourceError("EMA workbook header changed")
        records, seen = [], set()
        for values in rows[1:]:
            if len(values) != len(HEADERS):
                raise SourceError("EMA workbook row width changed")
            if values[0] not in {"Human", "Veterinary"}:
                raise SourceError("EMA workbook has an unknown product category")
            if values[0] != "Human":
                continue
            row = _record(dict(zip(HEADERS, values)))
            if row["key"] in seen:
                raise SourceError("EMA workbook repeated a product number")
            seen.add(row["key"])
            records.append(row)
            if len(records) > self.max_records:
                raise SourceError("EMA human-medicine export exceeds max_records")
        if not records:
            raise SourceError("EMA workbook contains no human medicines")
        return records

    def describe_change(self, name, before, after):
        if name == "indication":
            return f"Indikasjon endret: {_excerpt(before)} → {_excerpt(after)}"
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            details = ("Nytt observert EMA-produkt", f"Legemiddel: {fields['name']}",
                       f"Produktstatus: {fields['medicine_status']}",
                       f"Virkestoff: {fields['active_substance'] or 'ikke oppgitt'}",
                       f"Innehaver/søker: {fields['mah'] or 'ikke oppgitt'}")
        else:
            details = ("Endret EMA-produkt", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _workbook_rows(raw, max_unpacked_bytes, max_rows=5000):
    try:
        with ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if (len(infos) > 50 or any(info.flag_bits & 1 or info.file_size > max_unpacked_bytes for info in infos)
                    or sum(info.file_size for info in infos) > max_unpacked_bytes
                    or len({info.filename for info in infos}) != len(infos)):
                raise SourceError("EMA workbook exceeds safe XLSX bounds")
            names = {info.filename for info in infos}
            if not {"xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"} <= names:
                raise SourceError("EMA workbook structure changed")
            shared = _shared_strings(archive.read("xl/sharedStrings.xml"))
            return _sheet_rows(archive.read("xl/worksheets/sheet1.xml"), shared, max_rows)
    except (BadZipFile, ET.ParseError, KeyError, ValueError, IndexError) as exc:
        raise SourceError("EMA returned an invalid XLSX workbook") from exc


def _shared_strings(raw):
    root = ET.fromstring(raw)
    return ["".join(node.text or "" for node in item.iter(NS + "t"))
            for item in root.findall(NS + "si")]


def _sheet_rows(raw, shared, max_rows):
    root = ET.fromstring(raw)
    output = []
    for row in root.findall(".//" + NS + "sheetData/" + NS + "row"):
        number = int(row.get("r", "0"))
        if number < 9:
            continue  # Workbook metadata, including generation time, is deliberately ignored.
        if len(output) >= max_rows:
            raise SourceError("EMA workbook exceeds the worksheet row bound")
        values = [""] * len(HEADERS)
        seen_cells = set()
        for cell in row.findall(NS + "c"):
            if cell.find(NS + "f") is not None:
                raise SourceError("EMA workbook contains formulas")
            index = _column(cell.get("r", ""))
            if index in seen_cells:
                raise SourceError("EMA workbook repeats a cell reference")
            seen_cells.add(index)
            if index >= len(HEADERS):
                continue
            if cell.get("t") == "s":
                value = cell.find(NS + "v")
                if value is None or value.text is None or not value.text.isdigit():
                    raise SourceError("EMA workbook has an invalid shared-string index")
                shared_index = int(value.text)
                if shared_index >= len(shared):
                    raise SourceError("EMA workbook has an invalid shared-string index")
                text = shared[shared_index]
            elif cell.get("t") == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(NS + "t"))
            else:
                value = cell.find(NS + "v")
                text = "" if value is None else value.text or ""
            if not isinstance(text, str) or len(text) > 12000:
                raise SourceError("EMA workbook cell exceeds text bounds")
            values[index] = text.strip()
        output.append(values)
    return output


def _column(reference):
    match = re.fullmatch(r"([A-Z]+)\d+", reference)
    if not match:
        raise SourceError("EMA workbook has an invalid cell reference")
    result = 0
    for character in match.group(1):
        result = result * 26 + ord(character) - 64
    return result - 1


def _record(row):
    product_id = row["EMA product number"]
    if not PRODUCT_ID.fullmatch(product_id):
        raise SourceError("EMA human medicine lacks a stable product number")
    name, status, opinion = row["Name of medicine"], row["Medicine status"], row["Opinion status"]
    if not name or len(name) > 300 or status not in STATUSES or opinion not in OPINIONS:
        raise SourceError("EMA medicine has invalid name or status")
    limits = {"International non-proprietary name (INN) / common name": 500,
              "Active substance": 1000, "Therapeutic area (MeSH)": 500,
              "Therapeutic indication": 10000,
              "Marketing authorisation developer / applicant / holder": 500}
    for field, limit in limits.items():
        if len(row[field]) > limit:
            raise SourceError(f"EMA medicine has invalid {field}")
    authorisation = _optional_date(row["Marketing authorisation date"], "authorisation date")
    first_published = _optional_date(row["First published date"], "first-published date")
    url = row["Medicine URL"]
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != "www.ema.europa.eu"
            or not parsed.path.startswith("/en/medicines/human/EPAR/") or parsed.query or parsed.fragment):
        raise SourceError("EMA medicine has an invalid public URL")
    fields = {"name": name, "medicine_status": STATUSES[status], "opinion_status": OPINIONS[opinion],
              "inn": row["International non-proprietary name (INN) / common name"],
              "active_substance": row["Active substance"],
              "therapeutic_area": row["Therapeutic area (MeSH)"],
              "indication": row["Therapeutic indication"],
              "mah": row["Marketing authorisation developer / applicant / holder"],
              "authorisation_date": authorisation.isoformat() if authorisation else ""}
    return {"key": product_id, "title": f"{name} · EMA", "url": url,
            "published": first_published.isoformat() if first_published else None, "fields": fields}


def _optional_date(value, label):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        raise SourceError(f"EMA {label} is invalid") from None


def _excerpt(value):
    value = " ".join(value.split())
    return value if len(value) <= 240 else value[:237] + "…"
