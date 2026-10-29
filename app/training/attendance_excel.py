"""Fill the supplied XLSX package without reauthoring its styles or logos.

Only cell contents and repeated 24-row print pages change. No spreadsheet
engine is required on the server; all other original package parts are kept.
"""
import io
import math
import re
import uuid
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
from django.conf import settings
from .analytics import format_phone

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
TEMPLATE = Path(settings.BASE_DIR) / 'training' / 'resources' / 'attendance_template.xlsx'


def _text(value):
    value = str(value or '')
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
    if len(value) > 32767:
        raise ValueError('Um campo excede o limite de texto do Excel. Reveja os dados do formando.')
    return escape(value)


def _fill_cell(xml, address, value):
    pattern = r'<c\b([^>]*\br="' + re.escape(address) + r'"[^>/]*)(?:/>|>.*?</c>)'
    def replace(match):
        attrs = re.sub(r'\s+t="[^"]*"', '', match.group(1))
        attrs = attrs.rstrip().rstrip('/')
        if value is None or value == '':
            return '<c' + attrs + '/>'
        # Inline strings prevent formula execution and retain leading zeros.
        return '<c' + attrs + ' t="inlineStr"><is><t xml:space="preserve">' + _text(value) + '</t></is></c>'
    result, count = re.subn(pattern, replace, xml, flags=re.S)
    if count != 1:
        raise ValueError('O modelo Excel não contém o campo esperado: ' + address)
    return result


def _shift_refs(xml, offset):
    xml = re.sub(r'\br="(\d+)"', lambda m: 'r="' + str(int(m[1]) + offset) + '"', xml)
    return re.sub(r'\br="([A-Z]+)(\d+)"', lambda m: 'r="' + m[1] + str(int(m[2]) + offset) + '"', xml)


def _prune_strings(parts, sheet):
    """Drop unused source names/contact notes, even from the ZIP internals."""
    strings = ET.fromstring(parts['xl/sharedStrings.xml']).findall('s:si', NS)
    used = []
    def replace(match):
        index = int(match[2])
        if index not in used:
            used.append(index)
        return match[1] + str(used.index(index)) + match[3]
    sheet = re.sub(r'(<c\b[^>]*\bt="s"[^>]*>\s*<v>)(\d+)(</v>)', replace, sheet)
    # Serialize individual rich strings with their own valid namespace.
    body = ''.join(ET.tostring(strings[i], encoding='unicode') for i in used)
    count = len(re.findall(r'<c\b[^>]*\bt="s"', sheet))
    parts['xl/sharedStrings.xml'] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="' + NS['s'] + '" count="' + str(count) + '" uniqueCount="' + str(len(used)) + '">' + body + '</sst>').encode()
    return sheet


