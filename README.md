# DocuSight — multimodal RAG for complex PDFs & spreadsheets

Drop in a PDF or Excel file. DocuSight immediately parses it and shows every key field, metric, table, date and insight. It also flags anomalies. You can then ask it anything. Answers cite their sources, and numbers from spreadsheets are computed exactly with SQL. Charts, scans and stamps are found by **ColPali** visual search and read by the LLM's vision (DeepSeek or Claude).

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

## Run it locally: step by step

The first start downloads the AI models automatically, so you **don't need EmbeddingGemma (or any model) installed beforehand**. Step 5 covers machines that can't download from the internet.

### What you need

| | Minimum | Recommended |
|---|---|---|
| OS | macOS, Linux, or Windows (via WSL2) | Apple Silicon Mac or an NVIDIA GPU (ColPali is much faster) |
| Python | 3.10+ | 3.12 |
| Node.js | 20.9+ | 22 or newer |
| RAM | 8 GB (ColPali off) | 16 GB (everything on) |
| Free disk | 3 GB (ColPali off) | 10 GB (everything on) |
| API key | — | DeepSeek **or** Anthropic (for AI extraction and chat) |

Without an API key, parsing, tables, SQL, search and a basic extraction still work. Only the AI summary, field extraction and chat need a key.

### Step 1: Get the code

```bash
git clone https://github.com/buwagaurav/DocuSight.git
cd DocuSight
```

### Step 2: Install the backend (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows (WSL): same command
pip install --upgrade pip
pip install -r backend/requirements.txt
```

This installs FastAPI, the PDF/Excel parsers, DuckDB, PyTorch, sentence-transformers (for EmbeddingGemma) and fastembed (for the fallback embedder and the reranker). It takes a few minutes.

**Optional but recommended: ColPali (visual page search).**

```bash
pip install -r backend/requirements-colpali.txt
```

Skip this if your machine has less than 16 GB of RAM. Everything else still works, and the app just won't search pages by how they look.

**Optional: OCR for scanned PDFs (Apple Silicon Macs only).**

```bash
pip install -r backend/requirements-ocr.txt
```

This adds DeepSeek-OCR-2 (4-bit MLX, ≈2.6 GB, downloaded on the first scanned page). It runs only on pages with no text layer, such as scans and photos of documents, and makes them keyword- and semantic-searchable, with their tables queryable in SQL. Digital PDFs never use it. On other platforms it switches itself off.

### Step 3: Install the frontend (Node.js)

```bash
cd frontend
npm install
cd ..
```

### Step 4: Configure `.env`

```bash
cp .env.example .env
```

Open `.env` and set **one** LLM provider:

```bash
# Option A: DeepSeek (low cost, reads page images)
LLM_MODEL=deepseek:deepseek-flash
DEEPSEEK_API_KEY=sk-your-deepseek-key

# Option B: Claude
# LLM_MODEL=anthropic:claude-opus-5-5
# ANTHROPIC_API_KEY=sk-ant-your-key
```

Keep the model lines that match your machine:

```bash
EMBED_MODEL=google/embeddinggemma-2    # downloaded automatically (≈1.4 GB)
COLPALI_MODEL=vidore/colpali-v1.3      # ≈5.5 GB; use vidore/colSmol-500M (≈0.95 GB) on smaller machines
# COLPALI_ENABLED=false                # uncomment if you skipped Step 2's ColPali install
```

`.env` is git-ignored, so your keys never get committed.

### Step 5: Get the models (EmbeddingGemma and ColPali)

**You don't have to do anything if the machine has internet.** Models download from Hugging Face the first time they're needed and are cached in `~/.cache/huggingface/` for every later run:

| Model | Used for | Size | Downloaded when |
|---|---|---|---|
| `google/embeddinggemma-2` | Text embeddings (semantic search) | ≈1.4 GB | Backend starts |
| `vidore/colpali-v1.3` + `vidore/colpaligemma-3b-pt-448-base` | ColPali visual page search | ≈5.5 GB | First PDF is uploaded |
| `Xenova/ms-marco-MiniLM-L-6-v2` | Reranking | ≈90 MB | First search |
| `mlx-community/DeepSeek-OCR-2-4bit` (optional) | OCR of scanned pages | ≈2.6 GB | First scanned page |

None of these models are gated, so **no Hugging Face account or token is needed**. Setting `HF_TOKEN=...` in `.env` only raises download rate limits.

Pick the option that fits your situation:

**A) Download ahead of time.** Useful so the first start is instant, or before going offline:

```bash
source .venv/bin/activate
hf download google/embeddinggemma-2
hf download vidore/colpali-v1.3
hf download vidore/colpaligemma-3b-pt-448-base
```

**B) The machine has no internet (or a firewall blocks Hugging Face).** On any machine with internet, download into a folder:

```bash
hf download google/embeddinggemma-2 --local-dir models/embeddinggemma-2
```

Copy the `models/` folder into the project on the offline machine, then set in `.env`:

```bash
EMBED_MODEL=./models/embeddinggemma-2   # relative paths are resolved from the project root
HF_HUB_OFFLINE=1                        # never try to reach Hugging Face
```

For ColPali offline, either copy the whole `~/.cache/huggingface/hub` folder after running option A, or set `COLPALI_ENABLED=false`.

**C) The machine is too small for EmbeddingGemma, or you want the lightest setup.** Use the built-in lightweight embedder (≈130 MB, ONNX, CPU-only):

```bash
EMBED_MODEL=BAAI/bge-small-en-v1.5
COLPALI_ENABLED=false
```

If EmbeddingGemma fails to load for any reason, DocuSight **switches to `bge-small-en-v1.5` automatically** and logs `[embed] could not load ... falling back`, so the app always starts. Changing `EMBED_MODEL` later is safe, because existing documents are re-embedded automatically on the next search.

### Step 6: Start DocuSight

**One command** (macOS/Linux/WSL):

```bash
./run.sh
```

This starts the backend on **:8000** and the frontend on **:3000**, and installs any missing dependencies first. Stop both with `Ctrl+C`.

**Or start them manually in two terminals:**

```bash
# Terminal 1 — backend
source .venv/bin/activate
cd backend
python -X faulthandler -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev
```

The first backend start takes about 1–2 minutes while EmbeddingGemma downloads. Later starts take a few seconds.

### Step 7: Check that it works

```bash
curl http://localhost:8000/api/health
```

You should see something like:

```json
{"llm": {"available": true, "model": "deepseek:deepseek-flash"},
 "colpali": {"available": true, "model": "vidore/colpali-v1.3"},
 "embedding_model": "google/embeddinggemma-2 (768d)"}
