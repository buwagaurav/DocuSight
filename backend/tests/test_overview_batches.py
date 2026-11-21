"""Long-PDF Overview: page batching and merging of per-batch results (no model calls)."""
from __future__ import annotations

import unittest

from app.llm import extraction
from app.llm.extraction import merge_batches, needs_batches, page_batches
from app.store import store


class OverviewBatchTests(unittest.TestCase):
    def _doc(self, page_chars: list[int]) -> str:
        meta = store.create("batch-test.pdf", "pdf", b"%PDF-1.4")
        meta.n_pages = len(page_chars)
        store.parents[meta.id] = {f"{meta.id}:pp{i}": "x" * n for i, n in enumerate(page_chars, start=1)}
        self.addCleanup(store.delete, meta.id)
        return meta.id

    def test_short_document_keeps_single_call(self):
        self.assertFalse(needs_batches(self._doc([2_000] * 10)))

    def test_long_or_many_pages_are_batched(self):
        self.assertTrue(needs_batches(self._doc([10_000] * 13)))     # 130k chars
        self.assertTrue(needs_batches(self._doc([100] * (extraction.SINGLE_PASS_PAGES + 1))))

    def test_batches_respect_char_and_page_limits_and_cover_every_page(self):
        doc_id = self._doc([25_000] * 7 + [100] * 40)
        batches = page_batches(doc_id)
        self.assertEqual([p for b in batches for p in b], list(range(1, 48)))
        texts = extraction._page_texts(doc_id)
        for b in batches:
            self.assertLessEqual(len(b), extraction.BATCH_MAX_PAGES)
            if len(b) > 1:
                self.assertLessEqual(sum(len(texts[p - 1]) for p in b), extraction.BATCH_CHARS)

    def test_merge_dedupes_and_keeps_highest_importance(self):
        field = lambda label, value, imp, src: {"label": label, "value": value, "category": "Financial",  # noqa: E731
                                                "importance": imp, "source": src}
        parts = [
            {"key_fields": [field("Invoice total", "$1,250.00", "normal", "p.2")], "metrics": [],
             "entities": [{"name": "ACME Inc.", "type": "organization", "role": "buyer"}], "dates": [],
             "sections": [], "anomalies": ["Totals don't add up"]},
            {"key_fields": [field("Invoice Total", "$1,250.00", "critical", "p.31"), field("PO", "77812", "high", "p.33")],
             "metrics": [], "entities": [{"name": "acme inc", "type": "organization", "role": ""}], "dates": [],
             "sections": [], "anomalies": ["Totals don't add up."]},
        ]
        merged = merge_batches(parts)
        self.assertEqual([f["label"] for f in merged["key_fields"]], ["Invoice total", "PO"])
        self.assertEqual(merged["key_fields"][0]["importance"], "critical")
        self.assertEqual(len(merged["entities"]), 1)
        self.assertEqual(len(merged["anomalies"]), 1)


if __name__ == "__main__":
    unittest.main()
