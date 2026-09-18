from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from difflib import SequenceMatcher
import json
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from .common import Source, SourceError
from ..models import Item

BASE = "https://data.stortinget.no/eksport"
_IDENTITY_VERSION = 2
_STATE_VERSION_KEY = "stortinget_identity_version"
_RECORDS_STATE_KEY = "stortinget_records"
# Transport, general biography and maintenance fields do not describe a change
# to a parliamentary question, case or hearing.
_VOLATILE_FIELDS = {
    "respons_dato_tid", "versjon", "sist_oppdatert_dato", "legacy_id",
    "foedselsdato", "doedsdato", "kjoenn", "vara_representant",
    "historisk_fylke", "representert_parti",
}
_PERSON_FIELDS = {
    "sporsmal_fra", "sporsmal_til", "besvart_av", "besvart_pa_vegne_av",
    "rette_vedkommende", "fremsatt_av_annen", "representant", "person",
}
_FIELD_LABELS = {
    "tittel": "Tittel", "korttittel": "Korttittel", "status": "Status",
    "besvart_dato": "Besvart", "sendt_dato": "Sendt", "datert_dato": "Datert",
    "sporsmal_fra": "Spørsmål fra", "sporsmal_til": "Spørsmål til",
    "besvart_av": "Besvart av", "besvart_pa_vegne_av": "Besvart på vegne av",
    "sporsmal_til_minister_tittel": "Ansvarlig statsråd",
    "besvart_av_minister_tittel": "Svarende statsråd",
    "besvart_pa_vegne_av_minister_tittel": "På vegne av statsråd",
    "rette_vedkommende": "Videresendt til",
    "rette_vedkommende_minister_tittel": "Videresendt til statsråd",
    "flyttet_til": "Flyttet til", "emne_liste": "Emner", "komite": "Komité",
    "horing_status": "Høringsstatus", "innspillsfrist": "Innspillsfrist",
    "anmodningsfrist_dato_tid": "Påmeldingsfrist", "soknadfrist_dato": "Søknadsfrist",
    "start_dato": "Høringsdato", "horing_dato_tid": "Høringsdato",
    "horingstidspunkt_liste": "Høringstid og sted", "horing_sak_info_liste": "Høringssaker",
    "status_info_tekst": "Statusinformasjon", "henvisning": "Dokumenthenvisning",
    "innstilling_kode": "Innstilling", "innstilling_id": "Innstillings-ID",
    "saksordfoerer_liste": "Saksordførere", "forslagstiller_liste": "Forslagsstillere",
    "behandlet_sesjon_id": "Behandlet i sesjon", "sesjon_id": "Sesjon",
    "sporsmal_nummer": "Spørsmålsnummer", "sted": "Sted", "tidspunkt": "Tidspunkt",
}
_STATUS_LABELS = {"til_behandling": "Til behandling", "besvart": "Besvart",
                  "mottatt": "Mottatt", "behandlet": "Behandlet", "trukket": "Trukket"}
_XML_CONTRACTS = {
    "saker": ("saker_oversikt", "saker_liste", "sak:"),
    "skriftligesporsmal": ("sporsmal_oversikt", "sporsmal_liste", "sporsmal:"),
    "horinger": ("horinger_oversikt", "horinger_liste", "horing:"),
}
_NAMESPACE = "{http://data.stortinget.no}"


