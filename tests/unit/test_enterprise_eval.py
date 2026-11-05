"""Enterprise evaluation set (T-0103): schema, role expectations and the committed dataset."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.enterprise_evaluation import (
    EnterpriseCategory,
    EnterpriseEvalCase,
    expected_decisions,
)
from src.core.enums import AccessLevel
from src.evaluation.enterprise import gold_access, load_cases, summarize
from src.ingestion.internal_docs import load_internal_dir

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "tests" / "eval" / "enterprise_v1.jsonl"


def case(**overrides: object) -> EnterpriseEvalCase:
    data: dict[str, object] = {
        "case_id": "c1",
        "question": "q",
        "category": "internal_policy",
        "question_type": "direct",
        "gold_provision_ids": ["doc__art_1"],
        "expected_by_role": {"employee": ["abstain"], "manager": ["answer"], "legal": ["answer"]},
        "annotation_source": "authored_unreviewed",
    }
    data.update(overrides)
    return EnterpriseEvalCase.model_validate(data)


def test_expected_decisions_follow_role_matrix() -> None:
    assert expected_decisions(["public"]) == {
        "employee": ["answer"],
        "manager": ["answer"],
        "legal": ["answer"],
    }
    assert expected_decisions(["internal"])["employee"] == ["abstain"]
    assert expected_decisions(["internal"])["manager"] == ["answer"]
    conf = expected_decisions(["confidential"])
    assert conf["manager"] == ["abstain"] and conf["legal"] == ["answer"]
    mixed = expected_decisions(["internal", "public"])
    assert mixed["employee"] == ["answer", "abstain"] and mixed["manager"] == ["answer"]
    assert set(map(tuple, expected_decisions([]).values())) == {("abstain",)}


def test_case_requires_every_role_and_gold_rules() -> None:
    with pytest.raises(ValidationError):
        case(expected_by_role={"employee": ["answer"], "manager": ["answer"]})
    with pytest.raises(ValidationError):
        case(category="out_of_scope")
    with pytest.raises(ValidationError):
        case(gold_provision_ids=[])
    with pytest.raises(ValidationError):
        case(gold_provision_ids=["a", "a"])
    oos = case(
        category="out_of_scope",
        gold_provision_ids=[],
        expected_by_role={r: ["abstain"] for r in ("employee", "manager", "legal")},
    )
    assert oos.category is EnterpriseCategory.OUT_OF_SCOPE


def test_gold_access_rejects_missing_and_inconsistent_cases() -> None:
    good = case()
    assert gold_access([good], {"doc__art_1": AccessLevel.INTERNAL}) == {"c1": AccessLevel.INTERNAL}
    with pytest.raises(ValueError, match="not in release"):
        gold_access([good], {})
    with pytest.raises(ValueError, match="disagrees"):
        gold_access([good], {"doc__art_1": AccessLevel.PUBLIC})


def test_summarize_uses_recall_and_mrr_definitions() -> None:
    rows = [(["a", "b"], {"b"}, 10.0), (["c"], {"x"}, 30.0)]
    metrics = summarize(rows, top_k=10)
    assert metrics.mrr == pytest.approx(0.25)
    assert metrics.recall_at_k == {"1": 0.0, "5": 0.5, "10": 0.5}
    assert metrics.p95_ms == 30.0


def test_committed_dataset_matches_corpus_access_levels() -> None:
    cases = load_cases(DATASET)
    docs, provisions = load_internal_dir(ROOT / "corpus" / "internal")[:2]
    access = {d.document_id: d.access_level for d in docs}
    levels: dict[str, AccessLevel] = {p.provision_id: access[p.document_id] for p in provisions}
    for item in cases:
        for pid in item.gold_provision_ids:
            if pid.startswith("law_"):
                levels.setdefault(pid, AccessLevel.PUBLIC)
    top = gold_access(cases, levels)
    internal_cases = [c for c in cases if c.category is not EnterpriseCategory.NEW_LAW]
    assert len(internal_cases) >= 50
    assert {top[c.case_id] for c in internal_cases if c.case_id in top} == set(AccessLevel)
    for probe in (c for c in cases if c.category is EnterpriseCategory.ACCESS_PROBE):
        assert probe.expected_by_role["employee"] == ["abstain"]
    assert all(c.review_status == "unreviewed" for c in cases)
