from __future__ import annotations

import os
from dataclasses import replace
from hashlib import sha256
import json
from typing import Any

from .common import Source, SourceError
from ..models import Item

# The Azure developer portal documents the API, but requests are served by api.doffin.no.
DEFAULT_URL = "https://api.doffin.no/public/v2/search"
_SCOPE_KEY = "doffin_query_scope"


class DoffinSource(Source):
    """Read published procurement notices from Doffin's Public API v2.

    The Public API requires an API Management subscription key. Production
    supplies it through the DOFFIN_API_KEY Actions secret.
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        if config.urls and config.urls != (DEFAULT_URL,):
            raise ValueError("Doffin accepts only the official API URL")
        self.endpoint = DEFAULT_URL
        self._completed_scope: str | None = None

    def fetch_with_state(self, previous: dict | None) -> list[Item]:
        self._previous_keys = set((previous or {}).get("seen", {}))
        items = self.fetch()
        if previous is not None and previous.get(_SCOPE_KEY) != self._completed_scope:
            # Expanding queries/pages can expose older notices that have never
            # been observed. Quietly learn the changed window once; retain all
            # existing notice identities instead of resetting the source state.
            return [replace(item, suppress_alert=True) for item in items]
        return items

    def augment_state(self, state: dict) -> dict:
        if self._completed_scope is None:
            return state
        return {**state, _SCOPE_KEY: self._completed_scope}

    def fetch(self) -> list[Item]:
        self.coverage_warnings = []
        self._completed_scope = None
        api_key = os.environ.get("DOFFIN_API_KEY", "").strip()
        if not api_key:
            raise SourceError("Doffin API key is not configured")

        page_size = min(max(int(self.config.options.get("page_size", 100)), 1), 100)
        max_pages = min(max(int(self.config.options.get("max_pages", 1)), 1), 5)
        queries = self.config.options.get("search_queries", [""])
        if not isinstance(queries, list) or not queries or not all(isinstance(q, str) for q in queries):
            raise SourceError("Doffin search_queries must be a non-empty string array")
        queries = list(dict.fromkeys(q.strip() for q in queries))
        scope = sha256(json.dumps({
            "version": 1,
            "search_queries": sorted(queries),
            "page_size": page_size,
            "max_pages": max_pages,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Accept": "application/json",
        }
        out: list[Item] = []
        seen: set[str] = set()
        previous_keys = getattr(self, "_previous_keys", set())

        for query in queries:
            overlap = False
            for page_index in range(max_pages):
                params: dict[str, Any] = {
                    "page": page_index + 1,
                    "numHitsPerPage": page_size,
                    "sortBy": "PUBLICATION_DATE_DESC",
                }
                if query:
                    params["searchString"] = query
                response = self.get(
                    self.endpoint,
                    params=params,
                    headers=headers,
                    allow_redirects=False,
                )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise SourceError("invalid Doffin JSON") from exc
                rows = _rows(payload)
                for row in rows:
                    item = _item(self.config.id, row)
                    overlap = overlap or item.key in previous_keys
                    if item.key in seen:
                        continue
                    seen.add(item.key)
                    out.append(item)
                if len(rows) < page_size:
                    break
                if page_index == max_pages - 1:
                    self.coverage_warnings.append("result_window_full")
                    if previous_keys and not overlap:
                        self.coverage_warnings.append("no_overlap_with_previous_window")
        self.coverage_warnings = list(dict.fromkeys(self.coverage_warnings))
        # The engine saves this together with seen keys only after successful
        # fetch/evaluation and any required delivery transaction.
        self._completed_scope = scope
        return out


def _rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise SourceError("invalid Doffin response")
    for key in ("hits", "notices", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            if not all(isinstance(row, dict) for row in value):
                raise SourceError("Doffin notice list contains an invalid row")
            return value
    raise SourceError("Doffin response contains no notice list")


def _item(source_id: str, row: dict[str, Any]) -> Item:
    notice_id = _first(row, "doffinId", "noticeId", "id", "notice_id", "publicationId")
    title = _first(row, "heading", "title", "noticeTitle", "name")
    if not notice_id or not title:
        raise SourceError("Doffin notice is missing identity or title")

    buyer = _first(row, "buyerName") or _text(
        row.get("buyer") or row.get("buyers") or row.get("contractingAuthority")
    )
    description = _first(row, "shortDescription", "description", "noticeDescription")
    notice_type = _first(row, "type", "noticeType")
    status = _first(row, "status")
    cpv = _text(row.get("cpvCodes") or row.get("cpvCode") or row.get("cpv"))
    published = _first(row, "publicationDate", "publishedDate", "issueDate", "date") or None
    deadline = _first(row, "deadline", "tenderDeadline", "submissionDeadline")
    estimated = _text(row.get("estimatedValue"))
    url = _first(row, "url", "noticeUrl", "webUrl", "doffinClassicUrl") or (
        f"https://www.doffin.no/notices/{notice_id}"
    )

    text = "\n".join(part for part in (description, buyer) if part)
    return Item(
        source_id=source_id,
        key=notice_id,
        title=title,
        url=url,
        published=published,
        text=text,
        metadata={
            "buyer": buyer,
            "type": notice_type,
            "status": status,
            "cpv": cpv,
            "deadline": deadline,
            "estimated_value": estimated,
        },
    )


def _first(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        text = _text(value)
        if text:
            return text
    return ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " | ".join(part for item in value if (part := _text(item)))
    if isinstance(value, dict):
        preferred = []
        for key in ("name", "value", "text", "label", "code"):
            if key in value:
                text = _text(value[key])
                if text:
                    preferred.append(text)
        if preferred:
            return " | ".join(dict.fromkeys(preferred))
        parts = [_text(item) for item in value.values()]
        return " | ".join(part for part in parts if part)
    return str(value).strip()
