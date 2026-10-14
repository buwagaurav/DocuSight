"""Structure-aware chunking.

Techniques:
  * Markdown/heading-aware splitting: chunks never straddle a section boundary,
    and the nearest heading travels with every chunk as metadata.
  * Table-preserving: markdown tables are kept whole (or split by rows with the
    header repeated) so a retrieved chunk is always a readable table.
  * Recursive fallback (paragraph -> sentence -> hard cut) with overlap.
"""
from __future__ import annotations

import re

from ..config import CHUNK_OVERLAP, CHUNK_SIZE

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
_BOLD_HEADING = re.compile(r"^\s*\*\*([^*]{3,120})\*\*\s*$")
_SENT = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z0-9(\"'])")


def _blocks(md: str) -> list[tuple[str, str]]:
    """Split markdown into (kind, text) blocks: 'heading' | 'table' | 'para'."""
    out: list[tuple[str, str]] = []
    para: list[str] = []
    table: list[str] = []

    def flush_para() -> None:
        if para and "".join(para).strip():
            out.append(("para", "\n".join(para).strip()))
        para.clear()

    def flush_table() -> None:
        if table:
            out.append(("table", "\n".join(table)))
        table.clear()

    for line in md.splitlines():
        if line.strip().startswith("|"):
            flush_para()
            table.append(line.rstrip())
            continue
        flush_table()
        m = _HEADING.match(line) or _BOLD_HEADING.match(line)
        if m:
            flush_para()
            out.append(("heading", m.group(m.lastindex or 1).strip("*# ").strip()))
        elif not line.strip():
            flush_para()
        else:
            para.append(line)
    flush_para()
    flush_table()
    return out


def _split_long(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    sentences = _SENT.split(text)
    pieces: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > size:  # a single monster sentence: hard cut
            pieces.append(s[:size])
            s = s[size - overlap:]
        if len(cur) + len(s) + 1 > size and cur:
            pieces.append(cur)
            cur = cur[-overlap:] + " " + s if overlap else s
        else:
            cur = f"{cur} {s}".strip()
    if cur.strip():
        pieces.append(cur)
    return pieces


def _split_table(table: str, size: int) -> list[str]:
    lines = table.splitlines()
    if len(table) <= size * 2 or len(lines) < 4:
        return [table]
    header = lines[:2] if re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[1]) else lines[:1]
    body = lines[len(header):]
    parts, cur = [], list(header)
    for row in body:
        if sum(len(r) for r in cur) + len(row) > size * 2 and len(cur) > len(header):
            parts.append("\n".join(cur))
            cur = list(header)
        cur.append(row)
    if len(cur) > len(header):
        parts.append("\n".join(cur))
    return parts


def chunk_markdown(md: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP,
                   section: str | None = None) -> list[tuple[str | None, str, str]]:
    """Return a list of (section_heading, kind, text)."""
    chunks: list[tuple[str | None, str, str]] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip() and not (buf.startswith("## ") and buf.strip().count("\n") == 0):
            for piece in _split_long(buf.strip(), size, overlap):
                chunks.append((section, "text", piece))
        buf = ""

    for kind, text in _blocks(md):
        if kind == "heading":
            flush()
            section = text[:160]
            buf = f"## {text}\n"
        elif kind == "table":
            flush()
            for part in _split_table(text, size):
                chunks.append((section, "table", part))
        else:
            only_heading = buf.startswith("## ") and buf.strip().count("\n") == 0
            if buf.strip() and not only_heading and len(buf) + len(text) + 2 > size:
                flush()
            buf = f"{buf}\n\n{text}" if buf else text
    flush()
    return chunks
