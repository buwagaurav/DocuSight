"""Ingestion orchestration: parse → tables → contextual headers → embed → ready,
then (in the background) auto-extraction and the ColPali visual index."""
from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .config import CONTEXTUAL_RETRIEVAL
from .ingest.excel import ingest_excel
from .ingest.pdf import ingest_pdf
from .llm.contextual import contextualize
from .llm.extraction import run_extraction
from .retrieval.colpali import colpali
from .retrieval.hybrid import retriever
from .retrieval.models import embed_passages, embedding_signature
from .store import store
from .tables import describe_df, write_tables

_ingest_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ingest")
_extract_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="extract")
_visual_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="colpali")  # one GPU job at a time

PDF_EXT = {".pdf"}
EXCEL_EXT = {".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"}


def submit(doc_id: str) -> None:
    _ingest_pool.submit(_process, doc_id)


def submit_extraction(doc_id: str) -> None:
    _extract_pool.submit(run_extraction, doc_id)


def submit_visual(doc_id: str) -> None:
    if colpali.available and store.docs[doc_id].kind == "pdf":
        store.update(doc_id, visual_index="pending")
        _visual_pool.submit(_visual, doc_id)


def _process(doc_id: str) -> None:
    meta = store.docs[doc_id]
    path = store.source_path(doc_id)

    def progress(stage: str, p: float) -> None:
        store.update(doc_id, stage=stage, progress=round(min(p, 0.99), 3))

    try:
        techniques = []
        if meta.kind == "pdf":
            chunks, parents, tables, info = ingest_pdf(doc_id, path, progress)
            store.update(doc_id, n_pages=info["n_pages"])
            techniques += ["Page rendering", "Layout-aware markdown", "Native table detection"]
            if info["scanned_pages"]:
                techniques.append(f"{len(info['scanned_pages'])} image-only page(s) handled visually")
        else:
            chunks, parents, tables, info = ingest_excel(doc_id, path, progress)
            store.update(doc_id, sheets=info["sheets"])
            techniques += ["Merged-cell propagation", "Multi-table sheet splitting", "Header detection"]

        progress("Building SQL tables", 0.62)
        write_tables(store.tables_db(doc_id), tables)
        table_meta = [{
            "name": t["name"], "title": t["title"], "source": t["source"], "page": t.get("page"),
            "sheet": t.get("sheet"), "n_rows": int(len(t["df"])), "n_cols": int(len(t["df"].columns)),
            "columns": describe_df(t["df"]),
        } for t in tables]
        store.update(doc_id, tables=table_meta)
        if tables:
            techniques.append("Text-to-SQL tables (DuckDB)")

        # contextual chunk headers (cheap, always on)
        for c in chunks:
            loc = f"page {c.page}" if c.page else f"sheet {c.sheet}" if c.sheet else ""
            c.context = " · ".join(x for x in (meta.filename, loc, c.section or "") if x)
        techniques += ["Structure-aware chunking", "Contextual chunk headers", "Small-to-big parents"]

        if CONTEXTUAL_RETRIEVAL:
            progress("Contextual retrieval (LLM chunk context)", 0.65)
            full = "\n\n".join(parents.values())
            n = contextualize(full, chunks, lambda f: progress("Contextual retrieval", 0.65 + 0.15 * f))
            if n:
                techniques.append("LLM contextual retrieval")

        progress(f"Embedding {len(chunks)} chunks", 0.82)
        dense = embed_passages([c.text for c in chunks], [c.context for c in chunks])
        np.save(store.dir(doc_id) / "dense.npy", dense)
        (store.dir(doc_id) / "dense.sig").write_text(embedding_signature())
        store.set_chunks(doc_id, chunks, parents)
        techniques += ["BM25 index", f"Dense embeddings ({embedding_signature().replace(':', ', ')}d)"]

        store.update(doc_id, status="ready", stage="Ready", progress=1.0, n_chunks=len(chunks), techniques=techniques)
        retriever.invalidate()
        submit_extraction(doc_id)
        submit_visual(doc_id)
    except Exception as e:
        traceback.print_exc()
        store.update(doc_id, status="error", stage="Failed", error=f"{type(e).__name__}: {e}")


def _visual(doc_id: str) -> None:
    meta = store.docs.get(doc_id)
    if not meta:
        return
    try:
        store.update(doc_id, visual_index="building")
        colpali.index_document(doc_id, meta.n_pages,
                               lambda f: store.update(doc_id, stage=f"ColPali indexing {int(f * 100)}%"))
        techs = list(dict.fromkeys((store.docs[doc_id].techniques or []) + ["ColPali visual index"]))
        store.update(doc_id, visual_index="ready", stage="Ready", techniques=techs)
    except Exception as e:
        traceback.print_exc()
        store.update(doc_id, visual_index="error", stage="Ready", error=f"ColPali: {e}"[:300])
