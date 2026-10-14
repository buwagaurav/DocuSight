# DocuSight — multimodal RAG for complex PDFs & spreadsheets

Drop in a PDF or Excel file. DocuSight immediately parses it and shows every key field, metric, table, date and insight. It also flags anomalies. You can then ask it anything. Answers cite their sources, and numbers from spreadsheets are computed exactly with SQL. Charts, scans and stamps are read visually through **ColPali** and Claude's vision.

```
┌──────────── Next.js + React + TypeScript (frontend/) ─────────────┐
│ Sidebar: drag-drop upload · docs · chat scope                      │
│ Workspace: Overview (auto-extraction) · Tables & Data (grid,       │
│            charts, SQL console) · Pages/Sheets · Retrieval Lab     │
│ Chat: streaming answers · [S#] citations → page viewer · tool      │
│       timeline · reasoning · inline charts · settings              │
└───────────────────────────────┬────────────────────────────────────┘
                                │ REST + Server-Sent Events
┌───────────────────── FastAPI (backend/app/) ───────────────────────┐
│ ingest/   PDF → page PNGs + layout markdown + native tables        │
│           Excel/CSV → merged cells, multi-table, multi-row headers │
│ tables.py every table → DuckDB (Text-to-SQL)                       │
│ retrieval/ BM25 · EmbeddingGemma 2 · ColPali MaxSim → RRF → rerank  │
│ llm/      auto-extraction (vision + JSON schema) · agentic RAG     │
└────────────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
cp .env.example .env            # then pick a provider and add its key (see "LLM providers" below)
./run.sh                        # installs deps on first run, starts both servers
open http://localhost:3000
```

ColPali (recommended):

```bash
.venv/bin/pip install -r backend/requirements-colpali.txt
```

The first PDF you index downloads the model. `vidore/colpali-v1.3` is about 6 GB. On a laptop, set `COLPALI_MODEL=vidore/colSmol-500M` (about 1 GB). It runs on Apple Silicon (MPS), CUDA or CPU.

Try it with the bundled messy samples: `.venv/bin/python scripts/make_samples.py` creates `samples/`.

Without an API key, parsing, tables, SQL, search and a basic heuristic extraction still work. The configured LLM (DeepSeek or Claude) powers the full extraction and the chat.

## RAG techniques implemented

