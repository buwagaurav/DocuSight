# SpreadsheetAgent Integration Audit

**Scope:** repository reconnaissance only. No application code has been changed in this phase. The work is on `feature/spreadsheet-agent-integration`.

## Executive summary

DocuSight already supports Excel and delimited text as first-class uploads. It parses `.xlsx`, `.xlsm`, `.xls`, `.ods`, `.csv`, and `.tsv`; propagates merged values, heuristically splits multiple regions, attempts multi-row header reconstruction, stores complete parsed tables in per-document DuckDB, indexes row chunks, serves sheet previews and table pagination, and exposes a read-only SQL console and SQL chat tool. Its key advantage is deterministic numeric computation over DuckDB.

The missing layer is a persistent, range-aware workbook representation that retains source coordinates and visual structure. Ingestion converts grids/regions into pandas DataFrames without retaining each table's original Excel range, row/column indices, formulas, style, or merged-range relationships. As a result, SQL and chat can report a sheet/table but cannot reliably cite the source cells. The current schema-guided Extraction workspace is PDF-only. Spreadsheet summary extraction is a separate automatic document summary, not user-defined schema extraction.

Recommendation: keep the existing parser and DuckDB as the base; add a spreadsheet-only typed structural sketch, source-range mappings, allowlisted range/calculation tools, targeted cross-platform range render/LaTeX encoders, bounded extraction/verification, and provenance-aware UI/API. Do not use arbitrary model-generated Python. Do not touch PDF-specific ingestion or extraction paths.

## Repository and service architecture

| Concern | Current implementation |
|---|---|
| Backend/API | FastAPI, `backend/app/main.py`; uploads and document APIs are `/api/documents/...`; chat is streamed with SSE. |
| Pipeline orchestration | `backend/app/pipeline.py::_process` branches PDF vs spreadsheet, then writes DuckDB, metadata, chunks, embeddings, extraction, and ready state. |
| Spreadsheet parsing | `backend/app/ingest/excel.py`: `_grids`, `_regions`, `_parse_region`, `ingest_excel`. |
| Structured tables | `backend/app/tables.py`: `write_tables`, `describe_df`, `preview`, `run_sql`. One DuckDB file per document. |
| Document persistence | `backend/app/store.py::Store`; original source, `meta.json`, chunks, `sheets/{index}.json`, `tables.duckdb`, dense vectors under `data/docs/{doc_id}/`. |
| Metadata | `DocMeta.sheets` and `.tables`; `SheetMeta` and `TableMeta` TypeScript interfaces in `frontend/src/lib/api.ts`. |
| Retrieval | `backend/app/retrieval/hybrid.py::Retriever`: BM25 + dense + optional ColPali (PDF-page images) + RRF/rerank. Spreadsheet chunks identify sheet/section but not original cells. |
| Chat/tool orchestration | `backend/app/llm/agent.py::TOOLS`, `_exec_tool`, `run_agent`: search, list tables, read-only SQL, PDF page view, chart. Uses existing provider abstraction through `backend/app/llm/client.py`. |
| Automatic summary extraction | `backend/app/llm/extraction.py::run_extraction`, `_excel_context`, `heuristic_extraction`; model output includes free-text source strings. |
| Schema-guided extraction | `backend/app/extraction/`; project upload currently rejects non-PDFs and document extraction batches are page-based. It is not an Excel extraction path. |
| Frontend | `AppShell.tsx` exposes Overview, Tables & Data, Pages/Sheets, Retrieval Lab, Extraction. `PagesTab.tsx` displays the sheet value grid. `DataTab.tsx` previews parsed tables, charts and submits SQL. `OverviewTab.tsx` displays summary fields/source strings. |
| Tests | `backend/tests/test_extraction_core.py` tests schema extraction and exports; `test_pdf_evidence.py` covers PDF localization/rendering. No dedicated spreadsheet parser/agent integration tests are present. |

## Actual spreadsheet data flow

