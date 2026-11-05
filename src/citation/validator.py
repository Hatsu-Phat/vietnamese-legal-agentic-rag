"""Deterministic citation audit: every claim must cite evidence the LLM was given."""

from src.core.agent import CitationAudit, CitationIssue, DraftAnswer, Evidence


def audit_citations(draft: DraftAnswer, evidence: list[Evidence]) -> CitationAudit:
    """Check formal grounding only; it does not prove the source entails the claim."""
    if draft.decision != "answer":
        return CitationAudit(passed=True)
    known = {e.citation_id for e in evidence}
    issues: list[CitationIssue] = []
    if not draft.claims:
        issues.append(CitationIssue(code="ANSWER_WITHOUT_CLAIMS"))
    for index, claim in enumerate(draft.claims):
        if not claim.citation_ids:
            issues.append(CitationIssue(code="CLAIM_WITHOUT_CITATION", claim_index=index))
        issues.extend(
            CitationIssue(code="UNKNOWN_CITATION", claim_index=index, citation_id=cid)
            for cid in claim.citation_ids
            if cid not in known
        )
    return CitationAudit(passed=not issues, issues=issues)


def describe_issues(audit: CitationAudit) -> str:
    """Short feedback for query rewriting; never includes evidence text."""
    return "; ".join(
        f"{i.code}" + (f" (claim {i.claim_index})" if i.claim_index is not None else "")
        for i in audit.issues
    )
