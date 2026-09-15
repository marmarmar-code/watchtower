"""Public PFU decisions in a complete bounded treatment-date window."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
import re

from .changes import SnapshotSource, integer
from .common import SourceError

BASE_URL = "https://pfu.presse.no/pfu-basen/"
ALGOLIA_APP = "Z4HI2P3AJD"
# Public browser search-only key published by pfu.presse.no, not a private credential.
ALGOLIA_SEARCH_KEY = "009f926a3ff6b3d07e18b891344dcb84"
INDEX = "pfu-basen_processedAtDesc"
SEARCH_URL = f"https://{ALGOLIA_APP.lower()}-dsn.algolia.net/1/indexes/{INDEX}/query"
ATTRIBUTES = ["caseNumber", "complainant", "mediumId", "mediumName", "processedAt",
              "processedAtTimestamp", "status", "conclusion", "isViolation"]
STATUS_LABELS = {"violation": "Brudd", "criticism": "Kritikk",
                 "noViolation": "Ikke brudd", "awaitingProcessing": "Venter behandling"}


class PressCasesSource(SnapshotSource):
    """Discover new and changed PFU cases in a rolling processed-date window."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (BASE_URL,):
            raise ValueError("press_cases uses PFU's official public base")
        if self.complete or "removed" in self.events:
            raise ValueError("PFU rolling windows cannot confirm removals")
        if "case_numbers" in config.options:
            raise ValueError("press_cases discovers decisions by treatment window")
        self.lookback_days = integer(config.options.get("lookback_days", 30), "lookback_days", 1, 366)
        self.page_size = integer(config.options.get("page_size", 100), "page_size", 1, 100)
        self.max_pages = integer(config.options.get("max_pages", 5), "max_pages", 1, 20)
        self.allow_empty = config.options.get("allow_empty", True)
        self.field_labels = {"medium": "Medium", "outcome": "PFUs konklusjon",
                             "complainant": "Klager", "conclusion": "Kort konklusjon",
                             **self.field_labels}

    def read_records(self):
        today = datetime.now(timezone.utc).date()
        first = datetime.combine(today - timedelta(days=self.lookback_days), datetime.min.time(), timezone.utc)
        last = datetime.combine(today + timedelta(days=1), datetime.min.time(), timezone.utc) - timedelta(milliseconds=1)
        first_ms, last_ms = round(first.timestamp() * 1000), round(last.timestamp() * 1000)
        filters = f"processedAtTimestamp >= {first_ms} AND processedAtTimestamp <= {last_ms}"
        rows, seen, expected_total = [], set(), None
        for page in range(self.max_pages):
            response = self.post(SEARCH_URL, json={"query": "", "hitsPerPage": self.page_size,
                "page": page, "filters": filters, "attributesToRetrieve": ATTRIBUTES}, headers={
                    "Accept": "application/json", "x-algolia-application-id": ALGOLIA_APP,
                    "x-algolia-api-key": ALGOLIA_SEARCH_KEY}, stream=True, allow_redirects=False)
            payload = _response_json(response, self.max_bytes)
            required = {"hits", "nbHits", "page", "nbPages", "hitsPerPage"}
            if not isinstance(payload, dict) or not required <= set(payload):
                raise SourceError("PFU search response schema changed")
            hits, total, pages = payload["hits"], payload["nbHits"], payload["nbPages"]
            if (not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits)
                    or type(total) is not int or total < 0 or type(payload["page"]) is not int or payload["page"] != page
                    or type(payload["hitsPerPage"]) is not int or payload["hitsPerPage"] != self.page_size
                    or type(pages) is not int or pages != math.ceil(total / self.page_size)):
                raise SourceError("PFU search pagination is invalid")
            if expected_total is not None and total != expected_total:
                raise SourceError("PFU search total changed while paging")
            expected_total = total
            if total > self.max_records:
                raise SourceError("PFU treatment window exceeds max_records")
            expected = self.page_size if page + 1 < pages else total - page * self.page_size
            if len(hits) != expected:
                raise SourceError("PFU search returned an incomplete page")
            for hit in hits:
                row = _record(hit, first_ms, last_ms)
                if row["key"] in seen:
                    raise SourceError("PFU search repeated a case number")
                seen.add(row["key"]); rows.append(row)
            if total == 0 or page + 1 == pages:
                if len(rows) != total:
                    raise SourceError("PFU search result is incomplete")
                return rows
        raise SourceError("PFU search exceeds max_pages")

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        if event == "added":
            fields = row["fields"]
            details = ("Ny PFU-avgjørelse", f"Medium: {fields['medium']}",
                       f"PFUs konklusjon: {fields['outcome']}", f"Behandlet: {row['processed_day']}")
            if fields["complainant"]:
                details += (f"Klager: {fields['complainant']}",)
        else:
            details = ("Endret PFU-avgjørelse", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _record(hit, first_ms, last_ms):
    number = hit.get("caseNumber")
    if not isinstance(number, str) or not re.fullmatch(r"\d{2}-\d{3}", number):
        raise SourceError("PFU hit lacks a stable case number")
    medium_id, medium = hit.get("mediumId"), hit.get("mediumName")
    if not isinstance(medium_id, str) or not medium_id or not isinstance(medium, str) or not medium.strip():
        raise SourceError("PFU hit lacks its medium")
    status = hit.get("status")
    if status not in STATUS_LABELS:
        raise SourceError("PFU hit has an unknown outcome")
    violation = hit.get("isViolation")
    if violation is not None and type(violation) is not bool:
        raise SourceError("PFU hit has invalid violation metadata")
    processed, stamp = hit.get("processedAt"), hit.get("processedAtTimestamp")
    try:
        if not isinstance(processed, str) or type(stamp) is not int:
            raise ValueError
        parsed = datetime.fromisoformat(processed.replace("Z", "+00:00"))
        if parsed.tzinfo is None or abs(round(parsed.timestamp() * 1000) - stamp) > 1:
            raise ValueError
    except ValueError:
        raise SourceError("PFU hit has invalid treatment time") from None
    if not first_ms <= stamp <= last_ms:
        raise SourceError("PFU hit escaped the treatment window")
    complainant, conclusion = hit.get("complainant"), hit.get("conclusion")
    for name, value, limit in (("mediumName", medium, 300), ("complainant", complainant, 500),
                               ("conclusion", conclusion, 4000)):
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise SourceError(f"PFU hit has invalid {name}")
    fields = {"medium": medium.strip(), "outcome": STATUS_LABELS[status],
              "complainant": complainant.strip() if complainant else "",
              "conclusion": conclusion.strip() if conclusion else ""}
    return {"key": number, "title": f"PFU-sak {number} · {medium.strip()}",
            "url": BASE_URL + number, "published": None,
            "processed_day": parsed.date().isoformat(), "fields": fields}


def _response_json(response, max_bytes):
    try:
        chunks, total = [], 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise SourceError("PFU search exceeds max_bytes")
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("PFU search returned invalid JSON") from exc
    finally:
        response.close()
