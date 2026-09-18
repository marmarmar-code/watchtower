"""Published Enova project execution, bounded to explicitly selected sectors."""
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .changes import SnapshotSource, canonical, integer, strings, shown
from .common import SourceError

PAGE = 'https://kunnskapshuset.enova.no/prosjektliste'
HOST = 'kunnskapshuset.enova.no'
STATUSES = {'Innvilget', 'Prosjektgjennomføring', 'Ferdigstilt', 'Kansellert'}
TEXT_FIELDS = ('saksnummer', 'prosjekttittel', 'stotteobjekt_navn', 'stotteobjekt_underkategori',
               'status', 'stotteprogram', 'sektor')
OPTIONAL_TEXT = ('prosjekteier', 'organisasjonsnummer', 'prosjektfylke', 'prosjektkommune')
NUMBERS = ('stottebelop_vedtatt', 'stottebelop_vedtatt_opprinnelig', 'levetid',
           'klimaresultat_vedtatt', 'energiresultat_vedtatt', 'stotteobjekt_antall')
DATES = ('prosjektstart_gjeldende_dato', 'prosjektslutt_gjeldende_dato', 'vedtaksdato')
OBJECT_FIELDS = ('stotteobjekt_navn', 'stotteobjekt_underkategori', 'stotteobjekt_antall')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def template_literal(text, start):
    """Decode one static JS template literal without evaluating any JavaScript."""
    output, index = [], start
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f',
               '\\': '\\', '`': '`', '$': '$', '/': '/', '"': '"', "'": "'"}
    while index < len(text):
        char = text[index]; index += 1
        if char == '`':
            return ''.join(output), index
        if char == '$' and text[index:index + 1] == '{':
            raise SourceError('Enova dataset contains executable template interpolation')
        if char != '\\':
            output.append(char); continue
        if index == len(text):
            break
        escape = text[index]; index += 1
        if escape in escapes:
            output.append(escapes[escape])
        elif escape in {'x', 'u'}:
            width = 2 if escape == 'x' else 4
            digits = text[index:index + width]
            if len(digits) != width or not re.fullmatch(r'[0-9a-fA-F]+', digits):
                raise SourceError('Enova dataset contains an invalid string escape')
            output.append(chr(int(digits, 16))); index += width
        else:
            raise SourceError('Enova dataset contains an unsupported string escape')
    raise SourceError('Enova dataset string is incomplete')


