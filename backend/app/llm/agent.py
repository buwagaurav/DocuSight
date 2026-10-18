"""Agentic multimodal RAG.

Claude drives retrieval itself through tools, which gives us, per question:
  * query routing      – semantic search vs. exact SQL over tables vs. looking at pages
  * query rewriting    – multi-query (RAG-Fusion) + HyDE written by the model
  * corrective RAG     – if results are weak it reformulates and searches again
  * multimodal answers – retrieved page images go straight into Claude's vision
  * grounded citations – every source gets an [S#] id the UI turns into a link
Small documents skip retrieval entirely (adaptive routing: full-context mode).
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import numpy as np

from ..config import AGENT_MAX_STEPS, LLM_MODEL, SEMANTIC_CACHE_THRESHOLD
from ..retrieval.colpali import colpali
from ..retrieval.hybrid import retriever
from ..retrieval.models import embed_queries
from ..store import store
from ..tables import run_sql
from .client import describe_error, effort, image_block, llm_available, messages_api, model_label, provider_kwargs

FULL_CONTEXT_CHARS = 60_000
MAX_IMAGES_TOTAL = 10

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Hybrid multimodal search over the user's documents: BM25 keywords + dense embeddings + ColPali "
            "visual page retrieval, fused with RRF and reranked by a cross-encoder. Returns text passages and the "
            "most relevant page IMAGES. Write 2-4 diverse queries (rephrasings, synonyms, specific terms/IDs) and, "
            "for conceptual questions, a short hypothetical answer passage (HyDE) to improve recall."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "queries": {"type": "array", "items": {"type": "string"}, "description": "1-4 search queries"},
                "hypothetical_answer": {"type": "string", "description": "Optional: a plausible answer passage (HyDE)."},
                "doc_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional subset of document ids"},
                "top_k": {"type": "integer", "description": "Passages to return (default 8, max 15)"},
            },
            "required": ["queries"],
        },
    },
    {
        "name": "list_tables",
        "description": "List every structured table (Excel sheets / CSV / tables detected in PDFs) with columns, "
                       "types and statistics. Call before run_sql.",
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {"doc_ids": {"type": "array", "items": {"type": "string"}}}},
    },
    {
        "name": "run_sql",
        "description": "Run ONE read-only DuckDB SELECT over a document's tables for exact numbers: totals, "
                       "averages, filters, group-bys, rankings, comparisons. Quote identifiers with double quotes "
                       "when they contain spaces or capitals. Returns up to 200 rows.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}, "sql": {"type": "string"}},
            "required": ["doc_id", "sql"],
        },
    },
    {
        "name": "view_pages",
        "description": "Look at specific PDF pages as images (to read charts, scans, signatures, small print, or "
                       "to verify a number). Max 4 pages per call.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}, "pages": {"type": "array", "items": {"type": "integer"}}},
            "required": ["doc_id", "pages"],
        },
    },
    {
        "name": "create_chart",
        "description": "Render an interactive chart in the chat UI. Use when the user asks to plot/visualize, or "
                       "when comparing more than ~4 numbers over categories/time. Data must come from tool results.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_type": {"type": "string", "enum": ["bar", "line", "area", "pie", "scatter"]},
                "title": {"type": "string"},
                "data": {"type": "array", "items": {"type": "object"}, "description": "rows, e.g. [{\"month\":\"Jan\",\"revenue\":10}]"},
                "x_key": {"type": "string"},
                "y_keys": {"type": "array", "items": {"type": "string"}},
                "y_label": {"type": "string"},
            },
            "required": ["chart_type", "title", "data", "x_key", "y_keys"],
        },
    },
]

SYSTEM = """You are DocuSight, an expert analyst that answers questions about the user's uploaded documents \
(PDFs and spreadsheets, often complex: financial statements, invoices, contracts, scanned forms, multi-sheet workbooks).

