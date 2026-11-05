"""Mutable LangGraph state for one /ask request."""

import operator
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from src.core.access import PUBLIC_ONLY, AccessScope
from src.core.agent import (
    AbstainReason,
    AskRequest,
    CitationAudit,
    DraftAnswer,
    Evidence,
    Limitation,
)


class AgentState(BaseModel):
    """Nodes return partial updates; only `trace` accumulates across nodes."""

    model_config = ConfigDict(extra="forbid")

    request: AskRequest
    # Set from authentication, never from the request body.
    access: AccessScope = PUBLIC_ONLY
    # Set when the question contained personal identifiers (masked before any node runs);
    # the P3 router must keep such requests on the local model.
    contains_pii: bool = False
    query: str = ""
    rounds: int = 0
    evidence: list[Evidence] = Field(default_factory=list)
    usable: list[Evidence] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)
    draft: DraftAnswer | None = None
    audit: CitationAudit | None = None
    unsupported: bool = False
    abstain_reason: AbstainReason | None = None
    trace: Annotated[list[str], operator.add] = Field(default_factory=list)
