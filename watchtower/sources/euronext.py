from __future__ import annotations

from urllib.parse import urljoin
from bs4 import BeautifulSoup

from .common import Source, SourceError
from ..models import Item


class EuronextSource(Source):
    def fetch(self) -> list[Item]:
        if not self.config.urls:
            raise SourceError("Euronext source requires issuer URLs")
        out: list[Item] = []
        seen: set[str] = set()
        for page_url in self.config.urls:
            soup = BeautifulSoup(self.get(page_url).text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = urljoin(page_url, link["href"])
                if "/company-news/" not in href or href in seen:
                    continue
                title = " ".join(link.stripped_strings)
                if not title:
                    continue
                seen.add(href)
                parent = link.parent
                context = " ".join(parent.stripped_strings) if parent else title
                out.append(Item(
                    self.config.id,
                    href,
                    title,
                    href,
                    text=context,
                    metadata={"dataset": "issuer news"},
                ))
        return out
