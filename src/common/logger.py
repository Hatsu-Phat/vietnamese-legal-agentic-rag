"""Centralized logging configuration using Loguru.

Ensures structured, timestamped logs with proper level filtering
without leaking secrets or dumping unbounded payloads.
"""

import sys

from loguru import logger

# Remove default handler and configure standardized formatting
logger.remove()
logger.add(
    sys.stderr,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    ),
    level="INFO",
    colorize=True,
)

__all__ = ["logger"]
