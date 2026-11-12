"""Minimal standard-library ``.xlsx`` reader for the hospital export.

The build environment intentionally does not depend on ``openpyxl`` (the
project virtualenv has no third-party spreadsheet package).  This reader only
handles the subset produced by Excel: shared strings, inline strings, numeric
cells and booleans.  It returns every cell as text, which is what the export
columns contain.

Formatting/date styles are not interpreted.  The two hospital exports used by
the v6.7.2 dataset store dates as text; if a future export stores dates as
Excel serial numbers, the adapter must handle that explicitly rather than
guessing here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")


@dataclass(frozen=True)
class XlsxSheet:
    name: str
    rows: tuple[tuple[str, ...], ...]

    @property
    def header(self) -> tuple[str, ...]:
        return self.rows[0] if self.rows else ()

    def dict_rows(self) -> list[dict[str, str]]:
        header = self.header
        return [
            {name: (row[index] if index < len(row) else "") for index, name in enumerate(header)}
            for row in self.rows[1:]
        ]


def read_xlsx(path: str | Path) -> dict[str, XlsxSheet]:
    """Read all worksheets from an ``.xlsx`` file.

    The returned mapping preserves workbook order.  Only the first header row is
    treated as a header; blank rows are dropped.  Cell values are stripped of
    leading/trailing whitespace.
    """
    source = Path(path)
    with zipfile.ZipFile(source) as archive:
        shared = _read_shared_strings(archive)
        sheets = _read_workbook(archive)
        result: dict[str, XlsxSheet] = {}
        for name, target in sheets:
            rows = _read_sheet(archive, target, shared)
            result[name] = XlsxSheet(name=name, rows=tuple(rows))
        return result


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        data = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    strings: list[str] = []
    for item in root.findall(f"{{{_NS_MAIN}}}si"):
        texts = [node.text or "" for node in item.iter(f"{{{_NS_MAIN}}}t")]
        strings.append("".join(texts))
    return strings


def _read_workbook(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.get("Id"): rel.get("Target", "")
        for rel in rels.findall(f"{{{_NS_PKG_REL}}}Relationship")
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{_NS_MAIN}}}sheet"):
        name = sheet.get("name", "")
        rel_id = sheet.get(f"{{{_NS_REL}}}id", "")
        target = targets.get(rel_id, "")
        if not target:
            continue
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = posixpath.normpath(posixpath.join("xl", target))
        result.append((name, target))
    return result


def _read_sheet(
    archive: zipfile.ZipFile, target: str, shared: list[str],
) -> list[tuple[str, ...]]:
    root = ET.fromstring(archive.read(target))
    rows: list[tuple[str, ...]] = []
    for row in root.iter(f"{{{_NS_MAIN}}}row"):
        cells: dict[int, str] = {}
        max_index = -1
        for cell in row.findall(f"{{{_NS_MAIN}}}c"):
            ref = cell.get("r", "")
            index = _column_index(ref)
            if index < 0:
                continue
            value = _cell_value(cell, shared)
            cells[index] = value
            max_index = max(max_index, index)
        if max_index < 0:
            continue
        values = tuple(cells.get(index, "") for index in range(max_index + 1))
        if any(value.strip() for value in values):
            rows.append(tuple(value.strip() for value in values))
    return rows


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        node = cell.find(f"{{{_NS_MAIN}}}is")
        if node is None:
            return ""
        return "".join(text.text or "" for text in node.iter(f"{{{_NS_MAIN}}}t"))
    value_node = cell.find(f"{{{_NS_MAIN}}}v")
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def _column_index(reference: str) -> int:
    match = _CELL_REF.fullmatch(reference)
    if not match:
        return -1
    letters = match.group(1)
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1