def production_rows(bundle, limit):
    """Resolve the explicit backend branch, never a similarly shaped mock array."""
    ident = r'[A-Za-z_$][\w$]*'
    routing = re.findall(r'function\s+' + ident + r'\((' + ident + r')\)\{return \1===`backend`\?('
                         + ident + r'):(' + ident + r')\}', bundle)
    if (len(routing) != 1 or routing[0][1] == routing[0][2]
            or not re.search(r'function\s+' + ident + r'\(\)\{return ' + ident + r'\(\)\|\|`backend`\}', bundle)
            or '=`prosjektlisteDataSource`' not in bundle):
        raise SourceError('Enova production versus mock dataset routing is not explicit')
    backend, mock = routing[0][1:]
    mappings = []
    for variable in (backend, mock):
        matches = re.findall(r'(?:[,;]|\b(?:var|let|const)\s+)' + re.escape(variable)
                             + r'=(' + ident + r')\.map\((' + ident + r')\)[,;]', bundle)
        if len(matches) != 1:
            raise SourceError('Enova dataset mapping is ambiguous')
        mappings.append(matches[0])
    if mappings[0][0] == mappings[1][0] or mappings[0][1] != mappings[1][1]:
        raise SourceError('Enova production and mock dataset mappings are inconsistent')
    matches = list(re.finditer(r'(?:[,;]|\b(?:var|let|const)\s+)' + re.escape(mappings[0][0])
                              + r'=JSON\.parse\(`', bundle))
    if len(matches) != 1:
        raise SourceError('Enova production JSON dataset is missing or ambiguous')
    raw, end = template_literal(bundle, matches[0].end())
    if bundle[end:end + 1] != ')':
        raise SourceError('Enova dataset is not a single static JSON literal')
    try:
        rows = json.loads(raw, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError('Enova production dataset is invalid JSON') from exc
    if not isinstance(rows, list) or not 1 <= len(rows) <= limit:
        raise SourceError('Enova production dataset is empty or exceeds the export record bound')
    return rows


def iso_day(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or value % 86400000:
        raise SourceError('Enova date is not an integer UTC day')
    try:
        day = datetime.fromtimestamp(value / 1000, timezone.utc).date()
    except (ValueError, OverflowError, OSError) as exc:
        raise SourceError('Enova date is outside the supported range') from exc
    if not 1990 <= day.year <= 2100:
        raise SourceError('Enova date is outside the supported range')
    return day.isoformat()


def project_records(rows):
    groups = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or not set(TEXT_FIELDS + OPTIONAL_TEXT + NUMBERS + DATES) <= set(row):
            raise SourceError('Enova project fields are incomplete')
        if any(not isinstance(row[k], str) or not row[k].strip() or len(row[k]) > 2000 for k in TEXT_FIELDS):
            raise SourceError('Enova project text is invalid')
        if any(row[k] is not None and (not isinstance(row[k], str) or len(row[k]) > 2000) for k in OPTIONAL_TEXT):
            raise SourceError('Enova optional project text is invalid')
        if any(row[k] is not None and (type(row[k]) not in (int, float) or not math.isfinite(row[k])) for k in NUMBERS):
            raise SourceError('Enova project number is invalid')
        if not re.fullmatch(r'\d{2}/\d{1,8}', row['saksnummer']) or row['status'] not in STATUSES:
            raise SourceError('Enova case identity or project status is invalid')
        if row['organisasjonsnummer'] is not None and not re.fullmatch(r'\d{9}', row['organisasjonsnummer']):
            raise SourceError('Enova organisation identifier is invalid')
        for key in DATES:
            iso_day(row[key], nullable=key == 'prosjektslutt_gjeldende_dato')
        groups[row['saksnummer']].append(row)
    records = []
    for case, parts in sorted(groups.items()):
        first = parts[0]
        # Multi-object cases are genuinely present. Preserve every object row;
        # never sum the repeated project-level funding or collapse by case ID.
        common = set(TEXT_FIELDS + OPTIONAL_TEXT + NUMBERS + DATES) - set(OBJECT_FIELDS)
        if any(any(part[k] != first[k] for k in common) for part in parts[1:]):
            raise SourceError('Enova case rows disagree about project-level fields')
        objects = [{key: part[key] for key in OBJECT_FIELDS} for part in parts]
        records.append({'key': case, 'title': first['prosjekttittel'], 'url': PAGE, 'published': None,
            'owner': first['prosjekteier'], 'orgnr': first['organisasjonsnummer'],
            'sector': first['sektor'], 'program': first['stotteprogram'],
            'decision_date': iso_day(first['vedtaksdato']), 'objects': sorted(objects, key=canonical),
            'fields': {'status': first['status'],
                'project_start': iso_day(first['prosjektstart_gjeldende_dato']),
                'project_end': iso_day(first['prosjektslutt_gjeldende_dato'], nullable=True)}})
    return records


class EnovaProjectsSource(SnapshotSource):
    def fetch_with_state(self, previous):
        saved = ((previous or {}).get('source_state') or {}).get('records', {})
        self._previous_keys = set(saved.get('rows', {})) if saved.get('scope') == self.scope else set()
        return super().fetch_with_state(previous)

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if config.urls not in ((), (PAGE,)):
            raise ValueError('enova_projects accepts only the official Enova project list')
        if self.complete or self.allow_empty or 'removed' in self.events:
            raise ValueError('Enova absence cannot confirm project cancellation')
        self.sectors = strings(config.options.get('sectors'), 'sectors')
        if len(self.sectors) > 10:
            raise ValueError('sectors supports up to ten exact source sector names')
        self.from_year = integer(config.options.get('from_year', 2024), 'from_year', 2017, 2100)
        self.export_limit = integer(config.options.get('max_export_records', 100000), 'max_export_records', 1, 200000)
        self.bundle_limit = integer(config.options.get('max_bundle_bytes', 40000000), 'max_bundle_bytes', 1024, 60000000)
        self.field_labels = {'status': 'Oppført prosjektstatus', 'project_start': 'Gjeldende prosjektstart',
                             'project_end': 'Gjeldende prosjektslutt', **self.field_labels}

    def _read(self, url, limit):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != HOST or parsed.query or parsed.fragment:
            raise SourceError('Enova source location is outside the official app')
        response = self.get(url, stream=True, allow_redirects=False,
                            accepted_statuses=(301, 302, 303, 307, 308))
        try:
            if response.status_code != 200:
                raise SourceError('Enova source returned an unexpected redirect')
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise SourceError('Enova source exceeds its byte bound')
                chunks.append(chunk)
            return b''.join(chunks).decode('utf-8')
        except UnicodeError as exc:
            raise SourceError('Enova source is not UTF-8') from exc
        finally:
            response.close()

    def _export(self):
        soup = BeautifulSoup(self._read(PAGE, self.max_bytes), 'html.parser')
        scripts = [s.get('src') for s in soup.select('script[type="module"][src]')]
        if len(scripts) != 1 or not re.fullmatch(r'/assets/index-[\w-]+\.js', scripts[0]):
            raise SourceError('Enova main application bundle is missing or ambiguous')
        rows = production_rows(self._read(urljoin(PAGE, scripts[0]), self.bundle_limit), self.export_limit)
        return project_records(rows), len(rows)

    def read_records(self):
        records, raw_count = self._export()
        if (records, raw_count) != self._export():
            raise SourceError('Enova production dataset changed during reading')
        if not set(self.sectors) <= {r['sector'] for r in records}:
            raise SourceError('An explicitly selected Enova sector is absent')
        selected = [r for r in records if r['sector'] in self.sectors and int(r['decision_date'][:4]) >= self.from_year]
        if not selected or len(selected) > self.max_records:
            raise SourceError('Enova selection is empty or exceeds max_records')
        if getattr(self, '_previous_keys', set()) - {r['key'] for r in selected}:
            raise SourceError('A previously observed Enova project is absent; previous state preserved')
        self.export_summary = {'raw_rows': raw_count, 'projects': len(records),
            'latest_decision_date': max(r['decision_date'] for r in records), 'selected_projects': len(selected)}
        # A decision date is not a dataset refresh date or a status timestamp.
        self.coverage_warnings = ['enova_status_update_time_unavailable']
        return selected

    def _item(self, row, event, details, suppress):
        item = super()._item(row, event, details, suppress)
        brief = lambda value: value if len(value) <= 180 else value[:169] + ' … [utdrag]'
        info = ('Nyobservert Enova-prosjekt' if event == 'added' else 'Endret gjennomføring av Enova-prosjekt',
                f"Sak {row['key']} · {brief(row['owner'] or 'Prosjekteier ikke oppgitt')}",
                f"{brief(row['sector'])} · {brief(row['program'])}")
        for name in ('status', 'project_start', 'project_end'):
            label = self.field_labels[name] + ':'
            change = next((d for d in details[1:] if d.startswith(label)), None) if event == 'changed' else None
            info += (change or label + ' ' + shown(row['fields'][name]),)
        info += (f"Vedtaksdato: {row['decision_date']}; statusens oppdateringstid er ikke oppgitt.",
                 'Prosjektlisten oppgir gjennomføring; dette bekrefter ikke utbetaling eller realisert klimaeffekt.',)
        return replace(item, alert_details=info)
