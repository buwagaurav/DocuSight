"""Lazily-loaded local models: dense bi-encoder + cross-encoder reranker.

Dense embeddings default to Google's EmbeddingGemma 2 (sentence-transformers):
  * asymmetric prompts — queries use the `SearchQuery` prompt
    ("task: search result | query: ..."), passages use
    "title: <file · page · section> | text: <chunk>", so our contextual chunk
    header lands in the model's dedicated title slot
  * Matryoshka output: EMBED_DIM can be 768 / 512 / 256 / 128
  * 8K-token context, loaded text-only (vision/audio towers skipped)
  * bf16 on CUDA, fp32 elsewhere (fp16 silently yields NaNs for this model)

Any other EMBED_MODEL name is served by fastembed (ONNX), e.g. BAAI/bge-small-en-v1.5.
If EmbeddingGemma can't load, we fall back to bge automatically.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np

from ..config import EMBED_DIM, EMBED_MODEL, RERANK_ENABLED, RERANK_MODEL, ROOT
from .device import on_device

FALLBACK_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_lock = threading.Lock()
_backend: "_Embedder | None" = None
_reranker = None
_reranker_failed = False


def _norm(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    return m / np.clip(np.linalg.norm(m, axis=-1, keepdims=True), 1e-9, None)


class _Embedder:
    name: str
    dim: int

    def passages(self, texts: list[str], titles: list[str | None]) -> np.ndarray: ...
    def queries(self, texts: list[str]) -> np.ndarray: ...


class _GemmaEmbedder(_Embedder):
    def __init__(self, name: str, dim: int | None):
        import torch
        from sentence_transformers import SentenceTransformer

        if torch.cuda.is_available():
            device, dtype = "cuda", (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32)
        elif torch.backends.mps.is_available():
            device, dtype = "mps", torch.float32
        else:
            device, dtype = "cpu", torch.float32
        # Load on CPU, then move: transformers 5.x's threaded loader can segfault when
        # materialising weights straight onto MPS.
        kwargs = dict(device="cpu", model_kwargs={"torch_dtype": dtype}, truncate_dim=dim or None)
        try:  # text-only: skip the vision + audio encoders (740M -> 270M)
            self.model = SentenceTransformer(name, config_kwargs={"vision_config": None, "audio_config": None}, **kwargs)
        except Exception:
            self.model = SentenceTransformer(name, **kwargs)
        self.model.to(device)
        self.model.max_seq_length = min(self.model.max_seq_length or 2048, 2048)  # chunks are ~1k chars
        self.name = name
        get_dim = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim = int(get_dim())
        self.prompts = getattr(self.model, "prompts", {}) or {}

    def passages(self, texts: list[str], titles: list[str | None]) -> np.ndarray:
        formatted = [f"title: {(t or 'none').replace('|', '/')} | text: {x}" for x, t in zip(texts, titles)]
        return _norm(self.model.encode(formatted, batch_size=16, normalize_embeddings=True, convert_to_numpy=True))

    def queries(self, texts: list[str]) -> np.ndarray:
        kw = {"prompt_name": "SearchQuery"} if "SearchQuery" in self.prompts else {"prompt": "task: search result | query: "}
        return _norm(self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, **kw))


class _FastEmbedder(_Embedder):
    def __init__(self, name: str):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=name)
        self.name = name
        self.dim = int(next(iter(self.model.query_embed(["probe"]))).shape[-1])

    def passages(self, texts: list[str], titles: list[str | None]) -> np.ndarray:
        joined = [f"{t}\n{x}" if t else x for x, t in zip(texts, titles)]
        return _norm(np.stack(list(self.model.passage_embed(joined, batch_size=32))))

    def queries(self, texts: list[str]) -> np.ndarray:
        return _norm(np.stack(list(self.model.query_embed(texts))))


def _local_model_dir(spec: str) -> str | None:
    """A model given as a folder path; relative paths are resolved from the project root."""
    p = Path(os.path.expanduser(spec))
    for cand in ([p] if p.is_absolute() else [ROOT / p, p]):
        if cand.is_dir():
            return str(cand.resolve())
    return None


def embedder() -> _Embedder:
    return on_device(_embedder)


def _embedder() -> _Embedder:
    global _backend
    with _lock:
        if _backend is None:
            local_dir = _local_model_dir(EMBED_MODEL)  # e.g. ./models/embeddinggemma-2 (offline copy)
            if local_dir or "embeddinggemma" in EMBED_MODEL.lower() or EMBED_MODEL.startswith("sentence-transformers/"):
                try:
                    _backend = _GemmaEmbedder(local_dir or EMBED_MODEL, EMBED_DIM)
                    print(f"[embed] {EMBED_MODEL} ({_backend.dim}d)")
                except Exception as e:
                    print(f"[embed] could not load {EMBED_MODEL} ({e}); falling back to {FALLBACK_EMBED_MODEL}")
                    _backend = _FastEmbedder(FALLBACK_EMBED_MODEL)
            else:
                _backend = _FastEmbedder(EMBED_MODEL)
    return _backend


def embed_passages(texts: list[str], titles: list[str | None] | None = None) -> np.ndarray:
    e = embedder()
    if not texts:
        return np.zeros((0, e.dim), dtype=np.float32)
    return on_device(e.passages, texts, titles or [None] * len(texts))


def embed_queries(texts: list[str]) -> np.ndarray:
    return on_device(embedder().queries, texts)


def loaded_embedding_info() -> str:
    """For status display — never triggers a (slow) model load."""
    return f"{_backend.name} ({_backend.dim}d)" if _backend else f"{EMBED_MODEL} (loading)"


def embedding_signature() -> str:
    e = embedder()
    return f"{e.name}:{e.dim}"


def rerank(query: str, docs: list[str]) -> list[float] | None:
    """Cross-encoder relevance scores, or None if the reranker is unavailable."""
    global _reranker, _reranker_failed
    if not RERANK_ENABLED or _reranker_failed or not docs:
        return None
    try:
        with _lock:
            if _reranker is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                _reranker = TextCrossEncoder(model_name=RERANK_MODEL)
        return [float(s) for s in _reranker.rerank(query, docs)]
    except Exception as e:
        print(f"[rerank] disabled: {e}")
        _reranker_failed = True
        return None
