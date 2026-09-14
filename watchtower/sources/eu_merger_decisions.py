"""Decision documents from a bounded European Commission merger search."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import re

from .changes import SnapshotSource, document, integer
from .common import SourceError

API_BASE = "https://webgate.ec.europa.eu/es/search-api/rest"
CONFIG_URL = "https://competition-cases.ec.europa.eu/assets/env-json-config.json"
CASE_ID = re.compile(r"M\.[0-9]{1,6}")
DECISION_SUFFIX = re.compile(r"-DEC\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}")
DATE_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}[+-][0-9]{4}")
FIELDS = ("caseNumber", "caseTitle", "caseInstrument", "caseLastDecisionDate",
          "decisionAdoptionDate", "decisionTypes", "esST_REFERENCE", "esST_DATASOURCE")
# Official reference-data descriptions; unknown codes remain visible verbatim.
# Display labels never participate in decision identity or change detection.
TYPE_LABELS = {"DecisionType20310": "139/2004 Art. 6(1)(b)",
               "DecisionType201316": "139/2004 Art. 4(5) referral"}


def _type_text(values):
    return ", ".join(f"{TYPE_LABELS[code]} ({code})" if code in TYPE_LABELS else code for code in values)


class EUMergerDecisionsSource(SnapshotSource):
    """Select decisions after validating every document in a complete small window."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (API_BASE,)):
            raise ValueError("EU merger source accepts only the official search API")
        if self.complete or "removed" in self.events:
            raise ValueError("EU merger rolling windows cannot confirm removals")
        self.lookback_days = integer(config.options.get("lookback_days", 14), "lookback_days", 1, 366)
        self.max_records = integer(config.options.get("max_records", 100), "max_records", 1, 100)
        self.field_labels = {"case_number": "Saksnummer", "decision_date": "Vedtaksdato",
                             "decision_types": "Vedtakstype", **self.field_labels}

    def read_records(self):
        try:
            config = json.loads(document(self, CONFIG_URL))
            key = config["modules"]["odse"]["cs"]["apikey"]
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise SourceError("EU merger public runtime configuration changed") from exc
        if not isinstance(key, str) or not key.strip() or len(key) > 512:
            raise SourceError("EU merger public client value is invalid")
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=self.lookback_days)
        query = {"bool": {"must": [
            {"exists": {"field": "caseNumber"}}, {"term": {"caseInstrument": "M"}},
            {"range": {"caseLastDecisionDate": {
                "gte": first.isoformat() + "T00:00:00.000Z",
                "lte": today.isoformat() + "T23:59:59.999Z"}}}], "must_not": []}}
        parts = {name: ("blob", json.dumps(value), "application/json") for name, value in {
            "query": query, "sort": [{"field": "caseLastDecisionDate", "order": "DESC"}],
            "displayFields": list(FIELDS)}.items()}
        # The anonymous browser value stays out of config, state and error messages.
        response = self.post(API_BASE + "/search", params={"text": "", "pageNumber": 1,
                             "pageSize": 100, "apiKey": key.strip()}, files=parts,
                             stream=True, allow_redirects=False,
                             accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError("EU merger search returned an unexpected redirect")
            chunks, length = [], 0
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > self.max_bytes:
                    raise SourceError("EU merger response exceeds max_bytes")
                chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("EU merger search returned invalid JSON") from exc
        finally:
            response.close()
        return _records(payload, first, today)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            details = ("Nyobserverte vedtaksopplysninger i EU-fusjonssak",
                       f"Sak: {fields['case_number']}", f"Vedtaksdato: {fields['decision_date']}",
                       "Vedtakstype: " + _type_text(fields["decision_types"]),
                       "Lenken åpner den offisielle saken med tilhørende dokumenter")
        else:
            details = ("Endrede opplysninger om EU-fusjonsvedtak", *details[1:])
        return replace(item, alert_details=details)

    def describe_change(self, name, before, after):
        if name == "decision_types":
            return f"Vedtakstype: {_type_text(before)} → {_type_text(after)}"
        return super().describe_change(name, before, after)


def _single(metadata, name, limit=1000):
    value = metadata.get(name)
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], str)
            or not value[0].strip() or len(value[0]) > limit):
        raise SourceError(f"EU merger document has invalid {name}")
    return value[0]


def _date(metadata, name):
    value = _single(metadata, name, 40)
    try:
        if not DATE_TEXT.fullmatch(value):
            raise ValueError
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc).date()
    except ValueError:
        raise SourceError(f"EU merger document has invalid {name}") from None


def _records(payload, first, today):
    if (not isinstance(payload, dict) or type(payload.get("totalResults")) is not int
            or not isinstance(payload.get("results"), list)):
        raise SourceError("EU merger search response schema changed")
    total, results = payload["totalResults"], payload["results"]
    if (not 0 <= total <= 100 or len(results) != total
            or type(payload.get("pageNumber")) is not int or payload["pageNumber"] != 1
            or type(payload.get("pageSize")) is not int or payload["pageSize"] != 100
            or payload.get("warnings") not in (None, [])):
        raise SourceError("EU merger window is incomplete or exceeds the 100-document bound")
    rows, seen = {}, set()
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("metadata"), dict):
            raise SourceError("EU merger document is malformed")
        metadata = result["metadata"]
        ref, case = _single(metadata, "esST_REFERENCE", 120), _single(metadata, "caseNumber", 20)
        if not CASE_ID.fullmatch(case) or _single(metadata, "caseInstrument") != "M":
            raise SourceError("EU merger document is outside merger scope")
        if _single(metadata, "esST_DATASOURCE") != "CS_PROD_ODSE_PROD":
            raise SourceError("EU merger source identifier changed")
        if not first <= _date(metadata, "caseLastDecisionDate") <= today:
            raise SourceError("EU merger document is outside the requested date window")
        if ref in seen:
            raise SourceError("EU merger document IDs repeat")
        seen.add(ref)
        if ref == case or re.fullmatch(re.escape(case) + r"-ATT[0-9]+", ref):
            continue
        suffix = ref[len(case):] if ref.startswith(case) else ""
        match = re.fullmatch("(" + DECISION_SUFFIX.pattern + r")(?:-ATT[0-9]+)?", suffix)
        if not match:
            raise SourceError("EU merger document has an unknown reference shape")
        decision_id = case + match.group(1)
        adopted = _date(metadata, "decisionAdoptionDate")
        if not date(1970, 1, 1) <= adopted <= today:
            raise SourceError("EU merger decision adoption date is outside valid bounds")
        types = metadata.get("decisionTypes")
        if (not isinstance(types, list) or not types or len(types) > 30
                or any(not isinstance(v, str) or not re.fullmatch(r"DecisionType[0-9]+", v) for v in types)
                or len(set(types)) != len(types)):
            raise SourceError("EU merger decision type codes are invalid")
        title = _single(metadata, "caseTitle")
        fields = {"case_number": case, "decision_date": adopted.isoformat(), "decision_types": sorted(types)}
        row = {"key": decision_id, "title": title.strip(),
               "url": "https://competition-cases.ec.europa.eu/cases/" + case,
               "published": adopted.isoformat(), "fields": fields}
        if decision_id in rows and rows[decision_id]["fields"] != fields:
            raise SourceError("EU merger documents disagree about the same decision")
        rows[decision_id] = row
    return sorted(rows.values(), key=lambda row: row["key"])
