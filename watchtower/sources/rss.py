from __future__ import annotations

import xml.etree.ElementTree as ET
import re
from dataclasses import replace
from html import unescape
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .common import Source, SourceError
from ..models import Item
from ..rss_profiles import resolve_profile_urls


class RssSource(Source):
    """Monitor one or more ordinary RSS or Atom feeds."""

    def __init__(self, config, *args, **kwargs) -> None:
        super().__init__(config, *args, **kwargs)
        raw_profiles = config.options.get("profiles", [])
        if not isinstance(raw_profiles, list) or not all(
            isinstance(profile, str) for profile in raw_profiles
        ):
            raise ValueError("RSS profiles must be a string array")
        profile_urls = resolve_profile_urls(raw_profiles) if raw_profiles else ()
        self.feed_urls = tuple(dict.fromkeys((*config.urls, *profile_urls)))
        self.allow_empty = config.options.get("allow_empty", False)
        if not isinstance(self.allow_empty, bool):
            raise ValueError("RSS allow_empty must be true or false")

        raw_hosts = config.options.get("exclude_url_hosts", [])
        if not isinstance(raw_hosts, list) or any(not isinstance(host, str) for host in raw_hosts):
            raise ValueError("RSS exclude_url_hosts must be a string array of exact hostnames")
        self.exclude_url_hosts = set()
        for host in raw_hosts:
            normalized = host.casefold().rstrip(".")
            if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", normalized):
                raise ValueError("RSS exclude_url_hosts requires exact hostnames without scheme, port, path or wildcard")
            self.exclude_url_hosts.add(normalized)

        segments = config.options.get("exclude_url_path_segments", [])
        if not isinstance(segments, list) or any(
            not isinstance(segment, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", segment)
            for segment in segments
        ):
            raise ValueError("RSS exclude_url_path_segments requires exact path segments without slashes or wildcards")
        self.exclude_url_path_segments = set(segments)
        prefixes = config.options.get("exclude_url_path_segment_prefixes", [])
        if not isinstance(prefixes, list) or any(
            not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix)
            for prefix in prefixes
        ):
            raise ValueError(
                "RSS exclude_url_path_segment_prefixes requires non-empty path segment prefixes without slashes or wildcards"
            )
        self.exclude_url_path_segment_prefixes = tuple(dict.fromkeys(prefixes))
        categories = config.options.get("exclude_categories", [])
        if not isinstance(categories, list) or any(
            not isinstance(category, str) or not category.strip()
            or category != category.strip() for category in categories
        ):
            raise ValueError("RSS exclude_categories requires non-empty exact category labels")
        self.exclude_categories = frozenset(category.casefold() for category in categories)

    def fetch(self) -> list[Item]:
        if not self.feed_urls:
            raise SourceError("RSS source requires at least one feed URL")

        items: list[Item] = []
        seen: set[str] = set()
        for feed_url in self.feed_urls:
            response = self.get(feed_url)
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as exc:
                raise SourceError("invalid RSS or Atom XML") from exc

            root_type = _local(root.tag).casefold()
            if root_type == "feed":
                parsed = _atom_items(self.config.id, root, feed_url, self.exclude_categories)
                nodes = [node for node in root if _local(node.tag) == "entry"]
            elif root_type in {"rss", "rdf"}:
                if not any(_local(node.tag) == "channel" for node in root):
                    raise SourceError("RSS feed is missing its channel")
                parsed = _rss_items(self.config.id, root, feed_url, self.exclude_categories)
                nodes = [node for node in root.iter() if _local(node.tag) == "item"]
            else:
                raise SourceError("unsupported RSS or Atom format")
            if nodes and len(parsed) != len(nodes):
                raise SourceError("RSS or Atom feed contained no usable items or malformed entries")
            if not parsed and not self.allow_empty:
                raise SourceError("RSS or Atom feed contained no usable items")
            for item in parsed:
                if item.key in seen:
                    continue
                seen.add(item.key)
                if (self.exclude_url_hosts or self.exclude_url_path_segments
                        or self.exclude_url_path_segment_prefixes):
                    try:
                        parsed_url = urlsplit(item.url)
                        host = (parsed_url.hostname or "").casefold().rstrip(".")
                    except ValueError as exc:
                        raise SourceError("RSS item URL is invalid for URL filtering") from exc
                    path_segments = [segment for segment in parsed_url.path.split("/") if segment]
                    if (host in self.exclude_url_hosts
                            or self.exclude_url_path_segments.intersection(path_segments)
                            or any(
                                segment.startswith(prefix)
                                for segment in path_segments
                                for prefix in self.exclude_url_path_segment_prefixes
                            )):
                        # Keep identity/history and valid-feed health; suppress only delivery.
                        item = replace(item, suppress_alert=True)
                items.append(item)

        if not items and not self.allow_empty:
            raise SourceError("RSS or Atom feeds contained no usable items")
        return items


