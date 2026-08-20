from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .common import Source, SourceError
from ..models import Item

SITEMAP_URL = "https://www.domstol.no/sitemap.xml"
HR_RE = re.compile(r"\bHR-(20\d{2})-(\d+)-([A-Z])\b", re.I)


class HoyesterettSource(Source):
    """Monitor newly published Norwegian Supreme Court decisions.

    Discovery uses domstol.no's public sitemap. The first run reads a bounded
    recent window to establish a silent baseline. Later runs open only HR
    references not already present in state, which keeps hourly traffic small.
    """

    def fetch(self) -> list[Item]:
        return self.fetch_with_state(None)

    def fetch_with_state(self, previous: dict[str, Any] | None) -> list[Item]:
        sitemap_url = self.config.urls[0] if self.config.urls else SITEMAP_URL
        max_items = min(max(int(self.config.options.get("max_items", 40)), 1), 250)
        ranked = self._ranked_decisions(sitemap_url)[:max_items]
        if previous:
            known = set(previous.get("seen", {}))
            ranked = [row for row in ranked if row[2] not in known]

        out: list[Item] = []
        for _, _, ref, url in ranked:
            response = self.get(url)
            soup = BeautifulSoup(response.text, "html.parser")
            title_node = soup.find("h1")
            title = " ".join(title_node.stripped_strings) if title_node else ref
            main = soup.find("main") or soup.find("article") or soup.body
            text = " ".join(main.stripped_strings) if main else title
            out.append(Item(
                source_id=self.config.id,
                key=ref,
                title=title or ref,
                url=url,
                published=_published(text, ref),
                text=text,
                metadata={"reference": ref, "court": "Høyesterett"},
            ))
        return out

    def _ranked_decisions(self, sitemap_url: str) -> list[tuple[int, int, str, str]]:
        root = self._xml(sitemap_url)
        urls: set[str] = set()
        local = _local(root.tag)
        if local == "sitemapindex":
            sitemap_locs = [_element_text(node) for node in root.iter() if _local(node.tag) == "loc"]
            for child_url in sitemap_locs[:100]:
                if child_url:
                    urls.update(_locs(self._xml(child_url)))
        elif local == "urlset":
            urls.update(_locs(root))
        else:
            raise SourceError("unsupported domstol.no sitemap format")

        ranked: list[tuple[int, int, str, str]] = []
        for url in urls:
            if not _is_domstol(url) or "/no/hoyesterett/avgjorelser/avgjorelser-" not in url.casefold():
                continue
            match = HR_RE.search(url)
            if not match:
                continue
            ranked.append((int(match.group(1)), int(match.group(2)), match.group(0).upper(), url))
        ranked.sort(reverse=True)
        return ranked

    def _xml(self, url: str) -> ET.Element:
        response = self.get(url)
        try:
            return ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise SourceError("invalid domstol.no sitemap XML") from exc


def _locs(root: ET.Element) -> set[str]:
    return {
        text for node in root.iter()
        if _local(node.tag) == "loc" and (text := _element_text(node))
    }


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(node: ET.Element) -> str:
    return " ".join(part.strip() for part in node.itertext() if part and part.strip())


def _is_domstol(url: str) -> bool:
    try:
        return urlparse(url).hostname in {"domstol.no", "www.domstol.no"}
    except ValueError:
        return False


def _published(text: str, ref: str) -> str | None:
    idx = text.find(ref)
    prefix = text[max(0, idx - 120):idx] if idx >= 0 else text[:120]
    match = re.search(r"(\d{1,2}\.\s+[A-Za-zÆØÅæøå]+\s+20\d{2})", prefix)
    return match.group(1) if match else None
