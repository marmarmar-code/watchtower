"""Selected page text and link-list events; no browser or JavaScript runtime."""
import re
from copy import deepcopy
from dataclasses import replace
from difflib import SequenceMatcher
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from soupsieve import compile as compile_selector

from .changes import SnapshotSource, document, integer, public_url, strings
from .common import SourceError


class WebChangesSource(SnapshotSource):
    def describe_change(self, name, before, after):
        if self.config.kind != "web_page":
            return super().describe_change(name, before, after)
        old, new = before.split(), after.split()
        excerpts = []
        for tag, a, b, c, d in SequenceMatcher(None, old, new).get_opcodes():
            if tag == "equal":
                continue
            excerpts.append("Før: " + (" ".join(old[max(0,a-4):min(len(old),b+4)])[:180] or "(tomt)")
                            + " → Nå: " + (" ".join(new[max(0,c-4):min(len(new),d+4)])[:180] or "(tomt)"))
            if len(excerpts) == 3:
                break
        return " | ".join(excerpts)

    def __init__(self, config, *args, **kwargs):
        self.excluded_prefixes = []
        for prefix in strings(config.options.get("exclude_url_prefixes", []), "exclude_url_prefixes", empty=True):
            parsed = urlsplit(public_url(prefix))
            if (config.kind != "web_links" or prefix != prefix.strip()
                    or not parsed.path.endswith("/") or parsed.query or parsed.fragment
                    or any(char.isspace() for char in prefix) or "*" in prefix):
                raise ValueError("exclude_url_prefixes requires HTTPS directory URLs without queries or wildcards, for web_links only")
            self.excluded_prefixes.append((parsed.netloc.casefold(), parsed.path))
        # Notification routing and presentation do not alter monitored fields,
        # record identities or the scope of an existing snapshot.
        snapshot_config = replace(config, options={
            key: value for key, value in config.options.items()
            if key not in {"exclude_url_prefixes", "display_title_selector", "display_ignore_selectors", "text_selector",
                           "published_selector", "published_attribute", "published_text_separator"}
        })
        super().__init__(snapshot_config, *args, **kwargs)
        self.config = config
        if len(config.urls) != 1:
            raise ValueError("Web monitoring requires exactly one URL")
        self.url = public_url(config.urls[0])
        self.selector = config.options.get("selector")
        if not isinstance(self.selector, str) or not self.selector.strip():
            raise ValueError("Web monitoring requires an explicit CSS selector")
        self.ignore = strings(config.options.get("ignore_selectors", []), "ignore_selectors", empty=True)
        self.title_selector = config.options.get("title_selector")
        self.display_title_selector = config.options.get("display_title_selector")
        self.display_ignore = strings(
            config.options.get("display_ignore_selectors", []), "display_ignore_selectors", empty=True
        )
        self.text_selector = config.options.get("text_selector")
        self.published_selector = config.options.get("published_selector")
        self.published_attribute = config.options.get("published_attribute")
        self.published_text_separator = config.options.get("published_text_separator")
        self._previous_publication_rows = {}
        for name in ("title_selector", "display_title_selector", "text_selector", "published_selector"):
            selector = getattr(self, name)
            if selector is not None:
                if not isinstance(selector, str) or not selector.strip():
                    raise ValueError(f"{name} must be a non-empty CSS selector")
                compile_selector(selector)
        if config.kind != "web_links" and (
            self.display_title_selector is not None or self.text_selector is not None
            or self.published_selector is not None
        ):
            raise ValueError("display_title_selector, text_selector and published_selector require web_links")
        for name in ("published_attribute", "published_text_separator"):
            value = getattr(self, name)
            if value is not None and (not self.published_selector or not isinstance(value, str)
                                      or not value.strip() or len(value) > 100):
                raise ValueError(f"{name} requires published_selector and a non-empty string")
        if self.published_attribute and not re.fullmatch(r"[A-Za-z_:][\w:.-]*", self.published_attribute):
            raise ValueError("published_attribute must be an HTML attribute name")
        if self.display_ignore and self.display_title_selector is None:
            raise ValueError("display_ignore_selectors requires display_title_selector")
        for selector in (self.selector, *self.ignore, *self.display_ignore):
            compile_selector(selector)
        self.minimum = integer(config.options.get("min_text_length", 20), "min_text_length", 1, 100000)
        self.maximum = integer(config.options.get("max_text_length", 100000), "max_text_length", 1, 1000000)
        if self.minimum > self.maximum:
            raise ValueError("min_text_length exceeds max_text_length")
        if self.thresholds:
            raise ValueError("Numeric thresholds apply to structured records, not page text")

    def fetch_with_state(self, previous):
        if not self.published_selector:
            return super().fetch_with_state(previous)
        # A presentation upgrade must neither mutate the caller's history nor
        # invent the date of a legacy title that also changed at the source.
        previous = deepcopy(previous)
        stored = ((previous or {}).get("source_state") or {}).get("records", {})
        self._previous_publication_rows = stored.get("rows", {}) if stored.get("scope") == self.scope else {}
        try:
            return super().fetch_with_state(previous)
        finally:
            self._previous_publication_rows = {}

    def _normalize_legacy_publication_title(self, record):
        before = self._previous_publication_rows.get(record["key"], {})
        old_row = before.get("row", {})
        if not before.get("present") or old_row.get("publication_title_normalized"):
            return
        old_title = old_row.get("fields", {}).get("title")
        if not isinstance(old_title, str):
            raise SourceError("Invalid prior link title; previous state preserved")
        if old_title == record["fields"]["title"]:
            return
        publication_text = old_row.get("publication_text") or record["publication_text"]
        if publication_text not in old_title:
            raise SourceError("Publication presentation migration lacks a comparable prior date; previous state preserved")
        old_row["fields"]["title"] = re.sub(r"\s+", " ", old_title.replace(publication_text, "", 1)).strip()

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        if "display_title" in row:
            # On a new link the heading already carries the title; repeating the
            # complete card in its field details defeats clean presentation.
            item = replace(item, title=row["display_title"],
                           alert_details=details[:1] if event == "added" else item.alert_details)
        if row.get("search_text"):
            item = replace(item, text=item.text + "\n" + row["search_text"])
        parsed = urlsplit(item.url)
        if any(parsed.scheme == "https" and parsed.netloc.casefold() == host
               and parsed.path.startswith(path) for host, path in self.excluded_prefixes):
            return replace(item, suppress_alert=True)
        return item

    def read_records(self):
        raw = document(self, self.url)
        soup = BeautifulSoup(raw, "html.parser")
        for node in soup.select("script, style, noscript, nav, footer"):
            node.decompose()
        for selector in self.ignore:
            for node in soup.select(selector):
                node.decompose()
        nodes = soup.select(self.selector)
        if not nodes:
            raise SourceError("Configured page selector no longer matches; previous state preserved")
        if self.config.kind == "web_page":
            text = re.sub(r"\s+", " ", " ".join(node.get_text(" ", strip=True) for node in nodes)).strip()
            if not self.minimum <= len(text) <= self.maximum:
                raise SourceError("Selected page text is outside configured length limits")
            return [{"key": self.url, "title": self.config.label, "url": self.url, "fields": {"text": text}}]
        records = {}
        for node in nodes:
            if not node.get("href"):
                raise SourceError("Link selector must select anchors with href")
            href = node["href"].strip()
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            url = public_url(urldefrag(urljoin(self.url, href))[0])
            title_node = node.select_one(self.title_selector) if self.title_selector else node
            if title_node is None:
                raise SourceError("Configured link title selector no longer matches")
            title = re.sub(r"\s+", " ", title_node.get_text(" ", strip=True)).strip()
            if not title:
                raise SourceError("Selected link lacks a visible title")
            record = {"key": url, "title": title, "url": url, "fields": {"title": title}}
            if self.published_selector:
                dates = node.select(self.published_selector)
                if len(dates) != 1:
                    raise SourceError("Configured published selector must match exactly one date per link")
                date_node = dates[0]
                published = (date_node.get(self.published_attribute) if self.published_attribute
                             else date_node.get_text(" ", strip=True))
                if not isinstance(published, str) or not published.strip():
                    raise SourceError("Selected publication date or attribute is missing")
                if self.published_text_separator:
                    if self.published_text_separator not in published:
                        raise SourceError("Configured publication text separator no longer matches")
                    published = published.split(self.published_text_separator, 1)[0]
                published = re.sub(r"\s+", " ", published).strip()
                if not published:
                    raise SourceError("Selected publication date is empty")
                # Keep the source's date and timezone verbatim. A time element
                # inside a title is transport/presentation, not a news change.
                record["published"] = published
                clean_title = deepcopy(title_node)
                embedded_dates = clean_title.select(self.published_selector)
                if embedded_dates:
                    record["raw_title"] = title
                    record["publication_text"] = re.sub(r"\s+", " ", date_node.get_text(" ", strip=True)).strip()
                    for date in embedded_dates:
                        date.decompose()
                    title = re.sub(r"\s+", " ", clean_title.get_text(" ", strip=True)).strip()
                    if not title:
                        raise SourceError("Selected link lacks a title apart from its publication date")
                    record["title"] = record["fields"]["title"] = title
                    record["publication_title_normalized"] = True
                    self._normalize_legacy_publication_title(record)
            if self.display_title_selector:
                display_node = node.select_one(self.display_title_selector)
                if display_node is None:
                    raise SourceError("Configured display title selector no longer matches")
                display_node = deepcopy(display_node)
                for selector in self.display_ignore:
                    for ignored in display_node.select(selector):
                        ignored.decompose()
                display_title = re.sub(r"\s+", " ", display_node.get_text(" ", strip=True)).strip()
                if not display_title:
                    raise SourceError("Selected link lacks a visible display title")
                record["display_title"] = display_title
            if self.text_selector:
                text_nodes = node.select(self.text_selector)
                if not text_nodes:
                    raise SourceError("Configured link text selector no longer matches")
                record["search_text"] = re.sub(
                    r"\s+", " ", " ".join(part.get_text(" ", strip=True) for part in text_nodes)
                ).strip()
            if url in records and records[url]["title"] != title:
                raise SourceError("Link list has conflicting titles for the same URL")
            records[url] = record
        return list(records.values())
