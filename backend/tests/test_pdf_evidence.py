"""Geometry and original-PDF rendering regressions using generated PDFs."""
from __future__ import annotations

import hashlib
import shutil
import unittest
import uuid

import pymupdf

from app.extraction.coordinates import PDFCoordinateLocator
from app.extraction.rendering import render_annotated_pdf
from app.store import store


class PDFEvidenceTests(unittest.TestCase):
    def _document(self, *, rotation: int = 0) -> tuple[str, str]:
        doc_id = uuid.uuid4().hex[:12]
        pdf = pymupdf.open()
        page = pdf.new_page(width=612, height=792)
        page.insert_text((72, 100), "Revenue: $1,250 USD")
        page.insert_text((72, 150), "Repeated value 1,250 appears twice")
        page.insert_text((72, 200), "Repeated value 1,250 appears twice")
        page.set_rotation(rotation)
        payload = pdf.tobytes()
        pdf.close()
        meta = store.create("evidence-test.pdf", "pdf", payload)
        # Keep the generated ID from the shared registry and avoid ingest side effects.
        return meta.id, "Revenue: $1,250 USD"

    def test_quote_coordinates_are_normalized_and_rotated(self):
        doc_id, quote = self._document(rotation=90)
        try:
            with PDFCoordinateLocator(doc_id) as locator:
                result = locator.locate(1, quote)
            self.assertEqual(result["coordinate_status"], "NATIVE_TEXT_MATCHED")
            self.assertTrue(result["localization_validation"]["valid"])
            box = result["coordinates"][0]
            self.assertTrue(0 <= box["x"] <= 1 and 0 <= box["y"] <= 1)
            self.assertTrue(box["x"] + box["width"] <= 1.000001)
            self.assertEqual(box["page_rotation"], 90)
            self.assertEqual(box["coordinate_origin"], "top_left")
        finally:
            store.delete(doc_id)
            shutil.rmtree(store.root.parent / "extraction_projects" / "rendered" / doc_id, ignore_errors=True)

    def test_repeated_evidence_is_not_arbitrarily_resolved(self):
        doc_id, _ = self._document()
        try:
            with PDFCoordinateLocator(doc_id) as locator:
                result = locator.locate(1, "1,250")
            self.assertEqual(result["coordinate_status"], "MULTIPLE_MATCHES")
            self.assertEqual(len(result["coordinates"]), 3)
        finally:
            store.delete(doc_id)
            shutil.rmtree(store.root.parent / "extraction_projects" / "rendered" / doc_id, ignore_errors=True)

    def test_annotated_output_preserves_original_and_is_idempotent(self):
        doc_id, quote = self._document(rotation=90)
        try:
            original = store.source_path(doc_id)
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            with PDFCoordinateLocator(doc_id) as locator:
                location = locator.locate(1, quote)
            project = {"id": "render-test", "name": "Render test", "schema": {"fields": [{"id": "revenue", "name": "Revenue"}]},
                "observations": [{"id": "row-1", "fields": {"revenue": {"review_status": "unreviewed", "evidence": [
                    {"document_id": doc_id, "page": 1, "text": quote, **location}]}}}]}
            output = render_annotated_pdf(project, observation_id="row-1", field_id="revenue")
            self.assertTrue(output.is_file())
            self.assertNotEqual(output, original)
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), original_hash)
            rendered = pymupdf.open(str(output))
            self.assertTrue(rendered[0].get_drawings())
            rendered.close()
            self.assertEqual(render_annotated_pdf(project, observation_id="row-1", field_id="revenue"), output)
        finally:
            store.delete(doc_id)
            shutil.rmtree(store.root.parent / "extraction_projects" / "rendered" / doc_id, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
