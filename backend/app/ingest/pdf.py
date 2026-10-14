"""PDF ingestion.

  * Every page is rendered to PNG -> used by ColPali (visual retrieval) and sent
    to the LLM's vision so charts, scans, stamps and complex layouts are "read"
    exactly as a human sees them.
  * Scanned / image-only pages (no text layer) are OCR'd with DeepSeek-OCR-2 so
    they also become keyword/semantic-searchable and their tables reach DuckDB.
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

from ..config import OCR_MIN_CHARS, PAGE_DPI
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
        n, md_pages, tables, taken = _parse_pdf(doc_id, path, progress)
    # OCR runs outside the MuPDF lock: a long scan must not block other uploads
    ocr_pages = _ocr_scanned_pages(doc_id, n, md_pages, tables, taken, progress)
    chunks, parents, scanned_pages = _chunk_pages(doc_id, n, md_pages, ocr_pages, progress)
    info = {"n_pages": n, "scanned_pages": scanned_pages, "ocr_pages": sorted(ocr_pages)}
    return chunks, parents, tables, info


def _parse_pdf(doc_id: str, path: Path, progress: Callable[[str, float], None]):
    doc = pymupdf.open(str(path))
    n = doc.page_count
    pages_dir = store.pages_dir(doc_id)
    pages_dir.mkdir(exist_ok=True)

    progress("Rendering pages", 0.05)
    zoom = PAGE_DPI / 72
    for i, page in enumerate(doc):
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(store.page_path(doc_id, i + 1)))
        if i % 5 == 0:
            progress(f"Rendering pages ({i + 1}/{n})", 0.05 + 0.2 * (i + 1) / n)

    progress("Extracting layout & text", 0.27)
    md_pages = _page_markdown(path)
    tables: list[dict[str, Any]] = []
    taken: set[str] = set()
    for i, page in enumerate(doc):
        pno = i + 1
        md_pages[pno] = md_pages.get(pno) or page.get_text("text") or ""
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
    doc.close()
    return n, md_pages, tables, taken


def _ocr_scanned_pages(doc_id: str, n: int, md_pages: dict[int, str], tables: list[dict[str, Any]],
                       taken: set[str], progress: Callable[[str, float], None]) -> set[int]:
    """DeepSeek-OCR-2 on pages whose text layer is (nearly) empty; their text + tables replace the blanks."""
    from .ocr import ocr_available, ocr_page

    scanned = [p for p in range(1, n + 1) if len(md_pages.get(p, "").strip()) < OCR_MIN_CHARS]
    if not scanned or not ocr_available():
        return set()
    done: set[int] = set()
    for k, pno in enumerate(scanned):
        progress(f"OCR page {pno} ({k + 1}/{len(scanned)} scanned)", 0.3 + 0.3 * k / len(scanned))
        try:
            res = ocr_page(store.page_path(doc_id, pno))
        except Exception as e:
            print(f"[ocr] page {pno} failed: {e}")
            continue
        if not res.markdown.strip():
            continue
        md_pages[pno] = res.markdown
        done.add(pno)
        for t_i, df in enumerate(res.tables):
            name = sanitize(f"p{pno}_ocr_table{t_i + 1}", taken)
            tables.append({"name": name, "df": df, "source": f"page {pno} (OCR)",
                           "title": f"Table {t_i + 1} on page {pno} (OCR)", "page": pno})
    return done


def _chunk_pages(doc_id: str, n: int, md_pages: dict[int, str], ocr_pages: set[int],
                 progress: Callable[[str, float], None]):
    chunks: list[Chunk] = []
    parents: dict[str, str] = {}
    scanned_pages = []
    for pno in range(1, n + 1):
        md = md_pages.get(pno, "")
        if len(md.strip()) < 20:
            scanned_pages.append(pno)
        # --- chunks (children) + page parent ----------------------------------
        parent_id = f"{doc_id}:pp{pno}"
        if pno in ocr_pages:  # tell the LLM this text came from OCR, so it double-checks against the image
            parents[parent_id] = (f"[Page {pno} is a scan; text below was read by OCR (DeepSeek-OCR-2). "
                                  f"Verify critical values against the page image.]\n{md}")[:PARENT_MAX]
        else:
            parents[parent_id] = md[:PARENT_MAX] if md.strip() else f"[Page {pno} has no extractable text -- see page image]"
        pieces = chunk_markdown(md) if md.strip() else []
        if not pieces:  # image-only page with no OCR text: placeholder so ColPali hits still map to a chunk
            pieces = [(None, "text", f"[Page {pno}: image-only content (scan, chart or figure). Visual retrieval indexes this page.]")]
        for section, kind, text in pieces:
            chunks.append(Chunk(
                id=uuid.uuid4().hex[:16], doc_id=doc_id, text=text, kind=kind, page=pno,
                section=section, parent_id=parent_id,
            ))
        progress(f"Chunking ({pno}/{n})", 0.6 + 0.02 * pno / n)
    return chunks, parents, scanned_pages
