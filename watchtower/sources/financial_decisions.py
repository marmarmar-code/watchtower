"""Read-only FinKN decision search through its public Angular API."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import re

from .changes import SnapshotSource, integer
from .common import SourceError

DEFAULT_URL = "https://publisering.finkn.no/api/statements/searchstatements"
PUBLIC_URL = "https://publisering.finkn.no/statement/{}"
ID = re.compile(r"^\d{4}-\d+$")


class FinancialDecisionsSource(SnapshotSource):
    """Monitor public decisions in a bounded company and decision-date window."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (DEFAULT_URL,):
            raise ValueError("FinKN accepts only the official search endpoint")
        if self.complete or "removed" in self.events:
            raise ValueError("FinKN date windows cannot confirm removals")
        company = config.options.get("company")
        if not isinstance(company, str) or not company.strip() or len(company) > 200:
            raise ValueError("company is required as bounded text")
        if "free_text" in config.options:
            raise ValueError("FinKN monitoring uses decision dates, not ambiguous free text")
        self.company = company.strip()
        self.lookback_days = integer(config.options.get("lookback_days", 30), "lookback_days", 1, 366)
        self.max_records = integer(config.options.get("max_records", 500), "max_records", 1, 2000)
        self.allow_empty = config.options.get("allow_empty", True)
        self.field_labels = {"title": "Tittel", "company": "Finansforetak", "conclusion": "Konklusjon",
                             "closed_date": "Behandlet i nemnda", **self.field_labels}

    def read_records(self):
        today = datetime.now(timezone.utc).date()
        first, last = today - timedelta(days=self.lookback_days), today
        response = self.post(DEFAULT_URL, json={
            "SelectedCompany": self.company,
            "NemndClosedDateFrom": first.isoformat(),
            "NemndClosedDateTo": last.isoformat(),
            "SelectedKeywords": [],
        }, headers={"Accept": "application/json"}, stream=True, allow_redirects=False)
        rows = _read_json(response, self.max_bytes)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise SourceError("FinKN response is not a list of objects")
        if len(rows) > self.max_records:
            raise SourceError("FinKN result exceeds max_records; narrow the company selection")
        return [_record(row, self.company, first, last) for row in rows]

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        if event == "added":
            fields = row["fields"]
            details = (
                "Ny avgjørelse fra Finansklagenemnda",
                f"Finansforetak: {fields['company']}",
                f"Konklusjon: {fields['conclusion'] or 'ikke oppgitt'}",
                f"Behandlet i nemnda: {fields['closed_date']}",
                f"Publisert: {row['published']}",
            )
        else:
            details = ("Endret avgjørelse fra Finansklagenemnda", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _record(row, expected_company, first: date, last: date):
    key = row.get("sPkStatementId")
    if not isinstance(key, str) or not ID.fullmatch(key.strip()):
        raise SourceError("FinKN decision lacks a valid statement ID")
    title = row.get("sSummaryTitle") or row.get("sTitle")
    if not isinstance(title, str) or not title.strip():
        raise SourceError("FinKN decision lacks its public title")
    published = _datetime(row.get("dtPublishedDate"), "published date")
    closed = _datetime(row.get("dtNemndClosedDate"), "closed date")
    closed_day = closed.date()
    if not first <= closed_day <= last:
        raise SourceError("FinKN returned a decision outside the requested date window")
    company = row.get("sCompany")
    if not isinstance(company, str) or company.strip().casefold() != expected_company.casefold():
        raise SourceError("FinKN returned a decision outside the requested company")
    conclusion = row.get("sConclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        raise SourceError("FinKN conclusion has invalid type")
    fields = {"title": title.strip(), "company": expected_company,
              "conclusion": conclusion.strip() if conclusion else None,
              "closed_date": row["dtNemndClosedDate"]}
    return {"key": key.strip(), "title": title.strip(), "url": PUBLIC_URL.format(key.strip()),
            "published": row["dtPublishedDate"], "fields": fields}


def _datetime(value, name):
    try:
        if not isinstance(value, str) or "T" not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SourceError(f"FinKN {name} is invalid") from None
    return parsed


def _read_json(response, max_bytes):
    try:
        chunks, total = [], 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise SourceError("FinKN response exceeds max_bytes")
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("FinKN returned invalid JSON") from exc
    finally:
        response.close()
