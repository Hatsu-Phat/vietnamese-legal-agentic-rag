"""Common utilities, configurations, and exception definitions."""

from src.common.config import AppSettings, settings
from src.common.exceptions import (
    CorpusNotFoundError,
    CorpusParseError,
    DataValidationError,
    EvaluationImportError,
    LegalRAGException,
    ManifestError,
)
from src.common.logger import logger
from src.common.text_utils import (
    build_provision_id,
    compute_sha256,
    extract_article_number,
    normalize_document_id,
    normalize_legal_text,
    parse_document_number_metadata,
)

__all__ = [
    "AppSettings",
    "CorpusNotFoundError",
    "CorpusParseError",
    "DataValidationError",
    "EvaluationImportError",
    "LegalRAGException",
    "ManifestError",
    "build_provision_id",
    "compute_sha256",
    "extract_article_number",
    "logger",
    "normalize_document_id",
    "normalize_legal_text",
    "parse_document_number_metadata",
    "settings",
]
