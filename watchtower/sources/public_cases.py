"""Read-only adapter for the public KOFA case register.

The Klagenemndssekretariatet page exposes both incoming and decided cases in
an ordinary HTML table.  This adapter keeps the table contract deliberately
small: the case number is the identity and the selected columns form the
change fingerprint.
"""
from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .changes import SnapshotSource, document, integer, public_url
from .common import SourceError

DEFAULT_URL = (
    "https://www.klagenemndssekretariatet.no/"
    "klagenemda-for-offentlige-anskaffelser-kofa/innkomne-avgjorte-saker"
)
BOARD_SPECS = {
    "kofa": (DEFAULT_URL, "KOFA", ["dato", "saknr.", "type", "innklaget", "saken gjelder", "avgjørelse", "status"]),
    "media": ("https://www.klagenemndssekretariatet.no/medieklagenemnda/innkomne-avgjorte-saker", "Medieklagenemnda", ["dato", "saknr.", "klager", "avgjørelse", "status"]),
    "marketing": ("https://www.klagenemndssekretariatet.no/markedsradet/innkomne-avgjorte-saker", "Markedsrådet", ["dato", "saknr.", "part/klager", "saken gjelder", "avgjørelse", "regelverk", "status"]),
}


class PublicCasesSource(SnapshotSource):
    """Monitor the publicly listed KOFA incoming/decided cases."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        # The site returns one UI page (10 rows); a page is not a complete
        # register, so disappearance cannot safely mean deletion.
        if self.complete or "removed" in self.events:
            raise ValueError("KOFA page is incomplete; complete_snapshot and removed events are unsupported")
        self.board = config.options.get("board", "kofa")
        if self.board not in BOARD_SPECS:
            raise ValueError("board must be kofa, media or marketing")
        default_url, self.prefix, self.expected_headers = BOARD_SPECS[self.board]
        if config.urls and config.urls != (default_url,):
            raise ValueError("public_cases accepts only the selected board's official register URL")
        self.url = default_url
        statuses = config.options.get("statuses", [])
        if not isinstance(statuses, list) or any(not isinstance(x, str) or not x.strip() for x in statuses):
            raise ValueError("statuses must be a string array")
        self.statuses = {x.strip().casefold() for x in statuses}
        self.max_pages = integer(config.options.get("max_pages", 1), "max_pages", 1, 20)

    def read_records(self):
        url, visited, pages, all_rows = self.url, set(), 0, []
        while url and pages < self.max_pages:
            if url in visited or urlsplit(url).netloc != urlsplit(self.url).netloc:
                raise SourceError("KOFA pagination loops or leaves configured host")
            visited.add(url)
            soup = BeautifulSoup(document(self, url), "html.parser")
            table = soup.find("table")
            if table is None:
                raise SourceError("KOFA case table is missing")
            headers = [" ".join(cell.stripped_strings).casefold() for cell in table.find_all("th")]
            if headers[:len(self.expected_headers)] != self.expected_headers:
                raise SourceError("KOFA case table headers changed")
            rows = table.find_all("tr")
            if any(len(row.find_all("td")) not in (0, len(headers)) for row in rows):
                raise SourceError("KOFA case table contains an incomplete row")
            all_rows.extend(rows)
            if len(all_rows) > self.max_records + pages + 1:
                raise SourceError("Case-register window exceeds max_records")
            next_link = soup.select_one("a.next[href]")
            url = public_url(urljoin(url, next_link["href"])) if next_link else None
            pages += 1
        rows = []
        identities = set()
        for tr in all_rows:
            cells = tr.find_all("td")
            if not cells:
                continue
            values = [" ".join(cell.stripped_strings) for cell in cells]
            date, case = values[:2]
            try:
                if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", date):
                    raise ValueError
                datetime.strptime(date, "%d.%m.%Y")
            except ValueError:
                raise SourceError("Case-register date is invalid") from None
            if self.board == "kofa":
                kind, respondent, subject, decision, status = values[2:7]
                fields = {"type": kind, "respondent": respondent, "subject": subject, "decision": decision, "status": status}
            elif self.board == "media":
                respondent, decision, status = values[2:5]
                fields = {"respondent": respondent, "decision": decision, "status": status}
            else:
                respondent, subject, decision, regulation, status = values[2:7]
                fields = {"respondent": respondent, "subject": subject, "decision": decision, "regulation": regulation, "status": status}
            match = re.search(r"\b\d{4}/\d+\b", case)
            if not match:
                raise SourceError("KOFA row is missing a valid case number")
            key = match.group(0)
            if key in identities:
                raise SourceError("KOFA case table contains duplicate case numbers")
            identities.add(key)
            if self.statuses and status.casefold() not in self.statuses:
                continue
            link = cells[1].find("a", href=True)
            if link is None:
                raise SourceError("KOFA case number lacks a link")
            url = public_url(urljoin(self.url, link["href"]))
            if urlsplit(url).netloc != urlsplit(self.url).netloc:
                raise SourceError("Case detail link leaves the official host")
            fields["date"] = date
            rows.append({"key": f"{self.board}:{key}", "title": f"{self.prefix} {key} · {respondent}",
                         "url": url, "published": None, "fields": fields})
        if not rows and not self.statuses:
            raise SourceError("KOFA case table returned no selected cases")
        if len({row["key"] for row in rows}) != len(rows):
            raise SourceError("KOFA case table contains duplicate case numbers")
        return rows
