"""Contextual Retrieval (Anthropic, 2024): before indexing, a small model writes
a 1-2 sentence context situating each chunk within the whole document
("This chunk is from ACME's Q2 2024 10-Q, revenue section; prior quarter was
$314M"). That context is prepended to the chunk for BM25 + embeddings, which
sharply reduces failed retrievals for chunks that are ambiguous on their own.

The full document sits in a prompt-cached prefix, so each per-chunk call only
pays for the chunk itself. Opt-in via CONTEXTUAL_RETRIEVAL=true.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..config import FAST_MODEL
from ..store import Chunk
from .client import effort, get_client, response_text

MAX_CHUNKS = 400
DOC_CHARS = 300_000


def contextualize(doc_text: str, chunks: list[Chunk], progress: Callable[[float], None]) -> int:
    client = get_client()
    doc_block = {"type": "text", "text": f"<document>\n{doc_text[:DOC_CHARS]}\n</document>",
                 "cache_control": {"type": "ephemeral"}}
    targets = [c for c in chunks if c.kind in {"text", "table"}][:MAX_CHUNKS]
    done = 0

    def one(c: Chunk) -> None:
        nonlocal done
        try:
            resp = client.messages.create(
                model=FAST_MODEL,
                max_tokens=1024,
                output_config={"effort": effort("low")},
                messages=[{"role": "user", "content": [
                    doc_block,
                    {"type": "text", "text": f"<chunk>\n{c.text[:3000]}\n</chunk>\nGive a short (1-2 sentence) context "
                     "that situates this chunk within the overall document, to improve search retrieval of the "
                     "chunk. Mention entities, periods and the section it belongs to. Answer only with the context."},
                ]}],
            )
            ctx = response_text(resp).strip()
            if ctx:
                c.context = f"{c.context}\n{ctx}".strip()
        except Exception as e:
            print(f"[contextual] chunk {c.id}: {e}")
        done += 1
        if done % 10 == 0:
            progress(done / len(targets))

    if not targets:
        return 0
    one(targets[0])  # warm the prompt cache before fanning out
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, targets[1:]))
    return len(targets)
