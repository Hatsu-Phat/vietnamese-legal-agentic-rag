"""Custom exception classes for Vietnamese Legal Agentic RAG.

All custom exceptions inherit from LegalRAGException to enable centralized
error handling across ingestion, retrieval, and agent orchestration.
"""

from typing import Any


class LegalRAGException(Exception):
    """Base exception for all Vietnamese Legal Agentic RAG errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message


class CorpusNotFoundError(LegalRAGException):
    """Raised when the raw legal corpus file or required directory does not exist."""


class CorpusParseError(LegalRAGException):
    """Raised when parsing corpus data fails due to malformed JSON, CSV, or encoding errors."""


class DataValidationError(LegalRAGException):
    """Raised when data records fail schema validation or consistency checks."""


class ManifestError(LegalRAGException):
    """Raised when reading, generating, or validating manifest files fails."""


class EvaluationImportError(LegalRAGException):
    """Raised when importing Q&A evaluation datasets fails or format is invalid."""
