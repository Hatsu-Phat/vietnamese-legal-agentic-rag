"""Typed data directory layout, independent of pipeline frameworks."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DataPaths(BaseModel):
    """Directories configured in YAML; relative paths are project-relative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_dir: Path = Path("data/raw")
    samples_dir: Path = Path("data/samples")
    benchmarks_dir: Path = Path("data/benchmarks")
    processed_dir: Path = Path("data/artifacts/processed")
    indexes_dir: Path = Path("data/artifacts/indexes")
    manifests_dir: Path = Path("data/artifacts/manifests")
    reports_dir: Path = Path("docs/reports")

    def sample_outputs(self) -> "DataPaths":
        """Keep smoke runs isolated from the full corpus and cloud collections."""
        return self.model_copy(
            update={
                "processed_dir": self.processed_dir.parent / "sample" / "processed",
                "indexes_dir": self.indexes_dir.parent / "sample" / "indexes",
                "manifests_dir": self.manifests_dir.parent / "sample" / "manifests",
                "benchmarks_dir": self.benchmarks_dir / "sample",
                "reports_dir": self.reports_dir / "sample",
            }
        )

    @property
    def documents(self) -> Path:
        """Normalized document records."""
        return self.processed_dir / "documents.jsonl"

    @property
    def provisions(self) -> Path:
        """Normalized article records."""
        return self.processed_dir / "provisions.jsonl"

    @property
    def chunks(self) -> Path:
        """Chunks consumed by both retrieval indexes."""
        return self.processed_dir / "chunks.jsonl"

    @property
    def sample_corpus(self) -> Path:
        """Small raw corpus for smoke checks."""
        return self.samples_dir / "sample_corpus_subset.json"

    @property
    def source_manifest(self) -> Path:
        """Document provenance inventory."""
        return self.manifests_dir / "source_manifest.csv"

    @property
    def chunk_stats(self) -> Path:
        """Chunk audit statistics."""
        return self.manifests_dir / "chunk_stats.json"

    @property
    def index_manifest(self) -> Path:
        """Dense index run metadata."""
        return self.manifests_dir / "index_manifest.json"

    @property
    def bm25_index(self) -> Path:
        """Serialized lexical index."""
        return self.indexes_dir / "bm25" / "bm25_index.pkl"

    @property
    def bm25_metadata(self) -> Path:
        """Mapping from BM25 positions to chunk metadata."""
        return self.indexes_dir / "bm25" / "bm25_chunk_ids.json"

    @property
    def qdrant_dir(self) -> Path:
        """Local vector store."""
        return self.indexes_dir / "qdrant"

    @property
    def stopwords(self) -> Path:
        """Optional Vietnamese stopword source."""
        return self.raw_dir / "stopwords.txt"
