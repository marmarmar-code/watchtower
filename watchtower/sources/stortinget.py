from __future__ import annotations

from dataclasses import replace
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from .common import Source, SourceError
from ..models import Item

BASE = "https://data.stortinget.no/eksport"
_IDENTITY_VERSION = 2
_STATE_VERSION_KEY = "stortinget_identity_version"
_VOLATILE_FIELDS = {"respons_dato_tid", "versjon"}
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
        if previous and previous.get(_STATE_VERSION_KEY) != _IDENTITY_VERSION:
            if previous.get("seen") and not items:
                raise SourceError("cannot migrate Stortinget identities from an empty feed")
            datasets = self.config.options.get("datasets", list(_XML_CONTRACTS))
            for dataset in datasets:
                prefix = _XML_CONTRACTS[dataset][2]
                if (any(key.startswith(prefix) for key in previous.get("seen", {}))
                        and not any(item.key.startswith(prefix) for item in items)):
                    raise SourceError(f"cannot migrate Stortinget identities from an empty {dataset} dataset")
            # Old releases used descendant person/topic IDs as record IDs. There
            # is no lossless mapping back to the records they overwrote. Observe
            # the complete corrected feed once, preserving old seen keys, without
            # treating thousands of historical records as newly published.
            items = [replace(item, suppress_alert=True) for item in items]
        return items

    def augment_state(self, state: dict) -> dict:
        # Called by the engine only after the entire fetch/evaluation succeeds;
        # its existing delivery transaction commits this with the new seen keys.
        return {**state, _STATE_VERSION_KEY: _IDENTITY_VERSION}

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
            out.append(Item(
                self.config.id,
                f"sak:{item_id}",
                title,
                f"{BASE}/sak?{urlencode({'sakid': item_id})}",
                _date(node, "sist_oppdatert_dato", "dato") or None,
                text=_stable_text(node),
                metadata={"dataset": "sak", "status": status},
                alert_details=(f"Saksstatus: {status}",) if status else (),
            ))
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
            details = [f"Spørsmålsstatus: {status}"] if status else []
            if answered:
                details.append(f"Besvart: {answered}")
            out.append(Item(
                self.config.id,
                f"sporsmal:{item_id}",
                title,
                f"{BASE}/enkeltsporsmal?{urlencode({'NSporsmalId': item_id})}",
                _date(node, "sendt_dato", "datert_dato") or None,
                text=_stable_text(node),
                metadata={
                    "dataset": "skriftlig spørsmål",
                    "status": status,
                    "minister": _first(node, "sporsmal_til_minister_tittel"),
                    "answered_at": answered,
                },
                alert_details=tuple(details),
            ))
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
            if deadline:
                details.append(f"Frist: {deadline}")
            out.append(Item(
                self.config.id,
                f"horing:{item_id}",
                title,
                "https://www.stortinget.no/no/Hva-skjer-pa-Stortinget/Horing/horing/?"
                + urlencode({"h": item_id}),
                _date(node, "start_dato", "horing_dato_tid", "anmodningsfrist_dato_tid") or None,
                text=_stable_text(node),
                metadata={"dataset": "høring", "status": status, "deadline": deadline},
                alert_details=tuple(details),
            ))
        return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(node: ET.Element) -> str:
    return " ".join(part.strip() for part in node.itertext() if part and part.strip())


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
            return value
    return ""


def _all_text(node: ET.Element) -> list[str]:
    if _local(node.tag) in _VOLATILE_FIELDS:
        return []
    parts = [node.text.strip()] if node.text and node.text.strip() else []
    for child in node:
        parts.extend(_all_text(child))
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return parts


def _stable_text(node: ET.Element) -> str:
    """Keep searchable content stable across ordering and response timestamps.

    Response timestamps appear on both records and nested people/committees and
    change on every request. They are transport metadata, not editorial updates.
    Event dates, question status and hearing deadlines remain part of the hash.
    """
    return " ".join(sorted(_all_text(node), key=str.casefold))
