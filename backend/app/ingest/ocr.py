"""OCR for scanned / image-only PDF pages with DeepSeek-OCR-2 (MLX, on-device).

Where it sits in the pipeline — next to ColPali, not in place of it:

    page image ──► ColPali visual index                       (always)
        └─ page has (almost) no extractable text? ──► DeepSeek-OCR-2
                ├─► markdown text ──► chunks ──► BM25 + EmbeddingGemma
                └─► <table> blocks ──► DataFrames ──► DuckDB (Text-to-SQL)

Digital PDFs never pay for OCR; only pages whose text layer is (nearly) empty.
The model runs in a child process (ocr_worker.py) that is started on demand and
stopped after OCR_IDLE_SECONDS, so its ~4 GB is only held while ingesting scans.
"""
from __future__ import annotations

import html
import json
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import OCR_ENABLED, OCR_IDLE_SECONDS, OCR_MAX_TOKENS, OCR_MODEL

BACKEND_DIR = Path(__file__).resolve().parents[2]
PAGE_TIMEOUT = 300  # seconds per page before the worker is considered stuck


@dataclass
class OcrResult:
    markdown: str
    tables: list[pd.DataFrame] = field(default_factory=list)
    seconds: float = 0.0


# ------------------------------------------------------------------ parsing
_REF_DET = re.compile(r"<\|ref\|>(.*?)<\|/ref\|><\|det\|>.*?<\|/det\|>\s*", re.S)
_TABLE = re.compile(r"<table>.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t([dh])([^>]*)>(.*?)</t[dh]>", re.S | re.I)
_COLSPAN = re.compile(r'colspan\s*=\s*"?(\d+)', re.I)


def _html_table_rows(table_html: str) -> list[list[str]]:
    rows = []
    for row_html in _ROW.findall(table_html):
        row = []
        for _, attrs, cell in _CELL.findall(row_html):
            text = html.unescape(re.sub(r"<[^>]+>", " ", cell)).replace("|", "/")
            text = re.sub(r"\s+", " ", text).strip()
            span = int(m.group(1)) if (m := _COLSPAN.search(attrs)) else 1
            row.extend([text] + [""] * (span - 1))
        if row:
            rows.append(row)
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def parse_ocr_output(text: str) -> tuple[str, list[list[list[str]]]]:
    """DeepSeek-OCR grounding output -> (clean markdown, tables as row lists)."""
    text = _REF_DET.sub("", text)  # drop layout tags (<|ref|>type<|/ref|><|det|>[[boxes]]<|/det|>)
    tables: list[list[list[str]]] = []

    def to_md(m: re.Match) -> str:
        rows = _html_table_rows(m.group(0))
        if rows:
            tables.append(rows)
        return "\n\n" + _rows_to_markdown(rows) + "\n\n"

    text = _TABLE.sub(to_md, text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, tables


# ------------------------------------------------------------------- worker
class _Worker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._out: queue.Queue = queue.Queue()
        self._last_used = 0.0
        self.error: str | None = None
        threading.Thread(target=self._idle_reaper, daemon=True).start()

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "app.ingest.ocr_worker", OCR_MODEL],
            cwd=BACKEND_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self._out = queue.Queue()
        proc = self._proc

        def pump() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                try:
                    self._out.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
            self._out.put({"exited": True})

        threading.Thread(target=pump, daemon=True).start()
        msg = self._out.get(timeout=900)  # first run downloads the model (~2.6 GB)
        if not msg.get("ready"):
            self._stop()
            raise RuntimeError("OCR worker failed to start")
        print(f"[ocr] {OCR_MODEL} loaded in {msg.get('load_seconds')}s")

    def _stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def _idle_reaper(self) -> None:
        while True:
            time.sleep(30)
            with self._lock:
                if self._proc and time.time() - self._last_used > OCR_IDLE_SECONDS:
                    print("[ocr] idle — stopping worker to free memory")
                    self._stop()

    def run(self, image: Path) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            req_id = uuid.uuid4().hex
            self._proc.stdin.write(json.dumps({"id": req_id, "image": str(image), "max_tokens": OCR_MAX_TOKENS}) + "\n")  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
            try:
                while True:
                    msg = self._out.get(timeout=PAGE_TIMEOUT)
                    if msg.get("exited"):
                        self._proc = None
                        raise RuntimeError("OCR worker exited unexpectedly")
                    if msg.get("id") == req_id:
                        break
            except queue.Empty:
                self._stop()
                raise RuntimeError(f"OCR timed out after {PAGE_TIMEOUT}s")
            finally:
                self._last_used = time.time()
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg


_worker = _Worker()


def ocr_available() -> bool:
    if not OCR_ENABLED or _worker.error:
        return False
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False  # MLX backend is Apple Silicon only
    try:
        import mlx_vlm  # noqa: F401

        return True
    except Exception:
        return False


def ocr_page(image: Path) -> OcrResult:
    from ..tables import clean_columns, coerce_types

    msg = _worker.run(image)
    markdown, raw_tables = parse_ocr_output(msg.get("text", ""))
    frames = []
    for rows in raw_tables:
        if len(rows) < 2 or len(rows[0]) < 2:
            continue
        df = pd.DataFrame(rows[1:], columns=clean_columns(rows[0]))
        df = df.loc[~df.apply(lambda r: all(not str(v).strip() for v in r), axis=1)]
        frames.append(coerce_types(df.replace({"": None})).reset_index(drop=True))
    return OcrResult(markdown=markdown, tables=frames, seconds=msg.get("seconds", 0.0))
