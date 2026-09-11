"""Recently registered AS/ASA companies in selected BRREG industry divisions."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
from urllib.parse import urlencode, urlparse

from .changes import SnapshotSource, document, integer
from .common import SourceError
from .identifiers import valid_orgnr

API = "https://data.brreg.no/enhetsregisteret/api/enheter"
ENTITY_URL = API + "/{}"
INDUSTRY_PREFIX = re.compile(r"\d{2}")


class CompanyDiscoverySource(SnapshotSource):
    """Discover companies by registration date, industry division and legal form."""

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls:
            raise ValueError("company_discovery uses BRREG's fixed public entity search")
        if self.complete or "removed" in self.events:
            raise ValueError("A rolling registration window cannot confirm removals")
        options = config.options
        prefixes = options.get("industry_prefixes")
        if (not isinstance(prefixes, list) or not prefixes or len(prefixes) > 5
                or any(not isinstance(value, str) or not INDUSTRY_PREFIX.fullmatch(value) for value in prefixes)
                or len(prefixes) != len(set(prefixes))):
            raise ValueError("industry_prefixes must contain 1-5 unique two-digit SN2025 divisions")
        forms = options.get("organisation_forms", ["AS", "ASA"])
        if (not isinstance(forms, list) or not forms
                or any(not isinstance(value, str) or value not in {"AS", "ASA"} for value in forms)
                or len(forms) != len(set(forms))):
            raise ValueError("organisation_forms must select AS and/or ASA")
        self.prefixes = tuple(sorted(prefixes))
        self.forms = frozenset(forms)
        self.lookback_days = integer(options.get("lookback_days", 30), "lookback_days", 1, 90)
        self.page_size = integer(options.get("page_size", 100), "page_size", 1, 100)
        self.max_pages = integer(options.get("max_pages", 5), "max_pages", 1, 20)
        self.max_records = integer(options.get("max_records", 500), "max_records", 1, 5000)
        self.allow_empty = True
        self.field_labels = {"name": "Foretaksnavn", "organisation_form": "Organisasjonsform",
                             "industry_code": "Næringskode", "industry_description": "Næring",
                             "registration_date": "Registrert i Enhetsregisteret",
                             "municipality": "Forretningskommune", **self.field_labels}

    def read_records(self):
        today = datetime.now(timezone.utc).date()
        first = today - timedelta(days=self.lookback_days)
        records_by_key = {}
        fetched = 0
        for prefix in self.prefixes:
            expected_total = None
            prefix_keys = set()
            for page_number in range(self.max_pages):
                query = {"naeringskode": prefix, "fraRegistreringsdatoEnhetsregisteret": first.isoformat(),
                         "size": self.page_size, "page": page_number}
                try:
                    payload = json.loads(document(self, API + "?" + urlencode(query)))
                except (ValueError, UnicodeError) as exc:
                    raise SourceError("BRREG company discovery returned invalid JSON") from exc
                rows, paging = _payload(payload)
                total, pages = paging["totalElements"], paging["totalPages"]
                if (paging["size"] != self.page_size or paging["number"] != page_number
                        or pages != math.ceil(total / self.page_size)):
                    raise SourceError("BRREG company discovery pagination is inconsistent")
                if expected_total is None:
                    expected_total = total
                    if pages > self.max_pages or fetched + total > self.max_records:
                        raise SourceError("BRREG company discovery window exceeds configured bounds")
                elif total != expected_total:
                    raise SourceError("BRREG company discovery total changed while paging")
                expected_size = min(self.page_size, total - page_number * self.page_size)
                if len(rows) != expected_size:
                    raise SourceError("BRREG company discovery returned an incomplete page")
                if pages == 0:
                    break
                fetched += len(rows)
                for entity in rows:
                    record, form = _record(entity, prefix, self.prefixes, first, today)
                    if record["key"] in prefix_keys:
                        raise SourceError("BRREG company discovery repeated an organisation number within a query")
                    prefix_keys.add(record["key"])
                    previous = records_by_key.get(record["key"])
                    if previous is not None and previous != record:
                        raise SourceError("BRREG company discovery returned conflicting duplicate entities")
                    if form in self.forms:
                        records_by_key[record["key"]] = record
                if page_number + 1 == pages:
                    break
            else:
                raise SourceError("BRREG company discovery exceeded max_pages")
        return list(records_by_key.values())

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        fields = row["fields"]
        if event == "added":
            details = ("Nytt observert foretak i Enhetsregisteret", f"Foretak: {fields['name']}",
                       f"Organisasjonsform: {fields['organisation_form']}",
                       f"Næringskode: {fields['industry_code']} – {fields['industry_description']}",
                       f"Registrert i Enhetsregisteret: {fields['registration_date']}")
        else:
            details = ("Endret foretaksopplysning", *item.alert_details[1:])
        return replace(item, alert_details=details)


def _payload(payload):
    if not isinstance(payload, dict) or "page" not in payload:
        raise SourceError("BRREG company discovery response schema changed")
    embedded, paging = payload.get("_embedded"), payload["page"]
    required = {"size", "totalElements", "totalPages", "number"}
    if (not isinstance(paging, dict) or not required <= set(paging)
            or any(type(paging[key]) is not int or paging[key] < 0 for key in required)):
        raise SourceError("BRREG company discovery response types changed")
    if embedded is None and paging["totalElements"] == 0 and paging["totalPages"] == 0:
        rows = []
    else:
        rows = embedded.get("enheter") if isinstance(embedded, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise SourceError("BRREG company discovery response types changed")
    return rows, paging


def _record(entity, query_prefix, selected_prefixes, first: date, last: date):
    orgnr = entity.get("organisasjonsnummer")
    if not isinstance(orgnr, str) or not valid_orgnr(orgnr):
        raise SourceError("BRREG company discovery entity has an invalid organisation number")
    name = entity.get("navn")
    if not isinstance(name, str) or not name.strip() or len(name) > 500:
        raise SourceError("BRREG company discovery entity has an invalid name")
    form = entity.get("organisasjonsform")
    if not isinstance(form, dict) or not isinstance(form.get("kode"), str) or not form["kode"]:
        raise SourceError("BRREG company discovery entity has an invalid organisation form")
    industries = []
    for number in (1, 2, 3):
        industry = entity.get(f"naeringskode{number}")
        if industry is None:
            continue
        if (not isinstance(industry, dict) or not isinstance(industry.get("kode"), str)
                or not re.fullmatch(r"\d{2}\.\d{3}", industry["kode"])
                or not isinstance(industry.get("beskrivelse"), str) or not industry["beskrivelse"].strip()
                or len(industry["beskrivelse"]) > 500):
            raise SourceError("BRREG company discovery entity has an invalid industry code")
        industries.append((industry["kode"], industry["beskrivelse"].strip()))
    if not industries or not any(code.startswith(query_prefix + ".") for code, _ in industries):
        raise SourceError("BRREG returned an entity outside the selected industry division")
    matching = sorted({item for item in industries
                       if any(item[0].startswith(prefix + ".") for prefix in selected_prefixes)})
    registered = _date(entity.get("registreringsdatoEnhetsregisteret"), "registration date")
    if not first <= registered <= last:
        raise SourceError("BRREG returned an entity outside the registration-date window")
    address = entity.get("forretningsadresse")
    municipality = ""
    if address is not None:
        if not isinstance(address, dict) or not isinstance(address.get("kommune"), str):
            raise SourceError("BRREG company discovery address is invalid")
        municipality = address["kommune"].strip()
    link = entity.get("_links")
    self_value = link.get("self") if isinstance(link, dict) else None
    self_link = self_value.get("href") if isinstance(self_value, dict) else None
    expected_url = ENTITY_URL.format(orgnr)
    if self_link != expected_url or urlparse(self_link).hostname != "data.brreg.no":
        raise SourceError("BRREG company discovery entity link is invalid")
    fields = {"name": name.strip(), "organisation_form": form["kode"],
              "industry_code": ", ".join(code for code, _ in matching),
              "industry_description": "; ".join(description for _, description in matching),
              "registration_date": registered.isoformat(), "municipality": municipality}
    return {"key": orgnr, "title": f"{name.strip()} · {orgnr}", "url": expected_url,
            "published": None, "fields": fields}, form["kode"]


def _date(value, label):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise SourceError(f"BRREG company discovery {label} is invalid") from None
