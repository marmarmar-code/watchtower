"""Selected page text and link-list events; no browser or JavaScript runtime."""
import re
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
        # Notification routing does not alter the selected records or their scope.
        snapshot_config = replace(config, options={
            key: value for key, value in config.options.items() if key != "exclude_url_prefixes"
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
        if self.title_selector is not None:
            if not isinstance(self.title_selector, str):
                raise ValueError("title_selector must be a CSS selector")
            compile_selector(self.title_selector)
        for selector in (self.selector, *self.ignore):
            compile_selector(selector)
        self.minimum = integer(config.options.get("min_text_length", 20), "min_text_length", 1, 100000)
        self.maximum = integer(config.options.get("max_text_length", 100000), "max_text_length", 1, 1000000)
        if self.minimum > self.maximum:
            raise ValueError("min_text_length exceeds max_text_length")
        if self.thresholds:
            raise ValueError("Numeric thresholds apply to structured records, not page text")

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
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
            if url in records and records[url]["title"] != title:
                raise SourceError("Link list has conflicting titles for the same URL")
            records[url] = record
        return list(records.values())
