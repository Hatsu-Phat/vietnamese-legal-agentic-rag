"""LangGraph workflow: plan → retrieve → temporal check → draft → audit → verify → route.

Routing, validity screening, citation audit and the retry budget are deterministic Python;
the LLM only drafts structured answers, rewrites queries and reviews claim support.
"""

import time
from typing import Any, Literal
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from src.agents.state import AgentState
from src.citation.abstention import ABSTAIN_MESSAGES, route_after_audit
from src.citation.review import apply_review
from src.citation.temporal import screen_evidence
from src.citation.validator import audit_citations, describe_issues
from src.common.agent_errors import AgentError
from src.common.logger import logger
from src.common.search_errors import SearchError
from src.core.agent import (
    AbstainReason,
    AnswerGenerator,
    AskRequest,
    AskResponse,
    Citation,
    Claim,
    Evidence,
)
from src.core.enums import LegalValidityStatus
from src.core.search import SearchHit, SearchRequest
from src.core.search_ports import SearchPort

# Evidence shown to the LLM across all rounds; bounds prompt size and cost.
MAX_EVIDENCE = 16


def to_evidence(hit: SearchHit, citation_id: str) -> Evidence:
    """Copy only retrieval metadata; nothing here comes from the LLM."""
    return Evidence(
        citation_id=citation_id,
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        document_number=hit.document_number,
        title=hit.title,
        breadcrumb=hit.breadcrumb,
        text=hit.text,
        status=hit.status,
        issued_year=hit.issued_year,
        source_url=hit.source_url,
    )


def merge_evidence(current: list[Evidence], hits: list[SearchHit]) -> list[Evidence]:
    """Keep first-seen order and short IDs (S1, S2, ...) stable across rounds."""
    seen = {e.chunk_id for e in current}
    merged = list(current)
    for hit in hits:
        if len(merged) >= MAX_EVIDENCE:
            break
        if hit.chunk_id not in seen:
            seen.add(hit.chunk_id)
            merged.append(to_evidence(hit, f"S{len(merged) + 1}"))
    return merged


def build_ask_graph(
    search: SearchPort, generator: AnswerGenerator, max_rounds: int, verify_claims: bool = True
) -> Any:
    """Compile the bounded workflow; `request_id` is passed through the run config."""

    def plan(state: AgentState) -> dict[str, Any]:
        return {"query": state.request.question, "trace": ["plan"]}

    async def retrieve(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        request_id: UUID = config.get("configurable", {})["request_id"]
        response = await search.search(
            SearchRequest(
                query=state.query, top_k=state.request.top_k, filters=state.request.filters
            ),
            request_id,
        )
        return {
            "rounds": state.rounds + 1,
            "evidence": merge_evidence(state.evidence, response.results),
            "trace": [f"retrieve:{state.rounds + 1}"],
        }

    def temporal_check(state: AgentState) -> dict[str, Any]:
        usable, limitations = screen_evidence(state.evidence, state.request.as_of_date)
        return {"usable": usable, "limitations": limitations, "trace": ["temporal_check"]}

    def after_temporal(state: AgentState) -> Literal["draft", "rewrite", "abstain"]:
        if state.usable:
            return "draft"
        return "rewrite" if state.rounds < max_rounds else "abstain"

    async def draft(state: AgentState) -> dict[str, Any]:
        result = await generator.draft(state.request.question, state.usable)
        return {"draft": result, "unsupported": False, "trace": ["draft"]}

    def audit(state: AgentState) -> dict[str, Any]:
        assert state.draft is not None
        result = audit_citations(state.draft, state.usable)
        return {"audit": result, "trace": [f"audit:{'pass' if result.passed else 'fail'}"]}

    def after_audit(state: AgentState) -> Literal["verify", "finalize", "rewrite", "abstain"]:
        assert state.draft is not None and state.audit is not None
        route, _ = route_after_audit(state.draft, state.audit, state.rounds, max_rounds)
        if route == "finalize" and verify_claims and state.draft.decision == "answer":
            return "verify"
        return "rewrite" if route == "retry" else route

    async def verify(state: AgentState) -> dict[str, Any]:
        assert state.draft is not None
        review = await generator.review(state.draft.claims, state.usable)
        reviewed, limitations = apply_review(state.draft, review)
        removed = len(state.draft.claims) - len(reviewed.claims)
        return {
            "draft": reviewed,
            "unsupported": not reviewed.claims,
            "limitations": state.limitations + limitations,
            "trace": [f"verify:{len(reviewed.claims)}/{len(reviewed.claims) + removed}"],
        }

    def after_verify(state: AgentState) -> Literal["finalize", "rewrite", "abstain"]:
        if not state.unsupported:
            return "finalize"
        return "rewrite" if state.rounds < max_rounds else "abstain"

    async def rewrite(state: AgentState) -> dict[str, Any]:
        if not state.usable:
            feedback = "no usable evidence retrieved"
        elif state.unsupported:
            feedback = "claims not supported by the cited evidence"
        elif state.audit is not None and not state.audit.passed:
            feedback = describe_issues(state.audit)
        else:
            feedback = "evidence judged insufficient"
        query = await generator.rewrite_query(state.request.question, feedback)
        return {"query": query.strip()[:2000] or state.request.question, "trace": ["rewrite"]}

    def abstain(state: AgentState) -> dict[str, Any]:
        reason: AbstainReason | None
        if state.unsupported:
            reason = "UNSUPPORTED_CLAIMS"
        elif state.draft is not None and state.audit is not None:
            _, reason = route_after_audit(state.draft, state.audit, state.rounds, max_rounds)
        elif state.evidence:
            reason = "ONLY_INVALID_EVIDENCE"
        else:
            reason = "NO_EVIDENCE"
        return {"abstain_reason": reason, "trace": ["abstain"]}

    def finalize(state: AgentState) -> dict[str, Any]:
        return {"trace": ["finalize"]}

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan)
    graph.add_node("retrieve", retrieve)
    graph.add_node("temporal_check", temporal_check)
    graph.add_node("draft", draft)
    graph.add_node("audit", audit)
    graph.add_node("verify", verify)
    graph.add_node("rewrite", rewrite)
    graph.add_node("abstain", abstain)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "temporal_check")
    graph.add_conditional_edges("temporal_check", after_temporal)
    graph.add_edge("draft", "audit")
    graph.add_conditional_edges("audit", after_audit)
    graph.add_conditional_edges("verify", after_verify)
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("abstain", END)
    graph.add_edge("finalize", END)
    return graph.compile()


