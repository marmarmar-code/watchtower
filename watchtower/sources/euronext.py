from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .common import Source, SourceError
from ..models import Item


_LISTVIEW_PATH = "/listview/company-press-release/"
_DIRECT_NEWS_PATH = "/company-news/"
_DATE_TOKEN = r"(?:\d{1,2}/\d{1,2}/20\d{2}|\d{1,2}\s+[A-Za-zÀ-ÖØ-öø-ÿ]{3,12}\s+20\d{2})"
_DATE_ONLY = re.compile(rf"^{_DATE_TOKEN}$", re.IGNORECASE)
_DATE_AND_TITLE = re.compile(rf"^({_DATE_TOKEN})\s*(?:\||[-–—])\s*(.+)$", re.IGNORECASE)
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(?:\s+[A-Z]{2,5})?$")
_NOISE = {
    "abonner",
    "subscribe",
    "se alle",
    "see all",
    "voir tout",
    "open in new window",
    "press release",
}


class EuronextSource(Source):
    def fetch(self) -> list[Item]:
        if not self.config.urls:
            raise SourceError("Euronext source requires issuer URLs")

        out: list[Item] = []
        seen: set[str] = set()
        fallback_errors: list[SourceError] = []
        for page_url in self.config.urls:
            company_soup = BeautifulSoup(self.get(page_url).text, "html.parser")
            page_items = _company_page_items(self.config.id, company_soup, page_url)

            if not page_items:
                for list_url in _listview_urls(company_soup, page_url):
                    try:
                        list_soup = BeautifulSoup(self.get(list_url).text, "html.parser")
                    except SourceError as exc:
                        fallback_errors.append(exc)
                        continue
                    page_items.extend(_listview_items(self.config.id, list_soup, list_url))

            for item in page_items:
                if item.key in seen:
                    continue
                seen.add(item.key)
                out.append(item)

        if not out:
            if fallback_errors:
                raise SourceError(f"Euronext fallback failed: {fallback_errors[-1]}")
            raise SourceError("Euronext page contained no company news items")
        return out


def _company_page_items(source_id: str, soup: BeautifulSoup, page_url: str) -> list[Item]:
    section = _news_section(soup)
    rows = _dated_title_pairs(section)
    direct_urls = _direct_news_urls(section, page_url)
    if not direct_urls and section is not soup:
        direct_urls = _direct_news_urls(soup, page_url)
    if direct_urls and len(rows) > len(direct_urls):
        rows = rows[:len(direct_urls)]

    company = _issuer_name(soup)
    out: list[Item] = []
    for index, (published, title) in enumerate(rows):
        item_url = direct_urls[index] if index < len(direct_urls) else page_url
        key = item_url if item_url != page_url else _fallback_key(
            published=published,
            company=company,
            title=title,
        )
        text = " | ".join(part for part in (company, title) if part)
        out.append(Item(
            source_id=source_id,
            key=key,
            title=title,
            url=item_url,
            published=published,
            text=text,
            metadata={"company": company, "dataset": "issuer news"},
        ))
    if out:
        return out
    return _direct_items(source_id, soup, page_url, company=company)


def _news_section(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    for link in soup.find_all("a", href=True):
        if _LISTVIEW_PATH not in str(link.get("href") or ""):
            continue
        for parent in link.parents:
            if not isinstance(parent, Tag):
                continue
            pairs = _dated_title_pairs(parent)
            if 1 <= len(pairs) <= 20:
                return parent
    return soup


def _dated_title_pairs(node: Tag | BeautifulSoup) -> list[tuple[str, str]]:
    values = [" ".join(value.split()) for value in node.stripped_strings]
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(values):
        combined = _DATE_AND_TITLE.fullmatch(value)
        if combined:
            pair = (combined.group(1), combined.group(2).strip())
            if _use_title(pair[1]) and pair not in seen:
                out.append(pair)
                seen.add(pair)
            continue
        if not _DATE_ONLY.fullmatch(value):
            continue
        for candidate in values[index + 1:index + 6]:
            if _DATE_ONLY.fullmatch(candidate) or _DATE_AND_TITLE.fullmatch(candidate):
                break
            if _use_title(candidate):
                pair = (value, candidate)
                if pair not in seen:
                    out.append(pair)
                    seen.add(pair)
                break
    return out


def _use_title(value: str) -> bool:
    cleaned = " ".join(value.split()).strip(" |")
    if len(cleaned) < 8 or cleaned.casefold() in _NOISE:
        return False
    if _TIME_ONLY.fullmatch(cleaned):
        return False
    return any(character.isalpha() for character in cleaned)


def _issuer_name(soup: BeautifulSoup) -> str:
    page_title = soup.find("title")
    if page_title is not None:
        title = " ".join(page_title.stripped_strings)
        issuer = title.split("|", 1)[0].strip()
        if issuer:
            return issuer
    heading = soup.find("h1")
    return " ".join(heading.stripped_strings) if heading is not None else ""


def _direct_news_urls(soup: Tag | BeautifulSoup, page_url: str) -> list[str]:
    urls: list[str] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(page_url, str(link.get("href") or "").strip())
        if _DIRECT_NEWS_PATH in href and href not in urls:
            urls.append(href)
    return urls


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


def _direct_items(
    source_id: str,
    soup: BeautifulSoup,
    page_url: str,
    *,
    company: str = "",
) -> list[Item]:
    out: list[Item] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(page_url, str(link["href"]))
        if _DIRECT_NEWS_PATH not in href:
            continue
        title = " ".join(link.stripped_strings)
        if not _use_title(title):
            continue
        parent = link.parent
        context = " ".join(parent.stripped_strings) if parent else title
        published = _first_date(context)
        out.append(Item(
            source_id=source_id,
            key=href,
            title=title,
            url=href,
            published=published,
            text=" | ".join(part for part in (company, context) if part),
            metadata={"company": company, "dataset": "issuer news"},
        ))
    return out


def _first_date(value: str) -> str | None:
    match = re.search(_DATE_TOKEN, value, flags=re.IGNORECASE)
    return match.group(0) if match else None


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
