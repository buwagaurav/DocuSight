"""Coordinate-preserving spreadsheet structure and bounded range inspection."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .store import store

PARSER_VERSION = 1
MAX_INSPECT_CELLS = 10_000
MAX_OUTPUT_BYTES = 1_000_000


class CellRef(BaseModel):
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    address: str


class SpreadsheetRegion(BaseModel):
    id: str
    sheet: str
    kind: str
    address: str
    min_row: int
    max_row: int
    min_col: int
    max_col: int
    table: str | None = None
    title: str = ""
    columns: list[str] = Field(default_factory=list)
    header_rows: list[int] = Field(default_factory=list)
    confidence: float = 0.5
    review_status: str = "unverified"


class SpreadsheetSheet(BaseModel):
    index: int
    name: str
    rows: int
    cols: int
    hidden: bool = False
    merged_count: int = 0
    formulas: int = 0
    merged_ranges: list[str] = Field(default_factory=list)
    regions: list[SpreadsheetRegion] = Field(default_factory=list)


class WorkbookStructure(BaseModel):
    schema_version: int = 1
    parser_version: int = PARSER_VERSION
    source_sha256: str
    format: str
    built_at: float
    sheets: list[SpreadsheetSheet]
    verification: dict[str, Any] = Field(default_factory=lambda: {"status": "deterministic", "events": []})


def _col_name(number: int) -> str:
    name = ""
    while number:
        number, rem = divmod(number - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell_address(row: int, col: int) -> str:
    return f"{_col_name(col)}{row}"


def _range_address(min_row: int, min_col: int, max_row: int, max_col: int) -> str:
    return f"{_cell_address(min_row, min_col)}:{_cell_address(max_row, max_col)}"


def _blocks(grid: list[list[Any]]) -> list[tuple[int, int, int, int]]:
    """Return 1-based bounding boxes for contiguous non-empty rectangular blocks."""
    width = max((len(row) for row in grid), default=0)
    if not width:
        return []
    rows = [list(row) + [None] * (width - len(row)) for row in grid]
    occupied = [[value is not None and str(value).strip() != "" for value in row] for row in rows]
    row_spans: list[tuple[int, int]] = []
    start = None
    for idx, row in enumerate(occupied):
        if any(row) and start is None:
            start = idx
        elif not any(row) and start is not None:
            row_spans.append((start, idx - 1))
            start = None
    if start is not None:
        row_spans.append((start, len(rows) - 1))
    result = []
    for r0, r1 in row_spans:
        columns = [any(occupied[r][c] for r in range(r0, r1 + 1)) for c in range(width)]
        c = 0
        while c < width:
            if not columns[c]:
                c += 1
                continue
            c0 = c
            while c < width and columns[c]:
                c += 1
            used_rows = [r for r in range(r0, r1 + 1) if any(occupied[r][k] for k in range(c0, c))]
            if used_rows:
                result.append((min(used_rows) + 1, c0 + 1, max(used_rows) + 1, c))
    return result


def _source_cells(path: Path) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Read formula/style metadata when available; values are sourced from UI sheet previews."""
    merged: dict[str, list[str]] = {}
    formulas: dict[str, list[str]] = {}
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return [], merged
    try:
        import openpyxl
        if path.stat().st_size > 15_000_000:
            wb = openpyxl.load_workbook(path, data_only=False, read_only=True)
            rows = [{"name": ws.title, "hidden": ws.sheet_state != "visible",
                     "merged_ranges": [], "formulas": []} for ws in wb.worksheets]
            wb.close()
            return rows, merged
        wb = openpyxl.load_workbook(path, data_only=False, read_only=False)
        sheet_info = []
        for ws in wb.worksheets:
            merged[ws.title] = [str(rng) for rng in ws.merged_cells.ranges]
            formulas[ws.title] = [cell.coordinate for row in ws.iter_rows()
                                  for cell in row if isinstance(cell.value, str) and cell.value.startswith("=")]
            sheet_info.append({
                "name": ws.title,
                "hidden": ws.sheet_state != "visible",
                "merged_ranges": merged[ws.title],
                "formulas": formulas[ws.title],
            })
        wb.close()
        return sheet_info, merged
    except Exception:
        return [], merged


