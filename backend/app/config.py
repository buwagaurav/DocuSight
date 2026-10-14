"""Central configuration, read from environment / .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "backend" / ".env")
for _k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    if os.environ.get(_k) == "":  # an empty placeholder would shadow other credential sources
        del os.environ[_k]


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on", "auto"}


DATA_DIR = Path(os.getenv("DOCUSIGHT_DATA_DIR", ROOT / "data")).resolve()
DOCS_DIR = DATA_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

# --- LLM -------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5-5")          # answering + extraction
FAST_MODEL = os.getenv("FAST_MODEL", "claude-haiku-5-5")       # contextual chunk headers
MAX_IMAGES_PER_TURN = int(os.getenv("MAX_IMAGES_PER_TURN", "4"))
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "6"))

# --- Retrieval ---------------------------------------------------------------
EMBED_MODEL = os.getenv("EMBED_MODEL", "google/embeddinggemma-2")   # or BAAI/bge-small-en-v1.5 (fastembed)
EMBED_DIM = int(os.getenv("EMBED_DIM", "768")) or None                # Matryoshka: 768 / 512 / 256 / 128
RERANK_MODEL = os.getenv("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
RERANK_ENABLED = _bool("RERANK_ENABLED", "true")
CONTEXTUAL_RETRIEVAL = _bool("CONTEXTUAL_RETRIEVAL", "false")  # LLM-written chunk context (costs tokens)
SEMANTIC_CACHE_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.97"))

# --- ColPali -------------------------------------------------------------------
# "auto" = enabled when colpali-engine + torch are importable.
COLPALI_ENABLED = _bool("COLPALI_ENABLED", "auto")
COLPALI_MODEL = os.getenv("COLPALI_MODEL", "vidore/colpali-v1.3")
COLPALI_BATCH = int(os.getenv("COLPALI_BATCH", "2"))

# --- Ingestion -----------------------------------------------------------------
PAGE_DPI = int(os.getenv("PAGE_DPI", "130"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
ROWS_PER_CHUNK = int(os.getenv("ROWS_PER_CHUNK", "25"))

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
