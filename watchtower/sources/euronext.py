from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .common import Source, SourceError
from ..models import Item


_LISTVIEW_PATH = "/listview/company-press-release/"
_DIRECT_NEWS_PATH = "/company-news/"


class EuronextSource(Source):
    def fetch(self) -> list[Item]:
        if not self.config.urls:
            raise SourceError("Euronext source requires issuer URLs")

        out: list[Item] = []
        seen: set[str] = set()
        for page_url in self.config.urls:
            company_soup = BeautifulSoup(self.get(page_url).text, "html.parser")
            list_urls = _listview_urls(company_soup, page_url)
            page_items: list[Item] = []
            for list_url in list_urls:
                list_soup = BeautifulSoup(self.get(list_url).text, "html.parser")
                page_items.extend(_listview_items(self.config.id, list_soup, list_url))

            if not page_items:
                page_items.extend(_direct_items(self.config.id, company_soup, page_url))

            for item in page_items:
                if item.key in seen:
                    continue
                seen.add(item.key)
                out.append(item)

        if not out:
            raise SourceError("Euronext page contained no company news items")
        return out


def _listview_urls(soup: BeautifulSoup, page_url: str) -> tuple[str, ...]:
    urls: list[str] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(page_url, str(link["href"]))
        if _LISTVIEW_PATH in href and href not in urls:
            urls.append(href)
    return tuple(urls)


def _listview_items(source_id: str, soup: BeautifulSoup, page_url: str) -> list[Item]:
    out: list[Item] = []
    for table in soup.find_all("table"):
        headers = [" ".join(cell.stripped_strings).casefold() for cell in table.find_all("th")]
        title_index = _header_index(headers, ("tittel", "title"))
        if title_index is None:
            continue
        time_index = _header_index(headers, ("tid", "time"))
        company_index = _header_index(headers, ("selskap", "company"))
        sector_index = _header_index(headers, ("sektor", "sector"))
        category_index = _header_index(headers, ("kategori", "category"))
        current_date = ""

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            values = [" ".join(cell.stripped_strings) for cell in cells]
            if len(cells) == 1:
                if _looks_like_date(values[0]):
                    current_date = values[0]
                continue
            if title_index >= len(cells):
                continue

            title = values[title_index].strip()
            if not title:
                continue
            company = _value(values, company_index)
            time_value = _value(values, time_index)
            sector = _value(values, sector_index)
            category = _value(values, category_index)
            published = " ".join(part for part in (current_date, time_value) if part).strip()

            title_link = cells[title_index].find("a", href=True)
            item_url = page_url
            if title_link is not None:
                candidate = str(title_link.get("href") or "").strip()
                if candidate and not candidate.startswith(("#", "javascript:")):
                    item_url = urljoin(page_url, candidate)

            key = item_url if item_url != page_url else _fallback_key(
                published=published,
                company=company,
                title=title,
            )
            text = " | ".join(part for part in (company, title, sector, category) if part)
            out.append(Item(
                source_id=source_id,
                key=key,
                title=title,
                url=item_url,
                published=published or None,
                text=text,
                metadata={
                    "company": company,
                    "sector": sector,
                    "category": category,
                    "dataset": "issuer news",
                },
            ))
    return out


def _direct_items(source_id: str, soup: BeautifulSoup, page_url: str) -> list[Item]:
    out: list[Item] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(page_url, str(link["href"]))
        if _DIRECT_NEWS_PATH not in href:
            continue
        title = " ".join(link.stripped_strings)
        if not title:
            continue
        parent = link.parent
        context = " ".join(parent.stripped_strings) if parent else title
        out.append(Item(
            source_id=source_id,
            key=href,
            title=title,
            url=href,
            text=context,
            metadata={"dataset": "issuer news"},
        ))
    return out


def _header_index(headers: list[str], alternatives: tuple[str, ...]) -> int | None:
    for index, header in enumerate(headers):
        if any(alternative in header for alternative in alternatives):
            return index
    return None


def _value(values: list[str], index: int | None) -> str:
    if index is None or index >= len(values):
        return ""
    return values[index].strip()


def _looks_like_date(value: str) -> bool:
    return len(value) <= 50 and re.search(r"\b20\d{2}\b", value) is not None


def _fallback_key(*, published: str, company: str, title: str) -> str:
    payload = "\0".join((published, company, title)).encode("utf-8")
    return "euronext:" + hashlib.sha256(payload).hexdigest()[:24]
