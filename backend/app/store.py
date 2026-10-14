"""Document registry + on-disk persistence.

Layout per document (data/docs/<doc_id>/):
    source.<ext>        original upload
    meta.json           DocMeta
    chunks.json         child chunks (retrieval units) + parents (context units)
    dense.npy           dense embeddings of child chunks
    pages/0001.png      rendered page images (PDF)
    colpali.pt          ColPali multi-vector page embeddings
    tables.duckdb       every table found (Excel sheets, PDF tables) for Text-to-SQL
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import DOCS_DIR


@dataclass
class Chunk:
    id: str
    doc_id: str
    text: str                      # content shown to the LLM
    kind: str = "text"             # text | table | sheet_summary | rows
    page: int | None = None        # 1-based page number (PDF)
    sheet: str | None = None       # sheet name (Excel)
    section: str | None = None     # nearest heading / table title
    parent_id: str | None = None   # small-to-big: parent context unit
    context: str = ""              # contextual header prepended at index time

    def index_text(self) -> str:
        return f"{self.context}\n{self.text}" if self.context else self.text

    def location(self) -> str:
        if self.page:
            return f"p.{self.page}"
        if self.sheet:
            return f"sheet '{self.sheet}'"
        return ""


@dataclass
class DocMeta:
    id: str
    filename: str
    kind: str                       # pdf | excel | csv
    status: str = "processing"      # processing | ready | error
    stage: str = "queued"
    progress: float = 0.0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    n_pages: int = 0
    n_chunks: int = 0
    sheets: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    visual_index: str = "disabled"  # disabled | pending | building | ready | error
    extraction_status: str = "pending"  # pending | running | ready | error | skipped
    extraction: dict[str, Any] | None = None
    techniques: list[str] = field(default_factory=list)


class Store:
    def __init__(self, root: Path = DOCS_DIR):
        self.root = root
        self._lock = threading.RLock()
        self.docs: dict[str, DocMeta] = {}
        self.chunks: dict[str, list[Chunk]] = {}
        self.parents: dict[str, dict[str, str]] = {}
        self._load()

    # ---- paths -----------------------------------------------------------
    def dir(self, doc_id: str) -> Path:
        return self.root / doc_id

    def pages_dir(self, doc_id: str) -> Path:
        return self.dir(doc_id) / "pages"

    def page_path(self, doc_id: str, page: int) -> Path:
        return self.pages_dir(doc_id) / f"{page:04d}.png"

    def tables_db(self, doc_id: str) -> Path:
        return self.dir(doc_id) / "tables.duckdb"

    def source_path(self, doc_id: str) -> Path:
        meta = self.docs[doc_id]
        return self.dir(doc_id) / f"source{Path(meta.filename).suffix.lower()}"

    # ---- lifecycle -------------------------------------------------------
    def create(self, filename: str, kind: str, data: bytes) -> DocMeta:
        doc_id = uuid.uuid4().hex[:12]
        d = self.dir(doc_id)
        d.mkdir(parents=True)
        (d / f"source{Path(filename).suffix.lower()}").write_bytes(data)
        meta = DocMeta(id=doc_id, filename=filename, kind=kind)
        with self._lock:
            self.docs[doc_id] = meta
            self.chunks[doc_id] = []
            self.parents[doc_id] = {}
        self.save_meta(doc_id)
        return meta

    def update(self, doc_id: str, **kw: Any) -> None:
        with self._lock:
            meta = self.docs.get(doc_id)
            if not meta:
                return
            for k, v in kw.items():
                setattr(meta, k, v)
        self.save_meta(doc_id)

    def save_meta(self, doc_id: str) -> None:
        with self._lock:
            meta = self.docs.get(doc_id)
            if not meta or not self.dir(doc_id).exists():
                return
            tmp = self.dir(doc_id) / "meta.json.tmp"
            tmp.write_text(json.dumps(asdict(meta), default=str))
            tmp.replace(self.dir(doc_id) / "meta.json")

    def set_chunks(self, doc_id: str, chunks: list[Chunk], parents: dict[str, str]) -> None:
        with self._lock:
            self.chunks[doc_id] = chunks
            self.parents[doc_id] = parents
        payload = {"chunks": [asdict(c) for c in chunks], "parents": parents}
        (self.dir(doc_id) / "chunks.json").write_text(json.dumps(payload))

    def delete(self, doc_id: str) -> None:
        with self._lock:
            self.docs.pop(doc_id, None)
            self.chunks.pop(doc_id, None)
            self.parents.pop(doc_id, None)
        shutil.rmtree(self.dir(doc_id), ignore_errors=True)

    def ready_ids(self, doc_ids: list[str] | None = None) -> list[str]:
        with self._lock:
            ids = doc_ids or list(self.docs)
            return [i for i in ids if i in self.docs and self.docs[i].status == "ready"]

    def parent_text(self, chunk: Chunk) -> str:
        if chunk.parent_id:
            return self.parents.get(chunk.doc_id, {}).get(chunk.parent_id, chunk.text)
        return chunk.text

    def _load(self) -> None:
        for d in sorted(self.root.glob("*/meta.json")):
            try:
                raw = json.loads(d.read_text())
                meta = DocMeta(**{k: v for k, v in raw.items() if k in DocMeta.__dataclass_fields__})
                if meta.status == "processing":  # interrupted by a restart
                    meta.status, meta.error = "error", "Processing was interrupted. Please re-upload."
                if meta.visual_index in {"pending", "building"}:
                    meta.visual_index = "error"
                if meta.extraction_status in {"pending", "running"} and meta.extraction is None:
                    meta.extraction_status = "error"
                self.docs[meta.id] = meta
                cpath = d.parent / "chunks.json"
                if cpath.exists():
                    payload = json.loads(cpath.read_text())
                    self.chunks[meta.id] = [Chunk(**c) for c in payload["chunks"]]
                    self.parents[meta.id] = payload.get("parents", {})
                else:
                    self.chunks[meta.id], self.parents[meta.id] = [], {}
            except Exception as e:  # corrupted entry: skip, don't crash the server
                print(f"[store] skipping {d.parent.name}: {e}")


store = Store()
