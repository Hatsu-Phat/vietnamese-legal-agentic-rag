"""P2 role-based access: scopes are server-side, applied before top-k, and never leak."""

import hashlib
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.agents.graph import AskService
from src.api.dependencies import AccessResolver, ApiServices, Unauthorized
from src.common.config import load_yaml_config
from src.common.search_errors import InvalidSearchRequest
from src.core.access import (
    DEFAULT_ROLE_ACCESS,
    FULL_ACCESS,
    PUBLIC_ONLY,
    AccessPolicy,
    AccessScope,
)
from src.core.agent import AskRequest
from src.core.enums import AccessLevel, SourceKind
from src.core.models import LegalChunk
from src.core.search import SearchFilters, SearchRequest
from src.core.search_release import SearchRuntime
from src.retrieval.search_adapters import BM25SparseSearch, QdrantDenseSearch
from src.retrieval.search_catalog import CatalogSources, ChunkCatalog
from src.retrieval.search_service import SearchService
from tests.search_support import Encoder, real_bm25
from tests.unit.test_agent_graph import FakeGenerator, FakeSearch, answer, hit
from tests.unit.test_api import client_for

MANAGER: AccessScope = DEFAULT_ROLE_ACCESS["manager"]
KEYS = {
    "employee": SecretStr("employee-key-0123456789"),
    "manager": SecretStr("manager-key-0123456789"),
    "legal": SecretStr("legal-key-0123456789abc"),
}

# (document_id, source_kind, access_level); None = release before P1 (no access fields).
DOCS: list[tuple[str, SourceKind | None, AccessLevel | None]] = [
    ("law_old", None, None),
    ("law_new", SourceKind.LAW, AccessLevel.PUBLIC),
    ("internal_pub", SourceKind.INTERNAL, AccessLevel.PUBLIC),
    ("internal_int", SourceKind.INTERNAL, AccessLevel.INTERNAL),
    ("internal_conf", SourceKind.INTERNAL, AccessLevel.CONFIDENTIAL),
]


def mixed_catalog(path: Path) -> ChunkCatalog:
    chunks = []
    for doc, kind, level in DOCS:
        for article in (1, 2):
            cid = f"{doc}__art_{article}__c0"
            text = f"quy định lương thưởng bảo mật điều {article} {doc}"
            chunks.append(
                LegalChunk(
                    chunk_id=cid,
                    point_uuid=LegalChunk.generate_point_uuid(cid),
                    document_id=doc,
                    document_number=doc.upper(),
                    provision_id=f"{doc}__art_{article}",
                    article_id=str(article),
                    chunk_index=0,
                    total_chunks_in_provision=1,
                    is_full_article=True,
                    breadcrumb=f"{doc} > Điều {article}",
                    title=f"Điều {article}",
                    text=text,
                    embedding_text=text,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    char_length=len(text),
                    word_count=len(text.split()),
                    document_type="Quy chế",
                    issued_year=2026,
                    source_kind=kind,
                    access_level=level,
                )
            )
    path.write_text("\n".join(c.model_dump_json() for c in chunks) + "\n", encoding="utf-8")
    return ChunkCatalog(path)


@pytest.fixture
def catalog(tmp_path: Path) -> ChunkCatalog:
    return mixed_catalog(tmp_path / "chunks.jsonl")


@pytest.fixture
def service(tmp_path: Path, catalog: ChunkCatalog) -> Iterator[SearchService]:
    """Real Qdrant (in memory) and real BM25 over the mixed catalog."""
    client = QdrantClient(":memory:")
    client.create_collection("demo", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    client.upsert(
        "demo",
        points=[
            PointStruct(id=uid, vector=[1.0, 0.0, 0.0, 0.0], payload=c.model_dump(mode="json"))
            for uid, c in catalog.chunks.items()
        ],
    )
    indexer = real_bm25(tmp_path, catalog)
    yield SearchService(
        catalog,
        Encoder(),
        QdrantDenseSearch(client, "demo", catalog),
        BM25SparseSearch(indexer, catalog),
        SearchRuntime(vector_size=4, dense_candidates=20, sparse_candidates=20),
    )


def docs_of(chunk_ids: list[str]) -> set[str]:
    return {cid.split("__")[0] for cid in chunk_ids}


def test_policy_yaml_matches_default_matrix_and_rejects_bad_policies() -> None:
    assert AccessPolicy.from_config(load_yaml_config()).roles == DEFAULT_ROLE_ACCESS
    assert AccessPolicy.from_config({}).anonymous == PUBLIC_ONLY
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate({"roles": {"employee": ["public"]}})
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate({"roles": {**DEFAULT_ROLE_ACCESS, "manager": ["internal"]}})
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate({"anonymous": ["public", "internal"]})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "visible"),
    [
        (PUBLIC_ONLY, {"law_old", "law_new", "internal_pub"}),
        (MANAGER, {"law_old", "law_new", "internal_pub", "internal_int"}),
        (FULL_ACCESS, {doc for doc, *_ in DOCS}),
    ],
)
async def test_both_branches_filter_before_top_k(
    service: SearchService, scope: AccessScope, visible: set[str]
) -> None:
    run = await service.diagnose(
        SearchRequest(query="lương thưởng bảo mật", top_k=20), uuid4(), scope
    )
    for branch in (run.dense, run.bm25):
        ids = [service.catalog.chunks[str(c.point_uuid)].chunk_id for c in branch]
        assert docs_of(ids) == visible
    assert docs_of([h.chunk_id for h in run.response.results]) == visible
    # Small top-k still fills from allowed chunks only (no post-filter shortfall).
    small = await service.search(SearchRequest(query="lương thưởng", top_k=2), uuid4(), scope)
    assert small.returned_count == 2 and docs_of([h.chunk_id for h in small.results]) <= visible


