"""Published REMIT enforcement rows; report groups are not official case IDs."""
import base64
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import re
from urllib.parse import urlparse, parse_qs
from uuid import UUID, uuid4

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, integer, strings
from .common import SourceError
from .remit_table import PROPERTIES, table, participant_count

PAGE = "https://www.acer.europa.eu/remit/coordination-on-cases/enforcement-decisions"


def text(value):
    if not isinstance(value, str):
        raise SourceError("REMIT text field changed type")
    result = " ".join(value.replace("\u200b", "").split())
    if not result or len(result) > 10000 or "\ufffd" in result:
        raise SourceError("REMIT text field is empty or invalid")
    return result


def routing(page, embed):
    """Resolve only the official page's public embed; never persist its key."""
    soup = BeautifulSoup(page, "html.parser")
    if [h.get_text(" ", strip=True) for h in soup.select("h1")] != ["Enforcement decisions"]:
        raise SourceError("REMIT page heading changed")
    frames = [f.get("src", "") for f in soup.select("iframe") if urlparse(f.get("src", "")).hostname == "app.powerbi.com"]
    if len(frames) != 1:
        raise SourceError("REMIT public report embed is missing or ambiguous")
    url = frames[0]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "app.powerbi.com" or parsed.path != "/view" or parsed.fragment:
        raise SourceError("REMIT report embed route changed")
    try:
        encoded = parse_qs(parsed.query)["r"]
        if len(encoded) != 1:
            raise ValueError()
        descriptor = json.loads(base64.b64decode(encoded[0], validate=True))
        key = str(UUID(descriptor["k"]))
        UUID(descriptor["t"])
    except (ValueError, KeyError, TypeError):
        raise SourceError("REMIT public embed descriptor changed") from None
    notes = [text(h.get_text(" ", strip=True)) for h in soup.select("h6") if h.get_text(strip=True).startswith("*")]
    if len(notes) != 3 or [len(n) - len(n.lstrip("*")) for n in notes] != [1, 2, 3]:
        raise SourceError("REMIT monetary footnotes changed structure")
    if embed is None:
        return url, key, notes, None
    clusters = re.findall(r"var resolvedClusterUri\s*=\s*'([^']+)'", embed)
    if len(clusters) != 1:
        raise SourceError("REMIT public report routing is unavailable")
    cluster = urlparse(clusters[0])
    if cluster.scheme != "https" or cluster.path != "/" or cluster.query or cluster.fragment or not re.fullmatch(r"wabi-[a-z0-9-]+-redirect\.analysis\.windows\.net", cluster.netloc):
        raise SourceError("REMIT public report routing changed")
    api = "https://" + cluster.netloc.replace("-redirect.", "-api.")
    return url, key, notes, api


def model_contract(payload, max_age_days):
    try:
        models = payload["models"]
        if len(models) != 1:
            raise ValueError()
        model = models[0]
        ident = model["id"]
        if isinstance(ident, bool) or not isinstance(ident, int) or ident <= 0 or model["displayName"] != "Enforcement Decisions - official" or model["lastRefreshStatus"] != 0:
            raise ValueError()
        match = re.fullmatch(r"/Date\(([0-9]+)\)/", model["lastRefreshTime"])
        if not match:
            raise ValueError()
        refreshed = datetime.fromtimestamp(int(match[1]) / 1000, timezone.utc)
        now = datetime.now(timezone.utc)
        if not now - timedelta(days=max_age_days) <= refreshed <= now + timedelta(minutes=10):
            raise SourceError("REMIT source model refresh is stale or future-dated")
        queries = []
        for section in payload["exploration"]["sections"]:
            for visual in section["visualContainers"]:
                query = visual.get("query", "")
                if isinstance(query, str) and "Fine Amount" in query and "WEB URL" in query:
                    queries.append(json.loads(query))
        if len(queries) != 1:
            raise ValueError()
        query = queries[0]
        commands = query["Commands"]
        if len(commands) != 1 or set(commands[0]) != {"SemanticQueryDataShapeCommand"}:
            raise ValueError()
        command = commands[0]["SemanticQueryDataShapeCommand"]
        semantic = command["Query"]
        if set(semantic) - {"Version", "From", "Select", "OrderBy"} or semantic["Version"] != 2 or semantic["From"] != [{"Name": "a1", "Entity": "FINES", "Type": 0}]:
            raise ValueError()
        selects = semantic["Select"]
        if len(selects) != 8:
            raise ValueError()
        for select, prop in zip(selects, PROPERTIES):
            column = select.get("Column", select.get("Aggregation", {}).get("Expression", {}).get("Column"))
            if column != {"Expression": {"SourceRef": {"Source": "a1"}}, "Property": prop}:
                raise ValueError()
        if command["Binding"]["Primary"] != {"Groupings": [{"Projections": list(range(8))}]}:
            raise ValueError()
        return ident, refreshed.isoformat(), query
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        raise SourceError("REMIT public model or table contract changed") from None


