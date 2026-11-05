"""Safe /ask domain errors with stable codes; the API owns HTTP serialization."""

from src.common.exceptions import LegalRAGException
from src.core.agent import AgentErrorCode


class AgentError(LegalRAGException):
    """Only the fixed message/code may cross a service boundary."""

    def __init__(self, code: AgentErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status_code(self) -> int:
        """Convenience mapping for the future FastAPI exception handler."""
        return {
            "INVALID_REQUEST": 422,
            "SEARCH_UNAVAILABLE": 503,
            "SEARCH_TIMEOUT": 504,
            "LLM_UNAVAILABLE": 503,
            "LLM_TIMEOUT": 504,
            "INTERNAL_ERROR": 500,
        }[self.code]


class LLMUnavailable(AgentError):
    """Missing key, provider failure or unparseable structured output."""

    def __init__(self) -> None:
        super().__init__("LLM_UNAVAILABLE", "Mô hình ngôn ngữ chưa sẵn sàng. Vui lòng thử lại sau.")


class LLMTimeout(AgentError):
    """The provider exceeded the configured timeout."""

    def __init__(self) -> None:
        super().__init__(
            "LLM_TIMEOUT", "Mô hình ngôn ngữ vượt quá thời gian cho phép. Vui lòng thử lại sau."
        )
