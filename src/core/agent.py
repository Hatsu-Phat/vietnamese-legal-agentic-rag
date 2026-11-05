"""Framework-independent contracts for the grounded /ask agent workflow."""

from datetime import date
from typing import Annotated, Literal, Protocol

from pydantic import UUID4, ConfigDict, Field, StrictInt, StrictStr, field_validator

from src.core.enums import LegalValidityStatus
from src.core.search import Contract, SearchFilters, normalize_string

Decision = Literal["answer", "clarify", "abstain"]
AbstainReason = Literal[
    "NO_EVIDENCE",
    "ONLY_INVALID_EVIDENCE",
    "CITATION_INVALID",
    "INSUFFICIENT_EVIDENCE",
    "UNSUPPORTED_CLAIMS",
    "NEEDS_CLARIFICATION",
]
AgentErrorCode = Literal[
    "INVALID_REQUEST",
    "SEARCH_UNAVAILABLE",
    "SEARCH_TIMEOUT",
    "LLM_UNAVAILABLE",
    "LLM_TIMEOUT",
    "INTERNAL_ERROR",
]
LimitationCode = Literal[
    "VALIDITY_UNVERIFIED",
    "EXCLUDED_INVALID_EVIDENCE",
    "AS_OF_DATE_UNVERIFIED",
    "SOURCE_URL_UNAVAILABLE",
    "UNSUPPORTED_CLAIMS_REMOVED",
]


class AskRequest(Contract):
    """One legal question; the optional date is the moment the user asks about."""

    # Swagger example omits ``filters``: placeholder filter values match nothing.
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"question": "Thời hiệu khởi kiện tranh chấp hợp đồng là bao lâu?"}]
        }
    )

    question: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    as_of_date: date | None = None
    top_k: Annotated[StrictInt, Field(ge=1, le=20)] = 8
    filters: SearchFilters = Field(default_factory=SearchFilters)

    @field_validator("question", mode="before")
    @classmethod
    def normalize(cls, value: object) -> object:
        """Same NFC/whitespace normalization as SearchRequest."""
        return normalize_string(value)


class Evidence(Contract):
    """A retrieved chunk shown to the LLM under a stable citation ID."""

    citation_id: str
    chunk_id: str
    document_id: str
    document_number: str
    title: str
    breadcrumb: str
    text: str
    status: LegalValidityStatus
    issued_year: int | None
    source_url: str | None


class DraftClaim(Contract):
    """One legal statement proposed by the LLM, before validation."""

    text: Annotated[StrictStr, Field(min_length=1)]
    citation_ids: list[StrictStr] = Field(default_factory=list)


class DraftAnswer(Contract):
    """Structured LLM output; untrusted until audited against the evidence."""

    decision: Decision
    answer: StrictStr = ""
    claims: list[DraftClaim] = Field(default_factory=list)
    clarifying_question: StrictStr | None = None


class CitationIssue(Contract):
    """A deterministic citation audit failure."""

    code: Literal["UNKNOWN_CITATION", "CLAIM_WITHOUT_CITATION", "ANSWER_WITHOUT_CLAIMS"]
    claim_index: int | None = None
    citation_id: str | None = None


class CitationAudit(Contract):
    """Audit result; passing means formal grounding only, not entailment."""

    passed: bool
    issues: list[CitationIssue] = Field(default_factory=list)


class ClaimVerdict(Contract):
    """Reviewer judgement of whether the cited evidence states the claim."""

    claim_index: Annotated[StrictInt, Field(ge=0)]
    supported: bool


class ClaimReview(Contract):
    """Reviewer output; claims without a verdict count as unsupported."""

    verdicts: list[ClaimVerdict] = Field(default_factory=list)


class Limitation(Contract):
    """A caveat the user must see together with the answer."""

    code: LimitationCode
    message: str


class Citation(Contract):
    """User-facing source; every field comes from retrieval metadata, not the LLM."""

    citation_id: str
    chunk_id: str
    document_id: str
    document_number: str
    title: str
    breadcrumb: str
    status: LegalValidityStatus
    issued_year: int | None
    source_url: str | None


class Claim(Contract):
    """A validated statement whose citation IDs all resolve to evidence."""

    text: str
    citation_ids: list[str] = Field(min_length=1)


class AskResponse(Contract):
    """Stable /ask response shared by the agent service, CLI and HTTP adapters."""

    request_id: UUID4
    decision: Decision
    answer: str
    claims: list[Claim] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    clarifying_question: str | None = None
    abstain_reason: AbstainReason | None = None
    limitations: list[Limitation] = Field(default_factory=list)
    as_of_date: date | None = None
    retrieval_rounds: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    trace: list[str] = Field(default_factory=list)


class AskErrorResponse(Contract):
    """Safe error envelope; never echoes the question or provider errors."""

    request_id: UUID4
    code: AgentErrorCode
    message: str


class AnswerGenerator(Protocol):
    """The LLM boundary; implementations must enforce their own timeout."""

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        """Draft an answer citing only the given evidence IDs."""
        ...

    async def rewrite_query(self, question: str, feedback: str) -> str:
        """Return one alternative search query for the next retrieval round."""
        ...

    async def review(self, claims: list[DraftClaim], evidence: list[Evidence]) -> ClaimReview:
        """Judge each claim only against the evidence it cites."""
        ...
