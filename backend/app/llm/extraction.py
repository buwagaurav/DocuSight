"""Automatic extraction on upload.

As soon as a document is indexed, Claude reads it — page *images* (so it sees
stamps, signatures, charts, checkboxes, handwriting) plus the layout-aware text
and every table schema — and returns a schema-validated JSON profile:
document type, summary, the mandatory key fields with their source location,
metrics, entities, dates, insights, anomalies and suggested questions.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import LLM_MODEL
from ..store import store
from .client import FALLBACK_KW, describe_error, get_client, image_block, llm_available, response_text

MAX_CONTEXT_CHARS = 400_000   # ~100k tokens of document text per extraction call
MAX_EXTRACT_IMAGES = 12

_S = {"type": "string"}
_ARR = lambda props, req=None: {  # noqa: E731
    "type": "array",
    "items": {"type": "object", "properties": props, "required": req or list(props), "additionalProperties": False},
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["document_type", "title", "summary", "language", "key_fields", "metrics", "entities",
                 "dates", "sections", "insights", "anomalies", "suggested_questions"],
    "properties": {
        "document_type": {"type": "string", "description": "e.g. Invoice, Financial statement, Contract, Research paper, Sales workbook"},
        "title": _S,
        "summary": {"type": "string", "description": "3-6 sentence executive summary"},
        "language": _S,
        "key_fields": _ARR({
            "label": _S, "value": _S,
            "category": {"type": "string", "description": "e.g. Identification, Parties, Financial, Dates, Terms, Contact"},
            "importance": {"type": "string", "enum": ["critical", "high", "normal"]},
            "source": {"type": "string", "description": "where it was found: 'p.3' or \"sheet 'Q1'!B4\" or 'table sales'"},
        }),
        "metrics": _ARR({"label": _S, "value": _S, "unit": _S, "context": _S, "source": _S}),
        "entities": _ARR({"name": _S, "type": {"type": "string", "enum": ["person", "organization", "location", "product", "other"]}, "role": _S}),
        "dates": _ARR({"label": _S, "date": _S, "source": _S}),
        "sections": _ARR({"title": _S, "description": _S, "source": _S}),
        "insights": {"type": "array", "items": _S},
        "anomalies": {"type": "array", "items": _S, "description": "inconsistencies, missing values, totals that don't add up, risks"},
        "suggested_questions": {"type": "array", "items": _S},
    },
}

PROMPT = """You are a meticulous document analyst. Extract EVERYTHING a professional would consider \
required or mandatory from this {kind} — the values someone would otherwise copy out by hand.

