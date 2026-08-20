from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .common import Source, SourceError
from ..models import Item

SITEMAP_URL = "https://www.domstol.no/sitemap.xml"
HR_RE = re.compile(r"\bHR-(20\d{2})-(\d+)-([A-Z])\b", re.I)


class HoyesterettSource(Source):
    """Monitor newly published Norwegian Supreme Court decisions.

    domstol.no's decision search is JavaScript-driven. The public sitemap is a
    simpler and more stable discovery surface: find recent HR decision URLs,
    then read the decision page itself for title and searchable article text.
    """

    def fetch(self) -> list[Item]:
        sitemap_url = self.config.urls[0] if self.config.urls else SITEMAP_URL
        max_items = min(max(int(self.config.options.get("max_items", 80)), 1), 250)
        urls = self._decision_urls(sitemap_url)
        ranked: list[tuple[int, int, str, str]] = []
        for url in urls:
            match = HR_RE.search(url)
            if not match:
                continue
            year = int(match.group(1))
            number = int(match.group(2))
            ref = match.group(0).upper()
            ranked.append((year, number, ref, url))
        ranked.sort(reverse=True)

        out: list[Item] = []
        for _, _, ref, url in ranked[:max_items]:
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

    def _decision_urls(self, sitemap_url: str) -> set[str]:
        root = self._xml(sitemap_url)
        urls: set[str] = set()
        local = _local(root.tag)
        if local == "sitemapindex":
            sitemap_locs = [_element_text(node) for node in root.iter() if _local(node.tag) == "loc"]
            for child_url in sitemap_locs[:100]:
                if not child_url:
                    continue
                child = self._xml(child_url)
                urls.update(_locs(child))
        elif local == "urlset":
            urls.update(_locs(root))
        else:
            raise SourceError("unsupported domstol.no sitemap format")

        return {
            url for url in urls
            if _is_domstol(url)
            and "/no/hoyesterett/avgjorelser/avgjorelser-" in url.casefold()
            and HR_RE.search(url)
        }

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
    # Decision pages normally start with e.g. "Høyesterett dom 30. januar 2026, HR-...".
    idx = text.find(ref)
    prefix = text[max(0, idx - 120):idx] if idx >= 0 else text[:120]
    match = re.search(r"(\d{1,2}\.\s+[A-Za-zÆØÅæøå]+\s+20\d{2})", prefix)
    return match.group(1) if match else None
