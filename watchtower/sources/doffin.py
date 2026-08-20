from __future__ import annotations

import os
from typing import Any

from .common import Source, SourceError
from ..models import Item

DEFAULT_URL = "https://dof-notices-prod-api.developer.azure-api.net/public/v1/notices/search"


class DoffinSource(Source):
    """Read published procurement notices from Doffin's official Public API.

    The Public API is intended for machine-readable search/download of
    published notices and requires an API Management subscription key.
    Production supplies it through the DOFFIN_API_KEY Actions secret.
    """

    def fetch(self) -> list[Item]:
        api_key = os.environ.get("DOFFIN_API_KEY", "").strip()
        if not api_key:
            raise SourceError("Doffin API key is not configured")

        url = self.config.urls[0] if self.config.urls else DEFAULT_URL
        page_size = min(max(int(self.config.options.get("page_size", 100)), 1), 100)
        max_pages = min(max(int(self.config.options.get("max_pages", 1)), 1), 5)
        queries = self.config.options.get("search_queries", [""])
        if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
            raise SourceError("Doffin search_queries must be a string array")

        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Accept": "application/json",
        }
        out: list[Item] = []
        seen: set[str] = set()

        for query in dict.fromkeys(q.strip() for q in queries):
            for page in range(1, max_pages + 1):
                params: dict[str, Any] = {
                    "page": page,
                    "pageSize": page_size,
                }
                if query:
                    params["query"] = query
                response = self.get(url, params=params, headers=headers)
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise SourceError("invalid Doffin JSON") from exc
                rows = _rows(payload)
                for row in rows:
                    item = _item(self.config.id, row)
                    if item is None or item.key in seen:
                        continue
                    seen.add(item.key)
                    out.append(item)
                if len(rows) < page_size:
                    break
        return out


def _rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise SourceError("invalid Doffin response")
    for key in ("notices", "hits", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    raise SourceError("Doffin response contains no notice list")


def _item(source_id: str, row: dict[str, Any]) -> Item | None:
    notice_id = _first(row, "noticeId", "id", "doffinId", "notice_id", "publicationId")
    title = _first(row, "title", "noticeTitle", "name")
    if not notice_id or not title:
        return None

    buyer = _first(row, "buyerName") or _text(
        row.get("buyer") or row.get("buyers") or row.get("contractingAuthority")
    )
    description = _first(row, "shortDescription", "description", "noticeDescription")
    notice_type = _first(row, "type", "noticeType")
    status = _first(row, "status")
    cpv = _text(row.get("cpvCodes") or row.get("cpvCode") or row.get("cpv"))
    published = _first(row, "publishedDate", "issueDate", "publicationDate", "date") or None
    deadline = _first(row, "deadline", "tenderDeadline", "submissionDeadline")
    estimated = _text(row.get("estimatedValue"))
    url = _first(row, "url", "noticeUrl", "webUrl") or f"https://www.doffin.no/notices/{notice_id}"

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
