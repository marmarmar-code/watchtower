"""Bounded published ClinicalTrials.gov results for studies with Norwegian sites."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import math

from .changes import SnapshotSource, digest
from .clinical_trials import API, ClinicalTrialsSource, NCT_ID, _date_struct, _mapping
from .common import SourceError

FIELDS = ("NCTId", "BriefTitle", "LocationCountry", "HasResults", "LastUpdatePostDate",
          "ResultsFirstPostDate", "EnrollmentInfo", "ParticipantFlowModule",
          "OutcomeMeasuresModule", "AdverseEventsModule")


class ClinicalTrialResultsSource(ClinicalTrialsSource):
    """Discover newly published and revised result tables in a rolling window."""

    fields = FIELDS

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.field_labels = {
            "actual_enrollment": "Faktisk deltakerantall",
            "participant_flow": "Deltakertilflyt",
            "outcome_measures": "Resultatmål",
            "adverse_events": "Rapporterte skadehendelser",
            "outcome_measure_count": "Antall resultatmål",
            "serious_event_term_count": "Antall alvorlige hendelsestermer",
            "other_event_term_count": "Antall øvrige hendelsestermer",
            **self.field_labels,
        }

    def query_extras(self):
        return {"query.term": "AREA[HasResults]true"}

    def record(self, study, first, last):
        return _result_record(study, first, last)

    def describe_change(self, name, before, after):
        messages = {
            "participant_flow": "Publisert tabell for deltakertilflyt er revidert",
            "outcome_measures": "Publiserte tabeller for resultatmål er revidert",
            "adverse_events": "Publiserte tabeller for rapporterte skadehendelser er revidert",
        }
        if name in messages:
            return messages[name]
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        # Bypass ClinicalTrialsSource's study-status wording. Hashes are deliberately
        # kept out of user-facing details while still fingerprinting full modules.
        item = SnapshotSource._item(self, row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            details = (
                "Nyobserverte publiserte studieresultater ved norsk studiested",
                f"Faktisk deltakerantall: {fields['actual_enrollment']}",
                f"Resultatmål: {fields['outcome_measure_count']}",
                f"Rapporterte alvorlige hendelsestermer: {fields['serious_event_term_count']}",
                f"Rapporterte øvrige hendelsestermer: {fields['other_event_term_count']}",
                f"Resultater først publisert: {row['published']}",
            )
        else:
            details = ("Publiserte studieresultater er endret", *details[1:])
        return replace(item, alert_details=details)


def _result_record(study, first: date, last: date):
    protocol = _mapping(study, "protocolSection")
    identity = _mapping(protocol, "identificationModule")
    status = _mapping(protocol, "statusModule")
    design = _mapping(protocol, "designModule")
    locations_module = _mapping(protocol, "contactsLocationsModule")
    nct_id, title = identity.get("nctId"), identity.get("briefTitle")
    if not isinstance(nct_id, str) or not NCT_ID.fullmatch(nct_id):
        raise SourceError("ClinicalTrials.gov result lacks a stable NCT ID")
    if not isinstance(title, str) or not title.strip() or len(title) > 1000:
        raise SourceError("ClinicalTrials.gov result lacks a bounded title")
    locations = locations_module.get("locations")
    if (not isinstance(locations, list) or not locations
            or any(not isinstance(value, dict) or not isinstance(value.get("country"), str)
                   for value in locations)
            or not any(value["country"] == "Norway" for value in locations)):
        raise SourceError("ClinicalTrials.gov returned a result outside the Norway scope")
    if study.get("hasResults") is not True:
        raise SourceError("ClinicalTrials.gov selected a study without published results")
    updated = _date_struct(status, "lastUpdatePostDateStruct", "last-update date")
    if not first <= updated <= last:
        raise SourceError("ClinicalTrials.gov returned a result outside the update window")
    published = _date_struct(status, "resultsFirstPostDateStruct", "results-first-posted date")
    if published > updated:
        raise SourceError("ClinicalTrials.gov result dates are inconsistent")
    enrollment = _mapping(design, "enrollmentInfo")
    count = enrollment.get("count")
    if enrollment.get("type") != "ACTUAL" or type(count) is not int or count < 0:
        raise SourceError("ClinicalTrials.gov result lacks a valid actual enrollment")
    results = _mapping(study, "resultsSection")
    flow = _mapping(results, "participantFlowModule")
    outcomes = _mapping(results, "outcomeMeasuresModule")
    adverse = _mapping(results, "adverseEventsModule")
    flow_groups = _id_rows(flow.get("groups"), "participant-flow groups", "id")
    for group in flow_groups:
        _required_text(group, "title", "participant-flow group")
    periods = _dict_rows(flow.get("periods"), "participant-flow periods", empty=False)
    for period in periods:
        _required_text(period, "title", "participant-flow period")
        milestones = _dict_rows(period.get("milestones", []), "participant-flow milestones")
        for milestone in milestones:
            _id_rows(milestone.get("achievements", []), "participant-flow achievements", "groupId")
    measures = outcomes.get("outcomeMeasures")
    measures = _dict_rows(measures, "outcome measures", empty=False)
    measure_ids = []
    for measure in measures:
        for name in ("type", "title", "timeFrame"):
            _required_text(measure, name, "outcome measure")
        measure_ids.append((measure["type"], measure["title"], measure["timeFrame"]))
        if "groups" in measure:
            _id_rows(measure["groups"], "outcome-measure groups", "id")
        if "classes" in measure:
            _dict_rows(measure["classes"], "outcome-measure classes")
    if len(measure_ids) != len(set(measure_ids)):
        raise SourceError("ClinicalTrials.gov outcome measures repeat a compound identity")
    _id_rows(adverse.get("eventGroups"), "adverse-event groups", "id")
    serious = _event_rows(adverse.get("seriousEvents", []), "serious events")
    other = _event_rows(adverse.get("otherEvents", []), "other events")
    normalized_flow = _normalize(flow)
    ordered_outcomes = dict(outcomes)
    ordered_outcomes["outcomeMeasures"] = [measure for _, measure in sorted(
        zip(measure_ids, measures), key=lambda pair: pair[0])]
    normalized_outcomes = _normalize(ordered_outcomes)
    normalized_adverse = _normalize(adverse)
    fields = {
        "actual_enrollment": count,
        "participant_flow": digest(normalized_flow),
        "outcome_measures": digest(normalized_outcomes),
        "adverse_events": digest(normalized_adverse),
        "outcome_measure_count": len(measures),
        "serious_event_term_count": len(serious),
        "other_event_term_count": len(other),
    }
    return {"key": nct_id, "title": title.strip(),
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
            "published": published.isoformat(), "last_updated": updated.isoformat(), "fields": fields}


def _normalize(value, depth=0):
    """Validate JSON and sort only lists carrying unique source IDs."""
    if depth > 30:
        raise SourceError("ClinicalTrials.gov result nesting is too deep")
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SourceError("ClinicalTrials.gov result contains a non-finite number")
        return value
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise SourceError("ClinicalTrials.gov result contains an invalid object key")
        return {key: _normalize(item, depth + 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        result = [_normalize(item, depth + 1) for item in value]
        for key in ("id", "groupId"):
            if result and all(isinstance(item, dict) and isinstance(item.get(key), str)
                              and item[key] for item in result):
                ids = [item[key] for item in result]
                if len(ids) != len(set(ids)):
                    raise SourceError("ClinicalTrials.gov result repeats a source ID")
                return sorted(result, key=lambda item: item[key])
        return result
    raise SourceError("ClinicalTrials.gov result contains an invalid JSON value")


def _dict_rows(value, name, *, empty=True):
    if (not isinstance(value, list) or (not empty and not value)
            or any(not isinstance(row, dict) for row in value)):
        raise SourceError(f"ClinicalTrials.gov {name} are invalid")
    return value


def _id_rows(value, name, key):
    rows = _dict_rows(value, name, empty=False)
    ids = [row.get(key) for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise SourceError(f"ClinicalTrials.gov {name} have invalid or repeated source IDs")
    return rows


def _event_rows(value, name):
    rows = _dict_rows(value, name)
    for row in rows:
        term = row.get("term")
        if not isinstance(term, str) or not term.strip():
            raise SourceError(f"ClinicalTrials.gov {name} lack a term")
        _id_rows(row.get("stats"), f"{name} statistics", "groupId")
    return rows


def _required_text(row, key, name):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceError(f"ClinicalTrials.gov {name} lacks {key}")
    return value
