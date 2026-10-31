"""Atomic output helpers: failed serialization never replaces the previous file."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Any


@contextmanager
def atomic_open(path: Path, mode: str = "w", newline: str | None = None) -> Iterator[IO[Any]]:
    """Write alongside the destination; close before replacing for Windows support."""
    if mode not in {"w", "wb"}:
        raise ValueError("Atomic output supports w and wb only")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp: Path | None = None
    try:
        with NamedTemporaryFile(
            mode=mode,
            dir=path.parent,
            delete=False,
            encoding=None if "b" in mode else "utf-8",
            newline=newline,
        ) as stream:
            temp = Path(stream.name)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
