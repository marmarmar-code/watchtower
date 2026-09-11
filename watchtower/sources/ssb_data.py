"""Actual SSB observations and revisions in explicitly selected JSON-stat cubes."""
import itertools
import json
import math
import re
from urllib.parse import urlencode

from .changes import SnapshotSource, canonical, document, number, shown, strings
from .common import SourceError
from .ssb import DEFAULT_BASE_URL


# SSB/PxWeb metadata: https://www.ssb.no/api/pxwebapiv2
_ADJUSTMENTS = {"SesOnly": "sesongjustert", "WorkOnly": "kalenderjustert",
                "WorkAndSes": "kalender- og sesongjustert", "None": "ujustert"}


def _unit_text(units, *, revision=False):
    descriptions = []
    for unit in units.values():
        parts = [str(unit["base"])]
        if unit.get("base_period"):
            parts.append("basis " + str(unit["base_period"]))
        adjustment = unit.get("adjustment")
        if adjustment and (adjustment != "None" or revision):
            parts.append(_ADJUSTMENTS.get(str(adjustment), "justering: " + str(adjustment)))
        for name, value in unit.items():
            if name in {"base", "base_period", "adjustment"} or (name == "decimals" and not revision):
                continue
            if value is not None:
                parts.append(f"{'desimaler' if name == 'decimals' else name}: {shown(value)}")
        descriptions.append(", ".join(parts))
    return " / ".join(descriptions)


def _vector(value, size, *, status=False):
    if isinstance(value, list):
        if len(value) != size:
            raise SourceError("JSON-stat vector length differs from cube size")
        result = value
    elif isinstance(value, dict):
        if any(not key.isdigit() or str(int(key)) != key or int(key) >= size for key in value):
            raise SourceError("JSON-stat vector contains invalid positions")
        result = [value.get(str(index), "" if status else None) for index in range(size)]
    elif status and isinstance(value, str):
        result = [value] * size
    else:
        raise SourceError("JSON-stat observation vector is absent or invalid")
    if status and any(not isinstance(x, (str, type(None))) for x in result):
        raise SourceError("JSON-stat status codes must be strings")
    if not status and any(x is not None and (isinstance(x, (str, bool)) or number(x) is None) for x in result):
        raise SourceError("JSON-stat values must be finite numbers or null")
    return result


def observations(data, *, table, limit):
    if not isinstance(data, dict) or data.get("version") != "2.0" or data.get("class") != "dataset":
        raise SourceError("SSB did not return a JSON-stat 2 dataset")
    ids, sizes = data.get("id"), data.get("size")
    if (not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids)
            or len(ids) != len(set(ids)) or not isinstance(sizes, list) or len(ids) != len(sizes)
            or any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in sizes)):
        raise SourceError("JSON-stat dimensions are invalid")
    size = math.prod(sizes)
    if size > limit:
        raise SourceError("SSB selection exceeds max_records; select fewer values")
    dimensions = data.get("dimension")
    if not isinstance(dimensions, dict):
        raise SourceError("JSON-stat dimensions are absent")
    roles = data.get("role", {})
    times, metrics = roles.get("time", []), roles.get("metric", [])
    if not times or not metrics or set(times + metrics) - set(ids):
        raise SourceError("SSB time and metric dimensions are required")
    categories, codes = {}, []
    for name, count in zip(ids, sizes):
        category = dimensions.get(name, {}).get("category", {})
        index = category.get("index")
        if isinstance(index, dict):
            if any(isinstance(n, bool) or not isinstance(n, int) for n in index.values()) or set(index.values()) != set(range(count)):
                raise SourceError("JSON-stat category positions are inconsistent")
            index = sorted(index, key=index.get)
        if not isinstance(index, list) or len(index) != count or len(set(index)) != count or any(not isinstance(x, str) for x in index):
            raise SourceError("JSON-stat category identities are invalid")
        categories[name] = category
        codes.append(index)
    values = _vector(data.get("value"), size)
    statuses = _vector(data.get("status", {}), size, status=True)
    result = []
    for index, coordinate in enumerate(itertools.product(*codes)):
        selection = dict(zip(ids, coordinate))
        labels, units = [], {}
        for name, code in selection.items():
            category = categories[name]
            labels.append(str(category.get("label", {}).get(code, code)))
            if name in metrics:
                unit = category.get("unit", {}).get(code)
                if not isinstance(unit, dict) or not unit.get("base"):
                    raise SourceError("SSB observation lacks its unit")
                extension = dimensions[name].get("extension", {})
                units[name] = {**unit, "base_period": extension.get("basePeriod", {}).get(code),
                               "adjustment": extension.get("adjustment", {}).get(code)}
        result.append({"key": canonical(selection), "title": "SSB · " + " / ".join(labels),
                       "url": f"https://www.ssb.no/statbank/table/{table}",
                       "published": " / ".join(selection[name] for name in times),
                       "fields": {"value": values[index], "unit": units, "status": statuses[index] or ""}})
    return result


class SsbDataSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.table = config.options.get("table")
        if not isinstance(self.table, str) or not re.fullmatch(r"[0-9]{5}", self.table):
            raise ValueError("ssb_data requires one five-digit table")
        if config.urls:
            raise ValueError("ssb_data uses the official SSB endpoint")
        self.selection = config.options.get("value_codes")
        if not isinstance(self.selection, dict) or not self.selection:
            raise ValueError("ssb_data requires explicit value_codes selections")
        for values in self.selection.values():
            strings(values, "value_codes")
        if set(self.thresholds) - {"value"}:
            raise ValueError("SSB thresholds support the value field only")
        if "removed" in self.events or self.complete:
            raise ValueError("SSB rolling windows cannot produce removed events")
        params = {"lang": "no", "outputFormat": "json-stat2"}
        params.update({f"valueCodes[{name}]": ",".join(values) for name, values in self.selection.items()})
        self.url = f"{DEFAULT_BASE_URL}/tables/{self.table}/data?{urlencode(params)}"

    def read_records(self):
        raw = document(self, self.url)
        try:
            data = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise SourceError("SSB returned invalid JSON") from exc
        rows = observations(data, table=self.table, limit=self.max_records)
        # Require selection for every returned dimension; no silent API defaults.
        if set(data["id"]) != set(self.selection):
            raise SourceError("value_codes must explicitly select every SSB dimension")
        return rows

    def _item(self, row, event, details, suppress):
        label = {"added": "Ny statistikkobservasjon", "changed": "Revidert statistikkobservasjon"}[event]
        fields = row["fields"]
        context = "Enhet: " + _unit_text(fields["unit"])
        changes = details[1:]
        if event == "added":
            changes = [f"{self.field_labels.get('value', 'Verdi')}: {shown(fields['value'])}"]
            if fields["status"]:
                changes.append(f"{self.field_labels.get('status', 'Datastatus')}: {fields['status']}")
        return super()._item(row, event, (label, context, *changes), suppress)

    def describe_change(self, name, before, after):
        label = self.field_labels.get(name, {"value": "Verdi", "unit": "Enhet", "status": "Datastatus"}.get(name, name))
        if name == "unit":
            return f"{label}: {_unit_text(before, revision=True)} → {_unit_text(after, revision=True)}"
        return f"{label}: {shown(before)} → {shown(after)}"