def build_structure(doc_id: str, info: dict[str, Any], tables: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    meta = store.docs[doc_id]
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    source_sheets, merged = _source_cells(path)
    tables_by_sheet: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        tables_by_sheet.setdefault(table.get("sheet") or "", []).append(table)
    sheets = []
    for sheet in info.get("sheets", []):
        name = sheet["name"]
        preview_path = store.dir(doc_id) / "sheets" / f"{sheet['index']}.json"
        preview = json.loads(preview_path.read_text()) if preview_path.exists() else {"rows": []}
        regions = []
        sheet_tables = tables_by_sheet.get(name, [])
        boxes = [table.get("source_bounds") for table in sheet_tables if table.get("source_bounds")]
        if not boxes:
            boxes = _blocks(preview.get("rows", []))
        for idx, (r0, c0, r1, c1) in enumerate(boxes):
            table = sheet_tables[idx] if idx < len(sheet_tables) else None
            address = _range_address(r0, c0, r1, c1)
            regions.append(SpreadsheetRegion(
                id=f"{sheet['index']}:{idx}", sheet=name, kind="table" if table else "region",
                address=address, min_row=r0, max_row=r1, min_col=c0, max_col=c1,
                table=table.get("name") if table else None, title=table.get("title", "") if table else "",
                columns=[str(c.get("name", "")) for c in table.get("columns", [])] if table else [],
                header_rows=[r0], confidence=0.65 if table else 0.45,
            ))
        source = next((row for row in source_sheets if row["name"] == name), {})
        sheets.append(SpreadsheetSheet(
            index=sheet["index"], name=name, rows=sheet["rows"], cols=sheet["cols"],
            hidden=bool(source.get("hidden", sheet.get("hidden", False))),
            merged_count=len(source.get("merged_ranges", [])) or sheet.get("merged", 0),
            formulas=len(source.get("formulas", [])) or sheet.get("formulas", 0),
            merged_ranges=source.get("merged_ranges", []), regions=regions,
        ))
    structure = WorkbookStructure(source_sha256=digest, format=path.suffix.lower(), built_at=time.time(), sheets=sheets)
    result = structure.model_dump(mode="json")
    target = store.dir(doc_id) / "spreadsheet_structure.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False))
    tmp.replace(target)
    return result


def load_structure(doc_id: str) -> dict[str, Any]:
    path = store.dir(doc_id) / "spreadsheet_structure.json"
    if not path.exists():
        raise FileNotFoundError("Spreadsheet structure is not available")
    return WorkbookStructure.model_validate_json(path.read_text()).model_dump(mode="json")


def inspect_range(doc_id: str, sheet_name: str, address: str) -> dict[str, Any]:
    structure = load_structure(doc_id)
    sheet = next((item for item in structure["sheets"] if item["name"] == sheet_name), None)
    if sheet is None:
        raise KeyError("Sheet not found")
    match = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?", address.upper())
    if not match:
        raise ValueError("Range must be an A1 cell or range, such as B2:D8")
    def col_num(letters: str) -> int:
        total = 0
        for ch in letters:
            total = total * 26 + ord(ch) - 64
        return total
    c0, r0 = col_num(match.group(1)), int(match.group(2))
    c1, r1 = col_num(match.group(3) or match.group(1)), int(match.group(4) or match.group(2))
    if r1 < r0 or c1 < c0 or (r1-r0+1)*(c1-c0+1) > MAX_INSPECT_CELLS:
        raise ValueError(f"Range must be ordered and contain at most {MAX_INSPECT_CELLS} cells")
    if r1 > sheet["rows"] or c1 > sheet["cols"]:
        raise ValueError("Range exceeds sheet dimensions")
    meta = store.docs[doc_id]
    source_path = store.source_path(doc_id)
    rows = []
    formula_wb = value_wb = None
    try:
        if source_path.suffix.lower() in {".xlsx", ".xlsm"}:
            import openpyxl
            formula_wb = openpyxl.load_workbook(source_path, data_only=False, read_only=True)
            value_wb = openpyxl.load_workbook(source_path, data_only=True, read_only=True)
            if sheet_name not in formula_wb.sheetnames:
                raise KeyError("Sheet not found")
            formula_ws, value_ws = formula_wb[sheet_name], value_wb[sheet_name]
            formula_rows = formula_ws.iter_rows(min_row=r0, max_row=r1, min_col=c0, max_col=c1)
            value_rows = value_ws.iter_rows(min_row=r0, max_row=r1, min_col=c0, max_col=c1)
            for frow, vrow in zip(formula_rows, value_rows):
                cells = []
                row = r0 + len(rows)
                for col, (fcell, vcell) in enumerate(zip(frow, vrow), start=c0):
                    formula = fcell.value
                    cached = vcell.value
                    cells.append({"address": _cell_address(row, col), "value": cached,
                                  "raw_value": cached, "formula": formula if isinstance(formula, str) and formula.startswith("=") else None,
                                  "cached_value": cached if isinstance(formula, str) and formula.startswith("=") else None,
                                  "number_format": fcell.number_format})
                rows.append(cells)
        else:
            import pandas as pd
            ext = source_path.suffix.lower()
            if ext in {".csv", ".tsv", ".txt"}:
                frame = pd.read_csv(source_path, header=None, dtype=object, sep="\t" if ext == ".tsv" else None,
                                    engine="python", on_bad_lines="skip")
            else:
                book = pd.read_excel(source_path, sheet_name=None, header=None, dtype=object)
                frame = book.get(sheet_name)
                if frame is None:
                    raise KeyError("Sheet not found")
            for row in range(r0, r1 + 1):
                cells = []
                for col in range(c0, c1 + 1):
                    value = frame.iat[row-1, col-1] if row <= len(frame.index) and col <= len(frame.columns) else None
                    value = None if pd.isna(value) else str(value) if not isinstance(value, (int, float, bool)) else value
                    cells.append({"address": _cell_address(row, col), "value": value, "raw_value": value})
                rows.append(cells)
    finally:
        if formula_wb:
            formula_wb.close()
        if value_wb:
            value_wb.close()
    return {"doc_id": doc_id, "sheet": sheet_name, "address": _range_address(r0, c0, r1, c1),
            "rows": rows, "merged_ranges": [rng for rng in sheet.get("merged_ranges", [])],
            "verification_status": "source_cell_values", "formula_status": "expressions_and_cached_values" if source_path.suffix.lower() in {".xlsx", ".xlsm"} else "unavailable"}


def render_range_png(doc_id: str, sheet_name: str, address: str) -> bytes:
    data = inspect_range(doc_id, sheet_name, address)
    from PIL import Image, ImageDraw, ImageFont
    values = [[cell["address"] + "  " + str(cell["value"] or "") for cell in row] for row in data["rows"]]
    font = ImageFont.load_default()
    col_widths = [min(420, max(90, max((len(row[c]) for row in values), default=0) * 7 + 16)) for c in range(len(values[0]))]
    row_h = 26
    width, height = sum(col_widths), row_h * len(values)
    if width > 4000 or height > 4000:
        raise ValueError("Rendered range exceeds image dimensions")
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    x0 = 0
    for ci, cw in enumerate(col_widths):
        y = 0
        for row in values:
            draw.rectangle((x0, y, x0+cw, y+row_h), outline="#cbd5e1", fill="#f8fafc" if y == 0 else "white")
            draw.text((x0+6, y+7), row[ci][:120], fill="#111827", font=font)
            y += row_h
        x0 += cw
    output = __import__("io").BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def latex_range(doc_id: str, sheet_name: str, address: str) -> str:
    data = inspect_range(doc_id, sheet_name, address)
    escape = lambda value: str(value or "").replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("_", r"\_")
    ncols = len(data["rows"][0]) if data["rows"] else 1
    lines = [r"\begin{tabular}{" + "l" * ncols + "}", r"\hline"]
    for row in data["rows"]:
        lines.append(" & ".join(escape(cell["value"]) for cell in row) + r" \\")
    lines.extend([r"\hline", r"\end{tabular}"])
    return "\n".join(lines)
