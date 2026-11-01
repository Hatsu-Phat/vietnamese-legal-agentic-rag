"""Safe domain errors with stable codes; the API owns HTTP serialization."""

from src.common.exceptions import LegalRAGException
from src.core.search import ErrorCode


class SearchError(LegalRAGException):
    """Only the fixed message/code may cross a service boundary."""

    def __init__(self, code: ErrorCode, message: str) -> None:
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
            "INTERNAL_ERROR": 500,
        }[self.code]


class InvalidSearchRequest(SearchError):
    """An invalid request without echoing its contents."""

    def __init__(self) -> None:
        super().__init__(
            "INVALID_REQUEST", "Câu hỏi hoặc bộ lọc không hợp lệ; giới hạn câu hỏi là 512 token."
        )


class SearchUnavailable(SearchError):
    """A failed dependency, missing payload or incompatible release."""

    def __init__(self) -> None:
        super().__init__(
            "SEARCH_UNAVAILABLE", "Dịch vụ tìm kiếm chưa sẵn sàng. Vui lòng thử lại sau."
        )


class SearchTimeout(SearchError):
    """The request/dependency exceeded its deadline."""

    def __init__(self) -> None:
        super().__init__(
            "SEARCH_TIMEOUT", "Tìm kiếm vượt quá thời gian cho phép. Vui lòng thử lại sau."
        )
