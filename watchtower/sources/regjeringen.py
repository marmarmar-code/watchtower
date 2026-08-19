from __future__ import annotations

import xml.etree.ElementTree as ET
from html import unescape

from .common import Source, SourceError
from ..models import Item

DEFAULT_URL = "https://www.regjeringen.no/no/rss/Rss/2581966/"


class RegjeringenSource(Source):
    def fetch(self) -> list[Item]:
        urls = self.config.urls or (DEFAULT_URL,)
        items: list[Item] = []
        for url in urls:
            response = self.get(url)
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as exc:
                raise SourceError("invalid Regjeringen RSS") from exc
            for node in root.findall(".//item"):
                title = _text(node, "title")
                link = _text(node, "link")
                guid = _text(node, "guid") or link
                if not title or not guid:
                    continue
                categories = [c.text.strip() for c in node.findall("category") if c.text and c.text.strip()]
                description = unescape(_text(node, "description"))
                items.append(Item(
                    source_id=self.config.id,
                    key=guid,
                    title=title,
                    url=link or guid,
                    published=_text(node, "pubDate") or None,
                    text=description,
                    metadata={"categories": " | ".join(categories)},
                ))
        return items


def _text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""
