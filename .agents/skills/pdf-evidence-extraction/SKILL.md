---
name: pdf-evidence-extraction
description: Build or maintain evidence-grounded PDF extraction, source localization, human review, and annotated-PDF rendering in DocuSight.
---

# PDF evidence extraction

## Purpose and activation

Use this skill when changing schema-guided PDF extraction, evidence coordinates, OCR localization, evidence review, or rendering of source PDFs in DocuSight.

## Existing system and contracts

- The API is FastAPI in `backend/app`; the UI is Next.js in `frontend/`.
- Original uploaded PDFs are stored at `data/docs/{generated_document_id}/source.pdf` (the extension follows the upload). Never use a client-provided path.
- Page previews are filesystem-rendered PNGs in `data/docs/{id}/pages/{page:04d}.png`.
- Extraction projects and observations are atomically persisted as JSON in `data/extraction_projects/{project_id}.json`; this is not a relational database.
- PyMuPDF is the native text, geometry, and PDF rendering library. OCR is DeepSeek-OCR-2 through `backend/app/ingest/ocr.py`, but the current adapter returns markdown/tables, not word-level geometry.
- Evidence uses a 1-based page number. Normalized coordinates use top-left origin and `[0,1]` values relative to visible, rotated `page.rect`; native PDF points and page dimensions/rotation/boxes are retained for audit.
- Persist evidence text, offsets, verification, localization status/method, coordinate metadata, corrections, and review history.

## Workflow

1. Inspect repository instructions, extraction/storage contracts, PDF parsing, OCR, API, and UI before changing code.
2. Preserve query-guided schema discovery and explicit observation units. Require verbatim evidence for supported values; keep unsupported values empty/null with a review issue.
3. Validate the cited 1-based page against the generated document ID and open the original filesystem PDF.
4. Search native PDF text and word boxes. Normalize Unicode (NFKC), case, whitespace, and punctuation conservatively. Require the full quote or a contiguous word sequence. A result with multiple occurrences must remain marked `MULTIPLE_MATCHES`; do not silently take the first.
5. Validate each rectangle: finite values, positive size, page bounds, and matching evidence page. Store normalized and native-space metadata plus page rotation, CropBox, MediaBox, origin, method, and validation result.
6. If native text has no reliable match, use `LOCATION_NOT_FOUND`. OCR geometry is acceptable only when the OCR provider supplies per-word boxes and deterministic transforms are validated. The current adapter does not supply boxes; never infer them from markdown.
7. Persist evidence with the project's atomic JSON store. Keep source PDFs immutable. If relational persistence is added later, use transactions and foreign keys without storing PDF bytes in database rows.
8. Render from the original PDF and saved coordinates. Write an idempotent separate output under the project render directory; never overwrite the original. Validate IDs, pages, and boxes.
9. Show selected value, quote, status, page image/PDF, and highlight together. Support human correction and retain prior values/coordinates in review history.
10. Add deterministic tests for coordinate conversion, rotations, ambiguous matches, correction persistence, and original-preserving rendering. Report actual results; do not claim accuracy without benchmark evidence.

## Localization status vocabulary

Use `NATIVE_TEXT_MATCHED`, `NATIVE_WORD_MATCHED`, `MULTIPLE_MATCHES`, `HUMAN_VERIFIED`, `LOCATION_NOT_FOUND`, or `LOCATION_AMBIGUOUS`. Reserve model-coordinate states for a future validated model-coordinate contract. Do not claim a text match is verified solely because an LLM/OCR returned a plausible string.

## Security and filesystem rules

- Use generated document/project IDs and resolve paths through `store.source_path` and configured storage roots.
- Validate PDF type, page bounds, request IDs, and boxes before reading/writing.
- Never return internal absolute paths in API payloads.
- Use temporary files and atomic rename for generated PDFs; keep originals untouched.
- Do not log document contents or credentials.

## Error handling

Use meaningful 4xx errors for missing IDs, invalid page numbers, boxes, ambiguous render selection, and missing originals. Keep extraction value status separate from localization status. Localization failure must not turn an unsupported value into a supported one.

## Definition of done

- Evidence is stored with explicit, validated coordinate space and localization status.
- Ambiguous and unavailable locations are explicit and never fabricated.
- Users can inspect and correct evidence geometry.
- Annotated output derives from the original, is separate/repeatable, and follows existing API conventions.
- Tests cover normal and failure cases; OCR geometry limitations are documented.
