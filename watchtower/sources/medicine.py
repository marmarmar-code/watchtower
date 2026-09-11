"""Read-only adapter for DMP's public medicine-shortage overview."""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, document, integer, public_url
from .common import SourceError

DEFAULT_URL = "https://www.dmp.no/forsyningssikkerhet/legemiddelmangel/oversikt-over-legemiddelmangel---for-pasienter-og-helsepersonell"
IDENTITY = ("Legemiddelnavn", "Virkestoff(er)")
PERIOD_FIELDS = ("Mangelperiode fra", "Mangelperiode til", "Status", "Informasjon/tiltak", "Informasjon på nettside")
FIXED_COLUMNS = IDENTITY + PERIOD_FIELDS[:2] + PERIOD_FIELDS[3:]
STATUS_COLUMN = re.compile(r"^Status pr\. \d{2}\.\d{2}\.\d{4}$")


class MedicineSource(SnapshotSource):
    """Monitor DMP shortage periods grouped by medicine and active ingredient."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if len(config.urls) > 1:
            raise ValueError("Medicine source accepts at most one URL")
        if self.complete or "removed" in self.events:
            raise ValueError("DMP medicine snapshots cannot confirm removals")
        if {"id_fields", "fields", "column_aliases"} & set(config.options):
            raise ValueError("DMP medicine fields and identity are fixed")
        self.url = public_url(config.urls[0] if config.urls else DEFAULT_URL)
        self.max_records = integer(config.options.get("max_records", 5000), "max_records", 1, 10000)

    def _payload(self):
        soup = BeautifulSoup(document(self, self.url), "html.parser")
        node = soup.select_one("input#excelData")
        if node is None or not isinstance(node.get("value"), str):
            raise SourceError("DMP response lacks excelData")
        try:
            payload = json.loads(node["value"])
        except (TypeError, ValueError):
            raise SourceError("DMP excelData is invalid JSON") from None
        if not isinstance(payload, dict) or any(not isinstance(k, str) or not isinstance(v, list) for k, v in payload.items()):
            raise SourceError("DMP excelData must contain named columns")
        status_columns = [name for name in payload if STATUS_COLUMN.fullmatch(name)]
        if len(status_columns) != 1 or set(payload) != set(FIXED_COLUMNS) | set(status_columns):
            raise SourceError("DMP excelData column schema changed")
        lengths = {len(values) for values in payload.values()}
        if len(lengths) != 1 or next(iter(lengths), 0) == 0:
            raise SourceError("DMP excelData columns have unequal or empty lengths")
        if any(type(value) is not str for values in payload.values() for value in values):
            raise SourceError("DMP excelData cells must be text")
        status = status_columns[0]
        names = (*FIXED_COLUMNS, status)
        return [dict(zip(names, values)) for values in zip(*(payload[name] for name in names))]

    def read_records(self):
        groups, seen = {}, set()
        for row in self._payload():
            name, ingredient = row[IDENTITY[0]].strip(), row[IDENTITY[1]].strip()
            if not name or not ingredient:
                raise SourceError("DMP shortage record lacks stable identity")
            status_name = next(key for key in row if STATUS_COLUMN.fullmatch(key))
            period = {
                "Mangelperiode fra": row["Mangelperiode fra"].strip(),
                "Mangelperiode til": row["Mangelperiode til"].strip(),
                "Status": row[status_name].strip(),
                "Informasjon/tiltak": row["Informasjon/tiltak"].strip(),
                "Informasjon på nettside": row["Informasjon på nettside"].strip(),
            }
            if not period["Status"]:
                raise SourceError("DMP shortage record lacks status")
            raw_key = canonical([name, ingredient, period])
            if raw_key in seen:
                continue
            seen.add(raw_key)
            groups.setdefault(canonical([name, ingredient]), {"name": name, "ingredient": ingredient, "periods": []})["periods"].append(period)

        records = []
        for key, group in sorted(groups.items()):
            records.append({"key": key, "fields": {"ingredient": group["ingredient"], "periods": sorted(group["periods"], key=canonical)},
                            "title": group["name"], "url": self.url, "published": None})
        if len(records) > self.max_records:
            raise SourceError("DMP shortage response exceeds max_records")
        return records

    @staticmethod
    def _period_summary(periods):
        shown = []
        for period in periods[:3]:
            text = f"{period['Mangelperiode fra'] or 'ukjent start'} til {period['Mangelperiode til'] or 'ukjent slutt'}: {period['Status']}"
            if period["Informasjon/tiltak"]:
                text += f"; {period['Informasjon/tiltak'][:240]}"
            if period["Informasjon på nettside"]:
                text += f"; {period['Informasjon på nettside'][:160]}"
            shown.append(text)
        if len(periods) > 3:
            shown.append(f"og {len(periods) - 3} flere perioder")
        return " | ".join(shown)

    def describe_change(self, name, before, after):
        if name != "periods":
            return super().describe_change(name, before, after)
        prior = {canonical(p): p for p in before}
        current = {canonical(p): p for p in after}
        removed = [p for key, p in prior.items() if key not in current]
        added = [p for key, p in current.items() if key not in prior]
        return (f"Endrede periodeopplysninger før: {self._period_summary(removed) or 'ingen'} | "
                f"Etter: {self._period_summary(added) or 'ikke lenger i kildens periodeutvalg'}")

    def _item(self, row, event, details, suppress):
        if event == "added":
            details = ("Ny mangelregistrering", f"Virkestoff: {row['fields']['ingredient']}",
                       f"Mangelsituasjon: {self._period_summary(row['fields']['periods'])}")
        return super()._item(row, event, details, suppress)
