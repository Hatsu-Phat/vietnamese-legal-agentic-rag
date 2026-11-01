"""Regression checks for project-relative config and relocated pipeline outputs."""

import csv
import json
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from scripts import run_phase1_ingestion, run_phase2_indexing
from src.common.config import PROJECT_ROOT, load_data_paths, load_yaml_config
from src.ingestion.audit import CorpusAuditor


def test_default_paths_work_outside_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing the working directory must not redirect reads or default outputs."""
    monkeypatch.chdir(tmp_path)
    paths = load_data_paths(load_yaml_config("configs/dev.yaml"))
    assert paths.raw_dir == PROJECT_ROOT / "data/raw"
    assert paths.chunks == PROJECT_ROOT / "data/artifacts/processed/chunks.jsonl"
    assert paths.benchmarks_dir == PROJECT_ROOT / "data/benchmarks"
    assert not (tmp_path / "data").exists()


def test_invalid_config_fails_before_pipeline_runs(tmp_path: Path) -> None:
    """A typo or missing config must not silently select default output locations."""
    with pytest.raises(FileNotFoundError):
        load_yaml_config(tmp_path / "missing.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("- not-a-config-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_yaml_config(invalid)
    with pytest.raises(ValidationError):
        load_data_paths({"paths": {"proccessed_dir": str(tmp_path)}})


def test_pipeline_uses_configured_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ingestion, chunking and BM25 share custom paths without writing into the repo."""
    raw_dir = tmp_path / "input"
    raw_dir.mkdir()
    (raw_dir / "legal_corpus.json").write_text(
        json.dumps(
            [
                {
                    "law_id": "01/2020/NĐ-CP",
                    "articles": [
                        {
                            "article_id": "1",
                            "title": "Điều 1. Phạm vi",
                            "text": "Quy định bảo vệ môi trường và trách nhiệm tổ chức.",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for filename, qid in [("train_qna.csv", "dev-1"), ("best_test_qna.csv", "test-1")]:
        with (raw_dir / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["question_id", "question"])
            writer.writeheader()
            writer.writerow({"question_id": qid, "question": "Quy định bảo vệ môi trường?"})
    for filename, qid in [
        ("true_valid_results.json", "dev-1"),
        ("true_test_results.json", "test-1"),
    ]:
        (raw_dir / filename).write_text(json.dumps({qid: ["01/2020/NĐ-CP%1"]}), encoding="utf-8")
    (raw_dir / "stopwords.txt").write_text("và\n", encoding="utf-8")

    config = {
        "paths": {
            "raw_dir": str(raw_dir),
            "processed_dir": str(tmp_path / "output/processed"),
            "indexes_dir": str(tmp_path / "output/indexes"),
            "manifests_dir": str(tmp_path / "output/manifests"),
            "benchmarks_dir": str(tmp_path / "benchmark"),
            "samples_dir": str(tmp_path / "sample"),
            "reports_dir": str(tmp_path / "reports"),
        },
        "indexing": {"bm25": {"segment_vietnamese": False}},
    }
    config_file = tmp_path / "pipeline.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["phase1", "--config-path", str(config_file)])
    assert run_phase1_ingestion.main() == 0

    paths = load_data_paths(config)
    auditor = CorpusAuditor(
        processed_dir=paths.processed_dir,
        manifest_dir=paths.manifests_dir,
        gold_eval_dir=paths.benchmarks_dir,
        report_dir=paths.reports_dir,
    )
    assert len(auditor.load_documents()) == 1
    assert len(auditor.load_provisions()) == 1
    assert len(auditor.load_qa_items("qa_dev")) == 1
    assert len(auditor.load_qa_items("qa_test")) == 1

    monkeypatch.setattr(
        sys, "argv", ["phase2", "--config-path", str(config_file), "--step", "chunk"]
    )
    assert run_phase2_indexing.main() == 0
    monkeypatch.setattr(
        sys, "argv", ["phase2", "--config-path", str(config_file), "--step", "bm25"]
    )
    assert run_phase2_indexing.main() == 0
    assert paths.chunks.is_file()
    assert paths.source_manifest.is_file()
    assert paths.chunk_stats.is_file()
    assert paths.sample_corpus.is_file()
    assert (paths.reports_dir / "data_audit.md").is_file()
    assert (paths.reports_dir / "chunk_audit.md").is_file()
    indexer = run_phase2_indexing.run_bm25_step(config)
    assert indexer.index_path == paths.bm25_index
    assert indexer.query("môi trường")[0]["provision_id"] == "law_2020_nd_cp_01__art_1"


def test_sample_pipeline_cannot_replace_full_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reuse the real configured fixture run, then execute sample ingestion/chunking.
    test_pipeline_uses_configured_directories(tmp_path, monkeypatch)
    config_path = tmp_path / "pipeline.yaml"
    cfg = load_yaml_config(config_path)
    paths = load_data_paths(cfg)
    protected = [
        paths.documents,
        paths.provisions,
        paths.chunks,
        paths.source_manifest,
        paths.chunk_stats,
        paths.benchmarks_dir / "qa_dev.jsonl",
    ]
    before = {p: p.read_bytes() for p in protected}
    monkeypatch.setattr(
        sys,
        "argv",
        ["phase1", "--config-path", str(config_path), "--sample-only", "--sample-size", "1"],
    )
    assert run_phase1_ingestion.main() == 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "phase2",
            "--config-path",
            str(config_path),
            "--sample-only",
            "--sample-size",
            "1",
            "--step",
            "chunk",
        ],
    )
    assert run_phase2_indexing.main() == 0
    assert paths.sample_outputs().chunks.exists()
    assert all(p.read_bytes() == data for p, data in before.items())