@pytest.mark.asyncio
async def test_default_scope_is_public_and_user_filters_cannot_widen_it(
    service: SearchService,
) -> None:
    default = await service.search(SearchRequest(query="lương thưởng", top_k=20), uuid4())
    assert "internal_conf" not in docs_of([h.chunk_id for h in default.results])
    targeted = await service.search(
        SearchRequest(
            query="lương thưởng", top_k=20, filters=SearchFilters(document_id="internal_conf")
        ),
        uuid4(),
    )
    assert targeted.returned_count == 0
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"query": "x", "filters": {"access_level": "confidential"}})
    with pytest.raises(InvalidSearchRequest):
        await service.search(
            SearchRequest(query="x"), uuid4(), frozenset({AccessLevel.CONFIDENTIAL})
        )


def test_sources_hide_out_of_scope_chunks(catalog: ChunkCatalog) -> None:
    sources = CatalogSources(catalog)
    rid = uuid4()
    assert sources.get_source("internal_conf__art_1__c0", rid) is None
    assert sources.get_source("internal_conf__art_1__c0", rid, MANAGER) is None
    assert sources.get_source("internal_conf__art_1__c0", rid, FULL_ACCESS) is not None
    assert sources.get_source("law_old__art_1__c0", rid) is not None


def test_resolver_maps_keys_to_roles_and_rejects_unknown() -> None:
    resolver = AccessResolver(AccessPolicy(), KEYS)
    assert resolver.resolve(None) == (None, PUBLIC_ONLY)
    assert resolver.resolve("manager-key-0123456789") == ("manager", MANAGER)
    assert resolver.resolve("legal-key-0123456789abc")[1] == FULL_ACCESS
    with pytest.raises(Unauthorized):
        resolver.resolve("legal-key-0123456789ab")
    disabled = AccessResolver(AccessPolicy(), {"legal": SecretStr("   ")})
    with pytest.raises(Unauthorized):
        disabled.resolve("   ")
    with pytest.raises(ValueError):
        AccessResolver(AccessPolicy(), {"legal": SecretStr("short")})
    with pytest.raises(ValueError):
        AccessResolver(
            AccessPolicy(),
            {
                "manager": SecretStr("same-key-0123456789"),
                "legal": SecretStr("same-key-0123456789"),
            },
        )


@pytest.fixture
def fake_search() -> FakeSearch:
    return FakeSearch([[hit(1)]])


@pytest.fixture
def client(catalog: ChunkCatalog, fake_search: FakeSearch) -> Iterator[TestClient]:
    search = fake_search
    services = ApiServices(
        search=search,
        sources=CatalogSources(catalog),
        ask=AskService(search, FakeGenerator([answer("S1")]), 2),
        access=AccessResolver(AccessPolicy(), KEYS),
    )
    yield from client_for(services)


def test_api_passes_role_scope_and_hides_sources(
    client: TestClient, fake_search: FakeSearch
) -> None:
    search = fake_search
    assert client.post("/search", json={"query": "q"}).status_code == 200
    assert (
        client.post(
            "/search", json={"query": "q"}, headers={"X-API-Key": "manager-key-0123456789"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/ask", json={"question": "q"}, headers={"X-API-Key": "legal-key-0123456789abc"}
        ).status_code
        == 200
    )
    assert search.scopes == [PUBLIC_ONLY, MANAGER, FULL_ACCESS]

    bad = client.post("/search", json={"query": "q"}, headers={"X-API-Key": "guess"})
    assert bad.status_code == 401 and bad.json()["code"] == "UNAUTHORIZED"
    assert "guess" not in bad.text
    assert (
        client.post("/ask", json={"question": "q"}, headers={"X-API-Key": "x"}).status_code == 401
    )
    assert len(search.scopes) == 3

    hidden = client.get(
        "/sources/internal_conf__art_1__c0", headers={"X-API-Key": "employee-key-0123456789"}
    )
    missing = client.get(
        "/sources/no_such__art_1__c0", headers={"X-API-Key": "employee-key-0123456789"}
    )
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["code"] == missing.json()["code"] == "SOURCE_NOT_FOUND"
    shown = client.get(
        "/sources/internal_conf__art_1__c0", headers={"X-API-Key": "legal-key-0123456789abc"}
    )
    assert shown.status_code == 200
    body_scope = client.post(
        "/search", json={"query": "q", "filters": {"access_level": "confidential"}}
    )
    assert body_scope.status_code == 422


@pytest.mark.asyncio
async def test_agent_retrieves_with_the_callers_scope() -> None:
    search = FakeSearch([[hit(1)]])
    service = AskService(search, FakeGenerator([answer("S1")]), 2)
    await service.ask(AskRequest(question="q"), uuid4())
    await service.ask(AskRequest(question="q"), uuid4(), MANAGER)
    assert search.scopes == [PUBLIC_ONLY, MANAGER]
