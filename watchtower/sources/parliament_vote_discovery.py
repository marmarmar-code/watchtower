"""Discover votes through official session, meeting and agenda lists."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from .changes import SnapshotSource, document, integer
from .common import SourceError
from .parliament_votes import NS, _boolean, _count, _text

BASE = "https://data.stortinget.no/eksport/"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


class ParliamentVoteDiscoverySource(SnapshotSource):
    """Discover votes from every sitting in a bounded rolling window."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls:
            raise ValueError("parliament_vote_discovery uses fixed official API endpoints")
        if self.complete or "removed" in self.events:
            raise ValueError("A rolling meeting window cannot confirm removals")
        options = config.options
        self.lookback_days = integer(options.get("lookback_days", 30), "lookback_days", 1, 90)
        self.max_sessions = integer(options.get("max_sessions", 2), "max_sessions", 1, 3)
        self.max_meetings = integer(options.get("max_meetings", 40), "max_meetings", 1, 100)
        self.max_cases = integer(options.get("max_cases", 200), "max_cases", 1, 500)
        self.allow_empty = True
        self.field_labels = {
            "topic": "Voteringstema",
            "adopted": "Vedtatt ifølge Stortinget", "for": "For", "against": "Mot",
            "absent": "Ikke til stede", "personal_vote": "Personlig votering",
            "result_type": "Resultattype", "vote_time": "Voteringstid oppgitt av kilden",
            **self.field_labels,
        }

    def read_records(self):
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=self.lookback_days)
        sessions = _sessions(self, first, today)
        if len(sessions) > self.max_sessions:
            raise SourceError("Stortinget discovery window spans too many sessions")
        meetings = []
        for session_id in sessions:
            meetings.extend(_meetings(self, session_id, first, today))
        meeting_ids = sorted({identifier for identifier, _ in meetings})
        if len(meeting_ids) != len(meetings):
            raise SourceError("Stortinget repeated a meeting in the selected sessions")
        if len(meeting_ids) > self.max_meetings:
            raise SourceError("Stortinget discovery window exceeds max_meetings")

        cases = {}
        for meeting_id in meeting_ids:
            for case_id, title in _agenda(self, meeting_id):
                prior = cases.get(case_id)
                if prior is not None and prior != title:
                    raise SourceError("Stortinget returned conflicting titles for one case")
                cases[case_id] = title
        if len(cases) > self.max_cases:
            raise SourceError("Stortinget discovery window exceeds max_cases")

        votes = {}
        occurrences = 0
        for case_id in sorted(cases):
            for vote in _votes(self, case_id):
                occurrences += 1
                if occurrences > self.max_records:
                    raise SourceError("Stortinget discovery exceeds max_records before aggregation")
                identifier, fields = vote
                prior = votes.get(identifier)
                substantive = {key: value for key, value in fields.items() if key != "case_id"}
                if prior is None:
                    votes[identifier] = {"substantive": substantive, "cases": {case_id: cases[case_id]}}
                elif prior["substantive"] != substantive:
                    raise SourceError("Stortinget returned conflicting fields for one voting ID")
                else:
                    prior["cases"][case_id] = cases[case_id]

        records = []
        for identifier, vote in sorted(votes.items(), key=lambda item: int(item[0])):
            case_ids = sorted(vote["cases"])
            case_titles = " | ".join(vote["cases"][key] for key in case_ids)
            fields = vote["substantive"]
            records.append({"key": identifier, "title": f"Stortingsvotering {identifier}: {fields['topic']}",
                            "url": BASE + "voteringer?" + urlencode({"sakid": case_ids[0]}),
                            "published": None, "case_ids": case_ids, "case_titles": case_titles,
                            "fields": fields})
        return records

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        if event == "added":
            fields = row["fields"]
            details = ("Ny observert votering i det valgte møtevinduet",
                       f"Sak: {row['case_titles']}",
                       f"Vedtatt ifølge Stortinget: {'Ja' if fields['adopted'] else 'Nei'}",
                       f"For: {_shown_count(fields['for'])} · Mot: {_shown_count(fields['against'])} · Ikke til stede: {_shown_count(fields['absent'])}",
                       f"Voteringstid i kilden: {fields['vote_time']}")
        else:
            details = ("Endret voteringsresultat", *item.alert_details[1:])
        return replace(item, alert_details=details)

    def describe_change(self, name, before, after):
        if name in {"adopted", "personal_vote"} and isinstance(before, bool) and isinstance(after, bool):
            return f"{self.field_labels[name]}: {'Ja' if before else 'Nei'} → {'Ja' if after else 'Nei'}"
        return super().describe_change(name, before, after)


def _xml(source, endpoint, query=None):
    url = BASE + endpoint
    if query:
        url += "?" + urlencode(query)
    try:
        return ET.fromstring(document(source, url))
    except ET.ParseError as exc:
        raise SourceError(f"Stortinget {endpoint} returned invalid XML") from exc


def _one_list(root, root_name, list_name):
    if root.tag != NS + root_name:
        raise SourceError("Stortinget discovery response root changed")
    lists = root.findall(NS + list_name)
    if len(lists) != 1 or lists[0].get(XSI_NIL) == "true":
        raise SourceError("Stortinget discovery list is missing")
    return list(lists[0])


