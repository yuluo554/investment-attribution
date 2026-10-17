"""表格读取：xlsx（标准库 OOXML）与分隔文本。

**按文件形态判别，不按扩展名**（plan/03 §M1）：
``.xls`` 可能是 Tab 分隔文本，``.csv`` 可能是 GB18030。判别顺序是
二进制形态 → 编码探测 → 首行分隔符/列名，扩展名只用于报错展示。

行号一律取**源文件中的物理行号**（xlsx 取工作表行号），
因为审计行要求 ``source_line_no``，数组下标会在过滤后失去下钻能力。
"""

import io
import re
import zipfile
from xml.etree import ElementTree

from ..errors import InputError
from .encoding import decode_bytes
from .csvio import parse_rows

XLSX_MAGIC = b"PK\x03\x04"
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
NS_DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

DELIMITERS = ((9, "tab"), (44, "comma"), (59, "semicolon"), (124, "pipe"))


class RawTable(object):
    """解析后的原始表：``header`` 与 ``rows=[(line_no, cells)]``。"""

    def __init__(self, header, rows, encoding=None, delimiter=None, kind="delimited"):
        self.header = header
        self.rows = rows
        self.encoding = encoding
        self.delimiter = delimiter
        self.kind = kind

    def __repr__(self):
        return "<RawTable kind={} cols={} rows={}>".format(
            self.kind, len(self.header), len(self.rows)
        )


def is_xlsx(raw):
    return raw[:4] == XLSX_MAGIC


def _column_index(ref):
    match = re.match(r"([A-Z]+)", ref or "")
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _shared_strings(archive):
    try:
        payload = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(payload)
    strings = []
    for item in root.findall(NS_MAIN + "si"):
        strings.append("".join(node.text or "" for node in item.iter(NS_MAIN + "t")))
    return strings


def _workbook_sheet_path(archive):
    try:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    except KeyError:
        raise InputError("xlsx 缺 xl/workbook.xml", code="XLSX_INVALID")
    sheet = workbook.find(NS_MAIN + "sheets/" + NS_MAIN + "sheet")
    if sheet is None:
        raise InputError("xlsx 没有工作表", code="XLSX_INVALID")
    rel_id = sheet.get(NS_DOC_REL + "id")
    try:
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        raise InputError("xlsx 缺 workbook 关系表", code="XLSX_INVALID")
    for rel in rels.findall(NS_REL + "Relationship"):
        if rel.get("Id") == rel_id:
            target = rel.get("Target").lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise InputError("xlsx 找不到工作表关系", code="XLSX_INVALID")


def read_xlsx(raw, source="<xlsx>"):
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise InputError("{}: 不是合法的 xlsx（zip 解包失败）".format(source), code="XLSX_INVALID")
    with archive:
        shared = _shared_strings(archive)
        sheet_path = _workbook_sheet_path(archive)
        root = ElementTree.fromstring(archive.read(sheet_path))

    grid = []
    for row in root.iter(NS_MAIN + "row"):
        line_no = int(row.get("r") or (len(grid) + 1))
        cells = {}
        cursor = 0
        for cell in row.findall(NS_MAIN + "c"):
            ref = cell.get("r")
            index = _column_index(ref) if ref else cursor
            cursor = index + 1
            cell_type = cell.get("t")
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(NS_MAIN + "t"))
            elif cell_type == "s":
                node = cell.find(NS_MAIN + "v")
                try:
                    value = shared[int(node.text)]
                except (AttributeError, TypeError, ValueError, IndexError):
                    value = ""
            else:
                node = cell.find(NS_MAIN + "v")
                value = node.text if node is not None and node.text is not None else ""
            cells[index] = value
        width = max(cells) + 1 if cells else 0
        grid.append((line_no, [cells.get(i, "") for i in range(width)]))

    if not grid:
        return RawTable([], [], encoding="xlsx", delimiter=None, kind="xlsx")
    header_line, header = grid[0]
    width = len(header)
    # xlsx 的单元格按坐标（r="E5"）定位，缺格就是空值、不会造成错位；
    # 因此这里补齐到表头宽度。分隔文本反之：少一个分隔符就是错位。
    padded = []
    for line_no, cells in grid[1:]:
        if len(cells) < width:
            cells = cells + [""] * (width - len(cells))
        padded.append((line_no, cells))
    return RawTable(header, padded, encoding="xlsx", delimiter=None, kind="xlsx")


def _score_delimiter(lines, delimiter):
    counts = [line.count(delimiter) for line in lines if line.strip()]
    if not counts:
        return 0
    first = counts[0]
    if first == 0:
        return 0
    consistent = sum(1 for count in counts if count == first)
    return consistent * 10 + first


def sniff_delimiter(text):
    sample_lines = [line for line in text.splitlines() if line.strip()][:20]
    best = None
    best_score = 0
    for char, name in DELIMITERS:
        score = _score_delimiter(sample_lines, chr(char))
        if score > best_score:
            best = name
            best_score = score
    if best is None:
        raise InputError(
            "首行结构不可识别：未找到 Tab / 逗号 / 分号 / 竖线以外的稳定分隔符",
            code="DELIMITER_UNRECOGNIZED",
        )
    return best


def delimiter_char(name):
    for char, label in DELIMITERS:
        if label == name:
            return chr(char)
    raise KeyError(name)


def read_delimited(raw, source="<text>"):
    text, encoding = decode_bytes(raw, source)
    if not text.strip():
        raise InputError("{}: 文件为空".format(source), code="EMPTY_FILE")
    delimiter = sniff_delimiter(text)
    char = delimiter_char(delimiter)
    rows = parse_rows(text, char)
    if not rows:
        raise InputError("{}: 没有可解析的行".format(source), code="EMPTY_FILE")
    header_line, header = rows[0]
    return RawTable(header, rows[1:], encoding=encoding, delimiter=delimiter, kind="delimited")


def read_table(raw, source="<file>"):
    """按**字节形态**分流：zip 头 → xlsx，其余 → 编码探测 + 分隔符嗅探。"""
    if raw is None or raw == b"":
        raise InputError("{}: 文件为空（0 字节）".format(source), code="EMPTY_FILE")
    if is_xlsx(raw):
        return read_xlsx(raw, source)
    return read_delimited(raw, source)
