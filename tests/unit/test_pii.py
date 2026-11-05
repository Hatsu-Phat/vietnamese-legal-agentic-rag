"""PII guardrail (P2/T-0202): detection, masking, and that identifiers never leave the API."""

from pathlib import Path
from uuid import uuid4

import pytest
from loguru import logger

from src.agents.graph import AskService
from src.api.dependencies import ApiServices
from src.core.agent import AskRequest, DraftAnswer, Evidence
from src.evaluation.pii import load_labelled, score
from src.guardrails.pii import PiiType, detect_pii, mask_pii
from src.retrieval.search_catalog import CatalogSources
from tests.search_support import write_catalog
from tests.unit.test_agent_graph import FakeGenerator, FakeSearch, answer, hit
from tests.unit.test_api import client_for

ROOT = Path(__file__).resolve().parents[2]
SENSITIVE = ["001203004567", "0912 345 678", "an.nguyen@tnt.vn", "19035566778899"]
QUESTION = (
    "Tôi CCCD 001203004567, SĐT 0912 345 678, email an.nguyen@tnt.vn, "
    "STK 19035566778899: lương thử việc tối thiểu bao nhiêu?"
)


def test_masks_every_type_and_keeps_the_question() -> None:
    result = mask_pii(QUESTION)
    assert result.contains_pii
    assert set(result.types) == {
        PiiType.NATIONAL_ID,
        PiiType.PHONE,
        PiiType.EMAIL,
        PiiType.BANK_ACCOUNT,
    }
    assert not any(value in result.text for value in SENSITIVE)
    assert result.text.endswith("lương thử việc tối thiểu bao nhiêu?")
    assert "[SỐ_GIẤY_TỜ]" in result.text and "[SỐ_TÀI_KHOẢN]" in result.text


@pytest.mark.parametrize(
    "text",
    [
        "Điều 113 Bộ luật Lao động số 45/2019/QH14",
        "Quyết định 01/2026/QĐ-TNT, phạt 500.000.000 đồng",
        "Tổng đài 1900 1234, mức sẵn sàng 99,9%",
        "Mã số thuế doanh nghiệp 0312345678",
        "Tài khoản quản trị phải bật xác thực đa yếu tố",
    ],
)
def test_legal_text_is_not_pii(text: str) -> None:
    assert detect_pii(text) == []
    assert mask_pii(text).text == text


def test_card_needs_luhn_and_account_needs_keyword() -> None:
    assert [m.type for m in detect_pii("Thẻ 4111 1111 1111 1111")] == [PiiType.PAYMENT_CARD]
    assert detect_pii("Mã 4111 1111 1111 1112") == []
    assert detect_pii("Đơn hàng 19035566778899") == []


def test_labelled_set_is_consistent_and_fully_detected() -> None:
    report = score(load_labelled(ROOT / "tests" / "eval" / "pii_v1.jsonl"), [])
    assert report["labelled_values"] == 34
    assert report["precision"] == 1.0 and report["recall"] == 1.0


class RecordingGenerator(FakeGenerator):
    def __init__(self) -> None:
        super().__init__([answer("S1")])
        self.questions: list[str] = []

    async def draft(self, question: str, evidence: list[Evidence]) -> DraftAnswer:
        self.questions.append(question)
        return await super().draft(question, evidence)


@pytest.mark.asyncio
async def test_agent_never_sees_identifiers_and_reports_masking() -> None:
    search = FakeSearch([[hit(1)]])
    generator = RecordingGenerator()
    response = await AskService(search, generator, 2).ask(AskRequest(question=QUESTION), uuid4())
    assert generator.questions
    seen = " ".join([*search.queries, *generator.questions])
    assert not any(value in seen for value in SENSITIVE)
    assert "PII_MASKED" in [lim.code for lim in response.limitations]
    clean = await AskService(FakeSearch([[hit(1)]]), FakeGenerator([answer("S1")]), 2).ask(
        AskRequest(question="Lương thử việc tối thiểu bao nhiêu?"), uuid4()
    )
    assert "PII_MASKED" not in [lim.code for lim in clean.limitations]


def test_search_route_masks_query_and_logs_no_identifier(tmp_path: Path) -> None:
    search = FakeSearch([[hit(1)]])
    services = ApiServices(
        search=search,
        sources=CatalogSources(write_catalog(tmp_path / "chunks.jsonl", count=3)),
        ask=AskService(search, FakeGenerator([answer("S1")]), 2),
    )
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="DEBUG")
    try:
        for client in client_for(services):
            assert client.post("/search", json={"query": QUESTION}).status_code == 200
            assert client.post("/ask", json={"question": QUESTION}).status_code == 200
    finally:
        logger.remove(sink)
    assert len(search.queries) == 2
    assert not any(value in query for value in SENSITIVE for query in search.queries)
    assert not any(value in line for value in SENSITIVE for line in lines)
