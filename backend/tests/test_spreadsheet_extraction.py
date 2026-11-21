"""Spreadsheet schema extraction: table identity, source-cell addresses, full reads, workbook opened once."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import openpyxl
from openpyxl import Workbook

from app.extraction.spreadsheet import extract_document, match_columns
from app.ingest.excel import ingest_excel
from app.spreadsheet_structure import build_structure, latex_range
from app.store import store
from app.tables import describe_df, write_tables


def _field(fid: str, name: str, data_type: str = "string") -> dict:
    return {"id": fid, "name": name, "data_type": data_type}


FIELDS = [_field("customer", "Customer"), _field("revenue", "Revenue", "number"), _field("region", "Region")]


class SpreadsheetExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_root = store.root
        store.root = Path(self.temp.name) / "docs"
        store.root.mkdir()
        self.docs = []

    def tearDown(self):
        for doc in self.docs:
            store.delete(doc.id)
        store.root = self.old_root
        self.temp.cleanup()

    def _ingest(self, build) -> str:
        """Ingest a workbook through the real parser + structure builder (as pipeline._process does)."""
        wb = Workbook()
        build(wb)
        data = io.BytesIO()
        wb.save(data)
        doc = store.create("book.xlsx", "excel", data.getvalue())
        self.docs.append(doc)
        doc.status = "ready"
        chunks, parents, tables, info = ingest_excel(doc.id, store.source_path(doc.id), lambda *_: None)
        store.set_chunks(doc.id, chunks, parents)
        meta = [{"name": t["name"], "title": t["title"], "source": t["source"], "sheet": t["sheet"],
                 "n_rows": len(t["df"]), "n_cols": len(t["df"].columns), "columns": describe_df(t["df"]),
                 "source_bounds": t["source_bounds"], "header_row": t["header_row"],
                 "data_start_row": t["data_start_row"], "source_row_offsets": t["source_row_offsets"],
                 "source_column_offsets": t["source_column_offsets"]} for t in tables]
        write_tables(store.tables_db(doc.id), tables)
        doc.sheets, doc.tables = info["sheets"], meta
        build_structure(doc.id, info, meta, store.source_path(doc.id))
        return doc.id

    def _values(self, observations, field="customer"):
        return sorted(o["fields"][field]["normalized_value"] for o in observations if field in o["fields"])

    def test_two_stacked_tables_on_one_sheet_keep_all_rows(self):
        def build(wb):
            ws = wb.active
            ws.title = "Summary"
            for row in (["Customer", "Revenue", "Region"], ["Acme", 1250, "West"], ["Beta", 900, "East"],
                        [], [], ["Customer", "Revenue", "Region"], ["Gamma", 77, "North"], ["Delta", 88, "South"]):
                ws.append(row)
        observations, report = extract_document(self._ingest(build), FIELDS)
        self.assertEqual(self._values(observations), ["Acme", "Beta", "Delta", "Gamma"])
        self.assertEqual(len({o["id"] for o in observations}), 4)
        by_name = {o["fields"]["customer"]["normalized_value"]: o for o in observations}
        self.assertEqual(by_name["Acme"]["fields"]["revenue"]["evidence"][0]["address"], "B2")
        self.assertEqual(by_name["Gamma"]["fields"]["revenue"]["evidence"][0]["address"], "B7")
        self.assertTrue(all(c["evidence"][0]["verified"] for o in observations for c in o["fields"].values()))
        self.assertEqual(len(report), 2)

    def test_side_by_side_tables_sharing_rows_stay_separate(self):
        def build(wb):
            ws = wb.active
            ws.title = "S"
            ws.append(["Customer", "Revenue", "Region", None, "Customer", "Revenue", "Region"])
            ws.append(["Acme", 1, "West", None, "Zeta", 2, "North"])
            ws.append(["Beta", 3, "East", None, "Eta", 4, "South"])
        observations, _ = extract_document(self._ingest(build), FIELDS)
        self.assertEqual(self._values(observations), ["Acme", "Beta", "Eta", "Zeta"])
        zeta = next(o for o in observations if o["fields"]["customer"]["normalized_value"] == "Zeta")
        self.assertEqual(zeta["fields"]["revenue"]["evidence"][0]["address"], "F2")
        self.assertEqual(zeta["label"], "S row 2 · table E1:G3")
        self.assertEqual(len({o["label"] for o in observations}), 4)

    def test_title_row_above_table_does_not_shift_addresses(self):
        def build(wb):
            ws = wb.active
            ws.title = "Report"
            for row in (["Quarterly sales report", None, None], ["Customer", "Revenue", "Region"],
                        ["Acme", 1250, "West"], ["Beta", 900, "East"]):
                ws.append(row)
        observations, _ = extract_document(self._ingest(build), FIELDS)
        acme = next(o for o in observations if o["fields"]["customer"]["normalized_value"] == "Acme")
        self.assertEqual(acme["fields"]["revenue"]["evidence"][0]["address"], "B3")
        self.assertTrue(acme["fields"]["revenue"]["evidence"][0]["verified"])

    def test_reads_every_row_and_opens_the_workbook_once(self):
        def build(wb):
            ws = wb.active
            ws.title = "Big"
            ws.append(["Customer", "Revenue", "Region"])
            for i in range(6200):   # more than one 5,000-row page
                ws.append([f"C{i}", i, "R"])
        doc_id = self._ingest(build)
        real_load = openpyxl.load_workbook
        with mock.patch("openpyxl.load_workbook", side_effect=real_load) as load:
            observations, _ = extract_document(doc_id, FIELDS)
        self.assertEqual(len(observations), 6200)
        self.assertEqual(load.call_count, 2)   # formulas + cached values, once for the whole document
        last = max(observations, key=lambda o: o["fields"]["revenue"]["normalized_value"])
        self.assertEqual(last["fields"]["revenue"]["evidence"][0]["address"], "B6201")

    def test_unmatched_fields_are_reported(self):
        def build(wb):
            ws = wb.active
            for row in (["Customer", "Revenue", "Segment"], ["Acme", 1, "A"], ["Beta", 2, "B"]):
                ws.append(row)
        _, report = extract_document(self._ingest(build), FIELDS + [_field("hq", "Headquarters")])
        self.assertEqual(set(report[0]["unmatched"]), {"region", "hq"})


class MatchColumnsTests(unittest.TestCase):
    def test_exact_then_unique_leaf_of_multirow_header(self):
        matches, problems = match_columns(
            [_field("rev", "Revenue"), _field("cust", "customer name"), _field("q", "Q1")],
            ["Customer Name", "2025 / Revenue", "2024 / Q1", "2025 / Q1"])
        self.assertEqual(matches, {"cust": 0, "rev": 1})
        self.assertTrue(problems["q"].startswith("ambiguous"))

    def test_exact_header_wins_over_leaf(self):
        matches, _ = match_columns([_field("rev", "Revenue")], ["2025 / Revenue", "Revenue"])
        self.assertEqual(matches, {"rev": 1})


class LatexTests(unittest.TestCase):
    def test_special_characters_are_escaped_and_zero_is_kept(self):
        with mock.patch("app.spreadsheet_structure.inspect_range",
                        return_value={"rows": [[{"value": "#1 {a} $5 ~x^2 50%"}, {"value": 0}]]}):
            tex = latex_range("d", "S", "A1:B1")
        self.assertIn(r"\#1 \{a\} \$5 \textasciitilde{}x\textasciicircum{}2 50\% & 0 \\", tex)


if __name__ == "__main__":
    unittest.main()
