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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ..config import LLM_MODEL, LLM_PROVIDER
from ..store import store
from .client import describe_error, effort, image_block, llm_available, messages_api, model_label, provider_kwargs, response_text

MAX_CONTEXT_CHARS = 400_000   # ~100k tokens of document text per extraction call
MAX_EXTRACT_IMAGES = 12
# Long PDFs are read in page batches: one call over a long document drops fields from later pages and
# truncates long lists (ExtractBench, arXiv:2607.29677). Small documents keep the single call.
SINGLE_PASS_CHARS = 120_000
SINGLE_PASS_PAGES = 30
BATCH_CHARS = 60_000
BATCH_MAX_PAGES = 15
BATCH_IMAGES = 4
MAX_BATCHES = 12              # cost cap: ~720k characters; anything beyond is reported as not covered
BATCH_WORKERS = 3

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


# per-batch extraction: the page-local parts of SCHEMA plus short notes for the final synthesis
_BATCH_KEYS = ("key_fields", "metrics", "entities", "dates", "sections", "anomalies")
BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["notes", *_BATCH_KEYS],
    "properties": {"notes": {"type": "string", "description": "2-4 sentences: what these pages contain"},
                   **{k: SCHEMA["properties"][k] for k in _BATCH_KEYS}},
}
_SYNTH_KEYS = ("document_type", "title", "summary", "language", "insights", "anomalies", "suggested_questions")
SYNTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_SYNTH_KEYS),
    "properties": {k: SCHEMA["properties"][k] for k in _SYNTH_KEYS},
}

BATCH_PROMPT = """You are a meticulous document analyst reading pages {first}-{last} of a {n_pages}-page PDF \
("{filename}"). Other pages are read separately, so extract only what appears on THESE pages.

Rules:
- key_fields: every identifying / contractual / financial / date / party / contact value on these pages. Mark legally or \
financially essential ones as "critical". Copy values exactly as written (keep currency & units).
- metrics: the headline numbers on these pages (totals, KPIs, growth rates, counts) with units.
- sections: the parts of the document that appear on these pages, with a one-line description.
- anomalies: anything suspicious or inconsistent on these pages (or empty).
- notes: 2-4 sentences on what these pages contain, for writing the document summary later.
- source: always the page, as p.N. Never invent values; omit what isn't there."""

SYNTH_PROMPT = """You are writing the overview of a {n_pages}-page PDF ("{filename}") from notes taken while reading it \
in parts, and the fields extracted from those parts. Write:
- document_type, title, language.
- summary: a 3-6 sentence executive summary of the WHOLE document.
- insights: 3-8 non-obvious, decision-useful observations across the document.
- anomalies: the important inconsistencies or risks (merge duplicates; empty if none).
- suggested_questions: 6 specific questions a user would likely ask about THIS document.
Use only the notes and fields given; never invent values.{coverage}"""


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


