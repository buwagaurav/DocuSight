"""Deterministic schema extraction from spreadsheet tables, with every value checked against its source cell.

Each parsed table row becomes one observation, identified by its table and source row. Following
SpreadsheetAgent (arXiv:2604.12282), a table is addressed by its own recorded range, so two tables on one
sheet (stacked or side by side) never share rows, and a table is read from the original workbook as one
bounded range instead of cell by cell.
"""
from __future__ import annotations

import re
from typing import Any

from ..spreadsheet_structure import WorkbookReader, _cell_address, _range_address
from ..store import store
from ..tables import preview

PAGE_ROWS = 5000   # DuckDB page size; every row is read, in pages


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _leaf(column: str) -> str:
    """'2025 / Revenue_2' -> 'Revenue': the bottom label of a joined multi-row header, without dedupe suffix."""
    return re.sub(r"_\d+$", "", column.rsplit(" / ", 1)[-1])


def match_columns(fields: list[dict[str, Any]], columns: list[str]) -> tuple[dict[str, int], dict[str, str]]:
    """Field id -> column index. A field matches a column header by its name or id, ignoring case and
    punctuation; failing that, the leaf of a multi-row header ('2025 / Revenue') when exactly one column
    has that leaf. Returns (matches, problems) where problems maps field id -> why it has no column."""
    matches: dict[str, int] = {}
    problems: dict[str, str] = {}
    full = [_key(c) for c in columns]
    leaves = [_key(_leaf(c)) for c in columns]
    for field in fields:
        wanted = {_key(field.get("name")), _key(field.get("id"))} - {""}
        exact = [i for i, k in enumerate(full) if k in wanted]
        leaf = [i for i, k in enumerate(leaves) if k in wanted]
        if exact:
            matches[field["id"]] = exact[0]
        elif len(leaf) == 1:
            matches[field["id"]] = leaf[0]
        elif len(leaf) > 1:
            problems[field["id"]] = "ambiguous: " + ", ".join(columns[i] for i in leaf)
        else:
            problems[field["id"]] = "no column with this name"
    return matches, problems


def _comparable(value: Any) -> str:
    text = str(value if value is not None else "").strip().casefold()
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[,$%()]", "", text).strip()
    try:  # 1250 == 1250.0 == "1,250"
        number = float(text)
        text = str(int(number)) if number.is_integer() else repr(number)
    except ValueError:
        pass
    return f"-{text}" if negative else text


def _all_rows(doc_id: str, table: str) -> tuple[list[str], list[list[Any]]]:
    db = store.tables_db(doc_id)
    first = preview(db, table, limit=PAGE_ROWS, offset=0)
    rows = list(first.get("rows", []))
    while len(rows) < first.get("total", 0):
        page = preview(db, table, limit=PAGE_ROWS, offset=len(rows)).get("rows", [])
        if not page:
            break
        rows.extend(page)
    return [str(c) for c in first.get("columns", [])], rows


def extract_document(doc_id: str, fields: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Observations for every row of every sheet table in one spreadsheet, plus a per-table report."""
    meta = store.docs[doc_id]
    tables = [t for t in meta.tables if t.get("sheet") and t.get("source_bounds")]
    observations: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    per_sheet: dict[str, int] = {}
    for table in tables:
        per_sheet[table["sheet"]] = per_sheet.get(table["sheet"], 0) + 1
    with WorkbookReader(doc_id) as reader:   # the workbook is opened once for the whole document
        for table in tables:
            sheet = table["sheet"]
            shared_sheet = per_sheet[sheet] > 1   # name the table in row labels when a sheet holds several
            r0, c0, r1, c1 = table["source_bounds"]
            columns, rows = _all_rows(doc_id, table["name"])
            matches, problems = match_columns(fields, columns)
            col_offsets = table.get("source_column_offsets") or list(range(len(columns)))
            # older metadata has no per-row offsets: assume contiguous rows from the recorded data start
            row_offsets = table.get("source_row_offsets") or [
                (table.get("data_start_row") or r0 + 1) - r0 + i for i in range(len(rows))]
            report.append({"table": table["name"], "sheet": sheet, "range": _range_address(r0, c0, r1, c1),
                           "rows": len(rows), "matched": {fid: columns[i] for fid, i in matches.items()},
                           "unmatched": problems})
            if not matches:
                continue
            # one bounded read of the table's recorded range; cells are then looked up from memory
            cells = {cell["address"]: cell for row in reader.read(sheet, r0, c0, r1, c1) for cell in row}
            for row_index, values in enumerate(rows):
                if row_index >= len(row_offsets):
                    break   # no source position recorded for this row
                source_row = r0 + row_offsets[row_index]
                observation = {
                    "id": f"{doc_id}:{table['name']}:{source_row}", "record_key": f"{table['name']}:{source_row}",
                    "label": f"{sheet} row {source_row}" + (f" · table {_range_address(r0, c0, r1, c1)}" if shared_sheet else ""),
                    "document_id": doc_id, "filename": meta.filename, "observation_unit": "spreadsheet row",
                    "fields": {}, "spreadsheet_evidence": True,
                }
                for field_id, col_index in matches.items():
                    if col_index >= len(values) or col_index >= len(col_offsets):
                        continue
                    value = values[col_index]
                    if value is None or str(value).strip() == "":
                        continue
                    address = _cell_address(source_row, c0 + col_offsets[col_index])
                    source_cell = cells.get(address, {})
                    raw_value = source_cell.get("raw_value")
                    verified = raw_value is not None and _comparable(raw_value) == _comparable(value)
                    evidence = {"document_id": doc_id, "filename": meta.filename, "sheet": sheet,
                                "table": table["name"], "address": address, "range": address,
                                "text": str(raw_value if raw_value is not None else value),
                                "raw_value": raw_value, "formula": source_cell.get("formula"),
                                "cached_value": source_cell.get("cached_value"),
                                "verified": verified,
                                "verification_status": "source_cell_match" if verified else "source_cell_mismatch"}
                    observation["fields"][field_id] = {
                        "field_id": field_id, "document_id": doc_id, "value": str(value),
                        "raw_value": str(raw_value if raw_value is not None else value),
                        "normalized_value": value if verified else None,
                        "status": "supported" if verified else "unsupported", "evidence": [evidence],
                        "validation_issues": [] if verified else ["Source cell value did not match the parsed table row."],
                        "review_status": "unreviewed" if verified else "needs_review", "model": "deterministic-spreadsheet",
                    }
                if observation["fields"]:
                    observations.append(observation)
    return observations, report
