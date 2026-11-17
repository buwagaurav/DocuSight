"""Deterministic annotation of original PDFs from persisted evidence geometry."""
from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

import pymupdf

from ..config import EXTRACTION_PROJECTS_DIR
from ..store import store
from ..ingest.pdf import _MUPDF_LOCK as _PDF_LOCK



def _file_hash(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_annotated_pdf(project: dict[str, Any], *, observation_id: str | None = None,
                         field_id: str | None = None, page_number: int | None = None) -> Path:
    """Render selected saved evidence to a separate PDF in the project render folder."""
    observations = [r for r in project.get("observations", []) if not observation_id or r.get("id") == observation_id]
    if observation_id and not observations:
        raise KeyError("Observation not found")
    selected: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for row in observations:
        for fid, cell in row.get("fields", {}).items():
            if field_id and fid != field_id:
                continue
            label = next((f.get("name", fid) for f in project.get("schema", {}).get("fields", []) if f.get("id") == fid), fid)
            for source in cell.get("evidence", []):
                if page_number and source.get("page") != page_number:
                    continue
                if source.get("document_id") in store.docs and source.get("coordinates"):
                    selected.append((source, cell, label))
    if field_id and not any(any(fid == field_id for fid in r.get("fields", {})) for r in observations):
        raise KeyError("Field not found")
    if not selected:
        raise ValueError("No located evidence matches the requested render selection")

    docs = {source["document_id"] for source, _, _ in selected}
    if len(docs) != 1:
        raise ValueError("Render one source document at a time")
    doc_id = next(iter(docs))
    original = store.source_path(doc_id)
    if not original.is_file():
        raise FileNotFoundError("Original PDF is missing")
    file_hash = _file_hash(original)
    # Content-addressed deterministic output: rerendering identical evidence is idempotent.
    selection = [(s.get("page"), s.get("text"), s.get("coordinates"), label, cell.get("review_status"))
                 for s, cell, label in selected]
    digest = hashlib.sha256(repr((project["id"], selection, file_hash)).encode()).hexdigest()[:20]
    out_dir = EXTRACTION_PROJECTS_DIR / "rendered" / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{digest}.pdf"
    if output.is_file():
        return output
    tmp = out_dir / f".{uuid.uuid4().hex}.tmp.pdf"
    try:
        with _PDF_LOCK:
            pdf = pymupdf.open(str(original))
            try:
                for source, cell, label in selected:
                    pno = source.get("page")
                    if not isinstance(pno, int) or pno < 1 or pno > pdf.page_count:
                        continue
                    page = pdf[pno - 1]
                    page_bounds = page.rect
                    for item in source["coordinates"]:
                        x, y, width, height = (item.get(k) for k in ("x", "y", "width", "height"))
                        if not all(isinstance(v, (int, float)) for v in (x, y, width, height)):
                            continue
                        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
                            continue
                        displayed = pymupdf.Rect(page_bounds.x0 + x * page_bounds.width,
                                                 page_bounds.y0 + y * page_bounds.height,
                                                 page_bounds.x0 + (x + width) * page_bounds.width,
                                                 page_bounds.y0 + (y + height) * page_bounds.height)
                        native = displayed * page.derotation_matrix if page.rotation else displayed
                        verified = source.get("coordinate_status") in {"NATIVE_TEXT_MATCHED", "NATIVE_WORD_MATCHED", "HUMAN_VERIFIED"}
                        fill = (1.0, 0.85, 0.1) if verified else (1.0, 0.3, 0.2)
                        page.draw_rect(native, color=(0.8, 0.05, 0.05), fill=fill,
                                       fill_opacity=0.25, width=1.4, overlay=True)
                        page.insert_text((native.x0, max(8, native.y0 - 3)), label[:50],
                                         fontsize=7, color=(0.65, 0.0, 0.0), overlay=True)
                pdf.save(str(tmp), garbage=4, deflate=True)
            finally:
                pdf.close()
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
    return output
