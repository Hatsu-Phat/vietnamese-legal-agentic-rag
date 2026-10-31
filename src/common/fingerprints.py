"""Stable hashes shared by index builders and read-only search startup."""

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    """Hash file bytes in bounded blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def payload_fingerprint(payload: dict[str, Any]) -> str:
    """Canonical JSON hash, independent of field order or output whitespace."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def bm25_versions() -> dict[str, str]:
    """Record dependencies that affect tokenization and serialized rank-bm25."""
    import platform

    return {
        "python": platform.python_version(),
        **{p: version(p) for p in ("rank-bm25", "pyvi", "numpy")},
    }