Rules:
- key_fields: every identifying / contractual / financial / date / party / contact value present (IDs, \
numbers, names, addresses, totals, taxes, terms, signatures present?, status...). Mark legally or \
financially essential ones as "critical". Copy values exactly as written (keep currency & units). 15-60 fields is normal for dense documents.
- metrics: the headline numbers (totals, KPIs, growth rates, counts) with units.
- For spreadsheets, compute nothing you cannot see; use the column statistics provided.
- sections: the document's main parts (PDF) or sheets/tables (Excel) with a one-line description.
- insights: 3-8 non-obvious, decision-useful observations. anomalies: anything suspicious or inconsistent (or empty).
- suggested_questions: 6 specific questions a user would likely ask about THIS document.
- source: always say where (p.N, sheet/cell, or table name). Never invent values; omit what isn't there.
{coverage}"""


def _pdf_context(doc_id: str) -> tuple[list[dict[str, Any]], str]:
    meta = store.docs[doc_id]
    parents = store.parents.get(doc_id, {})
    pages = []
    for p in range(1, meta.n_pages + 1):
        pages.append(f"<page number=\"{p}\">\n{parents.get(f'{doc_id}:pp{p}', '')}\n</page>")
    text = "\n".join(pages)
    coverage = ""
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS]
        coverage = (f"\nNOTE: this document is long; only the first ~{MAX_CONTEXT_CHARS // 1000}k characters of text "
                    "are shown. Mention in the summary that extraction covers the beginning of the document; the chat can search all of it.")
    # images: first pages + pages that have tables/charts/scans are the most information-dense
    img_pages = list(range(1, min(meta.n_pages, 6) + 1))
    for t in meta.tables:
        if t.get("page") and t["page"] not in img_pages:
            img_pages.append(t["page"])
    for p in range(1, meta.n_pages + 1):
        if len(parents.get(f"{doc_id}:pp{p}", "")) < 80 and p not in img_pages:
            img_pages.append(p)  # scanned / image-only pages carry info only visually
    content: list[dict[str, Any]] = []
    for p in sorted(img_pages[:MAX_EXTRACT_IMAGES]):
        content.append({"type": "text", "text": f"Page {p} image:"})
        content.append(image_block(store.page_path(doc_id, p)))
    content.append({"type": "text", "text": f"<document filename=\"{meta.filename}\" pages=\"{meta.n_pages}\">\n{text}\n</document>"})
    return content, coverage


def _excel_context(doc_id: str) -> tuple[list[dict[str, Any]], str]:
    meta = store.docs[doc_id]
    parts = [f"Workbook: {meta.filename} — {len(meta.sheets)} sheet(s)"]
    for s in meta.sheets:
        parts.append(f"\n### Sheet '{s['name']}' ({s['rows']} rows × {s['cols']} cols, "
                     f"{s.get('formulas', 0)} formulas, {s.get('merged', 0)} merged ranges)")
        sp = store.dir(doc_id) / "sheets" / f"{s['index']}.json"
        if sp.exists():
            grid = json.loads(sp.read_text())["rows"][:60]
            parts.append("Raw cells (first rows):\n" + "\n".join(
                f"R{i + 1}: " + " | ".join(v for v in r if v) for i, r in enumerate(grid) if any(r)))
    for c in store.chunks.get(doc_id, []):
        if c.kind in {"sheet_summary"} or (c.kind == "text" and c.sheet):
            parts.append("\n" + store.parent_text(c) if c.kind == "sheet_summary" else "\n" + c.text)
    text = "\n".join(parts)
    coverage = ""
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS]
        coverage = "\nNOTE: workbook is large; only part of it is shown here. The chat can query all of it with SQL."
    return [{"type": "text", "text": text}], coverage


def run_extraction(doc_id: str) -> None:
    meta = store.docs.get(doc_id)
    if not meta:
        return
    if not llm_available():
        store.update(doc_id, extraction=heuristic_extraction(doc_id), extraction_status="ready")
        return
    store.update(doc_id, extraction_status="running")
    try:
        content, coverage = _pdf_context(doc_id) if meta.kind == "pdf" else _excel_context(doc_id)
        kind = "PDF document" if meta.kind == "pdf" else "spreadsheet"
        content.append({"type": "text", "text": PROMPT.format(kind=kind, coverage=coverage)})
        with get_client().beta.messages.stream(
            model=LLM_MODEL,
            max_tokens=32000,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}],
            **FALLBACK_KW,
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason == "refusal":
            raise RuntimeError("The model declined to process this document.")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Extraction output was truncated (document too dense).")
        data = json.loads(response_text(resp))
        data["_engine"] = LLM_MODEL
        store.update(doc_id, extraction=data, extraction_status="ready")
    except Exception as e:
        print(f"[extract] {doc_id}: {e}")
        fallback = heuristic_extraction(doc_id)
        fallback["_error"] = describe_error(e)
        store.update(doc_id, extraction=fallback, extraction_status="error")


# --------------------------------------------------------------------------
_SPLIT = re.compile(r"\s+(?=[A-Z][A-Za-z]+(?: [a-z]+){0,2}:\s)")
_KV = re.compile(r"^\s*\**([A-Za-z][\w .#/&()'-]{1,48}?)\**\s*[:：]\s*(.{1,160})$")


def heuristic_extraction(doc_id: str) -> dict[str, Any]:
    """No-LLM fallback: label:value pairs + table statistics."""
    meta = store.docs[doc_id]
    fields, metrics, seen = [], [], set()
    for c in [c for c in store.chunks.get(doc_id, []) if c.kind == "text"][:400]:
        # PDF text extraction often runs several "Label: value" pairs into one line — split them apart
        lines = [p for line in c.text.splitlines() for p in _SPLIT.split(line)]
        for line in lines:
            m = _KV.match(line.strip("|- "))
            if m and m.group(1).lower() not in seen and len(fields) < 60:
                seen.add(m.group(1).lower())
                fields.append({"label": m.group(1).strip(), "value": m.group(2).strip(" |*"), "category": "Extracted",
                               "importance": "normal", "source": c.location() or "document"})
    for t in meta.tables[:10]:
        for col in t.get("columns", []):
            if col.get("sum") is not None and len(metrics) < 24:
                metrics.append({"label": f"{t['title']} · {col['name']} (total)", "value": str(col["sum"]),
                                "unit": "", "context": f"min {col['min']}, max {col['max']}", "source": t["source"]})
    what = f"{meta.n_pages}-page PDF" if meta.kind == "pdf" else f"workbook with {len(meta.sheets)} sheet(s)"
    return {
        "document_type": "PDF document" if meta.kind == "pdf" else "Spreadsheet",
        "title": meta.filename, "language": "",
        "summary": f"{meta.filename}: {what}, {len(meta.tables)} table(s) detected, {meta.n_chunks} indexed chunks. "
                   "Connect an Anthropic API key for AI-generated summaries, insights and full field extraction.",
        "key_fields": fields, "metrics": metrics, "entities": [], "dates": [],
        "sections": [{"title": t["title"], "description": f"{t['n_rows']} rows × {t['n_cols']} columns",
                      "source": t["source"]} for t in meta.tables[:20]],
        "insights": [], "anomalies": [],
        "suggested_questions": ["Summarize this document", "What are the key numbers?",
                                "List all tables and what they contain"],
        "_engine": "heuristic",
    }