class StortingetSource(Source):
    def fetch(self) -> list[Item]:
        datasets = self.config.options.get("datasets", ["saker", "skriftligesporsmal", "horinger"])
        if not isinstance(datasets, list) or not datasets:
            raise SourceError("stortinget datasets must be a non-empty list")
        self._fetched_records: dict[str, dict[str, str]] = {}
        items: list[Item] = []
        for dataset in datasets:
            if dataset == "saker":
                items.extend(self._fetch_saker())
            elif dataset == "skriftligesporsmal":
                items.extend(self._fetch_questions())
            elif dataset == "horinger":
                items.extend(self._fetch_hearings())
            else:
                raise SourceError(f"unsupported Stortinget dataset: {dataset}")
        keys: set[str] = set()
        for item in items:
            if item.key in keys:
                raise SourceError(f"duplicate Stortinget record identity: {item.key}")
            keys.add(item.key)
        return items

    def fetch_with_state(self, previous: dict | None) -> list[Item]:
        items = self.fetch()
        self._previous_records = dict((previous or {}).get(_RECORDS_STATE_KEY, {}))
        legacy_identity = bool(previous and previous.get(_STATE_VERSION_KEY) != _IDENTITY_VERSION)
        if previous and (legacy_identity or not self._previous_records):
            if previous.get("seen") and not items:
                raise SourceError("cannot migrate Stortinget records from an empty feed")
            datasets = self.config.options.get("datasets", list(_XML_CONTRACTS))
            for dataset in datasets:
                prefix = _XML_CONTRACTS[dataset][2]
                if (any(key.startswith(prefix) for key in previous.get("seen", {}))
                        and not any(item.key.startswith(prefix) for item in items)):
                    raise SourceError(f"cannot migrate Stortinget records from an empty {dataset} dataset")
        out = []
        self._snapshot_initializations = 0
        for item in items:
            old = self._previous_records.get(item.key)
            fields = self._fetched_records[item.key]
            if legacy_identity:
                # Descendant IDs used by old versions cannot be mapped losslessly.
                item = replace(item, suppress_alert=True)
            elif old is None and item.key in (previous or {}).get("seen", {}):
                # A digest is not evidence of an old field value. Observe existing
                # records quietly once; new record IDs still produce normal alerts.
                item = replace(item, suppress_alert=True)
                self._snapshot_initializations += 1
            elif old is not None:
                changes = _changes(old, fields)
                if changes:
                    item = replace(item, alert_details=_details_with_changes(changes, item.alert_details))
            out.append(item)
        return out

    def augment_state(self, state: dict) -> dict:
        # Commit snapshots only through the engine's existing delivery transaction.
        records = {**getattr(self, "_previous_records", {}), **getattr(self, "_fetched_records", {})}
        return {**state, _STATE_VERSION_KEY: _IDENTITY_VERSION, _RECORDS_STATE_KEY: records,
                "stortinget_snapshot_initializations": getattr(self, "_snapshot_initializations", 0)}

    def _record(self, item: Item, node: ET.Element) -> Item:
        fields = _semantic_fields(node)
        if not hasattr(self, "_fetched_records"):
            self._fetched_records = {}
        self._fetched_records[item.key] = fields
        return replace(item, fingerprint=json.dumps(fields, ensure_ascii=False, sort_keys=True))

    def _xml(self, endpoint: str, params: dict[str, str] | None = None) -> ET.Element:
        url = f"{BASE}/{endpoint}"
        response = self.get(url, params=params)
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise SourceError(f"invalid Stortinget XML: {endpoint}") from exc
        root_name, list_name, _ = _XML_CONTRACTS[endpoint]
        if (root.tag != _NAMESPACE + root_name
                or len(root.findall(_NAMESPACE + list_name)) != 1):
            raise SourceError(f"unexpected Stortinget XML structure: {endpoint}")
        if any(child.tag != _NAMESPACE + _XML_CONTRACTS[endpoint][2][:-1]
               for child in root.find(_NAMESPACE + list_name)):
            raise SourceError(f"unexpected Stortinget XML records: {endpoint}")
        return root

    def _fetch_saker(self) -> list[Item]:
        root = self._xml("saker")
        out = []
        for node in _nodes(root, "sak"):
            item_id = _record_id(node, "sak", "id")
            title = _first(node, "korttittel", "tittel", "sak_korttittel")
            if not title:
                raise SourceError(f"missing Stortinget case title: {item_id}")
            status = _first(node, "status")
            details = [f"Saksstatus: {_display_status(status)}"] if status else []
            updated = _date(node, "sist_oppdatert_dato", "dato")
            if updated:
                details.append(f"Sist oppdatert hos kilden: {updated}")
            out.append(self._record(Item(
                self.config.id,
                f"sak:{item_id}",
                title,
                f"{BASE}/sak?{urlencode({'sakid': item_id})}",
                None,
                text=_stable_text(node),
                metadata={"dataset": "sak", "status": status},
                alert_details=tuple(details),
            ), node))
        return out

    def _fetch_questions(self) -> list[Item]:
        root = self._xml("skriftligesporsmal", {"status": "alle"})
        out = []
        for node in _nodes(root, "sporsmal"):
            item_id = _record_id(node, "sporsmal", "id")
            title = _first(node, "tittel")
            if not title:
                raise SourceError(f"missing Stortinget question title: {item_id}")
            status = _first(node, "status")
            answered = _date(node, "besvart_dato")
            details = [f"Spørsmålsstatus: {_display_status(status)}"] if status else []
            asked_by = _person(node, "sporsmal_fra")
            recipient = _person(node, "sporsmal_til")
            answerer = _person(node, "besvart_av")
            if asked_by:
                details.append(f"Spørsmål fra: {asked_by}")
            if recipient:
                role = _first(node, "sporsmal_til_minister_tittel")
                details.append(f"Spørsmål til: {recipient}" + (f" ({role})" if role else ""))
            sent = _date(node, "sendt_dato")
            dated = _date(node, "datert_dato")
            if sent:
                details.append(f"Sendt: {sent[:10]}")
            elif dated:
                details.append(f"Datert: {dated[:10]}")
            if answered:
                details.append(f"Besvart: {answered[:10]}")
            if answerer:
                role = _first(node, "besvart_av_minister_tittel")
                details.append(f"Besvart av: {answerer}" + (f" ({role})" if role else ""))
            out.append(self._record(Item(
                self.config.id,
                f"sporsmal:{item_id}",
                title,
                f"{BASE}/enkeltsporsmal?{urlencode({'NSporsmalId': item_id})}",
                None,
                text=_stable_text(node),
                metadata={
                    "dataset": "skriftlig spørsmål",
                    "status": status,
                    "minister": _first(node, "sporsmal_til_minister_tittel"),
                    "answered_at": answered,
                },
                alert_details=tuple(details),
            ), node))
        return out

    def _fetch_hearings(self) -> list[Item]:
        root = self._xml("horinger")
        out = []
        for node in _nodes(root, "horing"):
            item_id = _record_id(node, "horing", "id", "horing_id")
            title = _first(node, "tittel", "sak_korttittel")
            if not title:
                titles = [_first(case, "sak_korttittel", "sak_tittel")
                          for case in _nodes(node, "horing_sak_info")]
                title = " / ".join(dict.fromkeys(t for t in titles if t))
            if not title:
                raise SourceError(f"missing Stortinget hearing title: {item_id}")
            status = _first(node, "horing_status", "status")
            deadline = _date(node, "innspillsfrist", "anmodningsfrist_dato_tid")
            details = [f"Høringsstatus: {status}"] if status else []
            starts = _date(node, "start_dato", "horing_dato_tid")
            if starts:
                details.append(f"Høringsdato: {starts}")
            for field, label in (("innspillsfrist", "Innspillsfrist"), ("anmodningsfrist_dato_tid", "Påmeldingsfrist")):
                value = _date(node, field)
                if value:
                    details.append(f"{label}: {value}")
            out.append(self._record(Item(
                self.config.id,
                f"horing:{item_id}",
                title,
                "https://www.stortinget.no/no/Hva-skjer-pa-Stortinget/Horing/horing/?"
                + urlencode({"h": item_id}),
                None,
                text=_stable_text(node),
                metadata={"dataset": "høring", "status": status, "deadline": deadline},
                alert_details=tuple(details),
            ), node))
        return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(node: ET.Element) -> str:
    return _normalize(" ".join(part.strip() for part in node.itertext() if part and part.strip()))