```

`"embedding_model": "... (loading)"` just means the model is still downloading or loading; wait a minute.

Then open **http://localhost:3000**. The header badges show the active LLM and whether ColPali is on.

### Step 8: Try it with sample files

```bash
.venv/bin/python scripts/make_samples.py     # creates samples/acme_sales_fy2025.xlsx and samples/northwind_invoice.pdf
```

Drag both files onto the upload box. Within about 15 seconds the **Overview** tab fills in with extracted fields. Try these chat questions:

- *"What was the total revenue per sales rep after discounts? Show a chart."* (answered with SQL plus a chart)
- *"Which quarter missed the 95% delivery target by the most?"* (answered from the chart image)
- *"What's the invoice total due?"* (answered from page 1)

The first PDF also triggers the ColPali download and indexing, which runs in the background. The sidebar shows "ColPali…" until it's done.

### Troubleshooting

| Symptom | Fix |
|---|---|
| UI shows "Can't reach the backend" | The backend isn't running on :8000. Check Terminal 1 for errors. |
| `Address already in use` | Another process is using the port: `lsof -i :8000` (or `:3000`) and stop it. |
| Header shows "LLM: no API key" | Set `DEEPSEEK_API_KEY` or `ANTHROPIC_API_KEY` in `.env` and restart the backend. |
| `embedding_model` stays `(loading)` for a long time | The download is slow or blocked. Use Step 5 option A, B or C. |
| Log shows `could not load google/embeddinggemma-2 … falling back` | The app is running on bge-small. Check internet or disk space, or use Step 5 option B. |
| ColPali shows "off" | `pip install -r backend/requirements-colpali.txt`, and check `COLPALI_ENABLED` isn't `false`. |
| Out of memory, or the Mac is very slow | Set `COLPALI_MODEL=vidore/colSmol-500M` or `COLPALI_ENABLED=false`, and `EMBED_DIM=256`. |
| Scanned PDF isn't searchable by text | Install `backend/requirements-ocr.txt` (Apple Silicon only). `/api/health` should show `"ocr": {"available": true}`. |
| Start over with an empty workspace | Stop the backend and delete the `data/` folder. |

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
| | **OCR for scanned pages: DeepSeek-OCR-2** (4-bit MLX, on-device). Runs only on pages without a text layer. Its markdown feeds BM25 and EmbeddingGemma, and its tables go to DuckDB. The LLM is told the text came from OCR, so it checks critical values against the page image. Runs in a separate worker process that stops when idle to free about 4 GB. | `ingest/ocr.py`, `ingest/ocr_worker.py` |
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
| `OCR_ENABLED` | `auto` | DeepSeek-OCR-2 for scanned pages (Apple Silicon + `requirements-ocr.txt`) |
| `OCR_MIN_CHARS` | `50` | Pages with less extractable text than this get OCR'd |
| `OCR_IDLE_SECONDS` | `300` | Stop the OCR worker after this much idle time (frees about 4 GB) |
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
