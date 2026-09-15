"""Anonymous TED award/modification notices in a complete bounded date window."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
import re
from .changes import SnapshotSource, integer, strings
from .common import SourceError

DEFAULT_URL = "https://api.ted.europa.eu/v3/notices/search"
RESULT_TYPES = {"can-standard", "can-social", "can-modif"}
FIELDS = ["publication-number", "publication-date", "notice-title", "notice-type",
          "winner-name", "total-value", "total-value-cur", "organisation-name-tenderer"]


class TedNoticesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (DEFAULT_URL,):
            raise ValueError("TED accepts only the official search URL")
        if self.complete or "removed" in self.events:
            raise ValueError("TED date windows cannot confirm removals")
        if "query" in config.options:
            raise ValueError("Use structured notice_types, keywords and cpv, not raw query")
        self.notice_types = strings(config.options.get("notice_types", ["can-standard", "can-social"]), "notice_types")
        if set(self.notice_types) - RESULT_TYPES:
            raise ValueError("Unsupported TED result notice type")
        keywords = strings(config.options.get("keywords", []), "keywords", empty=True)
        if any(len(x) > 100 or any(ord(c) < 32 or c in '\\"' for c in x) for x in keywords):
            raise ValueError("TED keywords cannot contain quotes, backslashes or controls")
        cpv = config.options.get("cpv")
        if cpv is not None and (not isinstance(cpv, str) or not re.fullmatch(r"\d{8}", cpv)):
            raise ValueError("cpv must be an eight-digit code")
        clauses = ["buyer-country=NOR", "(" + " OR ".join(f"notice-type={t}" for t in self.notice_types) + ")"]
        clauses.extend(f'FT~"{x.strip()}"' for x in keywords)
        if cpv:
            clauses.append(f"classification-cpv={cpv}")
        self.query_prefix = " AND ".join(clauses)
        self.lookback_days = integer(config.options.get("lookback_days", 7), "lookback_days", 1, 30)
        self.limit = integer(config.options.get("limit", 100), "limit", 1, 100)
        self.max_pages = integer(config.options.get("max_pages", 5), "max_pages", 1, 10)

    def read_records(self):
        since = (datetime.now(timezone.utc).date() - timedelta(days=self.lookback_days)).strftime("%Y%m%d")
        query = self.query_prefix + f" AND publication-date>={since} SORT BY publication-date DESC"
        rows, ids, total = [], set(), None
        for page in range(1, self.max_pages + 1):
            response = self.post(DEFAULT_URL, json={"query": query, "fields": FIELDS,
                "limit": self.limit, "scope": "ALL", "paginationMode": "PAGE_NUMBER", "page": page},
                headers={"Accept": "application/json"}, stream=True, allow_redirects=False)
            payload = _read_json(response, self.max_bytes)
            if not isinstance(payload, dict) or payload.get("timedOut") is not False:
                raise SourceError("TED search did not complete")
            count, batch = payload.get("totalNoticeCount"), payload.get("notices")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise SourceError("TED returned an invalid total")
            if total is not None and total != count:
                raise SourceError("TED total changed while paging; retry on next poll")
            total = count
            if total > min(self.max_records, self.limit * self.max_pages):
                raise SourceError("TED date window exceeds bounds; narrow the selection")
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise SourceError("TED returned an invalid notice list")
            for row in batch:
                record = _record(row, self.notice_types)
                if record["key"] in ids:
                    raise SourceError("TED repeated a publication while paging")
                ids.add(record["key"])
                rows.append(record)
            if len(rows) == total:
                return rows
            if not batch or len(rows) > total:
                raise SourceError("TED returned an incomplete or inconsistent date window")
        raise SourceError("TED pagination exceeds max_pages")

    def describe_change(self, name, before, after):
        labels = {"title": "Tittel", "winner": "Vinner(e)", "tenderers": "Tilbydere",
                  "value": "Oppgitt samlet verdi i kunngjøringen", "currency": "Valuta", "notice_type": "Kunngjøringstype"}
        def shown(v):
            return ", ".join(v) if isinstance(v, list) else str(v) if v is not None else "ikke oppgitt"
        return f"{labels.get(name, name)}: {shown(before)} → {shown(after)}"

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        f = row["fields"]
        label = "Kunngjort kontraktsendring" if f["notice_type"] == "can-modif" else "Kunngjort kontraktsresultat"
        if event == "changed":
            label = "Endret kunngjøring: " + label.lower()
        if event == "added":
            value = "ikke oppgitt" if f["value"] is None else f'{f["value"]:,}'.replace(",", " ")
            details = (label, "Vinner(e): " + (", ".join(f["winner"]) or "ikke oppgitt"),
                       "Samlet kunngjøringsverdi: " + value + " " + (", ".join(f["currency"]) or "(valuta ikke oppgitt)"),
                       "Beløpet er kunngjøringens samlede verdi og er ikke fordelt på vinnerne.")
        else:
            details = (label, *details[1:])
        return replace(item, alert_details=details)


def _localized(value, *, many):
    if value is None and many:
        return []
    if not isinstance(value, dict) or not value:
        raise SourceError("TED localized field is missing or malformed")
    selected = value.get("eng", value[sorted(value)[0]])
    if many:
        if isinstance(selected, str):
            selected = [selected]
        if not isinstance(selected, list) or any(not isinstance(v, str) or not v.strip() for v in selected):
            raise SourceError("TED localized names are invalid")
        return sorted(set(v.strip() for v in selected))
    if not isinstance(selected, str) or not selected.strip():
        raise SourceError("TED notice title is invalid")
    return selected.strip()


def _record(row, types):
    key, published = row.get("publication-number"), row.get("publication-date")
    if not isinstance(key, str) or not re.fullmatch(r"\d{1,7}-\d{4}", key):
        raise SourceError("TED notice lacks publication identity")
    if not isinstance(published, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[+-]\d{2}:\d{2}|Z)?", published):
        raise SourceError("TED notice lacks publication date")
    if row.get("notice-type") not in types:
        raise SourceError("TED returned a notice outside the requested types")
    title = _localized(row.get("notice-title"), many=False)
    value, currency = row.get("total-value"), row.get("total-value-cur", [])
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
        raise SourceError("TED value is invalid")
    if not isinstance(currency, list) or any(not isinstance(c, str) or not re.fullmatch("[A-Z]{3}", c) for c in currency):
        raise SourceError("TED currency is invalid")
    return {"key": key, "title": title, "published": published,
            "url": f"https://ted.europa.eu/en/notice/{key}/html",
            "fields": {"title": title, "notice_type": row["notice-type"],
                       "winner": _localized(row.get("winner-name"), many=True),
                       "tenderers": _localized(row.get("organisation-name-tenderer"), many=True),
                       "value": value, "currency": sorted(set(currency))}}


def _read_json(response, limit):
    try:
        chunks, total = [], 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise SourceError("TED response exceeds max_bytes")
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("TED returned invalid JSON") from exc
    finally:
        response.close()
