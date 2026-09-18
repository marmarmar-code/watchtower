"""Bounded reads of explicitly selected anonymous public Power BI tables."""
import base64
from datetime import datetime, timedelta, timezone
import json
import math
import re
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from .common import SourceError


def fail():
    raise SourceError('Public report response contract changed; previous state preserved')


class PublicReport:
    def __init__(self, source, embed_url, model_name, max_age_days=7):
        self.source = source
        parsed = urlparse(embed_url)
        try:
            if (parsed.scheme, parsed.netloc, parsed.path) != ('https', 'app.powerbi.com', '/view') or parsed.fragment:
                fail()
            encoded = parse_qs(parsed.query)['r']
            if len(encoded) != 1:
                fail()
            descriptor = json.loads(base64.b64decode(encoded[0], validate=True))
            self.key = str(UUID(descriptor['k']))
            UUID(descriptor['t'])
            raw = self.download(embed_url).decode('utf-8')
            clusters = re.findall(r"var resolvedClusterUri\s*=\s*'([^']+)'", raw)
            if len(clusters) != 1:
                fail()
            cluster = urlparse(clusters[0])
            if cluster.scheme != 'https' or cluster.path != '/' or cluster.query or cluster.fragment or not re.fullmatch(r'wabi-[a-z0-9-]+-redirect\.analysis\.windows\.net', cluster.netloc):
                fail()
            self.api = 'https://' + cluster.netloc.replace('-redirect.', '-api.')
            self.headers = {'X-PowerBI-ResourceKey': self.key}
            self.model = self.json(self.api+'/public/reports/'+self.key+'/modelsAndExploration?preferReadOnlySession=true')
            models = self.model['models']
            if len(models) != 1 or models[0]['displayName'] != model_name or models[0]['lastRefreshStatus'] != 0:
                fail()
            self.model_id = models[0]['id']
            if isinstance(self.model_id, bool) or not isinstance(self.model_id, int) or self.model_id < 1:
                fail()
            stamp = re.fullmatch(r'/Date\(([0-9]+)\)/', models[0]['lastRefreshTime'])
            if not stamp:
                fail()
            self.refreshed = datetime.fromtimestamp(int(stamp[1])/1000, timezone.utc)
            now = datetime.now(timezone.utc)
            if not now-timedelta(days=max_age_days) <= self.refreshed <= now+timedelta(minutes=10):
                raise SourceError('Public report source refresh is stale or future-dated')
        except (KeyError, ValueError, TypeError, OverflowError, UnicodeError):
            fail()

    def download(self, url, body=None):
        args = {'stream': True, 'allow_redirects': False, 'headers': getattr(self, 'headers', {})}
        if body is not None:
            args['json'] = body
        response = (self.source.get if body is None else self.source.post)(url, **args)
        try:
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > self.source.max_bytes:
                    raise SourceError('Public report response exceeds max_bytes')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            response.close()

    def json(self, url, body=None):
        try:
            return json.loads(self.download(url, body))
        except (ValueError, UnicodeError):
            fail()

    def query(self, query):
        body = {'version': '1.0.0', 'queries': [{'Query': query, 'CacheKey': ''}],
                'cancelQueries': [], 'modelId': self.model_id}
        return self.json(self.api+'/public/reports/querydata?synchronous=true', body)


def flat_table(payload, selects, limit, entities=None):
    """Decode one complete flat grouping; preserve nulls and projection order."""
    try:
        if set(payload) != {'jobIds', 'results'} or len(payload['results']) != 1:
            fail()
        result = payload['results'][0]['result']
        if set(result) != {'data'}:
            fail()
        data = result['data']
        if set(data)-{'timestamp','rootActivityId','descriptor','metrics','fromCache','dsr'}:
            fail()
        dsr = data['dsr']
        if set(dsr) != {'Version','MinorVersion','DS'} or dsr['Version'] != 2 or dsr['MinorVersion'] != 1 or len(dsr['DS']) != 1:
            fail()
        ds = dsr['DS'][0]
        if set(ds)-{'N','PH','IC','HAD','ValueDicts'} or ds['N'] != 'DS0' or ds['IC'] is not True or ds['HAD'] is not True or len(ds['PH']) != 1 or set(ds['PH'][0]) != {'DM0'}:
            fail()
        descriptors = data['descriptor']['Select']
        if data['descriptor']['Version'] != 2 or len(descriptors) != len(selects):
            fail()
        for got, expected in zip(descriptors, selects):
            if got['Name'] != expected['Name']:
                fail()
            if 'Column' in expected:
                col = expected['Column']
                alias = col['Expression']['SourceRef']['Source']
                entity = (entities or {}).get(alias, alias)
                if got['Kind'] != 1 or got.get('Depth') != 0 or got.get('GroupKeys') != [{'Source': {'Entity': entity, 'Property': col['Property']}, 'Calc': got['Value'], 'IsSameAsSelect': True}]:
                    fail()
            elif got['Kind'] != 2:
                fail()
        rows = ds['PH'][0]['DM0']
        if not isinstance(rows, list) or not 1 <= len(rows) < limit:
            raise SourceError('Public report table is empty or reached its row limit')
        schema = rows[0]['S']
        if len(schema) != len(selects) or len({c['N'] for c in schema}) != len(schema) or {c['N'] for c in schema} != {d['Value'] for d in descriptors}:
            fail()
        dictionaries = ds.get('ValueDicts', {})
        if not isinstance(dictionaries, dict) or any(not isinstance(v, list) or len(v)>limit or any(not isinstance(s,str) for s in v) for v in dictionaries.values()):
            fail()
        for col in schema:
            if set(col)-{'N','T','DN'} or col['T'] not in (1,3,4,7) or ('DN' in col and (col['T'] != 1 or col['DN'] not in dictionaries)):
                fail()
        decoded = []
        for index, row in enumerate(rows):
            if set(row)-({'S','C','Ø'} if index==0 else {'C','R','Ø'}):
                fail()
            repeated, missing = row.get('R',0), row.get('Ø',0)
            if any(isinstance(m,bool) or not isinstance(m,int) or not 0<=m<2**len(schema) for m in (repeated,missing)) or repeated & missing:
                fail()
            values, cursor = [], 0
            for i, col in enumerate(schema):
                if missing & (1<<i):
                    value = None
                elif repeated & (1<<i):
                    if not decoded:
                        fail()
                    value = decoded[-1][i]
                else:
                    value = row['C'][cursor]
                    cursor += 1
                    if 'DN' in col and isinstance(value,int) and not isinstance(value,bool):
                        dictionary = dictionaries[col['DN']]
                        if not 0 <= value < len(dictionary):
                            fail()
                        value = dictionary[value]
                if value is not None:
                    if col['T']==1 and (not isinstance(value,str) or len(value)>10000):
                        fail()
                    if col['T'] in (3,4,7) and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                        fail()
                values.append(value)
            if cursor != len(row.get('C',[])):
                fail()
            decoded.append(values)
        positions = {c['N']:i for i,c in enumerate(schema)}
        return [[row[positions[d['Value']]] for d in descriptors] for row in decoded]
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        fail()