def _parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON parse: models without strict schema enforcement may wrap output in prose or ``` fences."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start:end + 1])


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return v if isinstance(v, str) else json.dumps(v) if isinstance(v, (dict, list)) else str(v)


def normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce model output to exactly the shape SCHEMA describes (and the UI relies on)."""
    out: dict[str, Any] = {}
    props = SCHEMA["properties"]
    for key, spec in props.items():
        val = data.get(key)
        if spec["type"] == "string":
            out[key] = _s(val)
        elif spec["items"].get("type") == "string":
            out[key] = [_s(v) for v in val if v not in (None, "")] if isinstance(val, list) else []
        else:
            item_props = spec["items"]["properties"]
            rows = []
            for item in val if isinstance(val, list) else []:
                if not isinstance(item, dict):
                    continue
                row = {k: _s(item.get(k)) for k in item_props}
                for k, p in item_props.items():
                    if "enum" in p and row[k] not in p["enum"]:
                        row[k] = p["enum"][-1]  # e.g. importance -> "normal", entity type -> "other"
                rows.append(row)
            out[key] = rows
    return out


def _call(content: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int = 32000) -> dict[str, Any]:
    """One structured-output model call; raises on refusal or truncation."""
    content = list(content)
    if LLM_PROVIDER != "anthropic":
        # Only Claude enforces output_config.format server-side; other providers accept the
        # parameter but may answer in prose, so the schema has to be in the prompt itself.
        content.append({"type": "text", "text": "Respond with ONLY one JSON object (no markdown, no prose, no code "
                        f"fences) that validates against this JSON Schema:\n{json.dumps(schema)}"})
    with messages_api().stream(
        model=LLM_MODEL,
        max_tokens=max_tokens,
        output_config={"effort": effort("medium"), "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
        **provider_kwargs(),
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "refusal":
        raise RuntimeError("The model declined to process this document.")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Extraction output was truncated (document too dense).")
    return _parse_json(response_text(resp))


def _page_texts(doc_id: str) -> list[str]:
    meta, parents = store.docs[doc_id], store.parents.get(doc_id, {})
    return [parents.get(f"{doc_id}:pp{p}", "") for p in range(1, meta.n_pages + 1)]


def needs_batches(doc_id: str) -> bool:
    meta = store.docs[doc_id]
    return meta.kind == "pdf" and (meta.n_pages > SINGLE_PASS_PAGES or sum(map(len, _page_texts(doc_id))) > SINGLE_PASS_CHARS)


def page_batches(doc_id: str) -> list[list[int]]:
    """Consecutive page groups of at most BATCH_CHARS characters and BATCH_MAX_PAGES pages."""
    batches: list[list[int]] = [[]]
    chars = 0
    for page, text in enumerate(_page_texts(doc_id), start=1):
        if batches[-1] and (chars + len(text) > BATCH_CHARS or len(batches[-1]) >= BATCH_MAX_PAGES):
            batches.append([])
            chars = 0
        batches[-1].append(page)
        chars += len(text)
    return [b for b in batches if b]


def _batch_content(doc_id: str, pages: list[int]) -> list[dict[str, Any]]:
    meta, texts = store.docs[doc_id], _page_texts(doc_id)
    table_pages = {t.get("page") for t in meta.tables}
    # images where text alone misses things: the first page, scanned pages, pages with tables
    visual = [p for p in pages if p == 1 or len(texts[p - 1]) < 80 or p in table_pages][:BATCH_IMAGES]
    content: list[dict[str, Any]] = []
    for p in visual:
        if store.page_path(doc_id, p).exists():
            content += [{"type": "text", "text": f"Page {p} image:"}, image_block(store.page_path(doc_id, p))]
    body = "\n".join(f"<page number=\"{p}\">\n{texts[p - 1]}\n</page>" for p in pages)
    content.append({"type": "text", "text": f"<document filename=\"{meta.filename}\" pages=\"{meta.n_pages}\">\n{body}\n</document>"})
    content.append({"type": "text", "text": BATCH_PROMPT.format(first=pages[0], last=pages[-1], n_pages=meta.n_pages,
                                                                filename=meta.filename)})
    return content


def _key(*parts: str) -> str:
    return "|".join(re.sub(r"\W+", "", p.casefold()) for p in parts)


_RANK = {"critical": 0, "high": 1, "normal": 2}


def merge_batches(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Union of per-batch results in page order, duplicates removed (the highest importance wins)."""
    merged: dict[str, Any] = {k: [] for k in _BATCH_KEYS}
    seen: dict[str, dict[str, int]] = {k: {} for k in _BATCH_KEYS}
    identity = {
        "key_fields": lambda r: _key(r["label"], r["value"]), "metrics": lambda r: _key(r["label"], r["value"]),
        "entities": lambda r: _key(r["name"]), "dates": lambda r: _key(r["label"], r["date"]),
        "sections": lambda r: _key(r["title"], r["source"]), "anomalies": lambda r: _key(r),
    }
    for part in parts:
        for k in _BATCH_KEYS:
            for row in part.get(k, []):
                ident = identity[k](row)
                if ident in seen[k]:
                    if k == "key_fields":
                        old = merged[k][seen[k][ident]]
                        if _RANK.get(row["importance"], 2) < _RANK.get(old["importance"], 2):
                            old["importance"] = row["importance"]
                    continue
                seen[k][ident] = len(merged[k])
                merged[k].append(row)
    return merged


def _batched_pdf_extraction(doc_id: str) -> dict[str, Any]:
    meta = store.docs[doc_id]
    batches = page_batches(doc_id)
    covered, skipped = batches[:MAX_BATCHES], batches[MAX_BATCHES:]
    done: dict[int, dict[str, Any]] = {}
    failures: list[str] = []

    def publish(summary: str) -> None:
        partial = normalize_extraction({**merge_batches([done[i] for i in sorted(done)]), "summary": summary,
                                        "title": meta.filename, "document_type": "PDF document"})
        store.update(doc_id, extraction={**partial, "_engine": model_label()})

    with ThreadPoolExecutor(max_workers=BATCH_WORKERS, thread_name_prefix="overview-batch") as pool:
        futures = {pool.submit(_call, _batch_content(doc_id, pages), BATCH_SCHEMA): i for i, pages in enumerate(covered)}
        for future in as_completed(futures):
            i = futures[future]
            pages = covered[i]
            try:
                part = future.result()
                done[i] = {**normalize_extraction(part), "notes": str(part.get("notes", ""))}
            except Exception as e:
                failures.append(f"pages {pages[0]}-{pages[-1]}: {describe_error(e)}")
                continue
            # show fields as each part of the document is read
            publish(f"Reading the document… {len(done)} of {len(covered)} parts done.")
    if not done:
        raise RuntimeError("; ".join(failures) or "No part of the document could be read.")

    merged = merge_batches([done[i] for i in sorted(done)])
    notes = "\n".join(f"Pages {covered[i][0]}-{covered[i][-1]}: {done[i]['notes']}" for i in sorted(done))
    fields = "\n".join(f"- {f['label']}: {f['value']} ({f['source']}, {f['importance']})"
                        for f in sorted(merged["key_fields"], key=lambda f: _RANK.get(f["importance"], 2))[:150])
    coverage = ""
    if skipped:
        coverage = (f"\nNOTE: only pages 1-{covered[-1][-1]} of {meta.n_pages} were read (cost limit). Say so in the "
                    "summary; the chat can search all of it.")
    synth = _call([{"type": "text", "text": f"Notes by page range:\n{notes}\n\nExtracted fields:\n{fields}\n\n"
                    f"Anomalies noted per part:\n" + "\n".join(f"- {a}" for a in merged["anomalies"])},
                   {"type": "text", "text": SYNTH_PROMPT.format(n_pages=meta.n_pages, filename=meta.filename,
                                                                coverage=coverage)}],
                  SYNTH_SCHEMA, max_tokens=8000)
    data = normalize_extraction({**merged, **{k: synth.get(k) for k in _SYNTH_KEYS}})
    data["_batches"] = {"read": len(done), "total": len(batches), "pages_read": covered[-1][-1]}
    if failures:
        data["_error"] = "Some pages could not be read: " + "; ".join(failures)
    return data


def run_extraction(doc_id: str) -> None:
    meta = store.docs.get(doc_id)
    if not meta:
        return
    if not llm_available():
        store.update(doc_id, extraction=heuristic_extraction(doc_id), extraction_status="ready")
        return
    store.update(doc_id, extraction_status="running")
    try:
        if needs_batches(doc_id):
            data = _batched_pdf_extraction(doc_id)
        else:
            content, coverage = _pdf_context(doc_id) if meta.kind == "pdf" else _excel_context(doc_id)
            kind = "PDF document" if meta.kind == "pdf" else "spreadsheet"
            content.append({"type": "text", "text": PROMPT.format(kind=kind, coverage=coverage)})
            data = normalize_extraction(_call(content, SCHEMA))
        data["_engine"] = model_label()
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
                   "Add an LLM API key in .env (Anthropic or DeepSeek) for AI-generated summaries, insights and full field extraction.",
        "key_fields": fields, "metrics": metrics, "entities": [], "dates": [],
        "sections": [{"title": t["title"], "description": f"{t['n_rows']} rows × {t['n_cols']} columns",
                      "source": t["source"]} for t in meta.tables[:20]],
        "insights": [], "anomalies": [],
        "suggested_questions": ["Summarize this document", "What are the key numbers?",
                                "List all tables and what they contain"],
        "_engine": "heuristic",
    }
