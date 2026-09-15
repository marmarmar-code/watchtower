"""Product and batch changes in Mattilsynet's public recall notices."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, document, integer, strings
from .common import SourceError

INDEX = "https://www.mattilsynet.no/tilbakekallinger"
LABELS = {
    "product": ("Produktnavn", "Produktnamn"),
    "recalling_company": ("Virksomhet som kaller tilbake varen", "Verksemd som kallar tilbake vara"),
    "manufacturer": ("Produksjonsvirksomhet", "Produksjonsverksemd"),
    "importer": ("Importør",),
    "expiry": ("Holdbarhetsdato", "Haldbarheitsdato"),
    "batch": ("Batchnummer",),
    "lot": ("Lot-nummer",),
    "ean": ("Varenummer, for eksempel EAN-nummer", "Varenummer, til dømes EAN-nummer"),
    "origin": ("Opprinnelsesland", "Opphavsland"),
    "pack_size": ("Vekt/pakningsstørrelse", "Vekt/pakningsstorleik"),
}
NAMES = {key: values[0] for key, values in LABELS.items()}
NAMES.update(title="Tilbakekalling", description="Begrunnelse og omfang")


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def _recall_url(url):
    p = urlsplit(url)
    if (p.scheme != "https" or p.netloc != "www.mattilsynet.no"
            or not p.path.startswith("/tilbakekallinger/") or p.query or p.fragment):
        raise SourceError("Recall link left the official recall section")
    return url


def parse_recall(raw, url, watched_fields):
    soup = BeautifulSoup(raw, "html.parser")
    main = soup.select_one("main")
    if main is None or main.select_one("h1") is None:
        raise SourceError("Recall page lacks its main heading")
    products = []
    # One notice can contain several product groups. Keep each group's batch,
    # expiry and importer together rather than overwriting repeated labels.
    for group in main.select("dl"):
        definitions = {}
        for dt in group.find_all("dt", recursive=False):
            label = _text(dt)
            dd = dt.find_next_sibling("dd")
            if dd is None or label in definitions:
                raise SourceError("Recall product fields are incomplete or duplicated")
            definitions[label] = _text(dd)
        if not any(alias in definitions for alias in LABELS["product"]):
            continue
        values = {}
        for key, aliases in LABELS.items():
            matched = [definitions[label] for label in aliases if label in definitions]
            if len(matched) > 1:
                raise SourceError("Recall product field has ambiguous language labels")
            values[key] = matched[0] if matched else None
        if not values["product"]:
            raise SourceError("Recall page lacks the product name")
        products.append({key: values[key] for key in watched_fields if key in LABELS})
    if not products:
        raise SourceError("Recall page lacks product groups")
    if len({canonical(p) for p in products}) != len(products):
        raise SourceError("Recall page repeats identical product groups")
    products.sort(key=canonical)
    pages = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except ValueError as exc:
            raise SourceError("Recall page has invalid publication metadata") from exc
        if isinstance(data, dict):
            pages.extend(row for row in data.get("@graph", [data])
                         if isinstance(row, dict) and row.get("@type") == "WebPage")
    # Some official pages emit their load balancer IP in JSON-LD. The fetched
    # official URL remains both identity and link; metadata must match its path.
    if (len(pages) != 1 or not isinstance(pages[0].get("url"), str)
            or urlsplit(pages[0]["url"]).path != urlsplit(url).path):
        raise SourceError("Recall publication metadata is missing or ambiguous")
    metadata = pages[0]
    published = metadata.get("datePublished")
    try:
        parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise SourceError("Recall publication timestamp is invalid") from exc
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SourceError("Recall page lacks its description")
    values = {"title": _text(main.select_one("h1"))}
    values["description"] = _text(BeautifulSoup(description, "html.parser"))
    return {"key": url, "url": url, "title": values["title"], "published": published,
            "fields": {"products": products, **{key: values[key] for key in watched_fields if key in values}}}


class FoodRecallsSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls and config.urls != (INDEX,):
            raise ValueError("food_recalls uses Mattilsynet's official recall index")
        if self.complete or "removed" in self.events:
            raise ValueError("A recall window cannot prove removal; removed is unsupported")
        self.limit = integer(config.options.get("max_notices", 10), "max_notices", 1, 10)
        self.fields = strings(config.options.get("watched_fields", list(NAMES)), "watched_fields")
        if set(self.fields) - NAMES.keys():
            raise ValueError("Unknown recall watched_fields")
        if "product" not in self.fields:
            raise ValueError("watched_fields must include product to preserve product-group identity")
        self.field_labels = {**NAMES, "products": "Produktomfang", **self.field_labels}

    def read_records(self):
        soup = BeautifulSoup(document(self, INDEX), "html.parser")
        anchors = soup.select("main ol#list a[href]")
        if not anchors:
            raise SourceError("Recall index has no notice links; previous state preserved")
        urls = [_recall_url(urljoin(INDEX, a["href"])) for a in anchors]
        if len(urls) != len(set(urls)):
            raise SourceError("Recall index has duplicate notice links")
        return [parse_recall(document(self, url), url, self.fields)
                for url in urls[:self.limit]]

    def describe_change(self, name, before, after):
        if name == "products":
            return "Produktomfang: " + _products_text(before) + " → " + _products_text(after)
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        label = "Ny tilbakekalling" if event == "added" else "Endret tilbakekalling"
        if event == "added":
            details = tuple(f"{self.field_labels.get(key, key)}: " +
                            (_products_text(value) if key == "products" else str(value))
                            for key, value in row["fields"].items())
        else:
            details = item.alert_details[1:]
        return replace(item, alert_details=(label, *details))


def _products_text(products):
    shown = ["; ".join(f"{NAMES[k]}: {str(v)[:160]}" for k, v in p.items() if v)
             for p in products[:4]]
    if len(products) > 4:
        shown.append(f"og {len(products) - 4} flere produktgrupper (se kilden)")
    return " | ".join(shown)
