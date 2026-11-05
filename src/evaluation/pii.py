"""Precision/recall of the PII guardrail on labelled text and on real legal questions."""

import json
from collections import Counter
from pathlib import Path

from src.guardrails.pii import PiiType, detect_pii


def load_labelled(path: Path) -> list[dict[str, object]]:
    """Cases with `text` and `pii` = [[type, value], ...]; every value must occur in its text."""
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for case in cases:
        text = str(case["text"])
        for kind, value in case["pii"]:
            PiiType(kind)
            if value not in text:
                raise ValueError(f"{case['case_id']}: labelled value not in text")
    return cases


def gold_spans(text: str, labels: list[list[str]]) -> list[tuple[PiiType, int, int]]:
    spans = []
    for kind, value in labels:
        start = text.index(value)
        spans.append((PiiType(kind), start, start + len(value)))
    return spans


def score(cases: list[dict[str, object]], negatives: list[str]) -> dict[str, object]:
    """A prediction is correct when its type matches and its span overlaps a labelled value."""
    tp: Counter[str] = Counter()
    fp: Counter[str] = Counter()
    fn: Counter[str] = Counter()
    errors: list[dict[str, object]] = []
    for case in cases:
        text = str(case["text"])
        gold = gold_spans(text, case["pii"])  # type: ignore[arg-type]
        found = detect_pii(text)
        matched: set[int] = set()
        for f in found:
            hit = next(
                (
                    i
                    for i, (kind, start, end) in enumerate(gold)
                    if i not in matched and kind is f.type and f.start < end and start < f.end
                ),
                None,
            )
            if hit is None:
                fp[f.type.value] += 1
                errors.append(
                    {"case_id": case["case_id"], "error": "false_positive", "type": f.type.value}
                )
            else:
                matched.add(hit)
                tp[f.type.value] += 1
        for i, (kind, _, _) in enumerate(gold):
            if i not in matched:
                fn[kind.value] += 1
                errors.append({"case_id": case["case_id"], "error": "missed", "type": kind.value})
    real_fp = Counter(f.type.value for q in negatives for f in detect_pii(q))
    per_type = {}
    for kind in PiiType:
        k = kind.value
        p_den, r_den = tp[k] + fp[k], tp[k] + fn[k]
        per_type[k] = {
            "tp": tp[k],
            "fp": fp[k],
            "fn": fn[k],
            "precision": tp[k] / p_den if p_den else None,
            "recall": tp[k] / r_den if r_den else None,
        }
    all_tp, all_fp, all_fn = sum(tp.values()), sum(fp.values()), sum(fn.values())
    return {
        "labelled_cases": len(cases),
        "labelled_values": all_tp + all_fn,
        "precision": all_tp / (all_tp + all_fp) if all_tp + all_fp else None,
        "recall": all_tp / (all_tp + all_fn) if all_tp + all_fn else None,
        "per_type": per_type,
        "real_questions_checked": len(negatives),
        "real_questions_flagged": sum(bool(detect_pii(q)) for q in negatives),
        "real_question_detections_by_type": dict(real_fp),
        "errors": errors,
    }
