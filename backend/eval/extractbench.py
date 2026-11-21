"""Run DocuSight on ExtractBench (arXiv:2607.29677) cases and write predictions for the official scorer.

Two modes:
  locate   No LLM. Feeds the gold values to DocuSight's highlight locator (app/locate.py) and emits
           the boxes it finds, so the official grounding metrics measure highlighting on its own.
  overview / overview-single
           The Overview auto-extraction (app/llm/extraction.py), batched or forced single-call,
           scored with proxy metrics (page reach, scalar recall); see predict_overview.
  extract  Full path: DocuSight ingestion (page text, OCR, tables) + the Extraction-tab engine
           (app/extraction/service.py) with the case's JSON Schema mapped to DocuSight fields,
           one observation per document. Values come from the engine; scalar boxes come from the
           engine's evidence localization, and list/object leaves are located with app/locate.py
           (the same lookup the UI runs when a value is clicked).

Everything runs against an isolated data directory, never the user's workspace. Score the output
with eval/extractbench_score.py inside a venv that has the ExtractBench package installed.

    python -m eval.extractbench run --mode locate
    python -m eval.extractbench run --mode extract --limit 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CACHE = REPO / "data" / "eval" / "extractbench"
DATASET = "llamaindex/ExtractBench"
REVISION = "main"

# Fixed, stratified sample: (split, task challenge, capture) -> count. Stable across runs because cases
# are ordered by a hash of their id, so before/after comparisons always score the same documents.
SAMPLE = [
    ("short", "T3", "scan", 4), ("short", "T3", "digital", 4), ("short", "T1", "digital", 4),
    ("short", "T1", "scan", 2), ("medium", "T1", "digital", 3), ("medium", "T2", "digital", 2),
    ("medium", "T1", "scan", 1),
]

# app modules read the data directory at import time, so it must be set before they are imported
os.environ.setdefault("DOCUSIGHT_DATA_DIR", str(CACHE / "docusight-data"))
os.environ.setdefault("COLPALI_ENABLED", "false")


# ---------------------------------------------------------------- dataset ----
def _download(rel: str, dest: Path) -> Path:
    # huggingface_hub (already a dependency) brings its own CA bundle; python.org builds of urllib have none
    from huggingface_hub import hf_hub_download

    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        cached = hf_hub_download(DATASET, rel, repo_type="dataset", revision=REVISION)
        shutil.copyfile(cached, dest)
    return dest


def load_split(split: str) -> list[dict[str, Any]]:
    path = _download(f"{split}.jsonl", CACHE / f"{split}.jsonl")
    return [json.loads(line) for line in path.open()]


def _bucket(row: dict[str, Any]) -> tuple[set[str], str]:
    tags = row.get("tags") or []
    challenges = {t.split(":", 1)[1][:2] for t in tags if t.startswith("challenge:")}
    capture = "scan" if any(t.startswith("perception:") for t in tags) else "digital"
    return challenges, capture


def _has_boxes(row: dict[str, Any]) -> bool:
    rules = json.loads(row.get("field_rules") or "{}")
    return isinstance(rules, dict) and any(
        rule.get("verified") and any(ev.get("bbox") for ev in rule.get("evidence") or [])
        for rule in rules.values() if isinstance(rule, dict))


def sample_cases() -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    for split in sorted({s for s, *_ in SAMPLE}):
        rows = sorted(load_split(split), key=lambda r: hashlib.sha1(r["id"].encode()).hexdigest())
        for s, challenge, capture, count in SAMPLE:
            if s != split:
                continue
            picked = [r for r in rows if r not in chosen and challenge in _bucket(r)[0]
                      and _bucket(r)[1] == capture and _has_boxes(r)][:count]
            chosen += picked
    return chosen


def materialize(row: dict[str, Any]) -> tuple[Path, Path]:
    """PDF + <stem>.test.json in the layout the ExtractBench loader expects (mirrors its download step)."""
    rel = row["pdf"]
    if Path(rel).is_absolute() or ".." in Path(rel).parts or not rel.startswith("docs/"):
        raise RuntimeError(f"unsafe dataset path {rel!r}")
    pdf = _download(rel, CACHE / "cases" / row["category"] / Path(rel).name)
    sidecar: dict[str, Any] = {"tags": row.get("tags") or [], "data_schema": json.loads(row["data_schema"]),
                               "expected_output": json.loads(row["expected_output"])}
    rules = json.loads(row.get("field_rules") or "{}")
    if isinstance(rules, dict) and rules:
        sidecar["_field_rules"] = rules
    elif isinstance(rules, list) and rules:
        sidecar["test_rules"] = rules
    if repeated := json.loads(row.get("repeated_structure") or "{}"):
        sidecar["_eval_row_identity"] = repeated
    test_json = pdf.with_name(pdf.stem + ".test.json")
    test_json.write_text(json.dumps(sidecar, ensure_ascii=False))
    return pdf, test_json


# ------------------------------------------------------------- DocuSight ----
def _register(pdf: Path) -> str:
    """Store the PDF in the isolated DocuSight store once; reuse it on later runs."""
    from app.store import store

    index_path = CACHE / "doc_ids.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    if (doc_id := index.get(pdf.name)) and doc_id in store.docs:
        return doc_id
    meta = store.create(pdf.name, "pdf", pdf.read_bytes(), project_id="extractbench")
    index[pdf.name] = meta.id
    index_path.write_text(json.dumps(index, indent=1))
    return meta.id


def _ingest(doc_id: str) -> None:
    from app import pipeline
    from app.store import store

    if store.docs[doc_id].status == "ready":
        return
    pipeline._process(doc_id, auto_extract=False, contextual_retrieval=False)
    if store.docs[doc_id].status != "ready":
        raise RuntimeError(f"ingestion failed: {store.docs[doc_id].error}")


def _leaves(value: Any, path: str):
    """(field_path, scalar) for every non-null leaf, in ExtractBench path syntax (a[0].b)."""
    if isinstance(value, dict):
        for key, v in value.items():
            yield from _leaves(v, f"{path}.{key}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _leaves(v, f"{path}[{i}]")
    elif value is not None and value != "":
        yield path, value


def _leaf_text(value: Any) -> str:
    if isinstance(value, bool):
        return ""  # checkboxes and flags have no locatable text
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _cite(field_path: str, page: int | None, box: dict[str, float] | None) -> dict[str, Any]:
    return {"field_path": field_path, "page": page,
            "bbox": [box["x"], box["y"], box["width"], box["height"]] if box else None}


def predict_locate(row: dict[str, Any], doc_id: str) -> dict[str, Any]:
    """Gold values in, DocuSight's boxes out: isolates highlighting from extraction quality."""
    from app.locate import locate

    expected = json.loads(row["expected_output"])
    rules = json.loads(row.get("field_rules") or "{}")
    citations = []
    for field_path, rule in rules.items():
        if not isinstance(rule, dict) or not rule.get("verified"):
            continue
        ev = next((e for e in rule.get("evidence") or [] if e.get("page") and e.get("value") is not None), None)
        if ev is None:
            continue
        text = _leaf_text(ev["value"])
        if not text:
            continue
        # the page hint mirrors what the extraction model cites ("p.N"); the value is what the UI searches for
        found = locate(doc_id, text, f"p.{ev['page']}")
        citations += [_cite(field_path, found["page"], box) for box in found.get("boxes", [])]
    return {"prediction": expected, "citations": citations}


