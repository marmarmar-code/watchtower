from __future__ import annotations

from datetime import date, timedelta
import os
from typing import Any

from .common import Source, SourceError
from ..models import Item

DEFAULT_URL = "https://api.doffin.no/public/v2/search"


class DoffinSource(Source):
    """Read published procurement notices from Doffin's official Public API.

    The API is free to use but requires an Azure API Management subscription key.
    Production supplies it through the DOFFIN_API_KEY Actions secret.
    """

    def fetch(self) -> list[Item]:
        api_key = os.environ.get("DOFFIN_API_KEY", "").strip()
        if not api_key:
            raise SourceError("Doffin API key is not configured")

        url = self.config.urls[0] if self.config.urls else DEFAULT_URL
        lookback_days = int(self.config.options.get("lookback_days", 14))
        page_size = min(max(int(self.config.options.get("page_size", 100)), 1), 200)
        max_pages = min(max(int(self.config.options.get("max_pages", 3)), 1), 10)
        today = date.today()
        params_base: dict[str, Any] = {
            "numHitsPerPage": str(page_size),
            "sortBy": "PUBLICATION_DATE_DESC",
            "issueDateFrom": (today - timedelta(days=lookback_days)).isoformat(),
            "issueDateTo": today.isoformat(),
        }
        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Accept": "application/json",
        }

        out: list[Item] = []
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            params = dict(params_base)
            params["page"] = str(page)
            response = self.get(url, params=params, headers=headers)
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceError("invalid Doffin JSON") from exc
            if not isinstance(payload, dict):
                raise SourceError("invalid Doffin response")
            hits = payload.get("hits", [])
            if not isinstance(hits, list):
                raise SourceError("invalid Doffin hits")
            for row in hits:
                if not isinstance(row, dict):
                    continue
                item = _item(self.config.id, row)
                if item is None or item.key in seen:
                    continue
                seen.add(item.key)
                out.append(item)
            if len(hits) < page_size:
                break
        return out


def _item(source_id: str, row: dict[str, Any]) -> Item | None:
    notice_id = _first(row, "id", "noticeId", "notice_id", "publicationId")
    title = _first(row, "title", "noticeTitle", "name")
    if not notice_id or not title:
        return None

    buyer = _text(row.get("buyer") or row.get("buyers") or row.get("contractingAuthority"))
    description = _first(row, "shortDescription", "description", "noticeDescription")
    notice_type = _first(row, "type", "noticeType")
    status = _first(row, "status")
    cpv = _text(row.get("cpvCodes") or row.get("cpvCode") or row.get("cpv"))
    published = _first(row, "issueDate", "publicationDate", "publishedDate", "date") or None
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
