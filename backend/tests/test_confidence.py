"""Cell confidence: model probability adjusted by DocuSight's own evidence checks."""
from __future__ import annotations

import unittest

from app.extraction.confidence import REVIEW_THRESHOLD, apply_confidence, score_cell


def cell(**kw):
    base = {"status": "supported", "normalized_value": 1250.0, "model_confidence": 0.9, "validation_issues": [],
            "review_status": "unreviewed",
            "evidence": [{"text": "Invoice total: $1,250.00", "page": 2, "verified": True, "coordinates": [{"x": 0.1}]}]}
    return {**base, **kw}


class ConfidenceTests(unittest.TestCase):
    def test_verified_located_value_keeps_model_confidence(self):
        self.assertEqual(score_cell(cell()), (0.9, []))

    def test_no_value_has_no_confidence(self):
        self.assertEqual(score_cell(cell(status="missing", normalized_value=None)), (None, []))

    def test_quote_not_on_page_halves_confidence(self):
        conf, reasons = score_cell(cell(evidence=[{"text": "Invoice total: $1,250.00", "page": 2, "verified": False}]))
        self.assertAlmostEqual(conf, 0.45)
        self.assertTrue(any("isn't on the cited page" in r for r in reasons))

    def test_value_missing_from_quote_is_penalised(self):
        conf, reasons = score_cell(cell(normalized_value=9999.0))
        self.assertAlmostEqual(conf, 0.72)
        self.assertTrue(any("doesn't appear in its quote" in r for r in reasons))

    def test_scanned_page_and_type_failure(self):
        conf, _ = score_cell(cell(evidence=[{"text": "1,250.00", "page": 1, "verified": None}],
                                  validation_issues=["Type or constraint validation failed: 'x' is not a number"]))
        self.assertAlmostEqual(conf, round(0.9 * 0.85 * 0.7, 3))

    def test_low_confidence_routes_to_review(self):
        c = cell(model_confidence=0.3, evidence=[])
        apply_confidence(c)
        self.assertLess(c["confidence"], REVIEW_THRESHOLD)
        self.assertEqual(c["review_status"], "needs_review")
        self.assertTrue(any(i.startswith("Review: confidence") for i in c["validation_issues"]))

    def test_reapplying_does_not_duplicate_review_issue(self):
        c = cell(model_confidence=0.3, evidence=[])
        apply_confidence(c)
        apply_confidence(c)
        self.assertEqual(sum(i.startswith("Review: confidence") for i in c["validation_issues"]), 1)


if __name__ == "__main__":
    unittest.main()
