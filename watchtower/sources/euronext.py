from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

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


@dataclass(frozen=True)
class _LinkedIssuerItem(Item):
    """Improve the presentation URL without changing the stored item identity."""

    identity_url: str = ""
    issuer_url: str = ""

    def _fallback_hash_payload(self, *, include_alert_details: bool) -> str:
        return Item._fallback_hash_payload(
            replace(self, url=self.identity_url),
            include_alert_details=include_alert_details,
        )


class EuronextSource(Source):
    def fetch_with_state(self, previous: dict | None) -> list[Item]:
        self._baseline = previous is None
        self._previous_seen = previous.get("seen", {}) if previous else {}
        try:
            self._historical_before = None
            checked = previous.get("last_checked_at") if previous else None
            if self.config.options.get("include_listview", False) and checked is not None:
                try:
                    last_success = datetime.fromisoformat(checked)
                    if last_success.tzinfo is None:
                        raise ValueError("missing timezone")
                    # Allow the preceding UTC date for timezone and delayed publication.
                    self._historical_before = (last_success.astimezone(timezone.utc).date()
                                               - timedelta(days=1)).isoformat()
                except (TypeError, ValueError) as exc:
                    raise SourceError("Invalid previous Euronext check time; history preserved") from exc
            return self.fetch()
        finally:
            self._previous_seen = {}
            self._baseline = False
            self._historical_before = None

    def fetch(self) -> list[Item]:
        if not self.config.urls:
            raise SourceError("Euronext source requires issuer URLs")

        expanded = self.config.options.get("include_listview", False)
        limit = self.config.options.get("max_items", 50)
        if not isinstance(expanded, bool) or type(limit) is not int or not 1 <= limit <= 50:
            raise SourceError("Euronext requires boolean include_listview and max_items from 1 to 50")
        self.coverage_warnings = []
        out: list[Item] = []
        seen: set[str] = set()
        for page_url in self.config.urls:
            page_items = self._fetch_issuer(page_url)

            for item in page_items:
                if item.key in seen:
                    continue
                seen.add(item.key)
                out.append(item)

        cutoff = getattr(self, "_historical_before", None)
        if cutoff:
            previous = getattr(self, "_previous_seen", {})
            for index, item in enumerate(out):
                if item.key in previous:
                    continue  # Known notices retain their existing revision semantics.
                published = _iso_date(item.published or "")
                if published is None:
                    raise SourceError("New expanded Euronext notice lacks a usable date; history preserved")
                if published < cutoff:
                    # Backfilled archive rows remain in history, but are not new news.
                    out[index] = replace(item, suppress_alert=True)
        return self._notification_links(out)

    def _notification_links(self, items: list[Item]) -> list[Item]:
        """Resolve only new/changed modal notices to their official NewsWeb link."""
        out = []
        previous = getattr(self, "_previous_seen", {})
        for item in items:
            if not isinstance(item, _LinkedIssuerItem):
                out.append(item)
                continue
            fallback = replace(item, url=item.identity_url)
            if item.suppress_alert or getattr(self, "_baseline", False) or previous.get(item.key) in item.compatible_content_hashes():
                out.append(fallback)
                continue
            node_id = item.url.rsplit("/", 1)[-1]
            try:
                response = self.get(
                    f"https://live.euronext.com/en/ajax/node/company-press-release/{node_id}"
                )
            except SourceError:
                out.append(fallback)
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            record = soup.select_one("[data-node-path][data-isin]")
            heading = soup.find("h1")
            isin = re.search(r"/equities/([A-Z]{2}[A-Z0-9]{10})-", (item.issuer_url or item.identity_url))
            published_date = _iso_date(item.published or "")
            node_path = str(record.get("data-node-path") or "") if record else ""
            matching_path = node_path == f"/node/{node_id}" or (
                published_date is not None
                and node_path.startswith(f"/products/equities/company-news/{published_date}-")
            )
            identifiers = {value.strip() for value in str(record.get("data-isin") or "").split(",")} if record else set()
            if (record is None or heading is None or isin is None
                    or not matching_path or isin.group(1) not in identifiers
                    or " ".join(heading.stripped_strings).casefold() != item.title.casefold()):
                out.append(fallback)
                continue
            links = {str(link["href"]) for link in record.find_all("a", href=True)
                     if re.fullmatch(r"https://newsweb\.oslobors\.no/message/[0-9]+", str(link["href"]))}
            out.append(replace(item, url=links.pop()) if len(links) == 1 else fallback)
        return out

    def _fetch_issuer(self, page_url: str) -> list[Item]:
        """Fetch one issuer, retrying only an HTTP-successful empty page."""
        if self.config.options.get("include_listview", False):
            return self._expanded_issuer(page_url)
        for attempt in range(1, self.retry_attempts + 1):
            company_soup = BeautifulSoup(self.get(page_url).text, "html.parser")
            page_items = _company_page_items(self.config.id, company_soup, page_url)

            if not page_items:
                fallback_errors: list[SourceError] = []
                for list_url in _listview_urls(company_soup, page_url):
                    try:
                        list_soup = BeautifulSoup(self.get(list_url).text, "html.parser")
                    except SourceError as exc:
                        fallback_errors.append(exc)
                        continue
                    page_items.extend(_listview_items(self.config.id, list_soup, list_url))

                if not page_items and fallback_errors:
                    raise SourceError(f"Euronext fallback failed: {fallback_errors[-1]}")

            if page_items:
                return page_items
            if attempt < self.retry_attempts:
                self.sleep(min(2.0, float(attempt)))

        raise SourceError("Euronext page contained no company news items")


    def _expanded_issuer(self, page_url: str) -> list[Item]:
        page = urlsplit(page_url)
        if page.scheme != "https" or page.netloc != "live.euronext.com":
            raise SourceError("Expanded Euronext requires an official issuer URL")
        company = BeautifulSoup(self.get(page_url).text, "html.parser")
        urls = [url for url in _listview_urls(company, page_url)
                if urlsplit(url).scheme == "https" and urlsplit(url).netloc == page.netloc
                and re.fullmatch(r"/[a-z]{2}/listview/company-press-release/[0-9]+", urlsplit(url).path)
                and not urlsplit(url).query and not urlsplit(url).fragment]
        if len(urls) != 1:
            raise SourceError("Expanded Euronext requires one unambiguous issuer list")
        soup = BeautifulSoup(self.get(urls[0]).text, "html.parser")
        items = _listview_items(self.config.id, soup, urls[0], issuer_url=page_url)
        if not items or len({item.key for item in items}) != len(items):
            raise SourceError("Expanded Euronext list is empty or has duplicate identities")
        latest = _company_page_items(self.config.id, company, page_url)
        pairs = {(_iso_date(item.published or ""), item.title) for item in items}
        if not latest or any((_iso_date(item.published or ""), item.title) not in pairs for item in latest):
            raise SourceError("Expanded Euronext list does not contain current issuer notices")
        limit = self.config.options.get("max_items", 50)
        items = items[:limit]
        previous = getattr(self, "_previous_seen", {})
        if previous and len(items) == limit and not any(item.key in previous for item in items):
            self.coverage_warnings.append("Euronext list has no overlap with previous history; older notices may be missing")
        return items


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
        item = Item(
            source_id=source_id,
            key=key,
            title=title,
            url=item_url,
            published=published,
            text=text,
            metadata={"company": company, "dataset": "issuer news"},
        )
        if item_url == page_url:
            direct_url = _node_news_url(section, page_url, published, title)
            if direct_url:
                item = _LinkedIssuerItem(
                    **{**vars(item), "url": direct_url}, identity_url=page_url,
                )
        out.append(item)
    if out:
        return out
    return _direct_items(source_id, soup, page_url, company=company)


