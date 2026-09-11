"""Bounded ClinicalTrials.gov studies updated recently at Norwegian locations."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
from urllib.parse import urlencode

from .changes import SnapshotSource, document, integer
from .common import SourceError

API = "https://clinicaltrials.gov/api/v2/studies"
FIELDS = ("NCTId", "BriefTitle", "OverallStatus", "Phase", "LeadSponsorName",
          "StudyFirstPostDate", "LastUpdatePostDate", "LocationCountry", "HasResults")
NCT_ID = re.compile(r"NCT\d{8}")
STATUS = {
    "NOT_YET_RECRUITING": "Ikke startet rekruttering", "RECRUITING": "Rekrutterer",
    "ENROLLING_BY_INVITATION": "Deltakelse etter invitasjon",
    "ACTIVE_NOT_RECRUITING": "Aktiv, rekrutterer ikke", "SUSPENDED": "Midlertidig stanset",
    "TERMINATED": "Avsluttet før planlagt", "COMPLETED": "Fullført",
    "WITHDRAWN": "Trukket før oppstart", "UNKNOWN": "Ukjent status",
}
PHASES = {"NA": "Ikke relevant", "EARLY_PHASE1": "Tidlig fase 1", "PHASE1": "Fase 1",
          "PHASE2": "Fase 2", "PHASE3": "Fase 3", "PHASE4": "Fase 4"}


class ClinicalTrialsSource(SnapshotSource):
    """Discover new and substantive changes in a rolling update window."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (API,):
            raise ValueError("clinical_trials accepts only the official API URL")
        if self.complete or "removed" in self.events:
            raise ValueError("ClinicalTrials.gov rolling windows cannot confirm removals")
        options = config.options
        self.lookback_days = integer(options.get("last_update_days", 14), "last_update_days", 1, 366)
        self.page_size = integer(options.get("page_size", 100), "page_size", 1, 100)
        self.max_pages = integer(options.get("max_pages", 5), "max_pages", 1, 10)
        self.max_records = integer(options.get("max_records", 500), "max_records", 1, 5000)
        self.allow_empty = options.get("allow_empty", True)
        self.field_labels = {"title": "Studietittel", "status": "Status", "phases": "Fase",
                             "sponsor": "Hovedsponsor", "has_results": "Resultater publisert",
                             **self.field_labels}

    def read_records(self):
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=self.lookback_days)
        rows, seen_ids, seen_tokens = [], set(), set()
        token = None
        expected_total = None
        for _page in range(self.max_pages):
            query = {"query.locn": "AREA[LocationCountry]Norway",
                     "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{first.isoformat()}, {today.isoformat()}]",
                     "fields": ",".join(FIELDS), "format": "json", "pageSize": self.page_size,
                     "countTotal": "true"}
            if token is not None:
                query["pageToken"] = token
            raw = document(self, API + "?" + urlencode(query))
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise SourceError("ClinicalTrials.gov returned invalid JSON") from exc
            if not isinstance(payload, dict) or "studies" not in payload or (expected_total is None and "totalCount" not in payload):
                raise SourceError("ClinicalTrials.gov response schema changed")
            studies = payload["studies"]
            total = payload.get("totalCount", expected_total)
            if (not isinstance(studies, list) or any(not isinstance(study, dict) for study in studies)
                    or type(total) is not int or total < 0):
                raise SourceError("ClinicalTrials.gov response types are invalid")
            if expected_total is None:
                expected_total = total
                if total > self.max_records or math.ceil(total / self.page_size) > self.max_pages:
                    raise SourceError("ClinicalTrials.gov window exceeds configured bounds")
            elif "totalCount" in payload and total != expected_total:
                raise SourceError("ClinicalTrials.gov total changed while paging")
            expected_page_size = min(self.page_size, total - len(rows))
            if len(studies) != expected_page_size:
                raise SourceError("ClinicalTrials.gov returned an incomplete page")
            for study in studies:
                row = _record(study, first, today)
                if row["key"] in seen_ids:
                    raise SourceError("ClinicalTrials.gov repeated an NCT ID")
                seen_ids.add(row["key"])
                rows.append(row)
            next_token = payload.get("nextPageToken")
            if len(rows) == total:
                if next_token is not None:
                    raise SourceError("ClinicalTrials.gov returned a token after the final page")
                return rows
            if not isinstance(next_token, str) or not next_token or next_token in seen_tokens:
                raise SourceError("ClinicalTrials.gov pagination token is invalid")
            seen_tokens.add(next_token)
            token = next_token
        raise SourceError("ClinicalTrials.gov window exceeds max_pages")

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        if event == "added":
            fields = row["fields"]
            details = ("Nyobservert studie med norsk studiested", f"Status: {fields['status']}",
                       f"Fase: {fields['phases'] or 'ikke oppgitt'}",
                       f"Hovedsponsor: {fields['sponsor']}",
                       f"Først registrert offentlig: {row['published']}",
                       f"Resultater publisert: {'ja' if fields['has_results'] else 'nei'}")
        else:
            details = ("Endret ClinicalTrials.gov-studie", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _record(study, first: date, last: date):
    protocol = _mapping(study, "protocolSection")
    identity = _mapping(protocol, "identificationModule")
    status_module = _mapping(protocol, "statusModule")
    sponsor_module = _mapping(protocol, "sponsorCollaboratorsModule")
    design = _mapping(protocol, "designModule")
    locations_module = _mapping(protocol, "contactsLocationsModule")
    nct_id, title = identity.get("nctId"), identity.get("briefTitle")
    if not isinstance(nct_id, str) or not NCT_ID.fullmatch(nct_id):
        raise SourceError("ClinicalTrials.gov study lacks a stable NCT ID")
    if not isinstance(title, str) or not title.strip() or len(title) > 1000:
        raise SourceError("ClinicalTrials.gov study lacks a bounded title")
    locations = locations_module.get("locations")
    if (not isinstance(locations, list) or not locations
            or any(not isinstance(location, dict) or not isinstance(location.get("country"), str)
                   for location in locations)
            or not any(location["country"] == "Norway" for location in locations)):
        raise SourceError("ClinicalTrials.gov returned a study outside the Norway scope")
    raw_status = status_module.get("overallStatus")
    if raw_status not in STATUS:
        raise SourceError("ClinicalTrials.gov study has an unknown status")
    first_posted = _date_struct(status_module, "studyFirstPostDateStruct", "first-posted date")
    last_updated = _date_struct(status_module, "lastUpdatePostDateStruct", "last-update date")
    if not first <= last_updated <= last:
        raise SourceError("ClinicalTrials.gov returned a study outside the update window")
    phases = design.get("phases", [])
    if (not isinstance(phases, list) or any(phase not in PHASES for phase in phases)
            or len(phases) != len(set(phases))):
        raise SourceError("ClinicalTrials.gov study has invalid phases")
    lead = _mapping(sponsor_module, "leadSponsor")
    sponsor = lead.get("name")
    if not isinstance(sponsor, str) or not sponsor.strip() or len(sponsor) > 500:
        raise SourceError("ClinicalTrials.gov study lacks a bounded lead sponsor")
    has_results = study.get("hasResults")
    if type(has_results) is not bool:
        raise SourceError("ClinicalTrials.gov study has invalid results metadata")
    fields = {"title": title.strip(), "status": STATUS[raw_status],
              "phases": "/".join(PHASES[phase] for phase in phases),
              "sponsor": sponsor.strip(), "has_results": has_results}
    return {"key": nct_id, "title": title.strip(), "url": f"https://clinicaltrials.gov/study/{nct_id}",
            "published": first_posted.isoformat(), "last_updated": last_updated.isoformat(), "fields": fields}


def _mapping(parent, name):
    value = parent.get(name) if isinstance(parent, dict) else None
    if not isinstance(value, dict):
        raise SourceError(f"ClinicalTrials.gov study lacks {name}")
    return value


def _date_struct(module, name, label):
    value = _mapping(module, name).get("date")
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise SourceError(f"ClinicalTrials.gov {label} is invalid") from None