1. **Upload:** `backend/app/main.py::upload` checks supported suffix and size (200 MB), classifies `.csv`/`.tsv` as `csv`, other sheet formats as `excel`, calls `store.create`, then `pipeline.submit`.
2. **Grid parsing:** `pipeline._process` calls `ingest_excel` for the non-PDF branch. `_grids` returns whole in-memory sheet grids and small per-sheet stats.
   - CSV uses `pandas.read_csv(header=None, dtype=object, sep=None, engine="python", on_bad_lines="skip")`; TSV fixes tab separation. The full file is read before downstream chunking.
   - `.xlsx`/`.xlsm` use openpyxl with `data_only=True` (cached formula results). Merged top-left values are copied to all cells of each merged range. For files under 15 MB, a second read-only pass counts formula strings. Hidden sheet state and merged/formula counts are kept; hidden rows/columns, formula expressions, styles, named ranges, and Excel table definitions are not retained in `DocMeta`.
   - `.xls`/`.ods` fall back to pandas `read_excel(sheet_name=None, header=None, dtype=object)`.
3. **Region/table detection:** `_regions` splits on fully blank rows then blank columns. `_parse_region` removes title-like rows, heuristically detects simple key/value forms, searches the first ten rows for likely headers, and sometimes joins adjacent header rows. It returns a DataFrame but not the source range or source cell mapping. Region boundaries are not linked across sheets.
4. **Preview and tables:** `ingest_excel` writes each sheet's first 300 rows × 60 columns as `sheets/{index}.json`. Each parsed table gets a generated DuckDB-safe name, title, source sheet, row/column counts and DataFrame column statistics. No source start/end row or column is recorded.
5. **Persistence/indexing:** `pipeline._process` calls `write_tables` (all parsed rows) to `tables.duckdb`; attaches filename/sheet/section context to chunks; saves parent text and chunks. Table summaries include stats and first 40 table rows, truncated to 6,000 characters. Up to 3,000 rows per table are indexed as 25-row Markdown chunks. DuckDB still covers every parsed row. Sheet notes and form-style regions become text chunks.
6. **Retrieval:** hybrid search retrieves row/summary/note chunks, with sheet and section metadata but no exact source cell/range. Spreadsheet visual retrieval is not active; ColPali is a PDF page-image index.
7. **Chat:** the agent can list table schemas and issue a read-only DuckDB query, returning exact computed result values. SQL sources include query/results but no cell provenance. For context mode, spreadsheet content is assembled by `_excel_context` from sheet summaries, first 60 preview rows, and note/summary chunks, bounded by `MAX_CONTEXT_CHARS`; this is not a complete representation for large books. Search-based chat can retrieve row chunks. The LLM can decide which SQL to run, but code itself executes through the constrained `run_sql`, not model-generated Python.
8. **Extraction/UI:** automatic Overview extraction prompts with spreadsheet context and table stats; it is not guaranteed to include all rows and does not validate source references. The separate schema-guided workspace in `backend/app/extraction/api.py` currently accepts PDFs only. In the UI, Sheets displays a capped raw-value preview (no formula/style rendering); Tables & Data paginates loaded DataFrames and offers a manual SQL console.

## Capability audit

| Capability | Verified status |
|---|---|
| Multi-sheet workbooks | Yes: each visible/meaningful sheet is enumerated and previewed; tables are separated by sheet. |
| Multiple tables per sheet | Yes, heuristically: blank row/column gaps define regions. Adjacent regions with no blank separator can be missed or merged. |
| Merged cells | Partially: values are propagated and merged count is reported. The merged range and its anchor/visual span are discarded after parsing. |
| Multi-row/hierarchical headers | Partially: limited adjacent-row heuristics; no general header tree. Header values can be guessed incorrectly. |
| Formula values | Cached values are loaded when available. Formula count is reported for small `.xlsx`/`.xlsm`; actual expressions and recalculation are unavailable. |
| Formatting semantics | No persistent fonts/fills/borders/alignment/number format; no range renderer. |
| Workbook structure | Partial metadata (sheet dims, table names/columns/stats, formula/merge counts, hidden sheet flag); no persisted structural graph/sketch. |
| Relationships between tables | No explicit relation discovery, key/foreign-key inference, or cross-sheet mapping. One workbook's tables share a DuckDB database but remain independent names. |
| Selective spreadsheet ranges | UI previews/tables paginate. API supports table row offsets, but no original-coordinate range query, formula inspection, or range-to-cell source mapping. |
| Exact cell references | No. Original coordinates are lost by `_regions`/`_parse_region` DataFrame conversion; source text from LLM can be free-form but is not validated. |
| Deterministic numerical calculations | Yes: DuckDB read-only SQL (`SELECT`/`WITH`, external access disabled, blocked keywords/functions, one statement, row limit). Table pages also chart a subset in-browser. |
| Large workbook handling | SQL includes all parsed table rows; parsing materializes full sheets in memory; only first 3,000 table rows become search chunks and summary/context is capped. |
| Existing spreadsheet extraction | Auto summary only; no user-defined schema-driven workbook extraction. |
| Human verification | General app can inspect table/grid and edit extraction cells in PDF project workflow; no spreadsheet structure verification, cell-to-result link, or spreadsheet evidence correction flow. |