How to work:
1. Route the question. Numeric / aggregate / filtering questions over tabular data → list_tables then run_sql \
(exact answers beat reading rows). Everything else → search_documents. Charts, scans, figures or "what does page N \
show" → look at page images (search results include them; view_pages for specific pages).
2. Retrieve well: give search_documents several diverse queries and, for conceptual questions, a hypothetical answer.
3. Self-check (corrective RAG): if results don't actually answer the question, reformulate and search again \
(different terms, other document, SQL). Cross-check important numbers between text, tables and page images.
4. Answer: lead with the direct answer, then supporting detail. Use markdown; use tables for multi-value answers. \
Cite every fact with its source id in square brackets, like [S2] or [S1][S4], placed right after the claim. \
Only cite ids that appear in tool results. Quote numbers exactly with units/currency.
5. If the documents don't contain the answer, say so plainly and say what they do contain. Never invent values.
6. Use create_chart when a visual comparison or trend genuinely helps, or when asked.
Keep answers focused; don't narrate your tool use."""


class SemanticCache:
    """Answer cache keyed on query embedding similarity + document set."""

    def __init__(self, threshold: float = SEMANTIC_CACHE_THRESHOLD, size: int = 200):
        self.threshold, self.size = threshold, size
        self.items: list[tuple[tuple, np.ndarray, dict[str, Any]]] = []

    def get(self, docs: tuple, q: str) -> dict[str, Any] | None:
        if not self.items:
            return None
        v = embed_queries([q])[0]
        best, best_s = None, 0.0
        for key, emb, payload in self.items:
            if key == docs:
                s = float(emb @ v)
                if s > best_s:
                    best, best_s = payload, s
        return best if best_s >= self.threshold else None

    def put(self, docs: tuple, q: str, payload: dict[str, Any]) -> None:
        self.items.append((docs, embed_queries([q])[0], payload))
        self.items = self.items[-self.size:]

    def drop_doc(self, doc_id: str) -> None:
        self.items = [it for it in self.items if doc_id not in it[0]]


cache = SemanticCache()