def fill_template(template, group, day, enrollments, second_copy=False, historical_period=None):
    with ZipFile(io.BytesIO(template)) as original:
        parts = {name: original.read(name) for name in original.namelist()}
    sheet = parts['xl/worksheets/sheet1.xml'].decode('utf-8')
    data = re.search(r'<sheetData>(.*?)</sheetData>', sheet, re.S)[1]
    rows = re.findall(r'<row\b.*?</row>', data, re.S)
    page = ''.join(r for r in rows if int(re.search(r'\br="(\d+)"', r)[1]) <= 24)
    pages = max(1, math.ceil(len(enrollments) / 12))
    page_xml = []
    for number in range(pages):
        current = page
        metadata = {
            'A3': f'Data: {day:%d/%m/%Y} | Horário: Início: {group.start_time:%H:%M} | Fim: {group.end_time:%H:%M}' if group and day else '',
            'E3': f'{chr(83)+chr(69)+chr(71)+chr(85)+chr(78)+chr(68)+chr(65)} VIA / CONSULTA - {group.title} - {group.code}' if second_copy else f'Título: {group.title} - {group.code}' if group else 'Título: ',
            'A4': f'Duração total: Formação: {float(group.hours_per_day):g} horas | Pausas: {float(group.break_hours):g} horas' if group else '',
            'E4': 'Local: ' + group.location if group else 'Local: ',
            'J23': f'Pág. {number + 1}/{pages}',
        }
        if historical_period:
            metadata['A3'] = 'Período histórico: ' + historical_period
            metadata['A4'] = 'Consulta do histórico; não comprova presenças.'
            metadata['E3'] = f'LISTA HISTÓRICA / SEGUNDA VIA - {group.title} - {group.code}'
        for address, value in metadata.items():
            current = _fill_cell(current, address, value)
        for position in range(12):
            index = number * 12 + position
            values = [''] * 10
            if index < len(enrollments):
                employee = enrollments[index].employee
                source = employee.source_data or {}
                values = [str(index + 1), employee.number, employee.name,
                    employee.function or employee.category,
                    employee.direction or employee.area, format_phone(employee.phone),
                    employee.email, 'EPAL', '', '']
            for column, value in zip('ABCDEFGHIJ', values):
                current = _fill_cell(current, f'{column}{position + 7}', value)
        page_xml.append(_shift_refs(current, number * 24))
    sheet = re.sub(r'<sheetData>.*?</sheetData>', lambda m: '<sheetData>' + ''.join(page_xml) + '</sheetData>', sheet, flags=re.S)
    sheet = re.sub(r'<dimension ref="[^"]*"', f'<dimension ref="A1:J{pages * 24}"', sheet)
    merges = re.search(r'<mergeCells\b[^>]*>(.*?)</mergeCells>', sheet, re.S)[1]
    first = [x for x in re.findall(r'<mergeCell ref="([^"]+)"/>', merges) if int(re.search(r'\d+', x)[0]) <= 24]
    merged = []
    for number in range(pages):
        for address in first:
            shifted = re.sub(r'(\d+)', lambda m: str(int(m[1]) + number * 24), address)
            merged.append('<mergeCell ref="' + shifted + '"/>')
    sheet = re.sub(r'<mergeCells\b.*?</mergeCells>', '<mergeCells count="' + str(len(merged)) + '">' + ''.join(merged) + '</mergeCells>', sheet, flags=re.S)
    breaks = ''.join(f'<brk id="{n * 24}" max="16383" man="1"/>' for n in range(1, pages))
    sheet = re.sub(r'<rowBreaks\b.*?</rowBreaks>', f'<rowBreaks count="{pages - 1}" manualBreakCount="{pages - 1}">{breaks}</rowBreaks>', sheet, flags=re.S)
    parts['xl/worksheets/sheet1.xml'] = _prune_strings(parts, sheet).encode('utf-8')
    workbook = parts['xl/workbook.xml'].decode('utf-8')
    workbook = re.sub(r'(<definedName name="_xlnm.Print_Area"[^>]*>).*?(</definedName>)', lambda m: m[1] + "'Lista de Presenças'!$A$1:$J$" + str(pages * 24) + m[2], workbook)
    workbook = re.sub(r'<mc:AlternateContent\b.*?</mc:AlternateContent>', '', workbook, flags=re.S)
    parts['xl/workbook.xml'] = workbook.encode('utf-8')
    # Repeat the exact original logo anchors for every printed page.
    drawing = parts['xl/drawings/drawing1.xml'].decode('utf-8')
    anchors = re.findall(r'<xdr:twoCellAnchor>.*?</xdr:twoCellAnchor>', drawing, re.S)
    original_height = sum(float(re.search(r'\bht="([^"]+)"', r)[1]) for r in rows[:24]) * 12700
    repeated = []
    for number in range(pages):
        for index, anchor in enumerate(anchors):
            if number:
                anchor = re.sub(r'(<xdr:row>)(\d+)(</xdr:row>)', lambda m: m[1] + str(int(m[2]) + number * 24) + m[3], anchor)
                anchor = re.sub(r'(<a:off\b[^>]*\by=")(\d+)(")', lambda m: m[1] + str(int(int(m[2]) + number * original_height)) + m[3], anchor)
                anchor = re.sub(r'(<xdr:cNvPr id=")\d+(" name=")[^"]*', lambda m: m[1] + str(100 + number * 2 + index) + m[2] + f'Logo {number + 1}-{index + 1}', anchor)
                anchor = re.sub(r'(<a16:creationId[^>]*\bid=")[^"]+', lambda m: m[1] + '{' + str(uuid.uuid4()).upper() + '}', anchor)
            repeated.append(anchor)
    drawing = re.sub(r'(<xdr:wsDr\b[^>]*>).*?(</xdr:wsDr>)', lambda m: m[1] + ''.join(repeated) + m[2], drawing, flags=re.S)
    parts['xl/drawings/drawing1.xml'] = drawing.encode('utf-8')
    # Remove personal creator metadata from the shared reference workbook.
    if 'docProps/core.xml' in parts:
        core = parts['docProps/core.xml'].decode('utf-8')
        core = re.sub(r'(<(?:dc:creator|cp:lastModifiedBy)>).*?(</[^>]+>)', lambda m: m[1] + 'PROÁGUA / EPAL' + m[2], core)
        parts['docProps/core.xml'] = core.encode('utf-8')
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def build_attendance_excel(group, day, enrollments, second_copy=False, historical_period=None):
    return fill_template(TEMPLATE.read_bytes(), group, day, enrollments, second_copy=second_copy, historical_period=historical_period)
