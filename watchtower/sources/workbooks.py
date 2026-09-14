"""Bounded text-table reading for named worksheets in public XLSX exports."""
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

from .common import SourceError

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


def column_index(reference, expected_row):
    match = re.fullmatch(r'([A-Z]+)(\d+)', reference)
    if not match or int(match.group(2)) != expected_row:
        raise SourceError('Workbook cell reference is invalid')
    result = 0
    for character in match.group(1):
        result = result * 26 + ord(character) - 64
    return result - 1


def _xml(raw):
    decoded = raw.decode('utf-8-sig')
    if '\x00' in decoded or '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
        raise SourceError('Workbook contains forbidden XML declarations')
    return ET.fromstring(decoded)


def table_rows(raw, sheet_name, max_unpacked, max_rows, columns):
    try:
        with ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if (not 1 <= len(infos) <= 100 or len({i.filename for i in infos}) != len(infos)
                    or sum(i.file_size for i in infos) > max_unpacked
                    or any(i.flag_bits & 1 or i.filename.startswith('/') or '..' in i.filename.split('/') for i in infos)):
                raise SourceError('Workbook exceeds safe archive bounds')
            workbook = _xml(archive.read('xl/workbook.xml'))
            if workbook.tag != NS+'workbook':
                raise SourceError('Workbook namespace changed')
            sheets = workbook.findall(NS+'sheets/'+NS+'sheet')
            if not sheets or len({s.get('name') for s in sheets}) != len(sheets) or len({s.get(RID) for s in sheets}) != len(sheets):
                raise SourceError('Workbook sheet names or relationships repeat')
            chosen = [s for s in sheets if s.get('name') == sheet_name]
            if len(chosen) != 1:
                raise SourceError('Requested worksheet is absent or ambiguous')
            relationships = _xml(archive.read('xl/_rels/workbook.xml.rels'))
            if relationships.tag != REL+'Relationships':
                raise SourceError('Workbook relationship namespace changed')
            rels = relationships.findall(REL+'Relationship')
            if len({r.get('Id') for r in rels}) != len(rels):
                raise SourceError('Workbook relationships repeat')
            links = [r for r in rels if r.get('Id') == chosen[0].get(RID)]
            if len(links) != 1 or links[0].get('TargetMode') == 'External':
                raise SourceError('Workbook worksheet relationship is invalid')
            target = links[0].get('Target','')
            target = target[1:] if target.startswith('/xl/') else 'xl/'+target
            if not re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml', target):
                raise SourceError('Workbook worksheet leaves its expected path')
            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                root = _xml(archive.read('xl/sharedStrings.xml'))
                if root.tag != NS+'sst':
                    raise SourceError('Workbook shared-string namespace changed')
                shared = [''.join(t.text or '' for t in item.iter(NS+'t')) for item in root.findall(NS+'si')]
            sheet = _xml(archive.read(target))
            return _rows(sheet, shared, max_rows, columns)
    except (BadZipFile, KeyError, RuntimeError, NotImplementedError, ET.ParseError, UnicodeError, ValueError, IndexError) as exc:
        raise SourceError('Public workbook is invalid') from exc


def _rows(sheet, shared, max_rows, columns):
    if sheet.tag != NS+'worksheet':
        raise SourceError('Worksheet namespace changed')
    data = sheet.findall(NS+'sheetData')
    if len(data) != 1:
        raise SourceError('Worksheet data is absent or repeated')
    parsed = {}
    for row in data[0].findall(NS+'row'):
        value = row.get('r','')
        if not value.isdigit() or not 1 <= int(value) <= max_rows or int(value) in parsed:
            raise SourceError('Worksheet row number is invalid, repeated or exceeds its bound')
        number = int(value)
        cells, seen = ['']*columns, set()
        for cell in row.findall(NS+'c'):
            index = column_index(cell.get('r',''), number)
            if index in seen or cell.find(NS+'f') is not None:
                raise SourceError('Worksheet cell repeats or contains a formula')
            seen.add(index)
            kind = cell.get('t','n')
            if kind not in {'n','s','inlineStr'}:
                raise SourceError('Worksheet cell type is unsupported')
            values = cell.findall(NS+'v')
            if len(values) > 1:
                raise SourceError('Worksheet cell value repeats')
            text = '' if not values else values[0].text or ''
            if kind == 's':
                if not text.isdigit() or int(text) >= len(shared):
                    raise SourceError('Worksheet shared-string index is invalid')
                text = shared[int(text)]
            elif kind == 'inlineStr':
                text = ''.join(t.text or '' for t in cell.iter(NS+'t'))
            if len(text) > 4000:
                raise SourceError('Worksheet cell exceeds text bounds')
            text = text.strip()
            if index >= columns:
                if text:
                    raise SourceError('Worksheet has unexpected populated columns')
                continue
            cells[index] = text
        parsed[number] = cells
    populated = [n for n, values in parsed.items() if any(values)]
    if not populated or min(populated) != 1 or max(populated) < 2:
        raise SourceError('Worksheet is empty or lacks header/data')
    last = max(populated)
    if any(n not in parsed or not any(parsed[n]) for n in range(1,last+1)):
        raise SourceError('Worksheet has missing or blank interior rows')
    return [parsed[n] for n in range(1,last+1)]
