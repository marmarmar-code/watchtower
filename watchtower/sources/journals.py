"""Bounded public eInnsyn journalpost search."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from .changes import SnapshotSource, document, integer, public_url
from .common import SourceError

DEFAULT_URL = "https://api.einnsyn.no/search"
PUBLIC_URL = "https://einnsyn.no/journalpost/{}"
_ID = re.compile(r"^jp_[0-9a-hjkmnp-tv-z]{26}$")
_REFERENCE = re.compile(r"^[a-z]+_[0-9a-hjkmnp-tv-z]{26}$")


class JournalsSource(SnapshotSource):
    """Monitor a complete bounded eInnsyn result window; never infer outcomes."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (DEFAULT_URL,):
            raise ValueError("journals uses the official eInnsyn search API")
        if self.complete or "removed" in self.events:
            raise ValueError("eInnsyn search cannot confirm removals")
        self.url = DEFAULT_URL
        self.max_pages = integer(config.options.get("max_pages", 10), "max_pages", 1, 20)
        self.limit = min(self.max_records, integer(config.options.get("limit", 100), "limit", 1, 100))
        self.query = _optional_text(config.options.get("query"))
        self.part = _optional_text(config.options.get("korrespondansepart_navn"))
        self.title = _optional_text(config.options.get("tittel"))
        self.lookback_days = integer(config.options.get("lookback_days", 14), "lookback_days", 1, 366)
        if not any((self.query, self.part, self.title)):
            raise ValueError("journals requires an explicit query, party or title")

    def read_records(self):
        today = datetime.now(timezone.utc).date()
        fixed = self._params(today)
        url, seen_urls, rows = self.url, set(), []
        for _ in range(self.max_pages):
            page_url = _with_params(url, fixed)
            if page_url in seen_urls:
                raise SourceError("eInnsyn pagination loop")
            seen_urls.add(page_url)
            payload = _json(document(self, page_url))
            if set(payload) - {"items", "next", "previous"}:
                raise SourceError("eInnsyn response schema changed")
            items = payload.get("items")
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise SourceError("eInnsyn response lacks a valid items array")
            rows.extend(_record(item) for item in items)
            if len(rows) > self.max_records:
                raise SourceError("eInnsyn exceeded max_records; narrow the query")
            next_url = payload.get("next")
            if next_url is None:
                return rows
            url = _next_url(next_url)
        raise SourceError("eInnsyn pagination exceeded max_pages")

    def _params(self, today: date):
        params = {
            "entity": "Journalpost", "limit": self.limit,
            "sortBy": "publisertDato", "sortOrder": "desc",
            "publisertDatoFrom": (today - timedelta(days=self.lookback_days)).isoformat(),
            "publisertDatoTo": today.isoformat(),
        }
        for key, value in (("query", self.query), ("korrespondansepartNavn", self.part), ("tittel", self.title)):
            if value:
                params[key] = value
        return params

    def describe_change(self, name, before, after):
        labels = {"offentligTittel": "Offentlig tittel", "journalposttype": "Journalposttype", "journaldato": "Journaldato"}
        return f"{labels[name]}: {before or 'ikke oppgitt'} → {after or 'ikke oppgitt'}"


def _record(row):
    identity = row.get("id")
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise SourceError("eInnsyn item lacks a valid journalpost id")
    if row.get("entity") not in (None, "Journalpost"):
        raise SourceError("eInnsyn search returned another entity type")
    title = row.get("offentligTittel")
    if not isinstance(title, str) or not title.strip():
        raise SourceError("eInnsyn journalpost lacks offentligTittel")
    published = _timestamp(row.get("publisertDato"), "publisertDato")
    journal_date = row.get("journaldato")
    if journal_date is not None:
        try:
            if not isinstance(journal_date, str):
                raise ValueError
            date.fromisoformat(journal_date)
        except ValueError:
            raise SourceError("eInnsyn journaldato is invalid") from None
    post_type = row.get("journalposttype")
    if post_type is not None and (not isinstance(post_type, str) or not post_type.strip()):
        raise SourceError("eInnsyn journalposttype is invalid")
    for name in ("saksmappe", "administrativEnhetObjekt"):
        value = row.get(name)
        if value is not None and (not isinstance(value, str) or not _REFERENCE.fullmatch(value)):
            raise SourceError(f"eInnsyn {name} reference is invalid")
    parties = row.get("korrespondansepart")
    if parties is not None and (not isinstance(parties, list) or any(not isinstance(value, str) or not _REFERENCE.fullmatch(value) for value in parties)):
        raise SourceError("eInnsyn korrespondansepart references are invalid")
    fields = {"offentligTittel": title.strip(), "journalposttype": post_type, "journaldato": journal_date}
    return {"key": identity, "title": title.strip(), "url": PUBLIC_URL.format(identity),
            "published": published, "fields": fields}


def _timestamp(value, name):
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError:
        raise SourceError(f"eInnsyn {name} is invalid") from None
    return value


def _json(data):
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError("eInnsyn response was not valid JSON") from exc
    if not isinstance(value, dict):
        raise SourceError("eInnsyn response was not an object")
    return value


def _next_url(value):
    if not isinstance(value, str) or not value:
        raise SourceError("eInnsyn next URL is invalid")
    url = urljoin(DEFAULT_URL, value)
    parts = urlsplit(url)
    cursors = parse_qs(parts.query).get("startingAfter", [])
    if (parts.scheme, parts.netloc, parts.path, parts.fragment) != ("https", "api.einnsyn.no", "/search", "") or len(cursors) != 2:
        raise SourceError("eInnsyn next URL left the search cursor scope")
    public_url(url)
    return url


def _with_params(url, params):
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query.update({key: [str(value)] for key, value in params.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))


def _optional_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("journal filter values must be strings")
    return value.strip()
