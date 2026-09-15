"""Formal consumer-enforcement decision links, grouped by source publication year."""
from __future__ import annotations

from dataclasses import replace
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, document, integer
from .common import SourceError

URL = "https://www.forbrukertilsynet.no/lov-og-rett/vedtak"
CASE = re.compile(r"FOV[-–][0-9]{4}[-–][0-9A-Z]+(?:[-–][0-9A-Z]+)*\b", re.I)
YEAR = re.compile(r"20[0-9]{2}")


class ConsumerDecisionsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (URL,)):
            raise ValueError("consumer_decisions accepts only the official decision index")
        if self.complete or "removed" in self.events:
            raise ValueError("Decision-index windows cannot confirm removals")
        self.latest_years = integer(config.options.get("latest_years", 3), "latest_years", 1, 10)
        self.field_labels = {"case_number": "Saksnummer", "listed_title": "Listetittel", **self.field_labels}

    def read_records(self):
        soup = BeautifulSoup(document(self, URL), "html.parser")
        articles = soup.select("main article.dokumenttype-vedtak")
        if len(articles) != 1:
            raise SourceError("Consumer decision index article is missing or ambiguous")
        blocks = articles[0].select("details.accordion__item, div.page-list-accordion")
        years = {}
        for block in blocks:
            label = block.find("summary" if block.name == "details" else "button", recursive=False)
            year = " ".join(label.stripped_strings) if label else ""
            if not YEAR.fullmatch(year) or year in years:
                raise SourceError("Consumer decision year sections are invalid or duplicated")
            years[year] = block
        if len(years) < self.latest_years:
            raise SourceError("Consumer decision index lacks the requested year groups")
        # Choose published groups, so January does not require a not-yet-published year.
        selected = sorted(years, reverse=True)[:self.latest_years]
        found = {}
        for year in selected:
            anchors = years[year].find_all("a")
            if not anchors:
                raise SourceError("Consumer decision year group is empty")
            for anchor in anchors:
                title = " ".join(anchor.stripped_strings)
                match = CASE.match(title)
                if not match or not title[match.end():].strip(" :–-"):
                    raise SourceError("Consumer decision link lacks a case number or title")
                href = _href(anchor.get("href"))
                case = match.group().replace("–", "-").upper()
                existing = found.get(href)
                if existing and (existing["case_number"] != case or existing["title"] != title):
                    raise SourceError("Consumer decision duplicate link has conflicting metadata")
                if existing is None:
                    existing = found.setdefault(href, {"title": title, "case_number": case, "years": set()})
                existing["years"].add(year)
                if len(found) > self.max_records:
                    raise SourceError("Consumer decision index exceeds max_records")
        return [{"key": href, "title": value["title"], "url": href, "published": None,
                 "source_years": sorted(value["years"]),
                 "fields": {"case_number": value["case_number"], "listed_title": value["title"]}}
                for href, value in sorted(found.items())]

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        label = "Nyoppført vedtakslenke" if event == "added" else "Endrede listeopplysninger"
        return replace(item, alert_details=(label, *details[1:],
            "Årsgruppe i kilden: " + ", ".join(row["source_years"]),
            "Årsgruppen er ikke en eksakt vedtaksdato; dokumentinnhold og senere klageutfall overvåkes ikke"))


def _href(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise SourceError("Consumer decision link is empty")
    href = urljoin(URL, raw)
    try:
        parsed = urlparse(href)
        if parsed.scheme != "https" or parsed.netloc != "www.forbrukertilsynet.no" or parsed.query or parsed.fragment:
            raise ValueError
        if not (re.fullmatch(r"/lov-og-rett/vedtak/[a-z0-9][a-z0-9-]*", parsed.path)
                or re.fullmatch(r"/wp-content/uploads/[0-9]{4}/[0-9]{2}/[a-z0-9][a-z0-9-]*\.pdf", parsed.path, re.I)):
            raise ValueError
    except ValueError:
        raise SourceError("Consumer decision link is outside the official path contract") from None
    return href