def _rss_items(source_id: str, root: ET.Element, feed_url: str, excluded_categories=()) -> list[Item]:
    nodes = [node for node in root.iter() if _local(node.tag) == "item"]
    out: list[Item] = []
    for node in nodes:
        title = _clean_markup(_child_text(node, "title"))
        link = _resolve_url(feed_url, _child_text(node, "link"))
        guid = _child_text(node, "guid")
        key = link or (f"{feed_url}#guid={guid}" if guid else "")
        if not title or not key:
            continue
        description = _clean_markup(
            _child_text(node, "description") or _child_text(node, "encoded")
        )
        categories = _child_texts(node, "category")
        out.append(
            Item(
                source_id=source_id,
                key=key,
                title=title,
                url=link or feed_url,
                published=_child_text(node, "pubDate") or _child_text(node, "date") or None,
                text=description,
                metadata={"categories": " | ".join(categories)},
                suppress_alert=any(category.casefold() in excluded_categories for category in categories),
            )
        )
    return out


def _atom_items(source_id: str, root: ET.Element, feed_url: str, excluded_categories=()) -> list[Item]:
    out: list[Item] = []
    for node in root:
        if _local(node.tag) != "entry":
            continue
        title = _clean_markup(_child_text(node, "title"))
        link = _resolve_url(feed_url, _atom_link(node))
        key = _child_text(node, "id") or link
        if not title or not key:
            continue
        body = _child_text(node, "summary") or _child_text(node, "content")
        categories = [
            str(child.attrib.get("term") or _element_text(child)).strip()
            for child in node
            if _local(child.tag) == "category"
            and str(child.attrib.get("term") or _element_text(child)).strip()
        ]
        out.append(
            Item(
                source_id=source_id,
                key=key,
                title=title,
                url=link or feed_url,
                published=_child_text(node, "published") or _child_text(node, "updated") or None,
                text=_clean_markup(body),
                metadata={"categories": " | ".join(categories)},
                suppress_alert=any(category.casefold() in excluded_categories for category in categories),
            )
        )
    return out


def _atom_link(node: ET.Element) -> str:
    fallback = ""
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = str(child.attrib.get("href") or "").strip()
        if not href:
            continue
        if child.attrib.get("rel", "alternate") == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def _child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _local(child.tag) == name:
            return _element_text(child)
    return ""


def _child_texts(node: ET.Element, name: str) -> list[str]:
    return [
        value
        for child in node
        if _local(child.tag) == name and (value := _element_text(child))
    ]


def _element_text(node: ET.Element) -> str:
    return " ".join(part.strip() for part in node.itertext() if part and part.strip())


def _clean_markup(value: str) -> str:
    return " ".join(BeautifulSoup(unescape(value), "html.parser").stripped_strings)


def _resolve_url(base_url: str, value: str) -> str:
    return urljoin(base_url, value) if value else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
