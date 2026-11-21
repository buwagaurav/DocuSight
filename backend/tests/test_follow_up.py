"""The follow-up pass re-asks only for empty cells and never overwrites or adds rows."""
from __future__ import annotations

import threading
import unittest
from unittest import mock

from app.extraction import service
from app.extraction.service import _follow_up, _normalize_model_record, _row_gaps, normalize_fields
from app.store import DocMeta, store

DOC = "follow-up-test-doc"
PAGE = "Acme Corp reported revenue of USD 1,250 in FY2025. Headquarters: Berlin."


def _cell(value, status="supported", quote=""):
    return {"raw_value": str(value or ""), "normalized_value": value, "status": status,
            "evidence": [{"text": quote, "page": 1, "start_char": None, "end_char": None}] if quote else []}


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.saved = store.docs.get(DOC), store.parents.get(DOC)
        store.docs[DOC] = DocMeta(id=DOC, filename="acme.pdf", kind="pdf", status="ready", n_pages=1)
        store.parents[DOC] = {f"{DOC}:pp1": PAGE}
        self.fields = normalize_fields([
            {"id": "company", "name": "Company", "data_type": "string"},
            {"id": "revenue", "name": "Revenue", "data_type": "number"},
            {"id": "city", "name": "HQ city", "data_type": "string"},
        ])
        first = {"record_key": "acme", "record_label": "Acme", "fields": {
            "company": _cell("Acme Corp", quote="Acme Corp"),
            "revenue": _cell(None, "missing"),
            "city": _cell(None, "missing")}}
        self.rows = [_normalize_model_record(first, DOC, self.fields, None)]
        self.batches = [[{"page": 1, "text": PAGE, "image": False}]]

    def tearDown(self):
        docs, parents = self.saved
        for table, value in ((store.docs, docs), (store.parents, parents)):
            if value is None:
                table.pop(DOC, None)
            else:
                table[DOC] = value

    def test_gaps_list_only_empty_selected_fields(self):
        self.assertEqual(_row_gaps(self.rows, self.fields, None), {0: ["revenue", "city"]})
        self.assertEqual(_row_gaps(self.rows, self.fields, {"company", "city"}), {0: ["city"]})

    def test_fills_gaps_keeps_existing_values_and_ignores_unknown_rows(self):
        calls = []

        def fake_llm(prompt, schema, max_tokens=0):
            calls.append((prompt[0]["text"], schema))
            return {"observations": [
                {"row_ref": "r0", "fields": {
                    "revenue": _cell(1250, quote="USD 1,250"),
                    "city": _cell(None, "missing")}},
                {"row_ref": "r9", "fields": {"revenue": _cell(1, quote="x"), "city": _cell("x", quote="x")}},
            ]}

        locator = mock.Mock(locate=lambda page, quote: {"coordinates": [], "coordinate_status": "unavailable",
                                                       "coordinate_source": None})
        with mock.patch.object(service, "_llm_json", fake_llm):
            filled = _follow_up({"objective": "o", "observation_unit": "company"}, DOC, self.rows, self.fields, None,
                                self.batches, threading.Event(), locator, lambda *a: None)

        self.assertEqual(filled, 1)
        self.assertEqual(len(calls), 1)
        prompt, schema = calls[0]
        self.assertIn("find: revenue, city", prompt)
        asked = schema["properties"]["observations"]["items"]["properties"]["fields"]["properties"]
        self.assertEqual(set(asked), {"revenue", "city"})            # "company" is not re-asked
        row = self.rows[0]
        self.assertEqual(len(self.rows), 1)                           # the unknown row_ref r9 added nothing
        self.assertEqual(row["fields"]["company"]["normalized_value"], "Acme Corp")
        self.assertNotIn("extraction_pass", row["fields"]["company"])
        self.assertEqual(row["fields"]["revenue"]["normalized_value"], 1250)
        self.assertEqual(row["fields"]["revenue"]["extraction_pass"], "follow_up")
        self.assertTrue(row["fields"]["revenue"]["evidence"][0]["verified"])
        self.assertEqual(row["fields"]["city"]["status"], "missing")

    def test_failed_follow_up_call_keeps_main_pass_rows(self):
        with mock.patch.object(service, "_llm_json", side_effect=ValueError("bad JSON")):
            filled = _follow_up({}, DOC, self.rows, self.fields, None, self.batches, threading.Event(),
                                mock.Mock(), lambda *a: None)
        self.assertEqual(filled, 0)
        self.assertEqual(self.rows[0]["fields"]["company"]["normalized_value"], "Acme Corp")
        self.assertEqual(self.rows[0]["fields"]["revenue"]["status"], "missing")

    def test_no_call_when_nothing_is_empty(self):
        for fid in ("revenue", "city"):
            self.rows[0]["fields"][fid]["status"] = "supported"
        with mock.patch.object(service, "_llm_json", side_effect=AssertionError("should not be called")):
            filled = _follow_up({}, DOC, self.rows, self.fields, None, self.batches, threading.Event(),
                                mock.Mock(), lambda *a: None)
        self.assertEqual(filled, 0)


if __name__ == "__main__":
    unittest.main()
