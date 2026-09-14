"""Reviewed public package advisories in a complete bounded update window."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse

from .changes import SnapshotSource, canonical, integer
from .common import SourceError

API = "https://api.github.com/advisories"
GHSA = re.compile(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}")
SEVERITIES = {"unknown": "Ukjent", "low": "Lav", "medium": "Middels", "high": "Høy", "critical": "Kritisk"}


class PackageAdvisoriesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API,)):
            raise ValueError("package_advisories accepts only GitHub's public global advisory API")
        if self.complete or "removed" in self.events:
            raise ValueError("Advisory windows cannot confirm removals")
        self.lookback_days = integer(config.options.get("updated_days", 3), "updated_days", 1, 31)
        self.page_size = integer(config.options.get("page_size", 100), "page_size", 1, 100)
        self.max_pages = integer(config.options.get("max_pages", 3), "max_pages", 1, 5)
        self.field_labels = {"summary": "Sammendrag", "cve": "CVE", "severity": "Alvorlighetsgrad",
                             "withdrawn_at": "Tilbaketrukket", "packages": "Pakker og versjoner", **self.field_labels}

    def read_records(self):
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=self.lookback_days)
        query = {"type": "reviewed", "updated": f"{first}..{today}", "sort": "updated",
                 "direction": "desc", "per_page": str(self.page_size)}
        url = API + "?" + urlencode(query)
        visited, seen, rows = set(), set(), []
        for _ in range(self.max_pages):
            if url in visited:
                raise SourceError("Advisory cursor repeats")
            visited.add(url)
            response = self.get(url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"},
                                stream=True, allow_redirects=False, accepted_statuses=(301, 302, 303, 307, 308))
            try:
                if response.status_code != 200:
                    raise SourceError("Advisory API returned an unexpected redirect")
                chunks, length = [], 0
                for chunk in response.iter_content(64 * 1024):
                    length += len(chunk)
                    if length > self.max_bytes:
                        raise SourceError("Advisory response exceeds max_bytes")
                    chunks.append(chunk)
                batch = json.loads(b"".join(chunks))
                next_url = _next_url(response.headers.get("Link", ""), query)
            except (ValueError, UnicodeError) as exc:
                raise SourceError("Advisory response is invalid JSON") from exc
            finally:
                response.close()
            if not isinstance(batch, list) or len(batch) > self.page_size or (next_url and len(batch) != self.page_size):
                raise SourceError("Advisory page is malformed or incomplete")
            if not batch and rows:
                raise SourceError("Advisory cursor returned an empty later page")
            for entry in batch:
                row = _record(entry, first, today)
                if row["key"] in seen:
                    raise SourceError("Advisory IDs repeat across the update window")
                seen.add(row["key"]); rows.append(row)
                if len(rows) > self.max_records:
                    raise SourceError("Advisory window exceeds max_records")
            if not next_url:
                return sorted(rows, key=lambda row: row["key"])
            url = next_url
        raise SourceError("Advisory window exceeds max_pages; previous state preserved")

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        label = "Nyobservert pakkerådgivning" if event == "added" else "Endret pakkerådgivning"
        info = (label, *(() if event == "added" else details[1:]),
                "Alvorlighetsgrad: " + SEVERITIES[fields["severity"]],
                "Status: " + ("Tilbaketrukket " + fields["withdrawn_at"] if fields["withdrawn_at"] else "Ikke markert tilbaketrukket"))
        for pkg in fields["packages"][:10]:
            info += (f"{pkg['ecosystem']} · {pkg['name']}: {pkg['affected_range']} · Første oppgitte fikseversjon: {pkg['first_patched'] or 'ikke oppgitt'}",)
        if len(fields["packages"]) > 10:
            info += (f"Ytterligere {len(fields['packages']) - 10} pakke-/versjonsoppføringer står i rådgivningen",)
        info += ("Registeret oppgir berørte versjoner; dette beviser ikke faktisk utnyttelse eller berørte lokale installasjoner",)
        return replace(item, alert_details=info)

    def describe_change(self, name, before, after):
        if name == "packages":
            return "Opplysninger om berørte pakker, versjonsgrenser eller fikseversjoner er endret"
        if name == "severity":
            return f"Alvorlighetsgrad: {SEVERITIES[before]} → {SEVERITIES[after]}"
        return super().describe_change(name, before, after)


def _next_url(header, expected):
    if not header:
        return None
    links = {}
    for part in header.split(","):
        match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
        if not match or match[2] in links:
            raise SourceError("Advisory pagination Link header is malformed")
        links[match[2]] = match[1]
    url = links.get("next")
    if url:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (parsed.scheme != "https" or parsed.netloc != "api.github.com" or parsed.path != "/advisories" or parsed.fragment
                or set(query) != set(expected) | {"after"}
                or any(query[k] != [v] for k, v in expected.items())
                or len(query["after"]) != 1 or not query["after"][0] or len(query["after"][0]) > 1000):
            raise SourceError("Advisory pagination changes the source or selection")
    return url


def _text(value, name, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SourceError(f"Advisory {name} is invalid")
    return " ".join(value.split())


def _time(value, name, nullable=False):
    if nullable and value is None:
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", value):
            raise ValueError
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SourceError(f"Advisory {name} timestamp is invalid") from None


def _record(value, first, last):
    if not isinstance(value, dict) or value.get("type") != "reviewed":
        raise SourceError("Advisory is outside reviewed scope")
    ident = value.get("ghsa_id")
    if not isinstance(ident, str) or not GHSA.fullmatch(ident) or value.get("html_url") != f"https://github.com/advisories/{ident}":
        raise SourceError("Advisory identity or official link is invalid")
    published = _time(value.get("published_at"), "published")
    updated = _time(value.get("updated_at"), "updated")
    _time(value.get("github_reviewed_at"), "reviewed")
    withdrawn = _time(value.get("withdrawn_at"), "withdrawn", nullable=True)
    if not first <= updated.date() <= last or published > updated or (withdrawn and withdrawn > updated):
        raise SourceError("Advisory timestamps are inconsistent or outside the requested window")
    cve = value.get("cve_id")
    if cve is not None and (not isinstance(cve, str) or not re.fullmatch(r"CVE-[0-9]{4}-[0-9]{4,10}", cve)):
        raise SourceError("Advisory CVE identifier is invalid")
    severity = value.get("severity")
    if not isinstance(severity, str) or severity not in SEVERITIES:
        raise SourceError("Advisory severity is unknown")
    vulnerabilities = value.get("vulnerabilities")
    if not isinstance(vulnerabilities, list) or len(vulnerabilities) > 500 or (not vulnerabilities and not withdrawn):
        raise SourceError("Advisory package list is invalid")
    packages, seen = [], set()
    for row in vulnerabilities:
        if not isinstance(row, dict) or not isinstance(row.get("package"), dict) or "first_patched_version" not in row:
            raise SourceError("Advisory package schema changed")
        patch = row["first_patched_version"]
        pkg = {"ecosystem": _text(row["package"].get("ecosystem"), "ecosystem", 50),
               "name": _text(row["package"].get("name"), "package name", 500),
               "affected_range": _text(row.get("vulnerable_version_range"), "version range"),
               "first_patched": _text(patch, "patched version", 500) if patch is not None else None}
        key = canonical(pkg)
        if key in seen:
            raise SourceError("Advisory package entries repeat")
        seen.add(key); packages.append(pkg)
    summary = _text(value.get("summary"), "summary", 4000)
    fields = {"summary": summary, "cve": cve, "severity": severity,
              "withdrawn_at": withdrawn.isoformat() if withdrawn else None, "packages": sorted(packages, key=canonical)}
    return {"key": ident, "title": f"{ident} · {summary}", "url": value["html_url"],
            "published": published.isoformat(), "fields": fields}
