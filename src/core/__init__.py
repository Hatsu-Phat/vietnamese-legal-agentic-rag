"""Core domain package for Vietnamese Legal Agentic RAG.

Exports canonical Pydantic v2 schemas and validation enums.
"""

from src.core.enums import (
    AnswerableStatus,
    DocumentRelationType,
    LegalValidityStatus,
    QuestionType,
)
from src.core.models import (
    DocumentRelation,
    LegalDocument,
    LegalProvision,
    QAEvaluationItem,
)

__all__ = [
    "AnswerableStatus",
    "DocumentRelation",
    "DocumentRelationType",
    "LegalDocument",
    "LegalProvision",
    "LegalValidityStatus",
    "QAEvaluationItem",
    "QuestionType",
]
