"""DMP maximum-price reassessments for specific medicine packages."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup

from .changes import SnapshotSource, document, integer
from .common import SourceError

PAGE_URL = "https://www.dmp.no/offentlig-finansiering/pris-pa-legemidler/maksimalpris"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HEADERS = ("Varenummer", "Handelsnavn", "MT-innehaver", "ATC-kode (pakning)",
           "ATC-navn (pakning)", "Legemiddelform", "Styrke", "Multippel",
           "Antall beholdere", "Mengde per beholder", "Måle-enhet", "Maks AIP Gyldig",
           "Maks AUP Gyldig", "Maks AIP Vedtatt", "Maks AUP Vedtatt",
           "Maks AIP Forhåndsvarslet", "Maks AUP Forhåndsvarslet", "Markedsføringsstatus")
LINK = re.compile(r"Oversikt over revurderte priser gyldig fra (\d{2}\.\d{2}\.\d{4})", re.I)
TITLE = re.compile(r"Revurderte priser (\d{1,2})\. (\S+) (\d{4})", re.I)
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "des": 12}
STATUSES = {"Markedsført", "Kan prisbehandles", "Midlertidig utgått", "Avregistrert"}


class DmpPricesSource(SnapshotSource):
    """Monitor the latest complete reassessment workbook linked by DMP."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (PAGE_URL,):
            raise ValueError("dmp_prices accepts only DMP's official maximum-price page")
        if self.complete or "removed" in self.events:
            raise ValueError("DMP's rotating reassessment workbook cannot confirm removals")
        self.max_records = integer(config.options.get("max_records", 1000), "max_records", 1, 3000)
        self.max_unpacked_bytes = integer(config.options.get("max_unpacked_bytes", 1000000),
                                          "max_unpacked_bytes", 1024, 10000000)
        self.allow_empty = False
        self.field_labels = {
            "package": "Pakning", "marketing_status": "Markedsføringsstatus",
            "valid_from": "Gyldig fra", "current_aip": "Gjeldende maksimal AIP (NOK)",
            "current_aup": "Gjeldende maksimal AUP (NOK)", "price_status": "Prisstatus",
            "next_aip": "Ny maksimal AIP (NOK)", "next_aup": "Ny maksimal AUP (NOK)",
            **self.field_labels,
        }

    def read_records(self):
        page = BeautifulSoup(document(self, PAGE_URL), "html.parser")
        candidates = []
        for anchor in page.select("a[href]"):
            match = LINK.search(" ".join(anchor.get_text(" ", strip=True).split()))
            if match:
                href = anchor.get("href")
                if not isinstance(href, str):
                    raise SourceError("DMP reassessment link is invalid")
                candidates.append((_date(match.group(1), "%d.%m.%Y", "link validity date"),
                                   urljoin(PAGE_URL, href)))
        if not candidates:
            raise SourceError("DMP maximum-price page lacks a reassessment workbook")
        valid_from = max(value[0] for value in candidates)
        links = {url for value, url in candidates if value == valid_from}
        if len(links) != 1:
            raise SourceError("DMP maximum-price page has ambiguous latest workbooks")
        workbook_url = links.pop()
        parsed = urlparse(workbook_url)
        if (parsed.scheme != "https" or parsed.netloc != "www.dmp.no" or parsed.query
                or parsed.fragment or not parsed.path.lower().endswith(".xlsx")):
            raise SourceError("DMP workbook link leaves the official host")
        rows, title = _xlsx(document(self, workbook_url), self.max_unpacked_bytes,
                            self.max_records + 4)
        if _title_date(title) != valid_from or not rows or tuple(rows[0]) != HEADERS:
            raise SourceError("DMP workbook title, validity date, or header changed")
        records, seen = [], set()
        for values in rows[1:]:
            record = _record(dict(zip(HEADERS, values)), valid_from)
            if record["key"] in seen:
                raise SourceError("DMP workbook repeats or ambiguously identifies a package")
            seen.add(record["key"])
            records.append(record)
        if not records or len(records) > self.max_records:
            raise SourceError("DMP workbook is empty or exceeds max_records")
        return records

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            details = ("Nyobservert revurdering av maksimalpris",
                       f"Pakning: {fields['package']}", f"Prisstatus: {fields['price_status']}",
                       f"Maksimal AIP: {fields['current_aip']} → {fields['next_aip']} NOK",
                       f"Maksimal AUP: {fields['current_aup']} → {fields['next_aup']} NOK",
                       f"Gyldig fra: {fields['valid_from']}")
        else:
            details = ("Endret revurdering av maksimalpris",
                       f"Prisstatus: {fields['price_status']}",
                       f"Gyldig fra: {fields['valid_from']}", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _record(row, valid_from):
    name = _text(row["Handelsnavn"], "trade name", 200, True)
    holder = _text(row["MT-innehaver"], "marketing-authorisation holder", 300, True)
    form = _text(row["Legemiddelform"], "pharmaceutical form", 300, True)
    strength = _text(row["Styrke"], "strength", 100, True)
    amount = _text(row["Mengde per beholder"], "amount per container", 100, True)
    unit = _text(row["Måle-enhet"], "unit", 100, True)
    status = row["Markedsføringsstatus"]
    if status not in STATUSES:
        raise SourceError("DMP package has an unknown marketing status")
    current_aip = _money(row["Maks AIP Gyldig"], "current AIP", positive=True)
    current_aup = _money(row["Maks AUP Gyldig"], "current AUP", positive=True)
    decided = (_money(row["Maks AIP Vedtatt"], "decided AIP"),
               _money(row["Maks AUP Vedtatt"], "decided AUP"))
    announced = (_money(row["Maks AIP Forhåndsvarslet"], "advance-notice AIP"),
                 _money(row["Maks AUP Forhåndsvarslet"], "advance-notice AUP"))
    decided_active = all(value != "0.00" for value in decided)
    announced_active = all(value != "0.00" for value in announced)
    if ((decided[0] == "0.00") != (decided[1] == "0.00")
            or (announced[0] == "0.00") != (announced[1] == "0.00")
            or decided_active == announced_active):
        raise SourceError("DMP package lacks exactly one complete next-price pair")
    price_status, next_prices = (("Vedtatt", decided) if decided_active
                                 else ("Forhåndsvarslet", announced))
    product_number = row["Varenummer"].strip()
    if product_number:
        # The current official sheet contains legacy numeric identifiers from
        # three through six digits; text cells retain any source-provided zero.
        if not re.fullmatch(r"\d{3,6}", product_number):
            raise SourceError("DMP package has an invalid product number")
        key = "varenummer:" + product_number
    else:
        fallback_names = ("Handelsnavn", "MT-innehaver", "ATC-kode (pakning)", "Legemiddelform",
                          "Styrke", "Multippel", "Antall beholdere", "Mengde per beholder", "Måle-enhet")
        fallback = tuple(_text(row[value], value, 300, value in {
            "Handelsnavn", "MT-innehaver", "ATC-kode (pakning)", "Legemiddelform", "Styrke",
            "Mengde per beholder", "Måle-enhet"}) for value in fallback_names)
        key = "pakning:" + "\x1f".join(fallback)
    containers = " × ".join(value for value in (row["Antall beholdere"].strip(), amount, unit) if value)
    package = " · ".join(value for value in (name, strength, form, containers) if value)
    fields = {"package": package, "marketing_status": status,
              "valid_from": valid_from.isoformat(), "current_aip": current_aip,
              "current_aup": current_aup, "price_status": price_status,
              "next_aip": next_prices[0], "next_aup": next_prices[1]}
    return {"key": key, "title": f"{name} · {strength} · maksimalpris",
            "url": PAGE_URL, "published": None, "fields": fields}


def _money(value, label, positive=False):
    if not isinstance(value, str):
        raise SourceError(f"DMP {label} has an invalid type")
    value = value.strip() or "0"
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise SourceError(f"DMP {label} is not a non-negative NOK amount")
    try:
        raw = Decimal(value)
        rounded = raw.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise SourceError(f"DMP {label} is invalid") from None
    if abs(raw - rounded) > Decimal("0.000000001") or (positive and rounded <= 0):
        raise SourceError(f"DMP {label} has invalid precision or value")
    return f"{rounded:.2f}"


def _text(value, label, limit, required=False):
    if not isinstance(value, str):
        raise SourceError(f"DMP {label} has invalid type")
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise SourceError(f"DMP {label} is absent or exceeds bounds")
    return value


def _date(value, pattern, label):
    try:
        result = datetime.strptime(value, pattern).date()
    except (TypeError, ValueError):
        raise SourceError(f"DMP {label} is invalid") from None
    if not date(2020, 1, 1) <= result <= date.today() + timedelta(days=730):
        raise SourceError(f"DMP {label} is outside valid bounds")
    return result


def _title_date(value):
    match = TITLE.fullmatch(value.strip()) if isinstance(value, str) else None
    month = MONTHS.get(match.group(2).lower()[:3]) if match else None
    try:
        result = date(int(match.group(3)), month, int(match.group(1)))
    except (AttributeError, TypeError, ValueError):
        raise SourceError("DMP workbook title date is invalid") from None
    return result


def _xlsx(raw, max_unpacked, max_rows):
    try:
        with ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if (len(infos) > 40 or len({info.filename for info in infos}) != len(infos)
                    or any(info.flag_bits & 1 or info.file_size > max_unpacked
                           or info.filename.startswith("/") or ".." in info.filename.split("/")
                           for info in infos)
                    or sum(info.file_size for info in infos) > max_unpacked):
                raise SourceError("DMP XLSX exceeds safe archive bounds")
            names = {info.filename for info in infos}
            worksheets = {name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml")}
            required = {"xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"}
            if not required <= names or worksheets != {"xl/worksheets/sheet1.xml"}:
                raise SourceError("DMP XLSX structure changed")
            shared = ["".join(node.text or "" for node in item.iter(NS + "t"))
                      for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall(NS + "si")]
            return _sheet(archive.read("xl/worksheets/sheet1.xml"), shared, max_rows)
    except (BadZipFile, ET.ParseError, KeyError, ValueError, IndexError) as exc:
        raise SourceError("DMP returned an invalid XLSX") from exc


def _sheet(raw, shared, max_rows):
    parsed = {}
    for row in ET.fromstring(raw).findall(".//" + NS + "sheetData/" + NS + "row"):
        try:
            number = int(row.get("r", "0"))
        except ValueError:
            raise SourceError("DMP worksheet row number is invalid") from None
        if number in parsed or number > max_rows + 4:
            raise SourceError("DMP worksheet exceeds row bounds or repeats a row")
        values, seen = [""] * len(HEADERS), set()
        for cell in row.findall(NS + "c"):
            if cell.find(NS + "f") is not None:
                raise SourceError("DMP workbook contains formulas")
            index = _column(cell.get("r", ""), number)
            if index in seen:
                raise SourceError("DMP workbook repeats a cell reference")
            seen.add(index)
            value = cell.find(NS + "v")
            text = "" if value is None else value.text or ""
            if cell.get("t") == "s":
                if not text.isdigit() or int(text) >= len(shared):
                    raise SourceError("DMP workbook has an invalid shared-string index")
                text = shared[int(text)]
            elif cell.get("t") == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(NS + "t"))
            if index >= len(HEADERS):
                if text.strip():
                    raise SourceError("DMP workbook has unexpected populated columns")
                continue
            if len(text) > 2000:
                raise SourceError("DMP workbook cell exceeds text bounds")
            values[index] = text.strip()
        parsed[number] = values
    if 1 not in parsed or 4 not in parsed:
        raise SourceError("DMP workbook lacks title or header")
    data_numbers = sorted(number for number in parsed if number >= 5)
    if not data_numbers or data_numbers != list(range(5, data_numbers[-1] + 1)):
        raise SourceError("DMP workbook data rows are empty or incomplete")
    return [parsed[4], *(parsed[number] for number in data_numbers)], parsed[1][0]


def _column(reference, expected_row):
    match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
    if not match or int(match.group(2)) != expected_row:
        raise SourceError("DMP workbook cell reference is invalid")
    result = 0
    for character in match.group(1):
        result = result * 26 + ord(character) - 64
    return result - 1
