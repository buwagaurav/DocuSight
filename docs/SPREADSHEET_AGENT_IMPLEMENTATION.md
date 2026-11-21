# SpreadsheetAgent Integration Implementation Plan

**Status:** implemented on `feature/spreadsheet-agent-integration`; locally validated.
**Branch:** `feature/spreadsheet-agent-integration`.

## Goals

- Preserve source-cell evidence for spreadsheet answers.
- Add a compact, validated workbook structure and targeted verification loop.
- Reuse DocuSight's current spreadsheet parser, DuckDB, provider abstraction, API, and UI.
- Keep PDF code paths unchanged.
- Avoid arbitrary model-generated code and unnecessary infrastructure.

## Work packages

### A. Contracts and deterministic baseline

1. Define typed models for workbook/sheet/region/table/header/cell evidence, coordinates, verification status, and tool events.
2. Add deterministic parser tests covering XLSX, XLSM, CSV, TSV, multi-sheet workbooks, multiple regions, hierarchical headers, merged cells, formulas with/without cached values, hidden sheets, empty sheets, and malformed input.
3. Define explicit format limitations for XLS/ODS and ensure unsupported evidence is marked unavailable.

**Acceptance:** sketch JSON validates, has source hash/parser version, stable source addresses, and distinguishes raw, normalized, formula, cached, and missing values.

### B. Spreadsheet-only coordinate mappings

1. Extend spreadsheet region detection to preserve inclusive source bounds and map each parsed DataFrame field to its source cell.
2. Preserve existing table names, columns, ordering, previews, and DuckDB writes.
3. Persist the deterministic sketch with document artifacts and rebuild only when source hash/parser version changes.
4. Add limits and diagnostics without broad parser rewrites.

**Acceptance:** an uploaded workbook's detected table rows map to exact original worksheet coordinates; existing PDF pipeline and spreadsheet SQL behavior remain compatible.

### C. Inspector and representations

1. Implement allowlisted, bounded sheet/range/cell inspection and range summaries.
2. Add coordinate-preserving compact and LaTeX-like serializers.
3. Add local PNG rendering for bounded regions using openpyxl/Pillow; optionally probe LibreOffice without requiring it.
4. Enforce cell, bytes, dimensions, and runtime caps.

**Acceptance:** callers can ask for a bounded range and receive source-anchored cells; rendering states accurately represent success/unavailable/failure.

### D. Structure extraction and verification

1. Add a coordinator that consumes compact sheet/region summaries and localized ranges.
2. Use the existing `llm.client` configuration; validate all structured output and resolve cited references with the local inspector.
3. Add bounded rounds and calls, configurable caps, and persisted event/status records.
4. Run vision checks only when configured provider/model supports image input. Mark skipped checks unavailable.
5. Preserve deterministic sketch and mark unresolved findings needs-review when output fails or the retry budget is exhausted.

**Acceptance:** fake-client tests demonstrate bounded tool calls, invalid-reference rejection, retry termination, and no false “verified” state when checks did not run.

### E. API, chat, extraction, and provenance

1. Add document-scoped sketch, range inspection, and verification APIs.
2. Add safe spreadsheet chat tools for structure/ranges and keep DuckDB SQL read-only.
3. Preserve table-row/source-row mapping for query results where deterministically available.
4. Add a spreadsheet-specific schema-guided extraction service and evidence model. Do not refactor the PDF extractor.
5. Add API tests for missing docs, non-spreadsheet docs, invalid ranges, output caps, and valid evidence responses.

**Acceptance:** schema results cite actual sheet/cell/range evidence or explicitly have no evidence; invalid or hallucinated references cannot be marked as verified.

### F. Frontend

1. Extend current Sheets view with detected structure and range navigation.
2. Add formula/cached-value and verification-state display where available.
3. Let extraction results navigate to the source cells/ranges.
4. Keep existing app navigation and PDF flows intact.

**Acceptance:** UI can select a result's referenced sheet and range, and labels distinguish verified, needs-review, and unavailable evidence.

## End-to-end verification plan (run after approval and implementation)

1. Create temporary CSV and XLSX fixtures with multiple sheets, regions, hierarchical headers, merged cells, formulas and cached/missing values.
2. Upload through the API and wait for the existing document pipeline to mark each spreadsheet ready.
3. Read the persisted workbook sketch; verify source hash/version and source ranges.
4. Inspect bounded ranges and cells; compare returned raw/formula/cached content with fixture values.
5. Exercise serializer and image-render endpoints (or documented unavailable status if optional renderer/provider absent).
6. Run structure extraction and verification with a deterministic fake LLM; separately test configured live provider only if credentials are already available and the user approves any billable calls.
7. Run schema-guided extraction against known fields; assert values and exact source references.
8. Run chat lookup and numeric aggregate; verify SQL remains read-only and source-row provenance is accurate.
9. Exercise the UI source-navigation path against the API responses.
10. Run existing backend unit/API tests and frontend type/lint/build checks; run the PDF evidence and PDF extraction regression tests to confirm their behavior remains unchanged.
11. Include boundary checks for oversized ranges, large workbook limits, malformed sketches, missing document IDs, formulas without cached values, and exhausted retries.

Do not claim full end-to-end completion until upload-to-UI source navigation has been exercised. Do not run live provider calls that can incur charges without explicit approval.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Existing whole-sheet parsing uses memory proportional to workbook size | Preserve current behavior initially; avoid duplicate grids, cap new sketch/render output, and keep chunked CSV as a follow-up optimization. |
| Formula values are absent/stale | Store formula and cached result separately; never recalculate silently; optionally offer LibreOffice-based recalculation as a distinct capability. |
| Header/region heuristics are imperfect | Store confidence and review state; allow targeted verification and user correction. |
| DataFrame normalization loses source details | Keep additive raw-cell and source-coordinate metadata beside normalized tables. |
| Model suggests invalid evidence | Validate ranges and values against local inspector before marking evidence verified. |
| Renderer/provider unsupported on a machine | Make image/vision checks optional and report unavailable accurately. |
| PDF regression | Limit code changes to spreadsheet services/branches, and include PDF regression checks. |

## Verification results

- Backend: all 14 tests pass, including HTTP spreadsheet upload → ingest → persisted structure → source range inspection, coordinate-grounded schema extraction, existing extraction tests, and PDF evidence regression tests.
- Frontend: ESLint and TypeScript `--noEmit` pass.
- Production build: blocked because Next.js could not fetch the repository's existing Geist fonts from Google Fonts in this network-restricted environment.
- Live provider verification: not run; it requires `EXTRACTION_CLOUD_ENABLED=true`, credentials, and explicit range-sharing consent in the UI.

## Remaining limits

- Spreadsheet field matching currently uses normalized schema field names/descriptions against detected table column names; it does not infer arbitrary synonyms.
- Table source row/column mappings are persisted, but unusual merged/multi-level headers and parser transformations may still be marked unsupported when a value cannot be matched back to its source cell.
- Vision verification is reported unavailable. The model review endpoint sends only selected bounded cell ranges and records a model review, not a human verification.
- No live Gemini/Anthropic/DeepSeek call or full browser-driven manual UI session was performed in this run.
- No commit or push was made.