## Main information-loss points and risks

1. `_regions` receives a grid but returns values only, not `A1:D28` or offset values. Table boundaries cannot be translated back to source coordinates after splitting.
2. Merged-cell propagation creates repeated values. The renderer/consumer cannot distinguish actual value cell from propagated display values without explicit merge metadata.
3. DataFrame normalization changes headers and types; formula expressions, number formats, style cues and raw cell values are unavailable in DuckDB. E.g., accounting strings and percentages are coerced to numerics by `coerce_types`.
4. Parser heuristics may pick title rows or header rows incorrectly; no confidence, verification state, or structured correction feedback is saved.
5. Preview and row serialization truncation mean neither prompt context nor row chunks are a complete workbook representation. SQL is complete only for tables successfully parsed.
6. Formula cached values may be stale or absent; openpyxl does not calculate formulas. Recalculation requires a real spreadsheet engine, and results must not be presented as recomputed without one.
7. SQL output provenance refers to a table/query, not original cells. A correct result can still be difficult to audit in the workbook.
8. CSV separator sniffing/skipped bad rows can silently affect input. Encoding and parse diagnostics are not preserved in sheet metadata.
9. The application currently has no user authentication/authorization. New structure/range endpoints must preserve existing local deployment expectations and must not accept arbitrary filesystem paths or arbitrary Python.

## Research implementation review

The paper describes two stages (structure extraction and solving), incremental local-range exploration, a structural sketch plus row/column summaries, and targeted structure verification with vision and LaTeX representations. It assigns code execution to spreadsheet operations, not a substitute for source fidelity. The verification loop iterates on focused uncertain regions and has a retry limit. These are architecture concepts, not a requirement to reproduce the original hosting stack.

The official implementation README uses a Docker/Jupyter code execution service. It documents an Excel-to-image path based on `win32com` (Windows-only), and requires large Qwen3-Coder and GLM-4.5V deployments for its reported setup. This is a poor direct fit for DocuSight's local-first macOS/Linux app. Recommendation: safe allowlisted Python helpers (no arbitrary execution), range-scoped openpyxl/Pillow rendering with optional LibreOffice fidelity, a local LaTeX-like serialization, and the single existing configured LLM provider where its capability permits. Use bounded rounds/calls, not an autonomous general agent framework.

The paper's claims and reported evaluations are research results, not expected DocuSight performance. No accuracy improvement should be claimed without a benchmark run on appropriate workbook questions.

## Integration opportunities and proposed architecture