def _nodes(root: ET.Element, name: str):
    for node in root.iter():
        if _local(node.tag) == name:
            yield node


def _first(node: ET.Element, *names: str) -> str:
    """Read record fields, never identically named fields on nested entities."""
    for name in names:
        for child in node:
            if _local(child.tag) == name:
                text = _element_text(child)
                if text:
                    return text
    return ""


def _record_id(node: ET.Element, dataset: str, *names: str) -> str:
    item_id = _first(node, *names)
    if not item_id or not item_id.isascii() or not item_id.isdecimal():
        raise SourceError(f"missing or invalid direct Stortinget {dataset} ID")
    return item_id


def _date(node: ET.Element, *names: str) -> str:
    """The API uses year-one timestamps for dates that have not been set."""
    for name in names:
        value = _first(node, name)
        if value and not value.startswith("0001-01-01"):
            return _normalize(value)
    return ""


def _normalize(value: str) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    # .NET's unset DateTime is serialized both with and without a UTC suffix.
    # Both mean absent, at every nesting depth, never a dated editorial event.
    if value.startswith("0001-01-01"):
        return ""
    return value


def _person(node: ET.Element, field: str | None = None) -> str:
    person = next((child for child in node if _local(child.tag) == field), None) if field else node
    if person is None:
        return ""
    return " ".join(filter(None, (_first(person, "fornavn"), _first(person, "etternavn")))) or _first(person, "id")


