"""Cross-encoder reranking of fused candidates (Phase 5 experiment; off in production)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.common.config import PROJECT_ROOT
from src.retrieval.embedding import resolve_device


class CrossEncoderReranker:
    """Load a pinned CrossEncoder from models/cache on first use."""

    def __init__(
        self,
        model_id: str,
        device: str = "auto",
        max_length: int = 512,
        revision: str | None = None,
        batch_size: int = 16,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.max_length = max_length
        self.revision = revision
        self.batch_size = batch_size
        self._model: Any = None

    @property
    def model(self) -> Any:
        """Return the lazily loaded model, recording the resolved revision."""
        if self._model is None:
            from huggingface_hub import hf_hub_download
            from sentence_transformers import CrossEncoder

            cache = str(PROJECT_ROOT / "models/cache")
            config_path = hf_hub_download(
                self.model_id, "config.json", cache_dir=cache, revision=self.revision
            )
            self.revision = Path(config_path).parent.name
            self._model = CrossEncoder(
                self.model_id,
                device=resolve_device(self.device),
                cache_folder=cache,
                revision=self.revision,
                max_length=self.max_length,
            )
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """One relevance score per passage, in input order."""
        if not passages:
            return []
        scores = self.model.predict(
            [(query, p) for p in passages], batch_size=self.batch_size, show_progress_bar=False
        )
        return [float(s) for s in scores]


def rerank[T](items: Sequence[T], scores: Sequence[float]) -> list[T]:
    """Sort by score descending; ties keep the incoming (fused) order."""
    if len(items) != len(scores):
        raise ValueError("One score is required per item")
    order = sorted(range(len(items)), key=lambda i: (-scores[i], i))
    return [items[i] for i in order]
