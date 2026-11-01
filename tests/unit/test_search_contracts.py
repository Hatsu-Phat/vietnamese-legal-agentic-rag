"""Search contracts, ranking, real adapters and bounded asynchronous execution."""

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.search import (
    SearchErrorResponse,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)
from tests.search_support import (
    catalog as catalog,
)


@pytest.mark.parametrize(
    "data",
    [
        {"query": " "},
        {"query": 123},
        {"query": []},
        {"query": "x", "top_k": True},
        {"query": "x", "top_k": "10"},
        {"query": "x", "top_k": 21},
        {"query": "x", "top_k": 0},
        {"query": "x", "unknown": 1},
        {"query": "x", "filters": {"issued_year": True}},
        {"query": "x", "filters": {"document_id": " "}},
        {"query": "x", "filters": {"status": "valid"}},
        {"query": "x", "filters": None},
        {"query": "x" * 2001},
    ],
)
def test_strict_request(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(data)


def test_normalization_and_contract_fixtures() -> None:
    q = unicodedata.normalize("NFD", " luật ")
    request = SearchRequest(query=q, filters=SearchFilters(document_type=q))
    assert request.query == "luật" and request.filters.document_type == "luật"
    root = Path(__file__).resolve().parents[1] / "fixtures/search"
    SearchRequest.model_validate_json((root / "request.json").read_text())
    success = SearchResponse.model_validate_json((root / "success.json").read_text())
    SearchResponse.model_validate_json((root / "empty.json").read_text())
    SearchErrorResponse.model_validate_json((root / "unavailable.json").read_text())
    assert "embedding_text" not in success.model_dump_json()
    with pytest.raises(ValidationError):
        SearchResponse.model_validate({**success.model_dump(), "returned_count": 2})


def test_invalid_response_scores_and_warning_duplicates() -> None:
    from src.core.search import SearchHit

    data = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures/search/success.json").read_text()
    )
    hit = data["results"][0]
    with pytest.raises(ValidationError):
        SearchHit.model_validate({**hit, "score": float("nan")})
    with pytest.raises(ValidationError):
        SearchHit.model_validate({**hit, "matched_by": ["dense", "dense"]})
    with pytest.raises(ValidationError):
        SearchResponse.model_validate({**data, "warnings": [data["warnings"][0]] * 2})
