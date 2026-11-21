"""Score DocuSight predictions with the official ExtractBench metrics.

Runs in a separate venv that has ExtractBench installed (it pins its own dependencies):

    python -m venv .venv-extractbench
    .venv-extractbench/bin/pip install "git+https://github.com/run-llama/ExtractBench@2f27f100730afc7c395602e26a2a87e07ea66b31"
    .venv-extractbench/bin/python backend/eval/extractbench_score.py data/eval/extractbench/runs/extract

Metrics (per document, then averaged; see the paper and the evaluator for full definitions):
  value_f1        unified value F1 over every expected value
  page_pass       share of verified fields whose value is right AND cited on an accepted page
  bbox_iou_pass   IoU of the predicted box with an accepted box when the value is right, else 0
  bbox_coverage   share of box-bearing fields that got any box on an accepted page
A failed case (error / no prediction) scores 0 on every metric it would have had.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from extract_bench.evaluation.metrics.field_grounding.extract_adapter import compute_extract_field_grounding_metrics
from extract_bench.evaluation.score_case import load_extract_test_case, score_extract_prediction

METRICS = {
    "value_f1": "extract_unified_value_f1",
    "page_pass": "extract_evidence_page_pass_rate",
    "bbox_iou_pass": "extract_evidence_bbox_IOU_pass_rate",
    "bbox_coverage": "extract_evidence_bbox_coverage",
}


def score(record: dict) -> dict[str, float | None]:
    case = load_extract_test_case(Path(record["test_json"]))
    prediction = record.get("prediction")
    out: dict[str, float | None] = {}
    value = score_extract_prediction(case, prediction if prediction is not None else {})
    out["value_f1"] = value["metrics"].get(METRICS["value_f1"]) if value["scorable"] else None
    citations = [SimpleNamespace(**c) for c in record.get("citations") or []]
    grounding = {m.metric_name: m.value for m in compute_extract_field_grounding_metrics(
        extracted_data=prediction if prediction is not None else {},
        field_rules=case.get_extract_field_rules(), field_citations=citations)}
    for short in ("page_pass", "bbox_iou_pass", "bbox_coverage"):
        out[short] = grounding.get(METRICS[short])
    return out


def _bucket(tags: list[str]) -> str:
    challenges = "+".join(sorted({t.split(":", 1)[1][:2] for t in tags if t.startswith("challenge:")}))
    capture = "scan" if any(t.startswith("perception:") for t in tags) else "digital"
    length = next((t.split(":", 1)[1] for t in tags if t.startswith("length:")), "?")
    return f"{length}/{challenges}/{capture}"


def main(run_dir: str) -> None:
    rows = []
    for path in sorted(Path(run_dir).glob("*.prediction.json")):
        record = json.loads(path.read_text())
        scores = score(record) if record.get("test_json") else {k: 0.0 for k in METRICS}
        rows.append({"id": record["id"], "bucket": _bucket(record.get("tags", [])), "error": record.get("error"),
                     "seconds": record.get("seconds"), **scores})

    def mean(items, key):
        vals = [r[key] for r in items if r[key] is not None]
        return sum(vals) / len(vals) if vals else None

    fmt = lambda v: "   —  " if v is None else f"{v:6.3f}"  # noqa: E731
    print(f"{'case':58} {'value_f1':>8} {'page':>6} {'iou':>6} {'cover':>6}  note")
    for r in rows:
        print(f"{r['id'][:58]:58} {fmt(r['value_f1']):>8} {fmt(r['page_pass'])} {fmt(r['bbox_iou_pass'])} "
              f"{fmt(r['bbox_coverage'])}  {(r['error'] or '')[:60]}")
    groups = defaultdict(list)
    for r in rows:
        groups[r["bucket"]].append(r)
    print(f"\n{'bucket':28} {'n':>3} {'value_f1':>8} {'page':>6} {'iou':>6} {'cover':>6}")
    for name, items in sorted(groups.items()) + [("ALL", rows)]:
        print(f"{name:28} {len(items):>3} {fmt(mean(items, 'value_f1')):>8} {fmt(mean(items, 'page_pass'))} "
              f"{fmt(mean(items, 'bbox_iou_pass'))} {fmt(mean(items, 'bbox_coverage'))}")
    report = Path(run_dir) / "scores.json"
    report.write_text(json.dumps({"cases": rows, "summary": {k: mean(rows, k) for k in METRICS}}, indent=1))
    print(f"\nerrors: {sum(1 for r in rows if r['error'])} / {len(rows)}   report: {report}")


if __name__ == "__main__":
    main(sys.argv[1])
