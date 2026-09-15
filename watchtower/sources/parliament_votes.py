"""Voting outcomes in explicitly selected Storting cases."""
from dataclasses import replace
from datetime import datetime
import re
import xml.etree.ElementTree as ET

from .changes import SnapshotSource, document, integer
from .common import SourceError

BASE = "https://data.stortinget.no/eksport/voteringer"
NS = "{http://data.stortinget.no}"


class ParliamentVotesSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls or self.complete or "removed" in self.events:
            raise ValueError("parliament_votes uses the official API and cannot confirm removals")
        cases = config.options.get("case_ids")
        if (not isinstance(cases, list) or not 1 <= len(cases) <= 10
                or any(type(value) is not int or value <= 0 for value in cases)
                or len(cases) != len(set(cases))):
            raise ValueError("case_ids must be 1–10 unique positive integer case IDs")
        self.case_ids = sorted(cases)
        self.max_records = integer(config.options.get("max_records", 500), "max_records", 1, 1000)
        self.field_labels = {"case_id": "Stortingssak", "topic": "Voteringstema",
                             "adopted": "Vedtatt ifølge Stortinget", "for": "For",
                             "against": "Mot", "absent": "Ikke til stede",
                             "personal_vote": "Personlig votering", "result_type": "Resultattype",
                             "vote_time": "Voteringstid oppgitt av kilden", **self.field_labels}

    def read_records(self):
        records = []
        seen = set()
        for case in self.case_ids:
            url = f"{BASE}?SakId={case}"
            try:
                root = ET.fromstring(document(self, url))
            except ET.ParseError as exc:
                raise SourceError("Stortinget returned invalid voting XML") from exc
            if root.tag != NS + "sak_votering_oversikt" or _text(root, "sak_id") != str(case):
                raise SourceError("Stortinget voting response does not match the selected case")
            lists = root.findall(NS + "sak_votering_liste")
            if len(lists) != 1 or lists[0].get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
                raise SourceError("Stortinget voting list is missing")
            for vote in lists[0]:
                if vote.tag != NS + "sak_votering" or _text(vote, "sak_id") != str(case):
                    raise SourceError("Stortinget voting record has an unexpected case or schema")
                identifier = _text(vote, "votering_id")
                if not re.fullmatch(r"[1-9][0-9]{0,11}", identifier) or identifier in seen:
                    raise SourceError("Stortinget voting ID is invalid or repeated")
                seen.add(identifier)
                stamp = _text(vote, "votering_tid")
                try:
                    if "T" not in stamp:
                        raise ValueError("missing time")
                    datetime.fromisoformat(stamp)
                except ValueError as exc:
                    raise SourceError("Stortinget voting time is invalid") from exc
                topic = _text(vote, "votering_tema")
                fields = {"case_id": case, "topic": topic,
                          "adopted": _boolean(vote, "vedtatt"),
                          "personal_vote": _boolean(vote, "personlig_votering"),
                          "for": _count(vote, "antall_for"), "against": _count(vote, "antall_mot"),
                          "absent": _count(vote, "antall_ikke_tilstede"),
                          "result_type": _text(vote, "votering_resultat_type"), "vote_time": stamp}
                records.append({"key": identifier, "title": f"Stortingssak {case}: {topic}",
                                "url": url, "published": None, "fields": fields})
                if len(records) > self.max_records:
                    raise SourceError("Stortinget voting records exceed max_records")
        return records

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        if event == "added":
            fields = row["fields"]
            details = ("Ny observert votering i valgt stortingssak",
                       f"Vedtatt ifølge Stortinget: {'Ja' if fields['adopted'] else 'Nei'}",
                       f"For: {fields['for']} · Mot: {fields['against']} · Ikke til stede: {fields['absent']}",
                       f"Voteringstid i kilden: {fields['vote_time']}")
        else:
            details = ("Endret voteringsresultat", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _text(node, name):
    matches = node.findall(NS + name)
    if len(matches) != 1 or not matches[0].text or not matches[0].text.strip() or len(matches[0].text) > 10000:
        raise SourceError(f"Stortinget voting field {name} is missing or invalid")
    return matches[0].text.strip()


def _boolean(node, name):
    value = _text(node, name)
    if value not in ("true", "false"):
        raise SourceError(f"Stortinget voting field {name} is not boolean")
    return value == "true"


def _count(node, name):
    value = _text(node, name)
    if not re.fullmatch(r"[0-9]{1,3}", value) or int(value) > 169:
        raise SourceError(f"Stortinget voting count {name} is invalid")
    return int(value)