def _iso_date(value: str) -> str | None:
    value = _first_date(value) or value
    for pattern in ("%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _node_news_url(
    section: Tag | BeautifulSoup, page_url: str, published: str, title: str,
) -> str | None:
    """Resolve a unique title/date pair using Euronext's own row node ID."""
    page = urlsplit(page_url)
    if page.scheme != "https" or page.netloc != "live.euronext.com":
        return None
    node_ids = set()
    for link in section.select("a[data-node-nid]"):
        node_id = str(link.get("data-node-nid") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,15}", node_id):
            continue
        if " ".join(link.stripped_strings) != title:
            continue
        row = link.find_parent("tr")
        if row is not None and (published, title) in _dated_title_pairs(row):
            node_ids.add(node_id)
    if len(node_ids) != 1:
        return None
    locale = page.path.split("/")[1]
    if not re.fullmatch(r"[a-z]{2}", locale):
        locale = "en"
    return f"https://live.euronext.com/{locale}/node/{node_ids.pop()}"


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


def _listview_items(source_id: str, soup: BeautifulSoup, page_url: str, *, issuer_url: str = "") -> list[Item]:
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
            item = Item(
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
            )
            if issuer_url and item_url == page_url and title_link is not None:
                node_id = str(title_link.get("data-node-nid") or "")
                if re.fullmatch(r"[1-9][0-9]{0,15}", node_id):
                    item = _LinkedIssuerItem(
                        **{**vars(item), "url": f"https://live.euronext.com/en/node/{node_id}"},
                        identity_url=page_url, issuer_url=issuer_url,
                    )
            out.append(item)
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