def queries(observed, limit):
    result = deepcopy(observed)
    cmd = result["Commands"][0]["SemanticQueryDataShapeCommand"]
    cmd["Query"].pop("OrderBy", None)
    cmd["Query"]["Select"] = [{"Column": {"Expression": {"SourceRef": {"Source": "a1"}}, "Property": p}, "Name": "FINES." + p} for p in PROPERTIES]
    cmd["Binding"]["DataReduction"]["Primary"] = {"Window": {"Count": limit}}
    count = deepcopy(result)
    c = count["Commands"][0]["SemanticQueryDataShapeCommand"]
    c["Query"]["Select"] = [{"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "a1"}}, "Property": "Market Participant Proper"}}, "Function": 5}, "Name": "CountNonNull"}]
    c["Binding"]["Primary"]["Groupings"][0]["Projections"] = [0]
    return result, count


class RemitEnforcementSource(SnapshotSource):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError("remit_enforcement accepts only the official ACER page")
        if self.complete or "removed" in self.events:
            raise ValueError("Report absence cannot establish withdrawal or acquittal")
        self.from_year = integer(config.options.get("from_year", 2024), "from_year", 2011, 2100)
        self.authorities = strings(config.options["authorities"], "authorities") if "authorities" in config.options else ()
        self.participants = strings(config.options["participants"], "participants") if "participants" in config.options else ()
        self.limit = integer(config.options.get("max_report_rows", 500), "max_report_rows", 10, 5000)
        self.max_age = integer(config.options.get("max_model_age_days", 45), "max_model_age_days", 1, 365)

    def _download(self, url, *, headers=None, body=None):
        method = self.get if body is None else self.post
        options = {"headers": headers or {}, "stream": True, "allow_redirects": False}
        if body is not None:
            options["json"] = body
        response = method(url, **options)
        try:
            size, chunks = 0, []
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.max_bytes:
                    raise SourceError("REMIT response exceeds max_bytes")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()

    def _json(self, url, **kwargs):
        try:
            return json.loads(self._download(url, **kwargs))
        except (ValueError, UnicodeError):
            raise SourceError("REMIT public report did not return JSON") from None

    def _poll(self):
        page = self._download(PAGE)
        embed_url, _, _, _ = routing(page, None)
        _, key, notes, api = routing(page, self._download(embed_url).decode("utf-8"))
        headers = {"X-PowerBI-ResourceKey": key, "ActivityId": str(uuid4()), "RequestId": str(uuid4())}
        model_url = api + "/public/reports/" + key + "/modelsAndExploration?preferReadOnlySession=true"
        model = self._json(model_url, headers=headers)
        model_id, refreshed, observed = model_contract(model, self.max_age)
        table_query, count_query = queries(observed, self.limit)
        results = []
        for query in (table_query, count_query):
            body = {"version": "1.0.0", "queries": [{"Query": query, "CacheKey": ""}], "cancelQueries": [], "modelId": model_id}
            results.append(self._json(api + "/public/reports/querydata?synchronous=true", headers=headers, body=body))
        rows = table(results[0], self.limit)
        count = participant_count(results[1])
        if len(rows) != count or count >= self.limit:
            raise SourceError("REMIT projected rows disagree with independent participant count or reach the row limit")
        return rows, notes, refreshed

    def _records(self, rows, notes):
        groups = {}
        for row in rows:
            breach, authority, participant, amount, status, link, year, url = row
            breach, authority, participant, amount, status, link, url = map(text, (breach, authority, participant, amount, status, link, url))
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise SourceError("REMIT source decision link is invalid")
            if year > datetime.now(timezone.utc).year:
                raise SourceError("REMIT decision year is in the future")
            if year < self.from_year or self.authorities and authority not in self.authorities or self.participants and participant not in self.participants:
                continue
            key = canonical([year, authority, participant, breach, url])
            if key not in groups:
                groups[key] = {"key": key, "title": "REMIT: " + participant + " – " + authority + " (" + str(year) + ")", "url": url, "published": None, "source_notes": notes, "fields": {"participant": participant, "authority": authority, "decision_year": year, "breach": breach, "entries": []}}
            groups[key]["fields"]["entries"].append({"amount": amount, "status": status, "link_label": link})
        for group in groups.values():
            group["fields"]["entries"].sort(key=canonical)
        return sorted(groups.values(), key=lambda row: row["key"])

    def read_records(self):
        rows, notes, refreshed = self._poll()
        again, notes_again, refresh_again = self._poll()
        if sorted(rows, key=canonical) != sorted(again, key=canonical) or notes != notes_again or refreshed != refresh_again:
            raise SourceError("REMIT report changed between complete reads")
        self.report_rows, self.model_refreshed = len(rows), refreshed
        return self._records(rows, notes)

    def fetch_with_state(self, previous):
        items = super().fetch_with_state(previous)
        old = ((previous or {}).get("source_state") or {}).get("records", {})
        if old.get("scope") == self.scope and old.get("model_refreshed", "") > self.model_refreshed:
            raise SourceError("REMIT source model refresh regressed")
        if old.get("scope") == self.scope:
            # A missing report group is not a withdrawal. Keep its last observed
            # values so a later return can still explain a genuine revision.
            self._next["rows"] = {**old.get("rows", {}), **self._next["rows"]}
            if len(self._next["rows"]) > self.max_records * 2:
                raise SourceError("REMIT retained report groups exceed the history bound")
        self._next["model_refreshed"] = self.model_refreshed
        self._next["report_rows"] = self.report_rows
        return items

    @staticmethod
    def _entries(entries):
        return " | ".join("Beløp/reaksjon: " + e["amount"] + "; kildestatus: " + e["status"] + "; " + e["link_label"] for e in entries)

    def describe_change(self, name, before, after):
        if name == "entries":
            if len(before) == len(after) == 1:
                return "; ".join(label + ": " + before[0][field] + " → " + after[0][field] for field, label in (("amount", "Oppgitt beløp/reaksjon"), ("status", "Kildestatus"), ("link_label", "Kildelenkens merknad")) if before[0][field] != after[0][field])
            return "Rapportens underposter før: " + self._entries(before) + " | Etter: " + self._entries(after)
        return super().describe_change(name, before, after)

    def _item(self, row, event, details, suppress):
        fields = row["fields"]
        content = ["Nyobservert REMIT-rapportoppføring" if event == "added" else "Endrede REMIT-rapportopplysninger", "Oppgitt vedtaksår: " + str(fields["decision_year"]) + " · Myndighet: " + fields["authority"], "Kildens regelreferanse: " + fields["breach"]]
        content.extend([self._entries(fields["entries"])] if event == "added" else details[1:])
        content.append("Beløp og reaksjon gjengis fra kilden; kan omfatte inndragning eller tilbakebetaling. År er ikke vedtaksdato. Gruppen er ikke en offisiell saks-ID.")
        content.append("Rapport og beløpsmerknader: " + PAGE)
        return super()._item(row, event, content, suppress)
