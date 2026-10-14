"""DocuSight API."""
from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import pipeline
from .config import COLPALI_MODEL, CORS_ORIGINS, LLM_MODEL, RERANK_MODEL
from .llm.agent import cache, run_agent
from .llm.client import llm_available
from .retrieval.colpali import colpali
from .retrieval.hybrid import retriever
from .retrieval.models import embedder, loaded_embedding_info
from .store import store
from .tables import preview, run_sql

@asynccontextmanager
async def lifespan(_: FastAPI):
    # load the embedding model in the background so the first upload/search isn't slow
    threading.Thread(target=embedder, daemon=True).start()
    yield


app = FastAPI(title="DocuSight", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

MAX_UPLOAD = 200 * 1024 * 1024




def _doc(doc_id: str):
    meta = store.docs.get(doc_id)
    if not meta:
        raise HTTPException(404, "Document not found")
    return meta


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "llm": {"available": llm_available(), "model": LLM_MODEL},
        "colpali": {"available": colpali.available, "model": COLPALI_MODEL, "error": colpali.error},
        "embedding_model": loaded_embedding_info(),
        "rerank_model": RERANK_MODEL,
    }


@app.get("/api/documents")
def list_documents() -> list[dict[str, Any]]:
    return [asdict(m) for m in sorted(store.docs.values(), key=lambda m: m.created_at, reverse=True)]


@app.post("/api/documents")
async def upload(files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
    out = []
    for f in files:
        name = Path(f.filename or "upload").name
        ext = Path(name).suffix.lower()
        if ext in pipeline.PDF_EXT:
            kind = "pdf"
        elif ext in pipeline.EXCEL_EXT:
            kind = "csv" if ext in {".csv", ".tsv"} else "excel"
        else:
            raise HTTPException(400, f"Unsupported file type '{ext}'. Upload PDF, XLSX/XLS/XLSM/ODS or CSV.")
        data = await f.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"{name} is larger than 200 MB")
        if not data:
            raise HTTPException(400, f"{name} is empty")
        meta = store.create(name, kind, data)
        pipeline.submit(meta.id)
        out.append(asdict(meta))
    return out


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str) -> dict[str, Any]:
    return asdict(_doc(doc_id))


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str) -> dict[str, bool]:
    _doc(doc_id)
    store.delete(doc_id)
    colpali.forget(doc_id)
    retriever.invalidate()
    cache.drop_doc(doc_id)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/extract")
def re_extract(doc_id: str) -> dict[str, bool]:
    if _doc(doc_id).status != "ready":
        raise HTTPException(409, "Document is not ready yet")
    store.update(doc_id, extraction_status="pending")
    pipeline.submit_extraction(doc_id)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/visual-index")
def rebuild_visual(doc_id: str) -> dict[str, bool]:
    meta = _doc(doc_id)
    if meta.kind != "pdf" or not colpali.available:
        raise HTTPException(400, "Visual index is only available for PDFs with ColPali installed")
    pipeline.submit_visual(doc_id)
    return {"ok": True}


@app.get("/api/documents/{doc_id}/file")
def original_file(doc_id: str):
    meta = _doc(doc_id)
    return FileResponse(store.source_path(doc_id), filename=meta.filename)


@app.get("/api/documents/{doc_id}/pages/{page}")
def page_image(doc_id: str, page: int):
    _doc(doc_id)
    path = store.page_path(doc_id, page)
    if not path.exists():
        raise HTTPException(404, "Page not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/documents/{doc_id}/sheets/{index}")
def sheet_grid(doc_id: str, index: int) -> dict[str, Any]:
    _doc(doc_id)
    path = store.dir(doc_id) / "sheets" / f"{index}.json"
    if not path.exists():
        raise HTTPException(404, "Sheet not found")
    return json.loads(path.read_text())


@app.get("/api/documents/{doc_id}/tables/{table}")
def table_rows(doc_id: str, table: str, limit: int = 500, offset: int = 0) -> dict[str, Any]:
    _doc(doc_id)
    if table not in {t["name"] for t in store.docs[doc_id].tables}:
        raise HTTPException(404, "Table not found")
    return preview(store.tables_db(doc_id), table, min(limit, 5000), max(offset, 0))


class SqlIn(BaseModel):
    sql: str


@app.post("/api/documents/{doc_id}/sql")
def sql_console(doc_id: str, body: SqlIn) -> dict[str, Any]:
    _doc(doc_id)
    try:
        return run_sql(store.tables_db(doc_id), body.sql, limit=1000)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/documents/{doc_id}/chunks")
def chunks(doc_id: str, limit: int = 200) -> list[dict[str, Any]]:
    _doc(doc_id)
    return [asdict(c) for c in store.chunks.get(doc_id, [])[:limit]]


class SearchIn(BaseModel):
    query: str
    doc_ids: list[str] = Field(default_factory=list)
    k: int = 8
    colpali: bool = True
    rerank: bool = True


@app.post("/api/search")
def search(body: SearchIn) -> dict[str, Any]:
    """Retrieval playground: raw hybrid results with per-signal ranks."""
    ids = store.ready_ids(body.doc_ids or None)
    res = retriever.search([body.query], ids, k=min(body.k, 20), use_colpali=body.colpali, use_rerank=body.rerank)
    return {
        "hits": [{**asdict(h.chunk), "score": h.score, "signals": h.signals,
                  "filename": store.docs[h.chunk.doc_id].filename} for h in res.hits],
        "pages": [{"doc_id": d, "page": p, "score": round(s, 4), "filename": store.docs[d].filename} for d, p, s in res.pages],
        "trace": res.trace,
    }


class ChatIn(BaseModel):
    question: str
    history: list[dict[str, str]] = Field(default_factory=list)
    doc_ids: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/chat")
def chat(body: ChatIn):
    def events():
        try:
            for ev in run_agent(body.question, body.history, body.doc_ids or list(store.docs), body.options):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:  # never break the stream silently
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
