"""Strict decoder for the observed public REMIT Power BI table contract."""
from .common import SourceError

PROPERTIES = (
    "Type of REMIT Breach", "NRA, Member State", "Market Participant Proper",
    "Fine Amount", "Status", "Link", "Decision Year", "WEB URL ",
)


def _fail():
    raise SourceError("REMIT Power BI response contract changed; previous state preserved")


def _data(payload, *, count=False):
    try:
        if not isinstance(payload, dict) or set(payload) != {"jobIds", "results"}:
            _fail()
        results = payload["results"]
        if not isinstance(results, list) or len(results) != 1:
            _fail()
        result = results[0]["result"]
        if not isinstance(result, dict) or set(result) != {"data"}:
            _fail()
        data = result["data"]
        if set(data) - {"timestamp", "rootActivityId", "descriptor", "metrics", "fromCache", "dsr"}:
            _fail()
        if not isinstance(data.get("fromCache"), bool):
            _fail()
        dsr = data["dsr"]
        if set(dsr) != {"Version", "MinorVersion", "DS"} or dsr["Version"] != 2 or dsr["MinorVersion"] != 1:
            _fail()
        if len(dsr["DS"]) != 1:
            _fail()
        ds = dsr["DS"][0]
        if set(ds) - {"N", "PH", "IC", "HAD", "ValueDicts", "Msg"} or ds.get("N") != "DS0" or ds.get("IC") is not True or ds.get("HAD") is not True:
            _fail()
        messages = ds.get("Msg", [])
        if messages and (not count or len(messages) != 1 or messages[0].get("Code") != "IgnoredDataReductionAlgorithm" or messages[0].get("Severity") != "Warning"):
            _fail()
        if len(ds["PH"]) != 1 or set(ds["PH"][0]) != {"DM0"}:
            _fail()
        if data["descriptor"].get("Version") != 2:
            _fail()
        return data, ds, ds["PH"][0]["DM0"]
    except (KeyError, TypeError, IndexError, AttributeError):
        _fail()


def table(payload, limit=500):
    data, ds, rows = _data(payload)
    try:
        descriptors = data["descriptor"]["Select"]
        if not isinstance(descriptors, list) or len(descriptors) != 8 or not isinstance(rows, list) or not 1 <= len(rows) <= limit:
            _fail()
        for index, (descriptor, prop) in enumerate(zip(descriptors, PROPERTIES)):
            value = "G" + str(index)
            groups = [{"Source": {"Entity": "FINES", "Property": prop}, "Calc": value, "IsSameAsSelect": True}]
            if descriptor.get("Kind") != 1 or descriptor.get("Depth") != 0 or descriptor.get("Value") != value or descriptor.get("GroupKeys") != groups or descriptor.get("Name") not in ("FINES." + prop, "ALL_FINES (2)." + prop):
                _fail()
        schema = rows[0].get("S")
        if not isinstance(schema, list) or len(schema) != 8:
            _fail()
        dictionaries = ds.get("ValueDicts", {})
        if not isinstance(dictionaries, dict) or any(not isinstance(v, list) or len(v) > limit or any(not isinstance(s, str) for s in v) for v in dictionaries.values()):
            _fail()
        names = set()
        for index, column in enumerate(schema):
            expected = {"N": "G" + str(index), "T": 4 if index == 6 else 1}
            if index != 6:
                name = column.get("DN")
                if not isinstance(name, str) or name not in dictionaries:
                    _fail()
                expected["DN"] = name
                names.add(name)
            if column != expected:
                _fail()
        if names != set(dictionaries):
            _fail()
        decoded = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) - ({"S", "C"} if index == 0 else {"C", "R"}) or not isinstance(row.get("C"), list):
                _fail()
            mask = row.get("R", 0)
            if isinstance(mask, bool) or not isinstance(mask, int) or not 0 <= mask < 256:
                _fail()
            values, cursor = [], 0
            for column in range(8):
                if mask & (1 << column):
                    value = decoded[-1][column]
                else:
                    if cursor >= len(row["C"]):
                        _fail()
                    value = row["C"][cursor]
                    cursor += 1
                    if column != 6 and isinstance(value, int) and not isinstance(value, bool):
                        dictionary = dictionaries[schema[column]["DN"]]
                        if not 0 <= value < len(dictionary):
                            _fail()
                        value = dictionary[value]
                if column == 6:
                    if isinstance(value, bool) or not isinstance(value, int) or not 2011 <= value <= 2100:
                        _fail()
                elif not isinstance(value, str) or not value.strip() or len(value) > 10000:
                    _fail()
                values.append(value)
            if cursor != len(row["C"]):
                _fail()
            decoded.append(values)
        if len({tuple(row) for row in decoded}) != len(decoded):
            _fail()
        return decoded
    except (KeyError, TypeError, IndexError, AttributeError):
        _fail()


def participant_count(payload):
    data, ds, rows = _data(payload, count=True)
    try:
        if data["descriptor"]["Select"] != [{"Kind": 2, "Value": "M0", "Name": "CountNonNull"}] or len(rows) != 1 or set(rows[0]) != {"S", "M0"} or rows[0]["S"] != [{"N": "M0", "T": 4}] or "ValueDicts" in ds:
            _fail()
        value = rows[0]["M0"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail()
        return value
    except (KeyError, TypeError, IndexError, AttributeError):
        _fail()
