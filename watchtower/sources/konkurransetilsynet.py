from __future__ import annotations

from urllib.parse import urljoin
from bs4 import BeautifulSoup

from .common import Source
from ..models import Item

DEFAULT_URL = "https://konkurransetilsynet.no/fusjoner-og-oppkjop-%C2%A716/"


class KonkurransetilsynetSource(Source):
    def fetch(self) -> list[Item]:
        url = self.config.urls[0] if self.config.urls else DEFAULT_URL
        soup = BeautifulSoup(self.get(url).text, "html.parser")
        out: list[Item] = []
        seen: set[str] = set()
        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            link = cells[-1].find("a", href=True)
            if not link:
                continue
            title = " ".join(link.stripped_strings)
            href = urljoin(url, link["href"])
            if not title or href in seen:
                continue
            seen.add(href)
            published = " ".join(cells[0].stripped_strings)
            out.append(Item(
                self.config.id,
                href,
                title,
                href,
                published or None,
                text=" ".join(row.stripped_strings),
                metadata={"dataset": "fusjon/oppkjøp"},
            ))
        return out