def _display_status(value: str) -> str:
    return _STATUS_LABELS.get(value, value.replace("_", " "))


def _field_value(node: ET.Element) -> str:
    name = _local(node.tag)
    if name in _VOLATILE_FIELDS:
        return ""
    if name in _PERSON_FIELDS:
        return _person(node)
    if not list(node):
        value = _normalize(node.text or "")
        # Dates with no time-of-day are equivalent even when the API varies the
        # redundant midnight suffix. Hearing times and deadlines retain their time.
        if value and ("dato" in name or name in {"innspillsfrist", "tidspunkt"}):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if name in {"datert_dato", "sendt_dato", "besvart_dato"}:
                    return parsed.date().isoformat()
                return parsed.isoformat(timespec="seconds")
            except ValueError:
                pass
        return value
    parts = []
    for child in node:
        value = _field_value(child)
        if value:
            child_name = _local(child.tag)
            label = _FIELD_LABELS.get(child_name, child_name.replace("_", " "))
            parts.append(f"{label}: {value}")
    return "; ".join(sorted(set(parts), key=str.casefold))


def _semantic_fields(node: ET.Element) -> dict[str, str]:
    fields = {}
    for child in node:
        name = _local(child.tag)
        if name in _VOLATILE_FIELDS or name == "id":
            continue
        value = _field_value(child)
        if value:
            fields[name] = value
    return fields


def _changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    out = []
    priorities = {name: index for index, name in enumerate((
        "status", "horing_status", "besvart_dato", "besvart_av",
        "besvart_av_minister_tittel", "besvart_pa_vegne_av", "innspillsfrist",
        "anmodningsfrist_dato_tid", "soknadfrist_dato", "start_dato",
        "horingstidspunkt_liste", "tittel", "korttittel",
    ))}
    for name in sorted(before.keys() | after.keys(), key=lambda key: (priorities.get(key, 100), key)):
        old, current = before.get(name, ""), after.get(name, "")
        if old == current:
            continue
        label = _FIELD_LABELS.get(name, name.replace("_", " ").capitalize())
        if name in {"status", "horing_status"}:
            old, current = _display_status(old), _display_status(current)
        old_excerpt, current_excerpt = _change_excerpts(old, current)
        out.append(f"{label}: {old_excerpt or 'ikke oppgitt'} → {current_excerpt or 'ikke oppgitt'}")
    return out


def _details_with_changes(changes: list[str], context: tuple[str, ...]) -> tuple[str, ...]:
    replaced = {value.partition(":")[0] for value in changes}
    if "Status" in replaced:
        replaced.update({"Spørsmålsstatus", "Saksstatus"})
    remaining = (value for value in context if value.partition(":")[0] not in replaced)
    return tuple(dict.fromkeys((*changes, *remaining)))


def _change_excerpts(before: str, after: str) -> tuple[str, str]:
    if max(len(before), len(after)) <= 180:
        return before, after
    op = next((op for op in SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
               if op[0] != "equal"), ("replace", 0, len(before), 0, len(after)))
    def excerpt(value: str, start: int) -> str:
        left = max(0, start - 50)
        right = min(len(value), left + 180)
        return ("…" if left else "") + value[left:right] + ("…" if right < len(value) else "")
    return excerpt(before, op[1]), excerpt(after, op[3])


def _stable_text(node: ET.Element) -> str:
    """Search the same named editorial fields that determine record changes."""
    return " ".join(value for _, value in sorted(_semantic_fields(node).items()))