class Turn:
    """Per-question state: numbered sources + images already shown."""

    def __init__(self) -> None:
        self.sources: list[dict[str, Any]] = []
        self.images_sent: set[tuple[str, int]] = set()
        self.parents_sent: set[str] = set()
        self.charts: list[dict[str, Any]] = []

    def add_source(self, **kw: Any) -> str:
        sid = f"S{len(self.sources) + 1}"
        kw["id"] = sid
        if kw.get("doc_id") in store.docs:
            kw["filename"] = store.docs[kw["doc_id"]].filename
        self.sources.append(kw)
        return sid

    def page_images(self, pages: list[tuple[str, int]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        blocks, new = [], []
        for doc_id, p in pages:
            if (doc_id, p) in self.images_sent or len(self.images_sent) >= MAX_IMAGES_TOTAL:
                continue
            path = store.page_path(doc_id, p)
            if not path.exists():
                continue
            self.images_sent.add((doc_id, p))
            sid = self.add_source(kind="page", doc_id=doc_id, page=p, snippet=f"Page {p} (image)")
            new.append(self.sources[-1])
            blocks.append({"type": "text", "text": f"[{sid}] Image of page {p} of {store.docs[doc_id].filename}:"})
            blocks.append(image_block(path))
        return blocks, new


def _catalog(doc_ids: list[str]) -> tuple[str, bool]:
    lines, total = [], 0
    for d in doc_ids:
        m = store.docs[d]
        desc = f"{m.n_pages} pages" if m.kind == "pdf" else f"{len(m.sheets)} sheet(s): " + ", ".join(s["name"] for s in m.sheets[:12])
        ex = m.extraction or {}
        lines.append(f"- doc_id `{d}` · {m.filename} ({m.kind}, {desc}, {len(m.tables)} SQL table(s))"
                     + (f" — {ex.get('document_type', '')}: {ex.get('summary', '')[:300]}" if ex else ""))
        total += sum(len(t) for t in store.parents.get(d, {}).values())
    full = total <= FULL_CONTEXT_CHARS
    return "\n".join(lines), full


def _full_context(doc_ids: list[str], turn: Turn) -> list[dict[str, Any]]:
    """Adaptive routing: small corpora are given whole (no retrieval loss)."""
    blocks = []
    for d in doc_ids:
        for pid, text in store.parents.get(d, {}).items():
            first = next((c for c in store.chunks[d] if c.parent_id == pid), None)
            sid = turn.add_source(kind="text", doc_id=d, page=first.page if first else None,
                                  sheet=first.sheet if first else None,
                                  section=first.section if first else None, snippet=text[:400])
            turn.parents_sent.add(pid)
            loc = f"p.{first.page}" if first and first.page else (f"sheet '{first.sheet}'" if first and first.sheet else "")
            blocks.append(f"[{sid}] {store.docs[d].filename} {loc}\n{text}")
    return [{"type": "text", "text": "<documents>\n" + "\n\n".join(blocks) + "\n</documents>"}]


# --------------------------------------------------------------------- tools
def _validate(name: str, args: Any) -> str | None:
    if not isinstance(args, dict):
        return "input must be an object"
    if name == "search_documents":
        q = args.get("queries")
        if not (isinstance(q, list) and q and all(isinstance(x, str) for x in q)):
            return "queries must be a non-empty array of strings"
    elif name == "run_sql":
        if not (isinstance(args.get("doc_id"), str) and isinstance(args.get("sql"), str)):
            return "doc_id and sql must be strings"
    elif name == "view_pages":
        if not (isinstance(args.get("doc_id"), str) and isinstance(args.get("pages"), list)
                and all(isinstance(p, int) for p in args["pages"])):
            return "doc_id must be a string and pages an array of integers"
    elif name == "create_chart":
        if not (isinstance(args.get("data"), list) and isinstance(args.get("x_key"), str)
                and isinstance(args.get("y_keys"), list)):
            return "data (array), x_key (string) and y_keys (array) are required"
    return None


def _exec_tool(name: str, args: dict[str, Any], doc_ids: list[str], turn: Turn, opts: dict[str, Any]
               ) -> tuple[list[dict[str, Any]] | str, dict[str, Any], list[dict[str, Any]]]:
    """Returns (tool_result content, ui detail, new sources)."""
    if name == "search_documents":
        targets = [d for d in (args.get("doc_ids") or doc_ids) if d in doc_ids] or doc_ids
        k = max(1, min(int(args.get("top_k") or 8), 15))
        res = retriever.search(args["queries"], targets, k=k, hyde=args.get("hypothetical_answer"),
                               use_colpali=opts.get("colpali", True), use_rerank=opts.get("rerank", True))
        parts, new = [], []
        for h in res.hits:
            c = h.chunk
            # small-to-big: send the parent context once; later sibling hits just reference it
            if c.parent_id and c.parent_id not in turn.parents_sent:
                turn.parents_sent.add(c.parent_id)
                body = store.parent_text(c)
            else:
                body = c.text
            sid = turn.add_source(kind="text", doc_id=c.doc_id, page=c.page, sheet=c.sheet, section=c.section,
                                  snippet=c.text[:400], score=h.score, signals=h.signals)
            new.append(turn.sources[-1])
            loc = c.location()
            parts.append(f"[{sid}] {store.docs[c.doc_id].filename}{' · ' + loc if loc else ''}"
                         f"{' · ' + c.section if c.section else ''}\n{body}")
        content: list[dict[str, Any]] = [{"type": "text", "text": "\n\n---\n\n".join(parts) or "No matching passages."}]
        if opts.get("vision", True):
            img_blocks, img_new = turn.page_images([(d, p) for d, p, _ in res.pages])
            content += img_blocks
            new += img_new
        detail = {"trace": res.trace, "hits": len(res.hits), "images": len([s for s in new if s["kind"] == "page"])}
        return content, detail, new

    if name == "list_tables":
        targets = [d for d in (args.get("doc_ids") or doc_ids) if d in doc_ids] or doc_ids
        out = []
        for d in targets:
            m = store.docs[d]
            for t in m.tables:
                cols = "; ".join(
                    f"\"{c['name']}\" {c['type']}" + (f" [min {c['min']}, max {c['max']}, sum {c['sum']}]" if "sum" in c
                                                     else f" e.g. {', '.join(c.get('top', [])[:3])}" if c.get("top") else "")
                    for c in t["columns"])
                out.append(f"doc_id `{d}` ({m.filename}) · table \"{t['name']}\" — {t['title']} ({t['source']}), "
                           f"{t['n_rows']} rows\n  columns: {cols}")
        text = "\n".join(out) or "No structured tables in these documents. Use search_documents."
        return text, {"tables": sum(len(store.docs[d].tables) for d in targets)}, []

    if name == "run_sql":
        d = args["doc_id"]
        if d not in doc_ids:
            raise ValueError(f"Unknown doc_id {d}. Valid: {', '.join(doc_ids)}")
        res = run_sql(store.tables_db(d), args["sql"])
        sid = turn.add_source(kind="sql", doc_id=d, sql=args["sql"], snippet=f"SQL → {res['row_count']} row(s)",
                              columns=res["columns"], rows=res["rows"][:50])
        header = "| " + " | ".join(map(str, res["columns"])) + " |\n|" + "---|" * len(res["columns"])
        rows = "\n".join("| " + " | ".join("" if v is None else str(v) for v in r) + " |" for r in res["rows"])
        note = "\n(truncated to 200 rows)" if res["truncated"] else ""
        return (f"[{sid}] Result of SQL on {store.docs[d].filename} ({res['row_count']} rows):\n{header}\n{rows}{note}",
                {"columns": res["columns"], "rows": res["rows"][:50], "row_count": res["row_count"], "sql": args["sql"]},
                [turn.sources[-1]])

    if name == "view_pages":
        d = args["doc_id"]
        if d not in doc_ids or store.docs[d].kind != "pdf":
            raise ValueError("view_pages needs the doc_id of a PDF in scope")
        n = store.docs[d].n_pages
        pages = [p for p in args["pages"] if 1 <= p <= n][:4]
        if not pages:
            raise ValueError(f"Pages must be between 1 and {n}")
        blocks, new = turn.page_images([(d, p) for p in pages])
        if not blocks:
            return "Those pages were already shown earlier in this answer (or the image budget is used up).", {}, []
        return blocks, {"pages": pages}, new

    if name == "create_chart":
        chart = {k: args.get(k) for k in ("chart_type", "title", "data", "x_key", "y_keys", "y_label")}
        chart["data"] = chart["data"][:500]
        turn.charts.append(chart)
        return "Chart rendered for the user.", {"chart": chart}, []

    raise ValueError(f"Unknown tool {name}")


def _tool_label(name: str, args: dict[str, Any]) -> str:
    if name == "search_documents":
        return "Searching: " + " · ".join(args.get("queries", [])[:3])
    if name == "list_tables":
        return "Inspecting table schemas"
    if name == "run_sql":
        return "Running SQL query"
    if name == "view_pages":
        return f"Looking at page(s) {', '.join(map(str, args.get('pages', [])))}"
    if name == "create_chart":
        return f"Drawing chart: {args.get('title', '')}"
    return name


# ---------------------------------------------------------------------- loop
def run_agent(question: str, history: list[dict[str, str]], doc_ids: list[str], opts: dict[str, Any]
              ) -> Iterator[dict[str, Any]]:
    if not llm_available():
        yield {"type": "error", "message": f"No API key for {model_label()}. Add it to the .env file in the project root "
                                           "and restart the backend. (Extraction, tables, search and SQL work without it.)"}
        return
    doc_ids = store.ready_ids(doc_ids) if doc_ids else []  # empty must not mean "all documents"
    if not doc_ids:
        yield {"type": "error", "message": "No ready documents selected. Upload a PDF or Excel file first."}
        return

    key = tuple(sorted(doc_ids)) + (json.dumps(opts, sort_keys=True),)
    if not history and opts.get("cache", True):
        hit = cache.get(key, question)
        if hit:
            yield {"type": "status", "text": "Answered from semantic cache"}
            yield {"type": "sources", "sources": hit["sources"]}
            for ch in hit["charts"]:
                yield {"type": "chart", "chart": ch}
            yield {"type": "text", "delta": hit["answer"]}
            yield {"type": "done", "cached": True, "techniques": hit.get("techniques", [])}
            return

    turn = Turn()
    catalog, small = _catalog(doc_ids)
    full_mode = small and opts.get("adaptive", True)
    system = [
        {"type": "text", "text": SYSTEM},
        {"type": "text", "text": f"Documents in scope:\n{catalog}\n"
                                  f"Visual retrieval (ColPali): {'on' if colpali.available and opts.get('colpali', True) else 'off'}."},
    ]
    messages: list[dict[str, Any]] = []
    for h in history[-12:]:
        if h.get("role") in {"user", "assistant"} and h.get("content", "").strip():
            messages.append({"role": h["role"], "content": h["content"]})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)

    user_content: list[dict[str, Any]] = []
    techniques = ["Agentic routing", "Inline citations"]
    if full_mode:
        user_content += _full_context(doc_ids, turn)
        yield {"type": "sources", "sources": turn.sources}
        techniques.append("Adaptive full-context (small corpus)")
    user_content.append({"type": "text", "text": question})
    messages.append({"role": "user", "content": user_content})

    api = messages_api()
    answer = ""
    for step in range(AGENT_MAX_STEPS + 1):
        last = step == AGENT_MAX_STEPS
        try:
            with api.stream(
                model=LLM_MODEL,
                max_tokens=32000,
                system=system,
                tools=TOOLS,
                tool_choice={"type": "none"} if last else {"type": "auto"},
                messages=messages,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": effort(opts.get("effort", "medium"))},
                cache_control={"type": "ephemeral"},
                **provider_kwargs(),
            ) as stream:
                for ev in stream:
                    if ev.type == "text":
                        answer += ev.text
                        yield {"type": "text", "delta": ev.text}
                    elif ev.type == "thinking":
                        yield {"type": "thinking", "delta": ev.thinking}
                resp = stream.get_final_message()
        except ValueError:
            yield {"type": "status", "text": "Retrying (malformed tool input)…"}
            continue
        except Exception as e:
            yield {"type": "error", "message": describe_error(e)}
            return

        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "refusal":
            yield {"type": "error", "message": "The model declined to answer this request."}
            return
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            break
        if resp.stop_reason == "max_tokens":
            yield {"type": "error", "message": "Response was cut off (max tokens)."}
            return

        results = []
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else {}
            yield {"type": "tool_start", "id": tu.id, "name": tu.name, "label": _tool_label(tu.name, args), "input": args}
            err = _validate(tu.name, tu.input)
            if err:
                results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                "content": json.dumps({"INVALID_INPUT": err})})
                yield {"type": "tool_end", "id": tu.id, "ok": False, "detail": {"error": err}}
                continue
            try:
                content, detail, new_sources = _exec_tool(tu.name, args, doc_ids, turn, opts)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
                if new_sources:
                    yield {"type": "sources", "sources": new_sources}
                if tu.name == "create_chart":
                    yield {"type": "chart", "chart": detail["chart"]}
                yield {"type": "tool_end", "id": tu.id, "ok": True,
                       "detail": {k: v for k, v in detail.items() if k != "chart"}}
                techniques += {
                    "search_documents": ["Hybrid search (BM25 + dense)", "RRF fusion", "Multi-query"]
                    + (["HyDE"] if args.get("hypothetical_answer") else [])
                    + (["ColPali visual retrieval"] if detail.get("trace", {}).get("colpali_pages") else [])
                    + (["Cross-encoder rerank"] if detail.get("trace", {}).get("reranked") else [])
                    + ["Small-to-big context", "MMR diversity"]
                    + (["Multimodal (page images)"] if detail.get("images") else []),
                    "run_sql": ["Text-to-SQL"], "list_tables": ["Schema inspection"],
                    "view_pages": ["Multimodal (page images)"], "create_chart": ["Chart generation"],
                }.get(tu.name, [])
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True, "content": str(e)[:1000]})
                yield {"type": "tool_end", "id": tu.id, "ok": False, "detail": {"error": str(e)[:300]}}
        messages.append({"role": "user", "content": results})
        if answer:
            answer += "\n\n"
            yield {"type": "text", "delta": "\n\n"}

    techniques = list(dict.fromkeys(techniques))
    if not history and answer.strip():
        cache.put(key, question, {"answer": answer, "sources": turn.sources, "charts": turn.charts,
                                  "techniques": techniques + ["Semantic cache"]})
    yield {"type": "done", "cached": False, "techniques": techniques}
