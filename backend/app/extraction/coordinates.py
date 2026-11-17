"""Evidence localization in a documented canonical normalized page space.

Normalized boxes have a top-left origin and are relative to the visible, rotated
page rectangle (PyMuPDF page.rect). Native PyMuPDF point coordinates are also
kept for audit. OCR coordinate recovery is deliberately unavailable until the
configured OCR adapter returns word-level boxes.
"""
from __future__ import annotations

import re
from typing import Any

import pymupdf

from ..store import store
from ..ingest.pdf import _MUPDF_LOCK as _LOCK



class PDFCoordinateLocator:
    """Own one PDF handle per extraction worker and return normalized rectangles."""

    def __init__(self, document_id: str):
        self.document_id = document_id
        self.document = None
        meta = store.docs.get(document_id)
        if meta and meta.kind == "pdf":
            try:
                with _LOCK:
                    self.document = pymupdf.open(str(store.source_path(document_id)))
            except Exception:
                self.document = None

    def locate(self, page_number: int | None, quote: str) -> dict[str, Any]:
        unavailable = {"coordinates": [], "coordinate_status": "LOCATION_NOT_FOUND", "coordinate_source": None,
                       "localization_method": None, "localization_validation": {"valid": False}}
        if self.document is None or not page_number or page_number > self.document.page_count or not quote.strip():
            return unavailable
        try:
            with _LOCK:
                page = self.document[page_number - 1]
                rects = page.search_for(quote, quads=False)
                method = "NATIVE_TEXT_MATCHED"
                if not rects:
                    rects = self._match_words(page, quote)
                    method = "NATIVE_WORD_MATCHED"
                bounds = page.rect
                if not rects or bounds.width <= 0 or bounds.height <= 0:
                    return unavailable
                located = []
                for rect in rects[:20]:
                    rotated = rect * page.rotation_matrix if page.rotation else rect
                    x0 = max(bounds.x0, min(bounds.x1, rotated.x0))
                    y0 = max(bounds.y0, min(bounds.y1, rotated.y0))
                    x1 = max(x0, min(bounds.x1, rotated.x1))
                    y1 = max(y0, min(bounds.y1, rotated.y1))
                    located.append({
                        "x0": round(x0, 2), "y0": round(y0, 2), "x1": round(x1, 2), "y1": round(y1, 2),
                        "x": round((x0 - bounds.x0) / bounds.width, 6),
                        "y": round((y0 - bounds.y0) / bounds.height, 6),
                        "width": round((x1 - x0) / bounds.width, 6),
                        "height": round((y1 - y0) / bounds.height, 6),
                        "page_width": round(bounds.width, 2), "page_height": round(bounds.height, 2),
                        "page_rotation": page.rotation,
                        "pdf_x0": round(rect.x0, 2), "pdf_y0": round(rect.y0, 2),
                        "pdf_x1": round(rect.x1, 2), "pdf_y1": round(rect.y1, 2),
                        "coordinate_origin": "top_left",
                        "coordinate_system": "normalized_visible_rotated_page",
                        "crop_box": [round(v, 2) for v in page.cropbox],
                        "media_box": [round(v, 2) for v in page.mediabox],
                    })
                valid = all(0 <= r["x"] <= 1 and 0 <= r["y"] <= 1 and
                            0 < r["width"] <= 1 and 0 < r["height"] <= 1 and
                            r["x"] + r["width"] <= 1.000001 and r["y"] + r["height"] <= 1.000001 for r in located)
                if not valid:
                    return {**unavailable, "coordinate_status": "MODEL_COORDINATES_INVALID",
                            "localization_validation": {"valid": False}}
                status = "MULTIPLE_MATCHES" if len(located) > 1 else method
                return {"coordinates": located, "coordinate_status": status, "coordinate_source": "pdf_text",
                        "localization_method": status,
                        "localization_validation": {"valid": True, "text_matched": True, "bounds_valid": True,
                                                    "matches": len(located)}}
        except Exception:
            return unavailable

    @staticmethod
    def _match_words(page, quote: str):
        """Fallback for whitespace/layout differences; only match contiguous PDF words."""
        target = _normalized_tokens(quote)
        if not target:
            return []
        words = page.get_text("words")
        tokens: list[str] = []
        token_words: list[int] = []
        for index, word in enumerate(words):
            for token in _normalized_tokens(str(word[4])):
                tokens.append(token)
                token_words.append(index)
        matches = []
        for start in range(len(tokens)):
            if tokens[start:start + len(target)] == target:
                word_indices = sorted(set(token_words[start:start + len(target)]))
                group = [words[index] for index in word_indices]
                # A quote can wrap lines; preserve a separate rectangle for each line.
                lines: dict[tuple[int, int, int], list[tuple]] = {}
                for word in group:
                    lines.setdefault((int(word[5]), int(word[6]), int(word[7])), []).append(word)
                matches.extend(pymupdf.Rect(min(w[0] for w in line), min(w[1] for w in line),
                                            max(w[2] for w in line), max(w[3] for w in line))
                               for line in lines.values())
        return matches

    def close(self) -> None:
        if self.document is not None:
            with _LOCK:
                self.document.close()
            self.document = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _normalized_tokens(text: str) -> list[str]:
    import unicodedata
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"\w+", normalized)
