from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from .common import Source, SourceError
from ..models import Item

BASE = "https://data.stortinget.no/eksport"


class StortingetSource(Source):
    def fetch(self) -> list[Item]:
        datasets = self.config.options.get("datasets", ["saker", "skriftligesporsmal", "horinger"])
        if not isinstance(datasets, list):
            raise SourceError("stortinget datasets must be a list")
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
        return items

    def _xml(self, endpoint: str, params: dict[str, str] | None = None) -> ET.Element:
        url = f"{BASE}/{endpoint}"
        response = self.get(url, params=params)
        try:
            return ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise SourceError(f"invalid Stortinget XML: {endpoint}") from exc

    def _fetch_saker(self) -> list[Item]:
        root = self._xml("saker")
        out = []
        for node in _nodes(root, "sak"):
            item_id = _first(node, "id")
            title = _first(node, "korttittel", "tittel", "sak_korttittel")
            if not item_id or not title:
                continue
            out.append(Item(
                self.config.id,
                f"sak:{item_id}",
                title,
                f"{BASE}/sak?{urlencode({'sakid': item_id})}",
                _first(node, "sist_oppdatert_dato", "dato") or None,
                text=_stable_text(node),
                metadata={"dataset": "sak"},
            ))
        return out

    def _fetch_questions(self) -> list[Item]:
        root = self._xml("skriftligesporsmal", {"status": "alle"})
        out = []
        for node in _nodes(root, "sporsmal"):
            item_id = _first(node, "id")
            title = _first(node, "tittel")
            if not item_id or not title:
                continue
            out.append(Item(
                self.config.id,
                f"sporsmal:{item_id}",
                title,
                f"{BASE}/enkeltsporsmal?{urlencode({'NSporsmalId': item_id})}",
                _first(node, "sendt_dato", "datert_dato") or None,
                text=_stable_text(node),
                metadata={
                    "dataset": "skriftlig spørsmål",
                    "status": _first(node, "status"),
                    "minister": _first(node, "sporsmal_til_minister_tittel"),
                },
            ))
        return out

    def _fetch_hearings(self) -> list[Item]:
        root = self._xml("horinger")
        out = []
        for node in _nodes(root, "horing"):
            item_id = _first(node, "id", "horing_id")
            title = _first(node, "tittel", "sak_korttittel")
            if not item_id:
                item_id = "|".join(_all_text(node))[:300]
            if not title:
                titles = [_element_text(x) for x in node.iter() if _local(x.tag) == "sak_korttittel"]
                title = " / ".join(t for t in titles if t)
            if not title:
                continue
            out.append(Item(
                self.config.id,
                f"horing:{item_id}",
                title,
                "https://www.stortinget.no/no/Hva-skjer-pa-Stortinget/Horing/",
                _first(node, "horing_dato_tid", "anmodningsfrist_dato_tid") or None,
                text=_stable_text(node),
                metadata={"dataset": "høring"},
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
    wanted = set(names)
    for child in node.iter():
        if _local(child.tag) in wanted:
            text = _element_text(child)
            if text:
                return text
    return ""


def _all_text(node: ET.Element) -> list[str]:
    return [part.strip() for part in node.itertext() if part and part.strip()]


def _stable_text(node: ET.Element) -> str:
    """Return searchable XML text without depending on element order.

    Stortinget occasionally returns the same record with nested elements in a
    different order. Item hashes include ``text``, so preserving response order
    turns those harmless reshuffles into apparent updates and rewrites state.
    Sorting the text fragments keeps every searchable value while making the
    representation deterministic.
    """
    return " ".join(sorted(_all_text(node), key=str.casefold))
