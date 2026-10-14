"""ColPali visual retrieval (late interaction over page images).

Instead of OCR -> chunk -> embed, ColPali embeds each *page image* into ~1k
patch vectors with a vision-language model. A query is embedded into token
vectors and scored with MaxSim (ColBERT-style late interaction):

    score(q, page) = sum_i  max_j  <q_i, d_j>

This retrieves pages by what they *look like* -- charts, tables, scanned
forms, infographics, handwriting -- which text pipelines miss entirely.

Supports the whole ColVision family via COLPALI_MODEL:
  vidore/colpali-v1.3 (default), vidore/colqwen2-v1.0, vidore/colqwen2.5-v0.2,
  vidore/colSmol-500M (lightweight), ...
"""
from __future__ import annotations

import threading
from typing import Callable

from ..config import COLPALI_BATCH, COLPALI_ENABLED, COLPALI_MODEL
from ..store import store
from .device import on_device


def _retie_meta_weights(model) -> None:
    """transformers 5 can leave tied output heads (e.g. PaliGemma's lm_head) on the
    `meta` device for ColVision wrappers. ColPali never uses that head, but `.to()`
    refuses to move meta tensors, so tie it back to the input embeddings (no copy)."""
    meta = [n for n, p in model.named_parameters() if p.is_meta]
    if not meta:
        return
    embeds = {tuple(p.shape): p for n, p in model.named_parameters() if n.endswith("embed_tokens.weight") and not p.is_meta}
    for name in meta:
        owner_name, attr = name.rsplit(".", 1)
        owner = model.get_submodule(owner_name)
        src = embeds.get(tuple(getattr(owner, attr).shape))
        if src is None:
            raise RuntimeError(f"ColPali weight {name} was not loaded")
        setattr(owner, attr, src)


class ColPaliIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._processor = None
        self._device = "cpu"
        self._cache: dict[str, list] = {}
        self.error: str | None = None

    # ------------------------------------------------------------------ setup
    @property
    def available(self) -> bool:
        if not COLPALI_ENABLED or self.error:
            return False
        try:
            import colpali_engine  # noqa: F401
            import torch  # noqa: F401

            return True
        except Exception:
            return False

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from colpali_engine import models as M

        name = COLPALI_MODEL.lower()
        if "colqwen2.5" in name or "colqwen2_5" in name:
            model_cls, proc_cls = M.ColQwen2_5, M.ColQwen2_5_Processor
        elif "colqwen3" in name:
            model_cls, proc_cls = M.ColQwen3, M.ColQwen3Processor
        elif "colqwen2" in name:
            model_cls, proc_cls = M.ColQwen2, M.ColQwen2Processor
        elif "colsmol" in name or "colidefics" in name:
            model_cls, proc_cls = M.ColIdefics3, M.ColIdefics3Processor
        else:
            model_cls, proc_cls = M.ColPali, M.ColPaliProcessor

        if torch.cuda.is_available():
            self._device, dtype = "cuda", torch.bfloat16
        elif torch.backends.mps.is_available():
            self._device, dtype = "mps", torch.bfloat16
        else:
            self._device, dtype = "cpu", torch.float32
        print(f"[colpali] loading {COLPALI_MODEL} on {self._device} (first run downloads the weights)")
        # Load on CPU, then move: transformers 5.x's threaded loader segfaults when
        # materialising weights straight onto MPS (device_map="mps").
        model = model_cls.from_pretrained(COLPALI_MODEL, torch_dtype=dtype, device_map="cpu").eval()
        _retie_meta_weights(model)
        self._model = model.to(self._device)
        self._processor = proc_cls.from_pretrained(COLPALI_MODEL)
        with torch.no_grad():  # warm up: MPS compiles kernels on the first query shape (~20s otherwise)
            self._model(**self._processor.process_queries(["warm up the query encoder"]).to(self._device))

    # --------------------------------------------------------------- indexing
    def index_document(self, doc_id: str, n_pages: int, progress: Callable[[float], None] | None = None) -> None:
        import torch
        from PIL import Image

        out: list = []
        for start in range(0, n_pages, COLPALI_BATCH):
            pages = range(start + 1, min(start + COLPALI_BATCH, n_pages) + 1)
            images = [Image.open(store.page_path(doc_id, p)).convert("RGB") for p in pages]
            # one device round-trip per batch, so chat queries can interleave with long indexing jobs
            out.extend(on_device(self._embed_images, images))
            if progress:
                progress(min(1.0, (start + COLPALI_BATCH) / n_pages))
        torch.save(out, store.dir(doc_id) / "colpali.pt")
        self._cache[doc_id] = out

    def _embed_images(self, images: list) -> list:
        import torch

        with self._lock:
            self._load()
            batch = self._processor.process_images(images).to(self._device)
            with torch.no_grad():
                emb = self._model(**batch)
            mask = batch.get("attention_mask")
            out = []
            for i in range(emb.shape[0]):
                e = emb[i]
                if mask is not None:  # drop padding vectors
                    e = e[mask[i].bool()]
                out.append(e.to("cpu", dtype=torch.float16))
            return out

    def _doc_embeddings(self, doc_id: str) -> list | None:
        if doc_id in self._cache:
            return self._cache[doc_id]
        path = store.dir(doc_id) / "colpali.pt"
        if not path.exists():
            return None
        import torch

        self._cache[doc_id] = torch.load(path)
        return self._cache[doc_id]

    def forget(self, doc_id: str) -> None:
        self._cache.pop(doc_id, None)

    # ---------------------------------------------------------------- search
    def search(self, queries: list[str], doc_ids: list[str], k: int = 6) -> list[tuple[str, int, float]]:
        """Return [(doc_id, page, score)] best pages for the queries (max over queries)."""
        targets = [d for d in doc_ids if store.docs.get(d) and store.docs[d].visual_index == "ready"]
        if not targets:
            return []
        qs = on_device(self._embed_queries, queries)
        return self._score(qs, targets, k)

    def _embed_queries(self, queries: list[str]) -> list:
        import torch

        with self._lock:
            self._load()
            batch = self._processor.process_queries(queries).to(self._device)
            with torch.no_grad():
                q_emb = self._model(**batch)
            q_mask = batch.get("attention_mask")
            qs = []
            for i in range(q_emb.shape[0]):
                e = q_emb[i]
                if q_mask is not None:
                    e = e[q_mask[i].bool()]
                qs.append(e.to("cpu", dtype=torch.float32))
        return qs

    def _score(self, qs: list, targets: list[str], k: int) -> list[tuple[str, int, float]]:
        import torch

        best: dict[tuple[str, int], float] = {}
        for doc_id in targets:
            pages = self._doc_embeddings(doc_id) or []
            for p_i, d in enumerate(pages):
                d32 = d.to(torch.float32)
                for q in qs:
                    s = float((q @ d32.T).max(dim=1).values.sum()) / max(1, q.shape[0])  # MaxSim, length-normalised
                    key = (doc_id, p_i + 1)
                    if s > best.get(key, float("-inf")):
                        best[key] = s
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [(d, p, s) for (d, p), s in ranked]


colpali = ColPaliIndex()
