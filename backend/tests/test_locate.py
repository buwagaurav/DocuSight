"""Locating Overview values in the original file: PDF page boxes and spreadsheet cells."""
from __future__ import annotations

import json
import unittest

import pymupdf

from app.locate import locate
from app.store import store


class LocatePdfTests(unittest.TestCase):
    def setUp(self):
        pdf = pymupdf.open()
        pdf.new_page(width=612, height=792).insert_text((72, 100), "Cover page")
        page = pdf.new_page(width=612, height=792)
        page.insert_text((72, 100), "Invoice total: $1,250.00")
        page.insert_text((72, 150), "Regions: North America")
        page.insert_text((72, 200), "and EMEA")
        meta = store.create("locate-test.pdf", "pdf", pdf.tobytes())
        pdf.close()
        meta.n_pages = 2
        self.doc_id = meta.id

    def tearDown(self):
        store.delete(self.doc_id)

    def test_exact_value_found_on_hinted_page(self):
        r = locate(self.doc_id, "$1,250.00", "p.2")
        self.assertEqual((r["status"], r["page"]), ("found", 2))
        box = r["boxes"][0]
        self.assertTrue(0 < box["x"] < 1 and 0 < box["y"] < 0.2 and box["width"] > 0)

    def test_wrong_page_hint_falls_back_to_other_pages(self):
        r = locate(self.doc_id, "$1,250.00", "p.1")
        self.assertEqual((r["status"], r["page"]), ("found", 2))

    def test_reformatted_number_matches_numeric_core(self):
        r = locate(self.doc_id, "USD 1,250.00", "p.2")
        self.assertEqual(r["page"], 2)
        self.assertTrue(r["boxes"])

    def test_list_value_highlights_each_part(self):
        r = locate(self.doc_id, "North America, EMEA", "p.2")
        self.assertEqual((r["status"], len(r["boxes"])), ("partial", 2))

    def test_missing_value_keeps_cited_page_without_boxes(self):
        r = locate(self.doc_id, "Not present anywhere", "p.2")
        self.assertEqual((r["status"], r["page"], r["boxes"]), ("not_found", 2, []))


class LocateSheetTests(unittest.TestCase):
    def setUp(self):
        meta = store.create("locate-test.xlsx", "excel", b"")
        meta.sheets = [{"index": 0, "name": "Notes", "rows": 1, "cols": 1, "tables": []},
                       {"index": 1, "name": "Summary", "rows": 3, "cols": 3, "tables": []}]
        meta.tables = [{"name": "summary", "sheet": "Summary"}]
        sheets = store.dir(meta.id) / "sheets"
        sheets.mkdir(parents=True, exist_ok=True)
        (sheets / "0.json").write_text(json.dumps({"name": "Notes", "rows": [["hello"]]}))
        (sheets / "1.json").write_text(json.dumps({"name": "Summary", "rows": [
            ["Region", "Q1", "Q2"], ["North America", "4126010", "4305357"], ["EMEA", "3111123", "3244364"]]}))
        self.doc_id = meta.id

    def tearDown(self):
        store.delete(self.doc_id)

    def test_cell_reference_is_used_when_value_matches(self):
        r = locate(self.doc_id, "4,305,357", "sheet 'Summary'!C2")
        self.assertEqual((r["status"], r["sheet_index"], r["cells"]), ("found", 1, [{"row": 1, "col": 2}]))

    def test_value_found_by_search_via_table_hint(self):
        r = locate(self.doc_id, "$3,111,123", "table 'summary'")
        self.assertEqual((r["sheet_index"], r["cells"]), (1, [{"row": 2, "col": 1}]))

    def test_list_value_marks_each_cell(self):
        r = locate(self.doc_id, "Q1 4126010; Q2 4305357", "table 'summary'")
        self.assertEqual(r["status"], "partial")
        self.assertIn({"row": 1, "col": 1}, r["cells"])
        self.assertIn({"row": 1, "col": 2}, r["cells"])

    def test_computed_value_reports_not_found_with_sheet(self):
        r = locate(self.doc_id, "10,705,464", "table 'summary' column statistics")
        self.assertEqual((r["status"], r["sheet_index"], r["cells"]), ("not_found", 1, []))


if __name__ == "__main__":
    unittest.main()
