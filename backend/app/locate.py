"""Find where an extracted value lives in its source file, so the UI can highlight it.

PDF: normalized page boxes from the native text layer (scanned pages without a text
layer can't be located yet). Spreadsheets: cells in the stored sheet preview grid.
The model's `source` hint ("p.3", "sheet 'Q1'!B4", "table sales") is tried first.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .extraction.coordinates import PDFCoordinateLocator, _normalized_tokens
from .store import store

_PAGE = re.compile(r"\bp(?:age|g)?\.?\s*(\d+)", re.I)
_CELL = re.compile(r"(?:sheet\s*)?'?([^'!]+?)'?!\$?([A-Z]{1,3})\$?(\d+)")
_SHEET = re.compile(r"sheet\s*'([^']+)'|sheet\s+(\S+)", re.I)
_NUM = re.compile(r"-?\(?\d[\d,]*(?:\.\d+)?\)?")
_CURRENCY = re.compile(r"^[\s$€£¥₹]+|[\s%]+$|\b(?:usd|eur|gbp|inr|rs\.?)\b", re.I)
_SPLIT = re.compile(r"\s*(?:;|,\s|→|\s/\s|\n)\s*")
MAX_PDF_SCAN = 300


def _parts(text: str) -> list[str]:
    """A list-valued field ("Q1 4126010; Q2 4305357", "Sales, Support, Ops") as its separate values."""
    parts = [p.strip() for p in _SPLIT.split(text) if p.strip()]
    return parts[:12] if len(parts) >= 2 else []


def locate(doc_id: str, text: str, source: str = "") -> dict[str, Any]:
    meta = store.docs[doc_id]
    text, source = text.strip(), (source or "").strip()
    if meta.kind == "pdf":
        return _locate_pdf(doc_id, meta.n_pages, text, source)
    return _locate_sheet(doc_id, meta, text, source)


# ------------------------------------------------------------------ PDF ----
def _candidates(text: str) -> list[str]:
    """Value as written, then without currency/units, then its numeric core."""
    out = [text, _CURRENCY.sub("", text).strip()]
    nums = sorted(_NUM.findall(text), key=len, reverse=True)
    if nums and len(nums[0].strip("()-")) >= 2:
        out.append(nums[0].strip("()"))
    return [c for i, c in enumerate(out) if len(c) >= 2 and c not in out[:i]]


def _page_index(doc_id: str, n_pages: int) -> dict[int, str]:
    """Each page's stored text as space-joined normalized tokens, for a cheap "could it be here?" check."""
    parents = store.parents.get(doc_id, {})
    return {p: f" {' '.join(_normalized_tokens(parents[f'{doc_id}:pp{p}']))} "
            for p in range(1, n_pages + 1) if parents.get(f"{doc_id}:pp{p}", "").strip()}


def _locate_pdf(doc_id: str, n_pages: int, text: str, source: str) -> dict[str, Any]:
    m = _PAGE.search(source)
    hint = int(m.group(1)) if m and 1 <= int(m.group(1)) <= n_pages else None
    order = ([hint] if hint else []) + [p for p in range(1, min(n_pages, MAX_PDF_SCAN) + 1) if p != hint]
    result = {"kind": "pdf", "page": hint, "boxes": [], "status": "not_found", "matched": None}
    if not text:
        return result
    index = _page_index(doc_id, n_pages)

    def pages_for(candidate: str) -> list[int]:
        # PyMuPDF search is the slow part on long PDFs: only run it on the cited page and on pages
        # whose stored text contains the value (pages without stored text are always tried)
        needle = f" {' '.join(_normalized_tokens(candidate))} "
        return [p for p in order if p == hint or p not in index or needle in index[p]]

    with PDFCoordinateLocator(doc_id) as locator:
        # exact value first on every page, looser forms only after that, so "1,250" never beats "$1,250.00"
        for candidate in _candidates(text):
            for page in pages_for(candidate):
                found = locator.locate(page, candidate)
                if found["coordinates"]:
                    boxes = [{k: r[k] for k in ("x", "y", "width", "height")} for r in found["coordinates"]]
                    return {**result, "page": page, "boxes": boxes, "matched": candidate,
                            "status": "found" if len(boxes) == 1 or candidate == text else "ambiguous"}
        parts = _parts(text)
        part_pages = {p for part in parts for c in _candidates(part) for p in pages_for(c)}
        for page in [p for p in order if p in part_pages]:
            boxes = []
            hits = 0
            for part in parts:
                for candidate in _candidates(part):
                    found = locator.locate(page, candidate)["coordinates"]
                    if found:
                        hits += 1
                        boxes += [{k: r[k] for k in ("x", "y", "width", "height")} for r in found]
                        break
            if hits * 2 >= len(parts):
                return {**result, "page": page, "boxes": boxes[:60], "matched": text, "status": "partial"}
    return result