def build_response(state: AgentState, request_id: UUID, elapsed_ms: float) -> AskResponse:
    """Map validated state to the public contract; sources come from evidence metadata."""
    common: dict[str, Any] = {
        "request_id": request_id,
        "as_of_date": state.request.as_of_date,
        "retrieval_rounds": state.rounds,
        "elapsed_ms": elapsed_ms,
        "trace": state.trace,
    }
    # Without cited sources, only the exclusion of invalid evidence is a relevant caveat.
    sourceless = [lim for lim in state.limitations if lim.code == "EXCLUDED_INVALID_EVIDENCE"]
    if state.abstain_reason is not None or state.draft is None:
        reason = state.abstain_reason or "NO_EVIDENCE"
        return AskResponse(
            decision="abstain",
            answer=ABSTAIN_MESSAGES[reason],
            abstain_reason=reason,
            limitations=sourceless,
            **common,
        )
    if state.draft.decision == "clarify":
        return AskResponse(
            decision="clarify",
            answer=state.draft.answer or ABSTAIN_MESSAGES["NEEDS_CLARIFICATION"],
            clarifying_question=state.draft.clarifying_question,
            abstain_reason="NEEDS_CLARIFICATION",
            limitations=sourceless,
            **common,
        )
    by_id = {e.citation_id: e for e in state.usable}
    cited = list(dict.fromkeys(cid for c in state.draft.claims for cid in c.citation_ids))
    citations = [
        Citation(
            citation_id=cid,
            document_number=by_id[cid].document_number,
            title=by_id[cid].title,
            breadcrumb=by_id[cid].breadcrumb,
            status=by_id[cid].status,
            issued_year=by_id[cid].issued_year,
            source_url=by_id[cid].source_url,
        )
        for cid in cited
    ]
    # Limitations describe only the sources the answer actually relies on.
    limitations = [
        lim
        for lim in state.limitations
        if lim.code == "EXCLUDED_INVALID_EVIDENCE"
        or (
            lim.code == "VALIDITY_UNVERIFIED"
            and any(c.status != LegalValidityStatus.VALID for c in citations)
        )
        or (lim.code == "SOURCE_URL_UNAVAILABLE" and any(c.source_url is None for c in citations))
        or lim.code in ("AS_OF_DATE_UNVERIFIED", "UNSUPPORTED_CLAIMS_REMOVED")
    ]
    return AskResponse(
        decision="answer",
        answer=state.draft.answer,
        claims=[Claim(text=c.text, citation_ids=c.citation_ids) for c in state.draft.claims],
        citations=citations,
        limitations=limitations,
        **common,
    )


class AskService:
    """Runs the compiled graph; the HTTP layer depends on this, not on LangGraph."""

    def __init__(
        self,
        search: SearchPort,
        generator: AnswerGenerator,
        max_rounds: int,
        verify_claims: bool = True,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        self._graph = build_ask_graph(search, generator, max_rounds, verify_claims)

    async def ask(self, request: AskRequest, request_id: UUID) -> AskResponse:
        """Return a grounded answer, clarification or abstention; raise only AgentError."""
        started = time.perf_counter()
        try:
            raw = await self._graph.ainvoke(
                AgentState(request=request),
                config={"configurable": {"request_id": request_id}},
            )
        except SearchError as exc:
            logger.warning("Ask failed id={} code={}", request_id, exc.code)
            raise AgentError(exc.code, exc.message) from None
        except AgentError as exc:
            logger.warning("Ask failed id={} code={}", request_id, exc.code)
            raise
        state = AgentState.model_validate(raw)
        elapsed = (time.perf_counter() - started) * 1000
        response = build_response(state, request_id, elapsed)
        # Trace only control flow and timing; never the question, evidence or answer text.
        logger.info(
            "Ask completed id={} decision={} rounds={} trace={} elapsed_ms={:.1f}",
            request_id,
            response.decision,
            response.retrieval_rounds,
            ",".join(response.trace),
            elapsed,
        )
        return response