def schema_to_fields(schema: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Map a case's top-level JSON Schema properties to DocuSight fields (one observation per document)."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def resolve(node: Any, depth: int = 0) -> Any:
        # depth counts $ref expansions only (it stops recursive definitions); plain nesting is unlimited
        if isinstance(node, dict):
            if "$ref" in node:
                if depth > 12:
                    return {}
                target = defs.get(node["$ref"].rsplit("/", 1)[-1], {})
                return resolve({**target, **{k: v for k, v in node.items() if k != "$ref"}}, depth + 1)
            return {k: (props(v, depth) if k == "properties" else resolve(v, depth))
                    for k, v in node.items() if k not in {"$defs", "definitions", "default", "title"}}
        if isinstance(node, list):
            return [resolve(v, depth) for v in node]
        return node

    def props(node: Any, depth: int) -> Any:
        # a "properties" map's keys are field names: a field may be called "title" or "default"
        return {k: resolve(v, depth) for k, v in node.items()} if isinstance(node, dict) else resolve(node, depth)

    def unwrap(node: dict[str, Any]) -> dict[str, Any]:
        options = [o for o in node.get("anyOf") or node.get("oneOf") or [] if o.get("type") != "null"]
        node = {**options[0], "description": node.get("description", options[0].get("description", ""))} if options else node
        if isinstance(node.get("type"), list):  # {"type": ["string", "null"]} -> "string"
            node = {**node, "type": next((t for t in node["type"] if t != "null"), "string")}
        return node

    fields, id_map = [], {}
    for prop, raw in (resolve(schema).get("properties") or {}).items():
        node = unwrap(raw)
        multiple = node.get("type") == "array"
        item = unwrap(node.get("items") or {}) if multiple else node
        kind = item.get("type", "string")
        data_type = {"string": "string", "number": "number", "integer": "integer",
                     "boolean": "boolean", "object": "object"}.get(kind, "string")
        fid = re.sub(r"[^A-Za-z0-9_-]", "_", prop)[:64]
        fid = fid if re.match(r"[A-Za-z]", fid) else f"f_{fid}"[:64]
        id_map[fid] = prop
        field: dict[str, Any] = {"id": fid, "name": raw.get("title") or prop, "description": node.get("description", ""),
                                 "data_type": data_type, "multiple": multiple, "nullable": True}
        if data_type == "object":
            props = {}
            for key, sub in (item.get("properties") or {}).items():
                sub = unwrap(sub)
                if sub.get("type") in {"string", "number", "integer", "boolean"}:
                    sub = {**sub, "type": [sub["type"], "null"]}  # any record value may be absent
                props[key] = sub
            field["constraints"] = {"properties": props, "required": list(props)}
        elif item.get("enum") and all(isinstance(v, str) for v in item["enum"]):
            field["allowed_values"] = item["enum"]
        fields.append(field)
    return fields, id_map


def predict_extract(row: dict[str, Any], doc_id: str) -> dict[str, Any]:
    from app.extraction.service import _extract_document, _page_batches, normalize_fields
    from app.locate import locate

    fields, id_map = schema_to_fields(json.loads(row["data_schema"]))
    fields = normalize_fields(fields)
    project = {"objective": "Extract every field defined by the schema from this document, exactly as the field "
                            "descriptions specify.",
               "observation_unit": "The whole document: return exactly one observation containing every field; "
                                   "list fields hold every matching record found in these pages.",
               "schema_version": 1, "observations": []}
    batches = len(_page_batches(doc_id))
    rows = _extract_document(project, doc_id, fields, None, None, threading.Event(), lambda *a: None)

    # one observation per document was requested; if the model split it, merge: first supported scalar wins,
    # list fields concatenate (in page order, duplicates removed)
    prediction: dict[str, Any] = {prop: None for prop in id_map.values()}
    evidence: dict[str, list[dict[str, Any]]] = {prop: [] for prop in id_map.values()}
    for r in rows:
        for fid, cell in r["fields"].items():
            prop = id_map[fid]
            evidence[prop] += cell.get("evidence", [])
            value = cell.get("normalized_value") if cell.get("status") == "supported" else None
            if value is None:
                continue
            if isinstance(value, list):
                merged = prediction[prop] or []
                prediction[prop] = merged + [v for v in value if v not in merged]
            elif prediction[prop] is None:
                prediction[prop] = value

    citations = []
    for prop, value in prediction.items():
        evs = [e for e in evidence[prop] if e.get("page")]
        if value is None:
            continue
        if not isinstance(value, (dict, list)):
            for e in evs:  # the engine's own quote-located boxes
                citations += [_cite(prop, e["page"], c) for c in e.get("coordinates") or []] or [_cite(prop, e["page"], None)]
            continue
        cited = sorted({e["page"] for e in evs})
        citations += [_cite(prop, p, None) for p in cited]
        for leaf_path, leaf in list(_leaves(value, prop))[:4000]:
            text = _leaf_text(leaf)
            if not text:
                continue
            found = locate(doc_id, text, f"p.{cited[0]}" if cited else "")
            citations += [_cite(leaf_path, found["page"], box) for box in found.get("boxes", [])[:3]]
    follow_up = sum(1 for r in rows for c in r["fields"].values()
                    if c.get("extraction_pass") == "follow_up" and c.get("status") == "supported")
    return {"prediction": prediction, "citations": citations, "batches": batches, "rows_returned": len(rows),
            "follow_up_filled": follow_up}


def _norm(value: Any) -> str:
    text = _leaf_text(value).casefold()
    try:  # numbers compare by value: "1,250.00" == 1250
        return repr(float(re.sub(r"[,$€£\s]", "", text)))
    except ValueError:
        return re.sub(r"\W+", "", text)


def predict_overview(row: dict[str, Any], doc_id: str, single_pass: bool) -> dict[str, Any]:
    """Overview (auto-extraction) proxy metrics. Not an official ExtractBench metric: the Overview has no schema.

    page reach   distinct pages cited by key fields / metrics / dates, and the furthest one
    recall       share of the case's expected top-level scalar values that appear among Overview values
    """
    from app.llm import extraction
    from app.store import store

    if single_pass:  # the pre-batching behaviour, for before/after comparison
        extraction.needs_batches = lambda _doc_id: False
    extraction.run_extraction(doc_id)
    data = store.docs[doc_id].extraction or {}
    if store.docs[doc_id].extraction_status != "ready":
        raise RuntimeError(data.get("_error") or "overview extraction failed")
    items = data.get("key_fields", []) + data.get("metrics", []) + data.get("dates", [])
    pages = sorted({int(m) for item in items for m in re.findall(r"p(?:age)?\.?\s*(\d+)", item.get("source", ""), re.I)})
    values = " ".join(_norm(item.get("value") or item.get("date")) for item in items)
    found = {_norm(i.get("value") or i.get("date")) for i in items}
    expected = [v for v in json.loads(row["expected_output"]).values()
                if v is not None and not isinstance(v, (list, dict, bool)) and len(_leaf_text(v)) >= 3]
    hits = sum(1 for v in expected if _norm(v) in found or (len(_norm(v)) >= 5 and _norm(v) in values))
    return {"prediction": None, "citations": [], "overview": {
        "fields": len(items), "cited_pages": len(pages), "max_cited_page": max(pages, default=0),
        "pages": store.docs[doc_id].n_pages, "expected_scalars": len(expected), "recall_hits": hits,
        "batches": data.get("_batches"), "error": data.get("_error")}}


# ------------------------------------------------------------------- CLI ----
def run(mode: str, out: Path, limit: int | None, only: list[str]) -> None:
    from app.store import store

    cases = sample_cases()
    if only:
        cases = [c for c in cases if any(o in c["id"] for o in only)]
    cases = cases[:limit] if limit else cases
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(cases)} cases → {out}", flush=True)
    for i, row in enumerate(cases, 1):
        stem = Path(row["pdf"]).stem
        dest = out / f"{stem}.prediction.json"
        if dest.exists():
            print(f"[{i}/{len(cases)}] {row['id']}: cached", flush=True)
            continue
        started = time.time()
        record: dict[str, Any] = {"id": row["id"], "tags": row["tags"], "mode": mode}
        try:
            pdf, test_json = materialize(row)
            record["test_json"] = str(test_json)
            doc_id = _register(pdf)
            _ingest(doc_id)
            record["pages"] = store.docs[doc_id].n_pages
            if mode == "locate":
                record.update(predict_locate(row, doc_id))
            elif mode == "extract":
                record.update(predict_extract(row, doc_id))
            else:
                record.update(predict_overview(row, doc_id, single_pass=mode == "overview-single"))
        except Exception as e:  # recorded and scored as a failure, never silently skipped
            record.update({"prediction": None, "citations": [], "error": f"{type(e).__name__}: {e}"[:500]})
        record["seconds"] = round(time.time() - started, 1)
        dest.write_text(json.dumps(record, ensure_ascii=False, default=str))
        status = record.get("error") or f"{len(record['citations'])} citations"
        print(f"[{i}/{len(cases)}] {row['id']}: {status} ({record['seconds']}s)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run DocuSight on the sample and write predictions")
    r.add_argument("--mode", choices=["locate", "extract", "overview", "overview-single"], required=True)
    r.add_argument("--out", type=Path, help="output dir (default data/eval/extractbench/runs/<mode>)")
    r.add_argument("--limit", type=int)
    r.add_argument("--case", action="append", default=[], help="only cases whose id contains this (repeatable)")
    sub.add_parser("sample", help="list the sampled case ids")
    args = parser.parse_args()
    if args.cmd == "sample":
        for row in sample_cases():
            print(row["id"], " ".join(t for t in row["tags"] if t.split(":")[0] in {"challenge", "perception"}))
        return
    run(args.mode, args.out or CACHE / "runs" / args.mode, args.limit, args.case)


if __name__ == "__main__":
    sys.exit(main())
