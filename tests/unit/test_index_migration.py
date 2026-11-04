"""Migration correctness: immutable inputs, collision policy and interrupted uploads."""

import hashlib
import json
import pickle
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from src.common.atomic import atomic_open
from src.common.exceptions import CorpusParseError, EvaluationImportError
from src.core.models import LegalChunk
from src.core.paths import DataPaths
from src.ingestion.qa_importer import QAImporter
from src.ingestion.thiemcun_adapter import ThiemCunCorpusAdapter
from src.retrieval.index_migration import TeamIndexMigration, decode_point, sha256_file
from src.retrieval.qdrant_indexer import QdrantIndexer
from src.retrieval.qdrant_store import QdrantStore


def make_chunk(index: int = 0, number: str = "01/2020/nđ-cp") -> LegalChunk:
    """Two aliases intentionally have identical IDs, text and structure."""
    text = f"Nội dung điều luật {index} về bảo vệ môi trường."
    cid = f"law_2020_nd_cp_01__art_{index + 1}__c0"
    return LegalChunk(
        chunk_id=cid,
        point_uuid=LegalChunk.generate_point_uuid(cid),
        document_id="law_2020_nd_cp_01",
        document_number=number,
        provision_id=f"law_2020_nd_cp_01__art_{index + 1}",
        article_id=str(index + 1),
        chunk_index=0,
        total_chunks_in_provision=1,
        is_full_article=True,
        breadcrumb=f"{number} > Điều {index + 1}",
        title=f"Điều {index + 1}",
        text=text,
        embedding_text=f"[{number}] {text}",
        word_count=len(text.split()),
        char_length=len(text),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


@pytest.fixture
def migration(tmp_path: Path) -> TeamIndexMigration:
    """Small real SQLite bundle with one alias collision and two stored vectors."""
    source = tmp_path / "import"
    manifests = source / "data/manifests"
    manifests.mkdir(parents=True)
    (manifests / "index_manifest.json").write_text(
        json.dumps(
            {
                "embedding_model_id": "BAAI/bge-m3",
                "vector_size": 4,
                "distance": "Cosine",
                "normalize_embeddings": True,
                "max_seq_length": 512,
                "collection_name": "source",
            }
        ),
        encoding="utf-8",
    )
    storage = source / "data/indexes/qdrant"
    storage.mkdir(parents=True)
    (storage / "meta.json").write_text(
        json.dumps(
            {
                "collections": {
                    "source": {
                        "vectors": {"size": 4, "distance": "Cosine"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    folder = storage / "collection/source"
    folder.mkdir(parents=True)
    first, second = make_chunk(), make_chunk(1)
    with sqlite3.connect(folder / "storage.sqlite") as db:
        db.execute("CREATE TABLE points(id TEXT PRIMARY KEY, point BLOB)")
        for chunk, vector in [(first, [1.0, 0.0, 0.0, 0.0]), (second, [0.0, 1.0, 0.0, 0.0])]:
            point = PointStruct(
                id=chunk.point_uuid, vector=vector, payload=chunk.model_dump(mode="json")
            )
            db.execute("INSERT INTO points VALUES (?, ?)", (chunk.point_uuid, pickle.dumps(point)))
    processed = source / "data/processed"
    processed.mkdir(parents=True)
    (processed / "chunks.jsonl").write_text(
        "\n".join(
            c.model_dump_json()
            for c in [
                make_chunk(number="01/2020/nd-cp"),
                first,
                second,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paths = DataPaths(
        processed_dir=tmp_path / "prepared",
        indexes_dir=tmp_path / "indexes",
        manifests_dir=tmp_path / "manifests",
        reports_dir=tmp_path / "reports",
    )
    return TeamIndexMigration(source, paths)


def test_prepare_deduplicates_aliases_and_preserves_source(migration: TeamIndexMigration) -> None:
    before = migration.checksums()
    report = migration.prepare()
    assert (report.input_chunks, report.unique_chunks, report.merged_rows) == (3, 2, 1)
    assert report.reused_vectors == 2 and report.encoded_vectors == 0
    assert migration.checksums() == before
    assert len(migration.alias_map.read_text(encoding="utf-8").splitlines()) == 3
    chunks = [
        LegalChunk.model_validate_json(line)
        for line in migration.paths.chunks.read_text(encoding="utf-8").splitlines()
    ]
    assert {c.document_number for c in chunks} == {"01/2020/nđ-cp"}
    assert migration.load_report() == report


def test_conflicting_collision_does_not_replace_outputs(migration: TeamIndexMigration) -> None:
    migration.prepare()
    before = sha256_file(migration.paths.chunks)
    source = migration.source / "data/processed/chunks.jsonl"
    lines = source.read_text(encoding="utf-8").splitlines()
    changed = json.loads(lines[0])
    changed["text"] = "Khác nội dung"
    lines[0] = json.dumps(changed)
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Conflicting"):
        migration.prepare()
    assert sha256_file(migration.paths.chunks) == before


def test_interrupted_upload_resumes_and_verifies(
    migration: TeamIndexMigration, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration.prepare()
    client = QdrantClient(":memory:")
    original = QdrantStore.upload
    calls = 0

    def interrupt(store: QdrantStore, points: list[PointStruct]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Simulated interruption")
        original(store, points)

    monkeypatch.setattr(QdrantStore, "upload", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        migration.upload(client, "target", batch_size=1)
    assert client.count("target").count == 1
    assert (
        json.loads(migration.checkpoint_path.read_text(encoding="utf-8"))["confirmed_points"] == 1
    )
    monkeypatch.setattr(QdrantStore, "upload", original)
    migration.upload(client, "target", resume=True, batch_size=1)
    # Mutated metadata with the same UUID must be repaired on resume.
    uid = make_chunk().point_uuid
    client.set_payload("target", {"title": "stale"}, points=[uid])
    migration.upload(client, "target", resume=True)
    report = migration.verify(client, "target")
    assert report.actual_points == 2
    assert report.compared_payloads == 2 and report.compared_vectors == 2
    assert report.alias_activated
    assert client.count("vn_legal_bge_m3").count == 2


def test_unknown_target_or_source_changes_rejected(migration: TeamIndexMigration) -> None:
    migration.prepare()
    client = QdrantClient(":memory:")
    QdrantStore(client, "other", 4).ensure_collection()
    with pytest.raises(ValueError, match="existing collection"):
        migration.upload(client, "other", resume=True)
    migration.paths.chunks.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Canonical chunks changed"):
        migration.load_report()


def test_untrusted_pickle_and_invalid_vectors_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported serialized global"):
        decode_point(pickle.dumps(Path("malicious-global")), 4)
    point = PointStruct(
        id=make_chunk().point_uuid,
        vector=[float("nan")] * 4,
        payload=make_chunk().model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="Invalid stored vector"):
        decode_point(pickle.dumps(point), 4)


def test_atomic_failure_keeps_previous_file(tmp_path: Path) -> None:
    path = tmp_path / "output.jsonl"
    path.write_text("previous", encoding="utf-8")
    with pytest.raises(RuntimeError), atomic_open(path) as stream:
        stream.write("partial")
        raise RuntimeError("failure")
    assert path.read_text(encoding="utf-8") == "previous"
    assert len(list(tmp_path.iterdir())) == 1


def test_ingestion_aliases_and_repeated_articles_keep_raw_ids() -> None:
    article = {"article_id": "1", "title": "Điều 1", "text": "Nội dung"}
    docs = [
        {"law_id": x, "articles": [article.copy() for _ in range(4)]}
        for x in ["01/2020/nd-cp", "01/2020/nđ-cp"]
    ]
    result = ThiemCunCorpusAdapter().process_corpus(docs)
    assert len(result.documents) == 1
    assert len(result.documents[0].source_aliases) == 2
    assert len({p.provision_id for p in result.provisions}) == 4
    assert {p.article_id for p in result.provisions} == {"1"}
    importer = QAImporter()
    a = importer._parse_gold_references(["01/2020/nd-cp%1"])
    b = importer._parse_gold_references(["01/2020/nđ-cp%1"])
    assert a == b
    docs[1]["articles"][0]["text"] = "conflict"
    with pytest.raises(CorpusParseError, match="Conflicting"):
        ThiemCunCorpusAdapter().process_corpus(docs)


def test_missing_benchmarks_and_empty_exports_preserve_existing(tmp_path: Path) -> None:
    old = tmp_path / "qa_dev.jsonl"
    old.write_text("old", encoding="utf-8")
    importer = QAImporter(raw_dir=tmp_path)
    with pytest.raises(EvaluationImportError, match="Missing required"):
        importer.import_datasets()
    with pytest.raises(EvaluationImportError, match="empty split"):
        importer.export_gold_eval([], [], tmp_path)
    assert old.read_text(encoding="utf-8") == "old"


def test_indexer_validates_existing_collection_and_duplicates(tmp_path: Path) -> None:
    client = QdrantClient(":memory:")
    QdrantStore(client, "wrong", 3).ensure_collection()
    indexer = QdrantIndexer(
        client=client,
        collection_name="wrong",
        vector_size=4,
        manifest_path=tmp_path / "manifest.json",
    )
    with pytest.raises(ValueError, match="incompatible"):
        indexer.index_chunks([make_chunk()])
    with pytest.raises(ValueError, match="Duplicate"):
        indexer.index_chunks([make_chunk(), make_chunk()])


def test_sample_outputs_isolated_and_chunk_guards(tmp_path: Path) -> None:
    from src.chunking.chunk_audit import ChunkAuditor
    from src.chunking.fallback_chunker import split_text_sliding_window
    from src.chunking.legal_chunker import LegalChunker

    paths = DataPaths(processed_dir=tmp_path / "processed")
    sample = paths.sample_outputs()
    assert sample.chunks != paths.chunks
    assert sample.benchmarks_dir != paths.benchmarks_dir
    with pytest.raises(ValueError):
        split_text_sliding_window("x " * 25, max_words=10, overlap_words=10)
    with pytest.raises(ValueError):
        LegalChunker(max_words_per_chunk=10, overlap_words=10)
    chunks = split_text_sliding_window("x " * 93, max_words=20, overlap_words=5, min_words=15)
    assert all(len(c.split()) <= 20 for c in chunks)
    stats = ChunkAuditor().compute_stats([make_chunk()], {"unknown"})
    assert stats["provision_coverage_rate"] == 0
    assert stats["unexpected_provisions_count"] == 1


def test_import_does_not_load_inference_libraries() -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.retrieval.index_migration; assert 'torch' not in sys.modules; assert 'sentence_transformers' not in sys.modules",
        ],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_migration_cli_upload_closes_client(
    migration: TeamIndexMigration, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from scripts import migrate_team_index
    from src.common.config import AppSettings

    migration.prepare()
    client = QdrantClient(":memory:")
    close = Mock(wraps=client.close)
    monkeypatch.setattr(client, "close", close)
    monkeypatch.setattr(migrate_team_index, "QdrantClient", lambda **kwargs: client)
    monkeypatch.setattr(migrate_team_index, "TeamIndexMigration", lambda *args: migration)
    monkeypatch.setattr(
        migrate_team_index,
        "AppSettings",
        lambda: AppSettings(
            _env_file=None, QDRANT_URL="https://example.invalid", QDRANT_API_KEY="fake-test-key"
        ),
    )
    monkeypatch.setattr(sys, "argv", ["migrate", "--step", "upload", "--collection", "target"])
    assert migrate_team_index.main() == 0
    close.assert_called_once()
    assert (
        json.loads(migration.checkpoint_path.read_text(encoding="utf-8"))["confirmed_points"] == 2
    )


def test_source_and_checkpoint_fingerprints_prevent_wrong_resume(
    migration: TeamIndexMigration,
) -> None:
    migration.prepare()
    client = QdrantClient(":memory:")
    migration.upload(client, "target")
    with pytest.raises(ValueError, match="another migration"):
        migration.upload(client, "different-target", resume=True)
    source = migration.source / "data/manifests/index_manifest.json"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Source bundle changed"):
        migration.load_report()


def test_indexer_resume_reencodes_changed_input(tmp_path: Path) -> None:
    from unittest.mock import Mock

    import numpy as np

    client = QdrantClient(":memory:")
    encoder = Mock()
    encoder.encode.return_value = np.array([[1.0, 0.0, 0.0, 0.0]])
    indexer = QdrantIndexer(
        client=client,
        collection_name="target",
        vector_size=4,
        embedding_model=encoder,
        manifest_path=tmp_path / "manifest.json",
    )
    chunk = make_chunk()
    assert indexer.index_chunks([chunk]) == 1
    assert indexer.index_chunks([chunk], resume=True) == 0
    chunk = chunk.model_copy(update={"embedding_text": chunk.embedding_text + " changed"})
    assert indexer.index_chunks([chunk], resume=True) == 1
    assert encoder.encode.call_count == 2
    assert (
        json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["total_vectors"] == 1
    )


def test_model_compatibility_and_query_audit(
    migration: TeamIndexMigration, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    from src.ingestion.indexing import model_audit

    migration.prepare()
    client = QdrantClient(":memory:")
    migration.upload(client, "target")
    report = migration.verify(client, "target")
    originals = {p.payload["embedding_text"]: p.vector for p in migration.iter_points()}

    class Model:
        incompatible = False

        def encode(self, value: Any, **kwargs: Any) -> Any:
            if isinstance(value, list):
                return np.array(
                    [[0.5] * 4 if self.incompatible else originals[text] for text in value]
                )
            return np.array([1.0, 0.0, 0.0, 0.0])

        def tokenizer(self, values: list[str], **kwargs: Any) -> dict[str, list[list[int]]]:
            return {"input_ids": [[1, 2] for _ in values]}

    model = Model()

    class Encoder:
        revision = "test-revision"

    encoder = Encoder()
    encoder.model = model
    monkeypatch.setattr(model_audit, "BGEEncoder", lambda *args: encoder)
    checked = model_audit.audit_model(migration, client, report)
    assert checked.compatibility_min_cosine == 1
    assert len(checked.text_queries) == 3
    assert checked.model_revision == "test-revision"
    assert checked.truncation_count == 0
    model.incompatible = True
    with pytest.raises(ValueError, match="compatibility"):
        model_audit.audit_model(migration, client, report)
    assert client.count("target").count == 2


@pytest.mark.parametrize(
    ("status", "content", "expected_calls"),
    [(503, b"overloaded", 2), (403, b"forbidden", 1), (503, b"disk quota exceeded", 1)],
)
def test_retry_policy_is_bounded(
    migration: TeamIndexMigration,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    content: bytes,
    expected_calls: int,
) -> None:
    from qdrant_client.http.exceptions import UnexpectedResponse

    from src.retrieval import index_migration

    migration.prepare()
    client = QdrantClient(":memory:")
    original = QdrantStore.upload
    calls = 0
    delays: list[int] = []

    def upload(store: QdrantStore, points: list[PointStruct]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UnexpectedResponse(
                status_code=status, reason_phrase="test", content=content, headers={}
            )
        original(store, points)

    monkeypatch.setattr(QdrantStore, "upload", upload)
    monkeypatch.setattr(index_migration.time, "sleep", delays.append)
    if expected_calls == 2:
        migration.upload(client, "target")
        assert delays == [1]
        assert client.count("target").count == 2
    else:
        with pytest.raises(RuntimeError):
            migration.upload(client, "target")
        assert delays == []
        assert client.count("target").count == 0
    assert calls == expected_calls
