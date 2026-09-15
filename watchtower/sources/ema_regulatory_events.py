"""Structured EMA safety communications, referrals and periodic assessments."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import re
from urllib.parse import urlparse

from .changes import SnapshotSource, document, public_url
from .common import SourceError

DATASETS = {
    "dhpc": {
        "url": "https://www.ema.europa.eu/en/documents/report/dhpc-output-json-report_en.json",
        "path": "/en/medicines/dhpc/", "url_field": "dhpc_url", "name": "name_of_medicine",
        "fields": ("name_of_medicine", "active_substances", "dhpc_type", "regulatory_outcome",
                   "referral_name", "other_related_medicines_nationally_authorised", "dissemination_date"),
        "schema": ("category", "name_of_medicine", "procedure_number", "active_substances",
                   "dhpc_type", "regulatory_outcome", "referral_name", "atc_code_human",
                   "atcvet_code_veterinary", "therapeutic_area_mesh", "species",
                   "other_related_medicines_nationally_authorised", "dissemination_date",
                   "first_published_date", "last_updated_date", "dhpc_url"),
    },
    "referrals": {
        "url": "https://www.ema.europa.eu/en/documents/report/referrals-output-json-report_en.json",
        "path": "/en/medicines/human/referrals/", "url_field": "referral_url", "name": "referral_name",
        "fields": ("referral_name", "international_non_proprietary_name_inn_common_name",
                   "current_status", "referral_type", "associated_names_centrally_authorised_medicines",
                   "associated_names_non_centrally_authorised_medicines", "prac_recommendation",
                   "procedure_start_date", "prac_recommendation_date", "cmdh_position_date",
                   "chmp_cvmp_opinion_date", "european_commission_decision_date"),
        "schema": ("category", "referral_name", "international_non_proprietary_name_inn_common_name",
                   "current_status", "safety_referral", "referral_type",
                   "associated_names_centrally_authorised_medicines",
                   "associated_names_non_centrally_authorised_medicines", "class", "reference_number",
                   "non_prac_decision_making_model", "prac_decision_making_model", "authorisation_model",
                   "prac_recommendation", "procedure_start_date", "prac_recommendation_date",
                   "cmdh_position_date", "chmp_cvmp_opinion_date", "european_commission_decision_date",
                   "first_published_date", "last_updated_date", "referral_url"),
    },
    "psusa": {
        "url": "https://www.ema.europa.eu/en/documents/report/medicines-output-periodic_safety_update_report_single_assessments-output-json-report_en.json",
        "path": "/en/medicines/psusa/", "url_field": "psusa_url",
        "name": "active_substances_in_scope_of_procedure",
        "fields": ("active_substances_in_scope_of_procedure", "active_substance",
                   "related_medicines", "regulatory_outcome"),
        "schema": ("category", "active_substances_in_scope_of_procedure", "active_substance",
                   "related_medicines", "procedure_number", "regulatory_outcome",
                   "first_published_date", "last_updated_date", "psusa_url"),
    },
}

OUTCOMES = {"", "Maintenance", "Variation", "Suspension", "Revocation", "Withdrawal"}
REFERRAL_STATUSES = {"Procedure started", "Under evaluation", "PRAC recommendation",
                     "CMDh final position", "CHMP opinion", "CVMP opinion",
                     "European Commission final decision"}


class EmaRegulatoryEventsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        dataset = config.options.get("dataset")
        if not isinstance(dataset, str) or dataset not in DATASETS:
            raise ValueError("dataset must be dhpc, referrals or psusa")
        self.spec = DATASETS[dataset]; self.dataset = dataset
        if tuple(config.urls) != (self.spec["url"],):
            raise ValueError("EMA regulatory dataset requires its exact official JSON URL")
        if self.complete or "removed" in self.events:
            raise ValueError("EMA regulatory files do not prove removals")
        if self.allow_empty:
            raise ValueError("EMA regulatory datasets must fail closed when empty")
        self.field_labels = {"name_of_medicine": "Legemiddel", "active_substances": "Virkestoff",
            "dhpc_type": "Type sikkerhetsinformasjon", "regulatory_outcome": "Regulatorisk utfall",
            "referral_name": "Prosedyre", "current_status": "Prosedyrestatus",
            "other_related_medicines_nationally_authorised": "Andre nasjonalt godkjente legemidler",
            "dissemination_date": "Distribusjonsdato", "referral_type": "Prosedyretype",
            "international_non_proprietary_name_inn_common_name": "Virkestoff eller fellesnavn",
            "associated_names_centrally_authorised_medicines": "Sentralt godkjente legemidler",
            "associated_names_non_centrally_authorised_medicines": "Nasjonalt godkjente legemidler",
            "prac_recommendation": "PRAC-anbefaling", "procedure_start_date": "Prosedyrestart",
            "prac_recommendation_date": "Dato for PRAC-anbefaling",
            "cmdh_position_date": "Dato for CMDh-posisjon",
            "chmp_cvmp_opinion_date": "Dato for komitéuttalelse",
            "european_commission_decision_date": "Dato for EU-kommisjonens beslutning",
            "active_substances_in_scope_of_procedure": "Virkestoff i vurderingen",
            "active_substance": "Virkestoff", "related_medicines": "Relaterte legemidler",
            **self.field_labels}

    def read_records(self):
        try:
            payload = json.loads(document(self, self.spec["url"]))
        except (ValueError, UnicodeError) as exc:
            raise SourceError("EMA regulatory dataset returned invalid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"meta", "data"}:
            raise SourceError("EMA regulatory dataset top-level schema changed")
        meta, rows = payload["meta"], payload["data"]
        if (not isinstance(meta, dict) or set(meta) != {"total_records", "timestamp"}
                or type(meta["total_records"]) is not int or meta["total_records"] < 0
                or not isinstance(meta["timestamp"], str)):
            raise SourceError("EMA regulatory metadata changed")
        _iso_timestamp(meta["timestamp"])
        if (not isinstance(rows, list) or meta["total_records"] != len(rows)
                or not rows or len(rows) > self.max_records):
            raise SourceError("EMA regulatory dataset is empty, incomplete or exceeds max_records")
        records, seen = [], set()
        for row in rows:
            if (not isinstance(row, dict) or set(row) != set(self.spec["schema"])
                    or any(not isinstance(value, str) for value in row.values())):
                raise SourceError("EMA regulatory row schema changed")
            _validate_enums(self.dataset, row)
            for field in self.spec["schema"]:
                if field.endswith("_date"):
                    _date(row[field], field, empty=True)
            if not self._selected(row):
                continue
            url = _ema_url(row[self.spec["url_field"]], self.spec["path"])
            if url in seen:
                raise SourceError("EMA regulatory dataset repeated a page URL")
            seen.add(url)
            name = (row[self.spec["name"]].strip() or row.get("active_substance", "").strip()
                    or row.get("procedure_number", "").strip())
            if not name or len(name) > 1000:
                raise SourceError("EMA regulatory record has no bounded title")
            fields = {field: row[field].strip() for field in self.spec["fields"]}
            published = _date(row["first_published_date"], "first_published_date")
            records.append({"key": url, "title": name, "url": url,
                            "published": published, "fields": fields})
        if not records:
            raise SourceError("EMA regulatory selection is empty")
        return records

    def _selected(self, row):
        if self.dataset == "dhpc":
            return row["category"] == "Human" and row["dhpc_type"] != "Medicine shortage"
        if self.dataset == "referrals":
            return row["category"] == "Human" and row["safety_referral"] == "Yes"
        return True

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            if self.dataset == "dhpc":
                details = ("Ny observert sikkerhetsinformasjon fra EMA", f"Legemiddel: {fields['name_of_medicine']}",
                           f"Type: {fields['dhpc_type']}", f"Regulatorisk utfall: {fields['regulatory_outcome'] or 'ikke oppgitt'}")
            elif self.dataset == "referrals":
                details = ("Ny observert sikkerhetsprosedyre hos EMA", f"Prosedyre: {fields['referral_name']}",
                           f"Status: {fields['current_status']}")
            else:
                details = ("Ny observert periodisk sikkerhetsvurdering hos EMA",
                           f"Virkestoff: {fields['active_substances_in_scope_of_procedure'] or fields['active_substance']}",
                           f"Regulatorisk utfall: {fields['regulatory_outcome'] or 'ikke oppgitt'}")
        else:
            details = ("Endret regulatorisk opplysning hos EMA", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _validate_enums(dataset, row):
    if dataset == "psusa":
        if row["category"] not in {"", "Human"} or row["regulatory_outcome"] not in OUTCOMES:
            raise SourceError("EMA PSUSA enum changed")
    elif dataset == "dhpc":
        if row["category"] not in {"Human", "Veterinary"} or row["regulatory_outcome"] not in OUTCOMES:
            raise SourceError("EMA regulatory enum changed")
    else:
        if row["category"] not in {"Human", "Veterinary"}:
            raise SourceError("EMA referral category changed")
        if row["safety_referral"] not in {"Yes", "No"} or row["current_status"] not in REFERRAL_STATUSES:
            raise SourceError("EMA referral enum changed")


def _date(value, label, empty=False):
    if empty and value == "":
        return None
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            raise ValueError
        return datetime.strptime(value, "%d/%m/%Y").date().isoformat()
    except (TypeError, ValueError):
        raise SourceError(f"EMA regulatory {label} is invalid") from None


def _iso_timestamp(value):
    try:
        if "T" not in value or not value.endswith("Z"):
            raise ValueError
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise SourceError("EMA regulatory generation timestamp is invalid") from None


def _ema_url(value, path):
    try:
        public_url(value)
    except ValueError as exc:
        raise SourceError("EMA regulatory page URL is invalid") from exc
    parsed = urlparse(value)
    slug = parsed.path.removeprefix(path)
    if (parsed.netloc != "www.ema.europa.eu" or not parsed.path.startswith(path)
            or not slug or "/" in slug or len(slug) > 500 or parsed.query or parsed.fragment):
        raise SourceError("EMA regulatory page URL is invalid")
    return value
