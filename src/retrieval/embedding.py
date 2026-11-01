"""Lazy BGE encoding, independent of storage and migration."""

from pathlib import Path
from typing import Any

from src.common.config import PROJECT_ROOT


def resolve_device(device: str = "auto") -> str:
    """Select acceleration only when encoding is actually requested."""
    if device != "auto":
        return device
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class BGEEncoder:
    """Load SentenceTransformer on first use, retaining its resolved revision."""

    def __init__(
        self,
        model_id: str,
        device: str = "auto",
        max_seq_length: int = 512,
        revision: str | None = None,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.max_seq_length = max_seq_length
        self._model: Any = None
        self.revision = revision

    @property
    def model(self) -> Any:
        """Return the lazily loaded model."""
        if self._model is None:
            from huggingface_hub import hf_hub_download
            from sentence_transformers import SentenceTransformer

            cache = str(PROJECT_ROOT / "models/cache")
            config_path = hf_hub_download(
                self.model_id, "config.json", cache_dir=cache, revision=self.revision
            )
            self.revision = Path(config_path).parent.name
            self._model = SentenceTransformer(
                self.model_id,
                device=resolve_device(self.device),
                cache_folder=cache,
                revision=self.revision,
            )
            self._model.max_seq_length = self.max_seq_length
        return self._model