def _sessions(source, first, last):
    root = _xml(source, "sesjoner")
    rows = _one_list(root, "sesjoner_oversikt", "sesjoner_liste")
    current = root.findall(NS + "innevaerende_sesjon")
    if len(current) != 1:
        raise SourceError("Stortinget current session is missing")
    sessions = {}
    for row in [current[0], *rows]:
        if row.tag not in {NS + "sesjon", NS + "innevaerende_sesjon"}:
            raise SourceError("Stortinget session schema changed")
        identifier = _text(row, "id")
        if not re.fullmatch(r"\d{4}-\d{2,4}", identifier):
            raise SourceError("Stortinget session ID is invalid")
        start = _timestamp(row, "fra").date(); end = _timestamp(row, "til").date()
        value = (start, end)
        if identifier in sessions and sessions[identifier] != value:
            raise SourceError("Stortinget returned conflicting sessions")
        sessions[identifier] = value
    selected = sorted((start, end, identifier) for identifier, (start, end) in sessions.items()
                      if start <= last and end >= first)
    if any(start > end for start, end, _ in selected) or not selected:
        raise SourceError("Stortinget sessions do not cover the selected window")
    covered_until = first - timedelta(days=1)
    for start, end, _ in selected:
        if start > covered_until + timedelta(days=1):
            raise SourceError("Stortinget sessions do not cover the selected window")
        if end > covered_until:
            covered_until = end
    if covered_until < last:
        raise SourceError("Stortinget sessions do not cover the selected window")
    return [identifier for _, _, identifier in selected]


def _meetings(source, session_id, first, last):
    root = _xml(source, "moter", {"sesjonid": session_id})
    if _text(root, "sesjon_id") != session_id:
        raise SourceError("Stortinget meeting response has the wrong session")
    rows = _one_list(root, "mote_oversikt", "moter_liste")
    selected = []
    for row in rows:
        if row.tag != NS + "mote":
            raise SourceError("Stortinget meeting schema changed")
        identifier = _text(row, "id")
        stamp = _timestamp(row, "mote_dato_tid")
        if identifier == "-1":
            continue
        if not re.fullmatch(r"[1-9]\d{0,11}", identifier):
            raise SourceError("Stortinget meeting ID is invalid")
        if first <= stamp.date() <= last:
            selected.append((int(identifier), stamp.date()))
    return selected


def _agenda(source, meeting_id):
    root = _xml(source, "dagsorden", {"moteid": meeting_id})
    if _text(root, "mote_id") != str(meeting_id):
        raise SourceError("Stortinget agenda response has the wrong meeting")
    _timestamp(root, "mote_dato_tid")
    rows = _one_list(root, "mote_dagsorden_oversikt", "dagsordensak_liste")
    result = []
    for row in rows:
        if row.tag != NS + "dagsordensak":
            raise SourceError("Stortinget agenda schema changed")
        identifier = _text(row, "sak_id")
        if identifier == "-1":
            continue
        if not re.fullmatch(r"[1-9]\d{0,11}", identifier):
            raise SourceError("Stortinget agenda case ID is invalid")
        title = _text(row, "dagsordensak_tekst")
        result.append((int(identifier), title))
    if len(result) != len({case for case, _ in result}):
        raise SourceError("Stortinget agenda repeats a case")
    return result


def _votes(source, case_id):
    root = _xml(source, "voteringer", {"sakid": case_id})
    if _text(root, "sak_id") != str(case_id):
        raise SourceError("Stortinget voting response has the wrong case")
    rows = _one_list(root, "sak_votering_oversikt", "sak_votering_liste")
    result = []
    for vote in rows:
        if vote.tag != NS + "sak_votering" or _text(vote, "sak_id") != str(case_id):
            raise SourceError("Stortinget voting record has an unexpected case or schema")
        identifier = _text(vote, "votering_id")
        if not re.fullmatch(r"[1-9][0-9]{0,11}", identifier):
            raise SourceError("Stortinget voting ID is invalid")
        stamp = _text(vote, "votering_tid")
        _timestamp(vote, "votering_tid")
        personal = _boolean(vote, "personlig_votering")
        fields = {"case_id": case_id, "topic": _text(vote, "votering_tema"),
                  "adopted": _boolean(vote, "vedtatt"), "personal_vote": personal,
                  "for": _vote_count(vote, "antall_for", personal),
                  "against": _vote_count(vote, "antall_mot", personal),
                  "absent": _vote_count(vote, "antall_ikke_tilstede", personal),
                  "result_type": _text(vote, "votering_resultat_type"), "vote_time": stamp}
        result.append((identifier, fields))
    if len(result) != len({identifier for identifier, _ in result}):
        raise SourceError("Stortinget repeated a voting ID within one case")
    return result


def _timestamp(node, name):
    value = _text(node, name)
    try:
        if "T" not in value:
            raise ValueError
        return datetime.fromisoformat(value)
    except ValueError:
        raise SourceError(f"Stortinget discovery field {name} is not a timestamp") from None


def _vote_count(node, name, personal):
    value = _text(node, name)
    if value == "-1" and not personal:
        return None
    return _count(node, name)


def _shown_count(value):
    return "ikke oppgitt" if value is None else str(value)