# ---------------------------------------------------------- spreadsheets ----
def _col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _number(s: str) -> float | None:
    s = _CURRENCY.sub("", s).replace(",", "").replace(" ", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = float(s.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def _same(cell: str, value: str) -> bool:
    c, v = cell.strip().casefold(), value.strip().casefold()
    if not c or not v:
        return False
    if c == v:
        return True
    cn, vn = _number(c), _number(v)
    if cn is not None and vn is not None:
        return abs(cn - vn) <= 1e-9 * max(1.0, abs(vn))
    return len(v) >= 4 and v in c


def _locate_sheet(doc_id: str, meta: Any, text: str, source: str) -> dict[str, Any]:
    sheets = meta.sheets
    by_name = {s["name"].casefold(): s["index"] for s in sheets}
    hint_sheet, hint_cell = None, None
    if m := _CELL.search(source):
        hint_sheet = by_name.get(m.group(1).strip().casefold())
        hint_cell = (int(m.group(3)) - 1, _col_index(m.group(2)))
    elif m := _SHEET.search(source):
        hint_sheet = by_name.get((m.group(1) or m.group(2)).strip("'\" ").casefold())
    if hint_sheet is None:
        table = next((t for t in meta.tables if t["name"].casefold() in source.casefold()), None)
        if table and table.get("sheet"):
            hint_sheet = by_name.get(table["sheet"].casefold())
    if hint_sheet is None and len(sheets) == 1:
        hint_sheet = sheets[0]["index"]

    order = ([hint_sheet] if hint_sheet is not None else []) + [s["index"] for s in sheets if s["index"] != hint_sheet]
    result = {"kind": "sheet", "sheet_index": hint_sheet, "cells": [], "status": "not_found"}
    for idx in order:
        path = store.dir(doc_id) / "sheets" / f"{idx}.json"
        if not path.exists():
            continue
        rows = json.loads(path.read_text())["rows"]
        if idx == hint_sheet and hint_cell:
            r, c = hint_cell
            if r < len(rows) and c < len(rows[r]) and (not text or _same(rows[r][c], text)):
                return {**result, "sheet_index": idx, "cells": [{"row": r, "col": c}], "status": "found"}
        if not text:
            continue
        cells = [{"row": r, "col": c} for r, row in enumerate(rows) for c, v in enumerate(row) if _same(v, text)][:50]
        if cells:
            return {**result, "sheet_index": idx, "cells": cells, "status": "found" if len(cells) == 1 else "ambiguous"}
        parts = _parts(text)
        if parts:
            cells, hits = [], 0
            for part in parts:
                for candidate in _candidates(part):
                    found = [{"row": r, "col": c} for r, row in enumerate(rows) for c, v in enumerate(row) if _same(v, candidate)]
                    if found:
                        hits += 1
                        cells += found[:5]
                        break
            if hits * 2 >= len(parts):
                return {**result, "sheet_index": idx, "cells": cells[:60], "status": "partial"}
    if hint_cell and hint_sheet is not None:
        # the model named a cell but the value didn't match anywhere: show its cell, flagged
        return {**result, "cells": [{"row": hint_cell[0], "col": hint_cell[1]}], "status": "source_only"}
    return result
