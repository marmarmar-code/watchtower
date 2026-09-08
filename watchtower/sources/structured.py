"""Configuration-only change monitoring for bounded public JSON and CSV."""
import csv
import io
import json
from urllib.parse import urljoin, urlparse

from .changes import SnapshotSource, canonical, document, field, integer, number, public_url, strings
from .common import SourceError


class StructuredSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if len(config.urls) != 1:
            raise ValueError("Structured monitoring requires exactly one URL")
        self.url = public_url(config.urls[0])
        o = config.options
        self.ids = strings(o.get("id_fields"), "id_fields")
        self.fields = strings(o.get("fields"), "fields")
        self.numeric_fields = strings(o.get("numeric_fields", []), "numeric_fields", empty=True)
        if set(self.numeric_fields) - set(self.fields):
            raise ValueError("numeric_fields must be included in fields")
        if set(self.thresholds) - set(self.fields):
            raise ValueError("threshold field must be included in fields")
        self.title_field, self.url_field = o.get("title_field"), o.get("url_field")
        self.published_field = o.get("published_field")
        self.records_path = o.get("records_path", "")
        self.next_path, self.total_path = o.get("next_path"), o.get("total_path")
        self.max_pages = integer(o.get("max_pages", 1), "max_pages", 1, 20)
        self.where = o.get("where", {})
        if not isinstance(self.where, dict):
            raise ValueError("where must be a table of field selections")
        for name, values in self.where.items():
            strings(values, "where selection")
        for value in (self.title_field, self.url_field, self.published_field, self.records_path, self.next_path, self.total_path):
            if value is not None and not isinstance(value, str):
                raise ValueError("record field paths must be strings")
        self.delimiter = o.get("delimiter", ",")
        if not isinstance(self.delimiter, str) or len(self.delimiter) != 1:
            raise ValueError("CSV delimiter must be one character")
        self.encoding = o.get("encoding", "utf-8-sig")
        if self.encoding not in {"utf-8", "utf-8-sig", "latin-1"}:
            raise ValueError("encoding must be utf-8, utf-8-sig or latin-1")

    def _rows(self):
        if self.config.kind == "csv_records":
            try:
                reader = csv.DictReader(io.StringIO(document(self, self.url).decode(self.encoding)), delimiter=self.delimiter, strict=True)
                names = reader.fieldnames
                if not names or len(names) != len(set(names)) or any(not name for name in names):
                    raise SourceError("CSV header is absent or ambiguous")
                rows = []
                for row in reader:
                    if None in row or None in row.values():
                        raise SourceError("CSV row does not match its header")
                    rows.append(row)
                    if len(rows) > self.max_records:
                        raise SourceError("CSV exceeds max_records")
                return rows
            except (UnicodeError, csv.Error) as exc:
                raise SourceError("Source returned invalid CSV") from exc
        rows, visited, url, total = [], set(), self.url, None
        for _ in range(self.max_pages):
            if url in visited or urlparse(url).netloc != urlparse(self.url).netloc:
                raise SourceError("JSON pagination loops or leaves the configured host")
            visited.add(url)
            raw = document(self, url)
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise SourceError("Source returned invalid JSON") from exc
            batch = field(data, self.records_path) if self.records_path else data
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise SourceError("JSON record selection is not an array of objects")
            rows.extend(batch)
            if len(rows) > self.max_records:
                raise SourceError("JSON exceeds max_records")
            if self.total_path:
                count = field(data, self.total_path)
                if isinstance(count, bool) or not isinstance(count, int) or count < 0 or (total is not None and count != total):
                    raise SourceError("JSON returned inconsistent total")
                total = count
            next_url = field(data, self.next_path) if self.next_path else None
            if next_url is None or next_url == "":
                if total is not None and total != len(rows):
                    raise SourceError("JSON result is incomplete")
                return rows
            if not isinstance(next_url, str) or not batch:
                raise SourceError("JSON returned invalid pagination")
            url = public_url(urljoin(url, next_url))
        raise SourceError("JSON pagination exceeds max_pages; previous state preserved")

    def read_records(self):
        records = []
        identities = set()
        for row in self._rows():
            identity = [field(row, name) for name in self.ids]
            if any(value is None or isinstance(value, (dict, list, bool)) or str(value).strip() == "" for value in identity):
                raise SourceError("Record identity must contain non-empty scalar values")
            key = canonical(identity)
            if key in identities:
                raise SourceError("Source returned duplicate record identities")
            identities.add(key)
            if any(str(field(row, name)) not in values for name, values in self.where.items()):
                continue
            values = {name: field(row, name) for name in self.fields}
            for name in self.numeric_fields:
                if values[name] is None or values[name] == "":
                    values[name] = None
                else:
                    numeric = number(values[name])
                    if numeric is None:
                        raise SourceError("Configured numeric field contains invalid data")
                    values[name] = format(numeric.normalize(), "f")
            try:
                canonical(values)
            except (ValueError, TypeError) as exc:
                raise SourceError("Record has unsupported field values") from exc
            records.append({
                "key": key, "fields": values,
                "title": str(field(row, self.title_field)) if self.title_field else self.config.label + " · " + " / ".join(map(str, identity)),
                "url": public_url(urljoin(self.url, str(field(row, self.url_field)))) if self.url_field else self.url,
                "published": str(field(row, self.published_field)) if self.published_field else None,
            })
        return records