1. **Coordinate-preserving parser adapter:** augment Excel region detection with sheet/range boundaries and map each DataFrame row/column back to original cell addresses; preserve raw value, cached formula value, formula text when available, merged range membership and source status. Avoid changing the PDF branch in `pipeline._process`.
2. **Typed structural sketch:** Pydantic-validated JSON, cached under each spreadsheet document (for example `spreadsheet_structure.json`) and versioned against source-file hash/parser version. Include workbook/sheet/region/table hierarchy, range bounds, merged ranges, header tree, summaries, formula/style indicators, cell address mapping, relationships and verification states. YAML can be an optional export, not the internal storage contract.
3. **Workbook Inspector and deterministic tool APIs:** bounded sheet/range retrieval, cell/formula/type lookup, table/range aggregates, and validation. Only predefined operations and allowlisted functions; never execute arbitrary LLM code. Computed outputs include their source addresses/row sets where possible.
4. **Multi-format range encoders:** selected range to HTML/LaTeX-like structured table with `multicolumn`/row-span metadata and to PNG with cell style/merge/border/background/alignment support. Keep rendering limited to requested ranges. Core should work on macOS/Linux; optionally call LibreOffice when installed for better fidelity.
5. **Extraction/verification coordinator:** reuse `llm.client` and its configured provider; ask for bounded structure updates from localized inputs; run bounded verifier calls against uncertain regions; record actual tool events/status and corrections, no chain-of-thought. `vision_pass`/`latex_pass` only when those checks actually ran and were passed. Stop on success or configured retry/call budget; otherwise mark review required.
6. **Serving integration:** add structure/range APIs and agent tools; inject compact structure into spreadsheet chat and guide to targeted cell/range retrieval or DuckDB computation. Preserve exact cell references in source objects. Keep the existing SQL safety model.
7. **Existing UI integration:** extend the current Sheets/Extraction surfaces, not duplicate tabs. Offer workbook/region map, range preview, verification state, formula details and result-to-cell navigation. Extend schema-guided Extraction to Excel/CSV through a spreadsheet-specific service branch; leave PDF extraction path unchanged.

## Dependencies, configuration, and API requirements

- Reuse installed `pandas`, `openpyxl`, `duckdb`, `pydantic` (via FastAPI), and Pillow. Avoid a new agent framework and a new database.
- Optional cross-platform fidelity: LibreOffice executable. It is not required for native range access or fallback Python/Pillow rendering. No Windows COM requirement.
- No new API key is inherently required. Existing `LLM_MODEL` plus its configured provider key supports structure/verification calls. A vision task requires that configured provider/model to support image input; otherwise mark visual verification unavailable. If a later user explicitly configures a second model, that can be optional but should not be the default.
- Proposed endpoints: read cached workbook structure; request/rebuild structure; inspect selected cell range with output format `cells|latex|image`; run verification; start/retrieve schema-guided spreadsheet extraction and results. Exact route shapes should follow current `/api/documents/{id}/...` patterns and existing Extraction project APIs.
- Keep binary workbook files in `data/docs/{id}`; persist sketch/ranges by generated document ID. Do not expose local absolute paths.

## Performance, costs, and trade-offs

- Parsing currently has an in-memory cost proportional to workbook dimensions. First version should preserve the current parser and add limits/bounds, lazy range reads, and a versioned sketch cache. Large CSV chunked reads are a later optimization.
- Structure LLM calls should sample/inspect regions, not transmit entire workbooks. Cache per source hash and parser version; invalidate on re-upload/change.
- Run deterministic inspection and LaTeX serialization without model calls. Gate visual calls and iterative verification behind configuration/user action because they incur image token/call costs.
- Initial rendering can approximate Excel display using openpyxl style metadata and Pillow; optional LibreOffice improves fidelity but adds a system install and cold-start/runtime dependency.
- Preserve raw and normalized views. DuckDB's numeric normalization is excellent for aggregate queries but must not replace original values or exact cell references.

## Recommended implementation sequence (approval required before application-code changes)

1. Add typed structure/range contracts and deterministic tests.
2. Extend only the spreadsheet ingestion branch to persist range/cell provenance and a deterministic baseline sketch.
3. Add bounded inspector/LaTeX/rendering services and API endpoints, with cross-platform tests.
4. Add LLM-driven structure extraction and targeted verification using the existing provider abstraction, bounded retry budgets and event logs.
5. Integrate structure and exact source references into SQL/chat/retrieval while preserving `run_sql` read-only constraints.
6. Extend the existing spreadsheet viewer and schema-guided Extraction workspace with cell navigation, verification and extraction evidence.
7. Run unit tests, API end-to-end upload→parse→sketch→range inspection→verification→query/extraction→source-cell UI tests, plus PDF regression suite unchanged. Include large workbook/resource limits and a macOS/Linux renderer matrix.

No application code should be modified until the user approves this plan, as requested in the supplied project instructions.
