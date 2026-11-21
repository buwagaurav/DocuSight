# SpreadsheetAgent Integration Setup

This is the local runtime setup for the SpreadsheetAgent-inspired features implemented in DocuSight. Spreadsheet structure capture, range inspection, local image/LaTeX rendering, and schema-guided extraction use local application dependencies. Optional model review requires the existing configured provider plus explicit range-sharing consent.

## Required local software

- Python version and Node.js version already required by the current DocuSight backend/frontend (use the versions pinned by the repository if present).
- Existing backend dependencies in `backend/requirements.txt` and frontend dependencies in the frontend package manifest.
- A supported LLM provider credential only for model-assisted structure interpretation/verification and LLM chat/extraction. Deterministic parsing, range inspection, serialization, and DuckDB operations should work without an API credential.
- Optional LibreOffice for enhanced workbook rendering or explicitly requested recalculation. Basic inspection/rendering should use Python/openpyxl/Pillow and must not require Microsoft Excel or Windows COM.

## Python dependencies

The current backend already declares the core expected packages: `pandas`, `openpyxl`, `duckdb`, `pillow`, FastAPI/Pydantic (through FastAPI), and existing LLM clients. The implementation should first reuse these rather than add an agent framework or a second database. Check optional spreadsheet-format support against current `xlrd`/pandas capabilities. Only add a package if an implemented feature cannot reasonably use the current stack, and document its platform requirements.

## API credentials and configuration

No new API key is expected. Structure extraction and verification use the same provider configuration already consumed by `backend/app/llm/client.py` and `backend/app/config.py`:

- Anthropic: existing Anthropic key/profile configuration.
- Gemini: `GEMINI_API_KEY` when `LLM_PROVIDER=gemini`.
- DeepSeek: `DEEPSEEK_API_KEY` and its configured base URL when `LLM_PROVIDER=deepseek`.

The selected `LLM_MODEL` must support the operation being requested. Image-based verification additionally requires a vision-capable selected model/provider; otherwise image verification should be reported unavailable. Keep secrets in the existing local `.env` and never add them to source control. The implementation may add bounded-call settings such as maximum verification rounds/calls, with conservative defaults and documentation, but must not require a second model by default.

## Optional LibreOffice

LibreOffice is optional and intended only where higher-fidelity range rendering or explicit formula recalculation is needed. Detect it at runtime and report whether it is available. Do not make the application fail to start without it. Recalculation must be an explicit operation because it may change formula caches and should not be implied by reading a workbook.

## Data and storage

The proposed workbook structure and verification events live under the existing generated document directory, e.g. `data/docs/{doc_id}/spreadsheet_structure.json`, alongside the original workbook, sheet previews, metadata, chunks, and DuckDB database. The sketch is rebuildable from the source file and versioned by source hash/parser version. It must not store absolute source paths or API credentials.

## Development and run flow after implementation approval

1. Install backend requirements in the project's existing virtual environment.
2. Install frontend packages using the repository's existing package manager and lockfile.
3. Copy/update the local environment file using the existing provider settings if model features are needed; no model key is needed for local deterministic workbook operations.
4. Start backend and frontend using the repository's documented development commands.
5. Upload a CSV/XLSX using the main Documents upload.
6. In Pages/Sheets, inspect detected ranges and A1 ranges; the UI shows cell addresses, cached formula values, and formulas when available.
7. In Extraction, attach the uploaded workbook to a project, save a schema whose field names match table columns, and run `Extract spreadsheet rows locally`.
8. Select an extracted field and use `Open source cells` to view the evidence range. Spreadsheet chat can also call the bounded range inspector.
9. To request model review, enable the explicit range-sharing consent in Sheets and choose `Ask model to review`. Cloud extraction must be enabled in `.env`; review calls can use the configured provider's quota.

Use the repository README's actual commands and supported runtime versions; this file intentionally does not invent commands not verified in the current docs.

## Research implementation is not a local dependency

Do not install the official SpreadsheetAgent's deployed Qwen/GLM model stack for this design. The paper's reported setup relies on large hosted/GPU model infrastructure; it is not needed to integrate its structural extraction and verification ideas into DocuSight. The official implementation's Windows `win32com` rendering path is also not a requirement for this cross-platform design.
