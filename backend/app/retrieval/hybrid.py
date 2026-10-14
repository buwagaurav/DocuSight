"""Hybrid multimodal retriever.

Pipeline for one search call:

  queries (multi-query / RAG-Fusion, written by the agent)
     + optional HyDE hypothetical answer
        │
        ├── BM25 (lexical: exact ids, codes, names, numbers)
        ├── Dense bi-encoder (semantic: paraphrases, synonyms)
        └── ColPali MaxSim over page images (visual: charts, scans, layouts)
        │
  Reciprocal Rank Fusion  ─►  cross-encoder rerank  ─►  MMR diversity
        │
  small-to-big expansion (child chunk hit → parent page/table context)
        │
  text sources + best page images (for Claude vision)
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

from ..config import MAX_IMAGES_PER_TURN
from ..store import Chunk, store
from .colpali import colpali
from .models import embed_passages, embed_queries, embedder, embedding_signature, rerank

_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
_STOP = set("""a an the and or of to in on for with by at from is are was were be been this that these those it its
as into than then there their what which who whom how when where why do does did can could should would will shall may
might must not no yes per vs via about over under all any each""".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Hit:
    chunk: Chunk
    score: float
    signals: dict[str, int] = field(default_factory=dict)  # retriever -> rank


@dataclass
class SearchResult:
    hits: list[Hit]
    pages: list[tuple[str, int, float]]           # (doc_id, page, colpali score)
    trace: dict[str, Any]


class _Corpus:
    """BM25 + dense matrix over the chunks of a set of documents (cached)."""

    def __init__(self, doc_ids: tuple[str, ...]):
        self.chunks: list[Chunk] = []
        mats = []
        sig, dim = embedding_signature(), embedder().dim
        for d in doc_ids:
            cs = store.chunks.get(d, [])
            self.chunks.extend(cs)
            path, sig_path = store.dir(d) / "dense.npy", store.dir(d) / "dense.sig"
            if path.exists() and sig_path.exists() and sig_path.read_text() == sig:
                m = np.load(path)
                if m.shape == (len(cs), dim):
                    mats.append(m)
                    continue
            # index built with another embedding model (or missing): re-embed once and persist
            m = embed_passages([c.text for c in cs], [c.context for c in cs]) if cs else np.zeros((0, dim), np.float32)
            if cs and store.dir(d).exists():
                np.save(path, m)
                sig_path.write_text(sig)
            mats.append(m)
        self.dense = np.concatenate(mats) if mats else np.zeros((0, dim), np.float32)
        self.bm25 = BM25Okapi([tokenize(c.index_text()) or ["_"] for c in self.chunks]) if self.chunks else None
        self.by_page: dict[tuple[str, int], list[int]] = {}
        for i, c in enumerate(self.chunks):
            if c.page:
                self.by_page.setdefault((c.doc_id, c.page), []).append(i)


class Retriever:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple, _Corpus] = {}

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def _corpus(self, doc_ids: list[str]) -> _Corpus:
        key = tuple(sorted(doc_ids)) + tuple(len(store.chunks.get(d, [])) for d in sorted(doc_ids))
        with self._lock:
            if key not in self._cache:
                if len(self._cache) > 8:
                    self._cache.clear()
                self._cache[key] = _Corpus(tuple(sorted(doc_ids)))
            return self._cache[key]

    def search(self, queries: list[str], doc_ids: list[str], k: int = 8, hyde: str | None = None,
               use_colpali: bool = True, use_rerank: bool = True) -> SearchResult:
        queries = [q.strip() for q in queries if q and q.strip()][:5] or [""]
        corpus = self._corpus(doc_ids)
        n = len(corpus.chunks)
        trace: dict[str, Any] = {"queries": queries, "hyde": bool(hyde), "candidates": n}
        if n == 0:
            return SearchResult([], [], trace)

        rank_lists: list[tuple[str, list[int], float]] = []  # (signal, ranked chunk idx, weight)
        depth = min(n, 50)

        # 1) lexical
        for q in queries:
            toks = tokenize(q)
            if toks and corpus.bm25 is not None:
                s = corpus.bm25.get_scores(toks)
                order = [int(i) for i in np.argsort(-s)[:depth] if s[i] > 0]
                rank_lists.append(("bm25", order, 1.0))
        # 2) dense (+ HyDE: embed a hypothetical answer as a *passage*)
        q_emb = embed_queries(queries)
        if hyde:  # HyDE: the hypothetical answer is embedded as a *document*, matching passage space
            q_emb = np.vstack([q_emb, embed_passages([hyde])])
        sims = q_emb @ corpus.dense.T
        for row in sims:
            rank_lists.append(("dense", [int(i) for i in np.argsort(-row)[:depth]], 1.0))
        trace["dense_top_sim"] = round(float(sims.max()), 3)

        # 3) visual (ColPali) — page hits fan out to the chunks on that page
        pages: list[tuple[str, int, float]] = []
        if use_colpali and colpali.available:
            try:
                pages = colpali.search(queries[:3], doc_ids, k=6)
                order = [i for d, p, _ in pages for i in corpus.by_page.get((d, p), [])]
                if order:
                    rank_lists.append(("colpali", order, 1.2))
                trace["colpali_pages"] = [{"doc_id": d, "page": p, "score": round(s, 3)} for d, p, s in pages]
            except Exception as e:
                trace["colpali_error"] = str(e)[:200]

        # 4) Reciprocal Rank Fusion
        fused: dict[int, float] = {}
        signals: dict[int, dict[str, int]] = {}
        for name, order, w in rank_lists:
            for r, idx in enumerate(order):
                fused[idx] = fused.get(idx, 0.0) + w / (60 + r + 1)
                sig = signals.setdefault(idx, {})
                sig[name] = min(sig.get(name, 10**6), r + 1)
        cands = sorted(fused, key=fused.get, reverse=True)[:30]
        trace["fusion"] = "RRF"

        # 5) cross-encoder rerank (precision)
        scores = {i: fused[i] for i in cands}
        if use_rerank and cands:
            ce = rerank(queries[0], [corpus.chunks[i].index_text()[:2000] for i in cands])
            if ce is not None:
                # blend: CE dominates, RRF breaks ties / keeps visual hits alive
                lo, hi = min(ce), max(ce)
                for i, s in zip(cands, ce):
                    scores[i] = 0.8 * ((s - lo) / (hi - lo + 1e-9)) + 0.2 * (fused[i] / max(fused.values()))
                    signals[i]["rerank"] = 1
                trace["reranked"] = True
        ordered = sorted(cands, key=scores.get, reverse=True)

        # 6) MMR-style diversity: cap hits per parent, penalise near-duplicates
        picked: list[int] = []
        per_parent: dict[str, int] = {}
        for i in ordered:
            c = corpus.chunks[i]
            key = c.parent_id or c.id
            if per_parent.get(key, 0) >= 2:
                continue
            if picked and float(np.max(corpus.dense[picked] @ corpus.dense[i])) > 0.95:
                continue
            picked.append(i)
            per_parent[key] = per_parent.get(key, 0) + 1
            if len(picked) >= k:
                break

        hits = [Hit(corpus.chunks[i], round(float(scores[i]), 4), signals.get(i, {})) for i in picked]

        # 7) page images for the vision model: ColPali's best pages + pages of top text hits
        seen, img_pages = set(), []
        for d, p, s in pages[:2]:
            if (d, p) not in seen:
                seen.add((d, p)); img_pages.append((d, p, s))
        for h in hits:
            if h.chunk.page and (h.chunk.doc_id, h.chunk.page) not in seen:
                seen.add((h.chunk.doc_id, h.chunk.page)); img_pages.append((h.chunk.doc_id, h.chunk.page, h.score))
        trace["signals_used"] = sorted({name for name, _, _ in rank_lists})
        return SearchResult(hits, img_pages[:MAX_IMAGES_PER_TURN], trace)


retriever = Retriever()
