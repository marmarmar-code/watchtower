"""Explicit references to a private, inline entity list; no network lookup."""

from __future__ import annotations

import re
from typing import Any

from .sources.identifiers import valid_orgnr


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("entity values must be non-empty string arrays")
    return list(dict.fromkeys(item.strip() for item in value))


def load_entities(rows: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("[[entity]] entries must be tables")
    entities = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"id", "name", "orgnr", "aliases", "isins"}:
            raise ValueError("invalid entity fields; use id, name, orgnr, aliases and isins")
        entity_id, name = row.get("id"), row.get("name")
        if not isinstance(entity_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", entity_id):
            raise ValueError("entity.id must be a lowercase identifier")
        if entity_id in entities:
            raise ValueError("duplicate entity.id")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("entity.name must be a non-empty string")
        orgnr = row.get("orgnr", "")
        if not isinstance(orgnr, str) or (orgnr and not valid_orgnr(orgnr)):
            raise ValueError("entity.orgnr must be a valid nine-digit organisation number")
        isins = [value.upper() for value in _strings(row.get("isins", []))]
        if any(not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value) for value in isins):
            raise ValueError("entity.isins must contain 12-character ISINs")
        entities[entity_id] = {
            "name": name.strip(), "orgnr": orgnr,
            "aliases": _strings(row.get("aliases", [])), "isins": isins,
        }
    return entities


def _selected(refs: Any, entities: dict) -> list[dict]:
    refs = _strings(refs)
    if any(ref not in entities for ref in refs):
        raise ValueError("unknown entity reference; define it in [[entity]]")
    return [entities[ref] for ref in refs]


def resolve_entity_terms(refs: Any, entities: dict) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        term for entity in _selected(refs, entities)
        for term in (entity["name"], *entity["aliases"])
    ))


def resolve_entity_options(kind: str, refs: Any, entities: dict, options: dict) -> dict:
    selected = _selected(refs, entities)
    if not selected:
        return options
    mappings = {
        "brreg": ("companies", "orgnr"),
        "patentstyret": ("companies", "orgnr"),
        "stotte": ("recipient_orgnrs", "orgnr"),
        "finanstilsynet_short_sale": ("isins", "isins"),
    }
    if kind not in mappings:
        raise ValueError("source.entity_refs requires a supported register; use filter.entity_refs for text matching")
    target, field = mappings[kind]
    if any(not entity[field] for entity in selected):
        raise ValueError(f"referenced entity requires {field} for this register")
    values = [value for entity in selected for value in (
        entity[field] if isinstance(entity[field], list) else [entity[field]]
    )]
    # Resolve legacy synonyms too: enabling references must not drop old selections.
    synonyms = {
        "patentstyret": "organisation_numbers", "stotte": "recipients",
        "finanstilsynet_short_sale": "allowed_isins",
    }
    existing = options.get(target, options.get(synonyms.get(kind, ""), []))
    return {**options, target: list(dict.fromkeys((*_strings(existing), *values)))}
