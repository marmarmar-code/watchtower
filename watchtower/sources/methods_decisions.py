"""National specialist-health-service decisions from Nye metoder's current XLSX."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup

from .changes import SnapshotSource, document, integer
from .common import SourceError

PAGE_URL = "https://www.nyemetoder.no/innforing-av-nye-metoder/beslutning/"
METHODS_URL = "https://www.nyemetoder.no/metoder/"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HEADERS = ("ID-nr", "Virkestoff", "Handelsenavn", "Bruksområde (indikasjon)", "Metodetype",
           "Fagområde", "Dato siste beslutning", "Gjeldende beslutning Kortversjon",
           "Gjeldende beslutning (Hele)")
ID = re.compile(r"\d{4}_\d{3}(?: ?[A-Z])?")
METHOD_TYPES = {"Legemidler", "Medisinsk utstyr, diagnostikk og tester",
                "Prosedyrer og organisatoriske tiltak"}


class MethodsDecisionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (PAGE_URL,):
            raise ValueError("methods_decisions accepts only Nye metoder's official decision page")
        if self.complete or "removed" in self.events:
            raise ValueError("The published decision overview cannot confirm removals")
        self.max_records = integer(config.options.get("max_records", 1500), "max_records", 1, 3000)
        self.max_unpacked_bytes = integer(config.options.get("max_unpacked_bytes", 3000000),
                                          "max_unpacked_bytes", 1024, 10000000)
        self.allow_empty = False
        self.field_labels = {"substance": "Virkestoff", "trade_name": "Handelsnavn",
                             "indication": "Bruksområde", "method_type": "Metodetype",
                             "field": "Fagområde", "decision_date": "Beslutningsdato",
                             "decision_short": "Gjeldende beslutning", "decision_text": "Beslutningstekst",
                             **self.field_labels}

    def read_records(self):
        page = BeautifulSoup(document(self, PAGE_URL), "html.parser")
        links = []
        for anchor in page.select('a[href]'):
            href = anchor.get("href")
            if isinstance(href, str) and urlparse(href).path.lower().endswith(".xlsx"):
                links.append(urljoin(PAGE_URL, href))
        links = list(dict.fromkeys(links))
        if len(links) != 1:
            raise SourceError("Nye metoder decision page lacks one unambiguous XLSX link")
        parsed = urlparse(links[0])
        if parsed.scheme != "https" or parsed.hostname != "www.nyemetoder.no" or parsed.query or parsed.fragment:
            raise SourceError("Nye metoder XLSX link leaves the official host")
        rows = _xlsx_rows(document(self, links[0]), self.max_unpacked_bytes, self.max_records + 100)
        if not rows or tuple(rows[0]) != HEADERS:
            raise SourceError("Nye metoder workbook header changed")
        grouped = {}
        for values in rows[1:]:
            record = _row(dict(zip(HEADERS, values)))
            identity = record.pop("identity")
            existing = grouped.get(identity)
            if existing is None:
                grouped[identity] = record
            else:
                before = existing["fields"]
                after = record["fields"]
                if {k: v for k, v in before.items() if k != "decision_text"} != {
                        k: v for k, v in after.items() if k != "decision_text"}:
                    raise SourceError("Nye metoder repeats an identity with conflicting metadata")
                texts = sorted(set(before["decision_text"].split("\n\n---\n\n")
                                   + after["decision_text"].split("\n\n---\n\n")))
                before["decision_text"] = "\n\n---\n\n".join(texts)
        if not grouped or len(grouped) > self.max_records:
            raise SourceError("Nye metoder decision selection is empty or exceeds max_records")
        return list(grouped.values())

    def describe_change(self, name, before, after):
        if name in {"indication", "decision_text"}:
            return f"{self.field_labels[name]} endret: {_excerpt(before)} → {_excerpt(after)}"
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            details = ("Nyobservert beslutning fra Nye metoder", f"Metode: {row['method_id']}",
                       f"Handelsnavn: {fields['trade_name'] or 'ikke oppgitt'}",
                       f"Bruksområde: {_excerpt(fields['indication']) or 'ikke oppgitt'}",
                       f"Beslutning: {fields['decision_short']}",
                       f"Beslutningsdato: {fields['decision_date'] or 'ikke oppgitt'}")
        else:
            details = ("Endret beslutning fra Nye metoder", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _row(row):
    method_id = row["ID-nr"].strip()
    if not ID.fullmatch(method_id):
        raise SourceError("Nye metoder row lacks a stable method ID")
    indication = _text(row["Bruksområde (indikasjon)"], "indication", 3000)
    method_type = _text(row["Metodetype"], "method type", 100, required=True)
    if method_type not in METHOD_TYPES:
        raise SourceError("Nye metoder row has an unknown method type")
    decision_short = _text(row["Gjeldende beslutning Kortversjon"], "short decision", 200, required=True)
    full = _text(row["Gjeldende beslutning (Hele)"], "decision text", 6000, required=True)
    decision_date = _excel_date(row["Dato siste beslutning"])
    fields = {"substance": _text(row["Virkestoff"], "substance", 200),
              "trade_name": _text(row["Handelsenavn"], "trade name", 100),
              "indication": indication, "method_type": method_type,
              "field": _text(row["Fagområde"], "field", 100),
              "decision_date": decision_date.isoformat() if decision_date else "",
              "decision_short": decision_short, "decision_text": full}
    normalized = " ".join(indication.split()).casefold()
    suffix = sha256(normalized.encode()).hexdigest()
    return {"identity": (method_id, normalized), "key": f"{method_id}:{suffix}",
            "method_id": method_id, "title": f"Nye metoder {method_id} · {row['Handelsenavn'] or row['Virkestoff']}",
            "url": METHODS_URL, "published": None, "fields": fields}


def _text(value, label, limit, required=False):
    if not isinstance(value, str):
        raise SourceError(f"Nye metoder {label} has invalid type")
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise SourceError(f"Nye metoder {label} exceeds bounds or is absent")
    return value


def _excel_date(value, date1904=False):
    if value in ("", "0"):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.0+)?", value):
        raise SourceError("Nye metoder decision date is not an Excel serial")
    serial = int(float(value))
    epoch = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    result = epoch + timedelta(days=serial)
    if result < date(2013, 1, 1) or result > date.today():
        raise SourceError("Nye metoder decision date is outside valid bounds")
    return result


def _xlsx_rows(raw, max_unpacked, max_rows):
    try:
        with ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if (len(infos) > 40 or len({i.filename for i in infos}) != len(infos)
                    or any(i.flag_bits & 1 or i.file_size > max_unpacked for i in infos)
                    or sum(i.file_size for i in infos) > max_unpacked):
                raise SourceError("Nye metoder XLSX exceeds safe bounds")
            names = {i.filename for i in infos}
            required = {"xl/workbook.xml", "xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"}
            if not required <= names:
                raise SourceError("Nye metoder XLSX structure changed")
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            prop = workbook.find(NS + "workbookPr")
            date1904 = prop is not None and prop.get("date1904") in {"1", "true"}
            shared = ["".join(t.text or "" for t in item.iter(NS + "t"))
                      for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall(NS + "si")]
            rows = _sheet(archive.read("xl/worksheets/sheet1.xml"), shared, max_rows)
            for values in rows[1:]:
                parsed = _excel_date(values[6], date1904)
                values[6] = "" if parsed is None else str((parsed - date(1899, 12, 30)).days)
            return rows
    except (BadZipFile, ET.ParseError, KeyError, ValueError, IndexError) as exc:
        raise SourceError("Nye metoder returned an invalid XLSX") from exc


def _sheet(raw, shared, max_rows):
    output = []
    for row in ET.fromstring(raw).findall(".//" + NS + "sheetData/" + NS + "row"):
        if len(output) >= max_rows:
            raise SourceError("Nye metoder worksheet exceeds row bounds")
        values, seen = [""] * len(HEADERS), set()
        for cell in row.findall(NS + "c"):
            if cell.find(NS + "f") is not None:
                raise SourceError("Nye metoder workbook contains formulas")
            index = _column(cell.get("r", ""))
            if index in seen:
                raise SourceError("Nye metoder workbook repeats a cell")
            seen.add(index)
            if index >= len(HEADERS):
                continue
            value = cell.find(NS + "v")
            text = "" if value is None else value.text or ""
            if cell.get("t") == "s":
                if not text.isdigit() or int(text) >= len(shared):
                    raise SourceError("Nye metoder shared-string index is invalid")
                text = shared[int(text)]
            if len(text) > 7000:
                raise SourceError("Nye metoder cell exceeds text bounds")
            values[index] = text.strip()
        output.append(values)
    return output


def _column(reference):
    match = re.fullmatch(r"([A-Z]+)\d+", reference)
    if not match:
        raise SourceError("Nye metoder cell reference is invalid")
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - 64
    return value - 1


def _excerpt(value):
    value = " ".join(value.split())
    return value if len(value) <= 240 else value[:237] + "…"
