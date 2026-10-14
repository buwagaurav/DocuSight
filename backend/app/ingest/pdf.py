"""PDF ingestion.

  * Every page is rendered to PNG -> used by ColPali (visual retrieval) and sent
    to Claude's vision so charts, scans, stamps and complex layouts are "read"
    exactly as a human sees them -- no OCR pipeline needed.
  * Layout-aware markdown per page (pymupdf4llm) keeps headings and tables.
  * Native table detection (PyMuPDF find_tables) -> DataFrames -> DuckDB.
  * Small-to-big: each page is a parent; its chunks are children.
"""
from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

import pymupdf

from ..config import PAGE_DPI
from ..store import Chunk, store
from ..tables import clean_columns, coerce_types, sanitize
from .chunking import chunk_markdown

PARENT_MAX = 6000
# MuPDF keeps global state and is not thread-safe: serialise all PDF parsing.
_MUPDF_LOCK = threading.Lock()


def _page_markdown(path: Path) -> dict[int, str]:
    try:
        import pymupdf4llm

        pages = pymupdf4llm.to_markdown(str(path), page_chunks=True, show_progress=False)
        out = {}
        for i, p in enumerate(pages):
            meta = p.get("metadata", {}) if isinstance(p, dict) else {}
            num = int(meta.get("page_number") or meta.get("page") or i + 1)
            text = p.get("text", "") if isinstance(p, dict) else str(p)
            text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
            out[num] = re.sub(r"<br\s*/?>", " ", text)
        return out
    except Exception as e:  # pymupdf4llm is best-effort; plain text fallback below
        print(f"[pdf] pymupdf4llm failed, falling back to plain text: {e}")
        return {}


def ingest_pdf(doc_id: str, path: Path, progress: Callable[[str, float], None]):
    with _MUPDF_LOCK:
        return _ingest_pdf(doc_id, path, progress)


def _ingest_pdf(doc_id: str, path: Path, progress: Callable[[str, float], None]):
    doc = pymupdf.open(str(path))
    n = doc.page_count
    pages_dir = store.pages_dir(doc_id)
    pages_dir.mkdir(exist_ok=True)

    progress("Rendering pages", 0.05)
    zoom = PAGE_DPI / 72
    for i, page in enumerate(doc):
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(store.page_path(doc_id, i + 1)))
        if i % 5 == 0:
            progress(f"Rendering pages ({i + 1}/{n})", 0.05 + 0.25 * (i + 1) / n)

    progress("Extracting layout & text", 0.32)
    md_pages = _page_markdown(path)

    chunks: list[Chunk] = []
    parents: dict[str, str] = {}
    tables: list[dict[str, Any]] = []
    taken: set[str] = set()
    scanned_pages = []

    for i, page in enumerate(doc):
        pno = i + 1
        md = md_pages.get(pno) or page.get_text("text") or ""
        if len(md.strip()) < 20:
            scanned_pages.append(pno)
        # --- native tables -> DuckDB --------------------------------------
        try:
            for t_i, tab in enumerate(page.find_tables().tables):
                df = tab.to_pandas()
                if df.shape[0] < 1 or df.shape[1] < 2:
                    continue
                df.columns = clean_columns(list(df.columns))
                df = coerce_types(df)
                name = sanitize(f"p{pno}_table{t_i + 1}", taken)
                tables.append({
                    "name": name, "df": df, "source": f"page {pno}",
                    "title": f"Table {t_i + 1} on page {pno}", "page": pno,
                })
        except Exception as e:
            print(f"[pdf] table detection failed on page {pno}: {e}")

        # --- chunks (children) + page parent ----------------------------------
        parent_id = f"{doc_id}:pp{pno}"
        parents[parent_id] = md[:PARENT_MAX] if md.strip() else f"[Page {pno} has no extractable text -- see page image]"
        pieces = chunk_markdown(md) if md.strip() else []
        if not pieces:  # scanned / image-only page: placeholder so ColPali hits still map to a chunk
            pieces = [(None, "text", f"[Page {pno}: image-only content (scan, chart or figure). Visual retrieval indexes this page.]")]
        for section, kind, text in pieces:
            chunks.append(Chunk(
                id=uuid.uuid4().hex[:16], doc_id=doc_id, text=text, kind=kind, page=pno,
                section=section, parent_id=parent_id,
            ))
        progress(f"Chunking ({pno}/{n})", 0.35 + 0.25 * pno / n)

    doc.close()
    info = {"n_pages": n, "scanned_pages": scanned_pages}
    return chunks, parents, tables, info