These follow the techniques catalogued in the [aman.ai RAG primer](https://aman.ai/primers/ai/RAG/).

| Stage | Technique | Where |
|---|---|---|
| Ingestion | **Page-image rendering** for vision-native retrieval (no OCR pipeline) | `ingest/pdf.py` |
| | **Layout-aware markdown** (headings, tables preserved) | `ingest/pdf.py` |
| | **Structure-aware chunking** (heading-bounded, tables never split mid-row, recursive fallback with overlap) | `ingest/chunking.py` |
| | **Small-to-big / parent-document retrieval** (retrieve chunks, feed the parent page/table) | `store.py`, `hybrid.py` |
| | **Contextual chunk headers** (file · page/sheet · section prepended before indexing) | `pipeline.py` |
| | **Contextual Retrieval** (LLM-written per-chunk context, prompt-cached; opt-in) | `llm/contextual.py` |
| | **Messy-spreadsheet normalisation**: merged cells, multiple tables per sheet, title rows, multi-row headers, form-style key/value blocks, `$1,234` / `(300)` / `12%` coercion | `ingest/excel.py` |
| Indexing | **ColPali late-interaction visual index** (multi-vector page embeddings, MaxSim) | `retrieval/colpali.py` |
| | **BM25** lexical index (IDs, codes, exact numbers) | `retrieval/hybrid.py` |
| | **Dense embeddings: Google EmbeddingGemma 2**. Asymmetric prompts: queries use `task: search result \| query:` and passages use `title: <file · page · section> \| text:`, so the contextual header lands in the model's title slot. 768-d Matryoshka output (`EMBED_DIM` 512/256/128 to shrink), 8K context, text-only load. Falls back to `bge-small-en-v1.5` automatically | `retrieval/models.py` |
| | **Structured store**: every table → DuckDB | `tables.py` |
| Query | **Agentic query routing** (semantic search vs Text-to-SQL vs page vision) | `llm/agent.py` |
| | **Multi-query / RAG-Fusion** (the model writes 2–4 reformulations) | `llm/agent.py` |
| | **HyDE** (hypothetical answer embedded as a passage) | `retrieval/hybrid.py` |
| | **Adaptive retrieval**: small corpora go to the model whole (no retrieval loss) | `llm/agent.py` |
| Retrieval | **Hybrid search + Reciprocal Rank Fusion** across BM25, dense and ColPali | `retrieval/hybrid.py` |
| | **Cross-encoder reranking** (`ms-marco-MiniLM`) | `retrieval/models.py` |
| | **MMR-style diversity** (per-parent cap and near-duplicate suppression) | `retrieval/hybrid.py` |
| Generation | **Multimodal RAG**: retrieved page images go to Claude's vision | `llm/agent.py` |
| | **Corrective / self-reflective RAG**: re-searches when evidence is weak | `llm/agent.py` (system prompt + tool loop) |
| | **Grounded citations** `[S#]` → clickable source viewer | `llm/agent.py`, `ChatPanel.tsx` |
| | **Text-to-SQL** for exact aggregates over sheets and PDF tables (read-only, sandboxed) | `tables.py` |
| | **Structured extraction** with a JSON-schema-constrained output | `llm/extraction.py` |
| Serving | **Semantic cache** (embedding-similarity answer reuse) | `llm/agent.py` |
| | **Prompt caching** of system prompt and tools; server-side refusal fallback | `llm/agent.py`, `llm/client.py` |

The **Retrieval Lab** tab shows which signals (BM25 / Dense / ColPali / Rerank) found each hit, and their ranks. Every chat answer lists the techniques it used.

## LLM providers

All AI features (extraction, chat, contextual retrieval) use one model, chosen by a `provider:model` spec. `FINSIGHT_MODEL` is accepted as an alias of `LLM_MODEL`.

| Spec | Key | Notes |
|---|---|---|
| `deepseek:deepseek-flash` | `DEEPSEEK_API_KEY` | DeepSeek V4.1 Flash. Low cost, 1M context, **reads page images**. Uses DeepSeek's Anthropic-compatible API, so the same agent code runs on both providers. |
| `anthropic:claude-opus-5-5` | `ANTHROPIC_API_KEY` | Strictest structured output, plus server-side refusal fallback |
| `deepseek:deepseek-v4-pro` | `DEEPSEEK_API_KEY` | Text only: charts, scans and page images are not seen |

Provider differences are handled in `llm/client.py`:
- **Effort:** the UI's low/medium/high maps to DeepSeek's low/high/max.
- **Structured output:** DeepSeek accepts `output_config.format` but doesn't enforce it, so for DeepSeek the JSON schema is also put in the prompt, and every extraction result is validated and normalized.

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_MODEL` / `FINSIGHT_MODEL` | `anthropic:claude-opus-5-5` | `provider:model` used for extraction and chat |
| `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | — | Key for the chosen provider |
| `COLPALI_MODEL` | `vidore/colpali-v1.3` | Also `vidore/colqwen2.5-v0.2`, `vidore/colSmol-500M` |
| `COLPALI_ENABLED` | `auto` | `false` to skip visual indexing |
| `CONTEXTUAL_RETRIEVAL` | `false` | `true` = LLM chunk context with Haiku (extra cost) |
| `EMBED_MODEL` | `google/embeddinggemma-2` | Any fastembed model also works, e.g. `BAAI/bge-small-en-v1.5`. Indexes re-embed automatically when this changes |
| `EMBED_DIM` | `768` | Matryoshka truncation: 512 / 256 / 128 |
| `RERANK_ENABLED` | `true` | |
| `SEMANTIC_CACHE_THRESHOLD` | `0.97` | |

## API

`POST /api/documents` (multipart `files`) · `GET /api/documents` · `GET /api/documents/{id}` · `DELETE /api/documents/{id}` · `POST /api/documents/{id}/extract` · `GET /api/documents/{id}/pages/{n}` · `GET /api/documents/{id}/sheets/{i}` · `GET /api/documents/{id}/tables/{name}` · `POST /api/documents/{id}/sql` · `POST /api/search` · `POST /api/chat` (SSE). Interactive docs are at http://localhost:8000/docs.

## Notes

- **Apple Silicon:** all GPU model work (EmbeddingGemma, ColPali) runs on one dedicated device thread, `retrieval/device.py`. PyTorch MPS deadlocks or crashes when driven from several threads. Models are loaded on CPU and then moved to MPS, which works around a transformers 5 loader crash.
- **First run:** EmbeddingGemma 2 (about 1.5 GB) loads at startup. ColPali v1.3 (about 6 GB) loads with the first PDF and then warms up.

- Data lives in `data/` (originals, page images, indexes, DuckDB files). Delete a document from the UI to remove it.
- SQL from the agent and the console runs against a **read-only** DuckDB connection, with external file access disabled.
- Formulas in `.xlsx` use the values Excel last cached. Workbooks that were generated by code and never opened in Excel may have empty formula cells.
