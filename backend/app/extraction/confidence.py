"""Per-cell confidence for extracted values, used to route uncertain cells to human review.

ConfBench (arXiv:2608.01792) finds that document-extraction confidence is best when the model sees the
page image as well as text, and that first-token log-probabilities calibrate better than a stated
("verbalized") probability. The Anthropic-style APIs used here expose no log-probabilities, so the score
starts from the model's stated probability and is then adjusted by checks DocuSight can verify itself:
is the quote really on the cited page, does the value appear in that quote, was it located on the page,
does it pass the field's type rules. Each adjustment comes with a plain-language reason for the reviewer.
"""
from __future__ import annotations

import re
from typing import Any

REVIEW_THRESHOLD = 0.6   # below this a supported cell is sent to review (mirrored in ExtractionWorkspace.tsx)
DEFAULT_MODEL_CONFIDENCE = 0.75  # used when the model gave no usable probability

# validation issues that come from the evidence checks below; the rest are type / constraint failures
_EVIDENCE_ISSUES = ("No source evidence", "Evidence has no valid page", "Evidence quote could not be matched",
                    "Evidence could not be checked", "Conflicting", "Review: confidence")


def _number(text: str) -> float | None:
    try:
        return float(re.sub(r"[,$€£¥₹%\s()]", "", text))
    except ValueError:
        return None


def _in_quote(value: Any, quotes: list[str]) -> bool:
    """Does the value appear in one of its quotes? Numbers compare by value ("1,250.00" == 1250)."""
    text = str(value).casefold()
    compact = re.sub(r"\W+", "", text)
    number = _number(text)
    for quote in quotes:
        if compact and compact in re.sub(r"\W+", "", quote.casefold()):
            return True
        if number is not None and any(_number(n) == number for n in re.findall(r"-?\d[\d,]*(?:\.\d+)?", quote)):
            return True
    return not compact


def score_cell(cell: dict[str, Any]) -> tuple[float | None, list[str]]:
    """(confidence 0-1, reasons) for a supported cell; (None, []) when there is no value to judge."""
    if cell.get("status") != "supported" or cell.get("normalized_value") in (None, "", []):
        return None, []
    reasons: list[str] = []
    stated = cell.get("model_confidence")
    score = float(stated) if isinstance(stated, (int, float)) and 0 <= stated <= 1 else DEFAULT_MODEL_CONFIDENCE
    if isinstance(stated, (int, float)) and stated < 0.5:
        reasons.append("The model said it was unsure of this value.")

    evidence = cell.get("evidence") or []
    if not evidence:
        score *= 0.5
        reasons.append("No source quote was returned.")
    else:
        checks = [e.get("verified") for e in evidence]
        if any(c is True for c in checks):
            pass
        elif all(c is False for c in checks):
            score *= 0.5
            reasons.append("The quoted text isn't on the cited page.")
        else:
            score *= 0.85
            reasons.append("Read from a scanned page image, so the quote couldn't be checked against page text.")
        value = cell.get("normalized_value")
        if not isinstance(value, (bool, dict, list)) and not _in_quote(value, [str(e.get("text", "")) for e in evidence]):
            score *= 0.8
            reasons.append("The value doesn't appear in its quote (it may be reformatted or inferred).")
        if any(c is True for c in checks) and not any(e.get("coordinates") for e in evidence):
            score *= 0.95
            reasons.append("The quote couldn't be located on the page for highlighting.")

    if any(not issue.startswith(_EVIDENCE_ISSUES) for issue in cell.get("validation_issues", [])):
        score *= 0.7
        reasons.append("The value fails the field's type or format rules.")
    return round(min(0.99, max(0.01, score)), 3), reasons


def apply_confidence(cell: dict[str, Any]) -> None:
    """Set confidence, reasons and review routing on a normalized cell (in place)."""
    confidence, reasons = score_cell(cell)
    cell["confidence"] = confidence
    cell["confidence_reasons"] = reasons
    for evidence in cell.get("evidence", []):
        evidence["confidence"] = confidence
    if confidence is not None and confidence < REVIEW_THRESHOLD:
        cell["review_status"] = "needs_review"
        issue = f"Review: confidence {round(confidence * 100)}% is below {round(REVIEW_THRESHOLD * 100)}%."
        if issue not in cell.get("validation_issues", []):
            cell.setdefault("validation_issues", []).append(issue)
