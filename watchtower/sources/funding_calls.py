"""Bounded EU Funding & Tenders call deadline windows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlparse

from dataclasses import replace

from .changes import SnapshotSource, integer
from .common import SourceError

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
FIELDS = ("identifier", "title", "status", "deadlineDate", "frameworkProgramme", "language", "url")


class FundingCallsSource(SnapshotSource):
    """Monitor English topic records in a complete short future-deadline window."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API,)):
            raise ValueError("funding_calls accepts only the official Search API")
        if self.complete or "removed" in self.events:
            raise ValueError("rolling funding-call windows cannot confirm removals")
        self.window_days = integer(config.options.get("window_days", 7), "window_days", 1, 31)
        self.max_records = integer(config.options.get("max_records", 100), "max_records", 1, 100)
        self.text = config.options.get("text", "innovation")
        if not isinstance(self.text, str) or len(self.text) > 100:
            raise ValueError("text must be short text")
        self.field_labels = {"topic_id": "Utlysnings-ID", "status_code": "Statuskode",
                             "deadline": "Søknadsfrist", "framework_programme": "Program",
                             **self.field_labels}

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        deadline = row["fields"]["deadline"][:10]
        text = ("Nyobservert EU-utlysning i fristvinduet",
                f"Frist i kilden: {deadline}",
                "Kilden oppgir ikke her budsjett eller kvalifikasjonsvilkår")
        if event == "changed":
            text = ("Endret EU-utlysning", f"Oppført frist: {deadline}", *details[1:])
        return replace(item, alert_details=text)

    def read_records(self):
        now = datetime.now(timezone.utc)
        end = now.date() + timedelta(days=self.window_days)
        start_text = now.date().isoformat() + "T00:00:00.000+0000"
        end_text = end.isoformat() + "T23:59:59.999+0000"
        query = {"bool": {"must": [
            {"terms": {"type": ["1"]}},
            {"terms": {"status": ["31094501", "31094502"]}},
            {"range": {"deadlineDate": {"gte": start_text, "lte": end_text}}},
            {"term": {"language": "en"}},
        ]}}
        parts = {name: ("blob", json.dumps(value), "application/json") for name, value in {
            "query": query, "sort": [{"field": "deadlineDate", "order": "ASC"}],
            "displayFields": list(FIELDS),
        }.items()}
        response = self.post(API, params={"text": self.text, "pageNumber": 1,
                                          "pageSize": self.max_records, "apiKey": "SEDIA",
                                          "language": "en"}, files=parts, stream=True, allow_redirects=False,
                                          accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError("Funding search returned an unexpected redirect or status")
            chunks, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > self.max_bytes:
                    raise SourceError("Funding search response exceeds max_bytes")
                chunks.append(chunk)
            try:
                payload = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError) as exc:
                raise SourceError("Funding search returned invalid JSON") from exc
        finally:
            response.close()
        return _records(payload, start_text, end_text, self.max_records)


def _one(meta, name, limit=1000):
    value = meta.get(name)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str) or not value[0].strip() or len(value[0]) > limit:
        raise SourceError(f"Funding document has invalid {name}")
    return value[0].strip()


def _records(payload, start, end, max_records):
    if not isinstance(payload, dict) or type(payload.get("totalResults")) is not int or not isinstance(payload.get("results"), list):
        raise SourceError("Funding search response schema changed")
    total, results = payload["totalResults"], payload["results"]
    if (total < 0 or total > max_records or len(results) != total
            or type(payload.get("pageNumber")) is not int or payload["pageNumber"] != 1
            or type(payload.get("pageSize")) is not int or payload["pageSize"] != max_records
            or payload.get("warnings") not in (None, [])) :
        raise SourceError("Funding deadline window is incomplete or exceeds the bound")
    start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)
    rows = {}
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("metadata"), dict):
            raise SourceError("Funding document is malformed")
        meta = result["metadata"]
        identifier, title, status, deadline = (_one(meta, n) for n in ("identifier", "title", "status", "deadlineDate"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}", identifier):
            raise SourceError("Funding document has invalid topic identifier")
        language = _one(meta, "language", 10)
        if language != "en":
            raise SourceError("Funding result is outside English scope")
        if status not in {"31094501", "31094502"}:
            raise SourceError("Funding document has unsupported status code")
        try:
            parsed = datetime.strptime(deadline, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)
        except ValueError:
            raise SourceError("Funding document has invalid deadlineDate") from None
        if not start_dt <= parsed <= end_dt:
            raise SourceError("Funding document is outside the requested deadline window")
        url = _one(meta, "url", 1000)
        parsed_url = urlparse(url)
        if (parsed_url.scheme != "https" or parsed_url.netloc != "ec.europa.eu"
                or parsed_url.query or parsed_url.fragment or parsed_url.params
                or parsed_url.path != "/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/" + identifier):
            raise SourceError("Funding topic URL is not the official identifier URL")
        programme = _one(meta, "frameworkProgramme", 100)
        fields = {"topic_id": identifier, "status_code": status, "deadline": parsed.date().isoformat(), "framework_programme": programme}
        row = {"key": identifier, "title": title, "url": url, "published": None, "fields": fields}
        if identifier in rows:
            raise SourceError("Funding search returned a duplicate topic identifier")
        rows[identifier] = row
    return sorted(rows.values(), key=lambda row: row["key"])
