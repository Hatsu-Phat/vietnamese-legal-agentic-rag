"""P4 prompt variants: schema of each variant's output, CoT reasoning stays internal, usage."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from src.agents.gemini import GeminiAnswerGenerator
from src.agents.graph import merge_evidence
from src.agents.ollama import COT_DRAFT_SCHEMA, DRAFT_SCHEMA, OllamaAnswerGenerator
from src.agents.prompts import (
    DRAFT_SYSTEM,
    PROMPT_VARIANTS,
    PromptVariant,
    draft_system,
    parse_variant,
)
from src.common.agent_errors import LLMUnavailable
from tests.unit.test_agent_graph import hit

ROOT = Path(__file__).resolve().parents[2]
FEW_SHOT_PROVISIONS = {"law_2015_qh13_91__art_19", "law_2015_qh13_91__art_20"}
DRAFT = {
    "decision": "answer",
    "answer": "a",
    "claims": [{"text": "c", "citation_ids": ["S1"]}],
    "clarifying_question": None,
}


def test_every_variant_keeps_the_base_rules() -> None:
    for variant in PROMPT_VARIANTS:
        assert draft_system(variant).startswith(DRAFT_SYSTEM)
    assert draft_system("zero_shot") == DRAFT_SYSTEM
    assert "Ví dụ 1" in draft_system("few_shot") and "reasoning" not in draft_system("few_shot")
    assert '"reasoning"' in draft_system("cot")
    assert parse_variant(" COT ") == "cot" and parse_variant("") == "zero_shot"
    with pytest.raises(ValueError):
        parse_variant("tree_of_thought")


def test_few_shot_examples_are_not_eval_gold() -> None:
    sets = [ROOT / "tests/eval/ask_dev_authored.jsonl", ROOT / "tests/eval/enterprise_v1.jsonl"]
    sets += list((ROOT / "data/benchmarks").glob("qa_*.jsonl"))
    for path in sets:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                assert not FEW_SHOT_PROVISIONS & set(json.loads(line)["gold_provision_ids"])


def test_cot_schema_asks_for_reasoning_first() -> None:
    assert next(iter(COT_DRAFT_SCHEMA["properties"])) == "reasoning"
    assert COT_DRAFT_SCHEMA["required"][0] == "reasoning"
    assert "reasoning" not in DRAFT_SCHEMA["properties"]


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", PROMPT_VARIANTS)
async def test_ollama_variant_output_validates_and_drops_reasoning(variant: PromptVariant) -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        content = {"reasoning": "S1 nêu trực tiếp.", **DRAFT} if variant == "cot" else DRAFT
        return httpx.Response(
            200,
            json={
                "message": {"content": json.dumps(content, ensure_ascii=False)},
                "prompt_eval_count": 100,
                "eval_count": 20,
            },
        )

    gen = OllamaAnswerGenerator(
        "m",
        client=httpx.AsyncClient(base_url="http://o", transport=httpx.MockTransport(handler)),
        prompt_variant=variant,
    )
    draft = await gen.draft("q", merge_evidence([], [hit(1)]))
    assert draft.decision == "answer" and "reasoning" not in draft.model_dump()
    assert sent[0]["messages"][0]["content"] == draft_system(variant)  # type: ignore[index]
    assert ("reasoning" in sent[0]["format"]["properties"]) == (variant == "cot")  # type: ignore[index]
    assert gen.usage == {"calls": 1, "prompt_tokens": 100, "output_tokens": 20}


@pytest.mark.asyncio
async def test_gemini_cot_output_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        system = json.loads(request.content)["systemInstruction"]["parts"][0]["text"]
        assert system == draft_system("cot")
        text = json.dumps({"reasoning": "r", **DRAFT})
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": text}]}}],
                "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 40},
            },
        )

    gen = GeminiAnswerGenerator(
        SecretStr("k"),
        "m",
        client=httpx.AsyncClient(base_url="https://g", transport=httpx.MockTransport(handler)),
        prompt_variant="cot",
    )
    draft = await gen.draft("q", merge_evidence([], [hit(1)]))
    assert draft.claims[0].citation_ids == ["S1"]
    assert gen.usage["calls"] == 1 and gen.usage["prompt_tokens"] == 300
    assert gen.usage["output_tokens"] == 40


@pytest.mark.asyncio
async def test_schema_errors_are_counted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": '{"decision": "maybe"}'}})

    gen = OllamaAnswerGenerator(
        "m", client=httpx.AsyncClient(base_url="http://o", transport=httpx.MockTransport(handler))
    )
    with pytest.raises(LLMUnavailable):
        await gen.draft("q", merge_evidence([], [hit(1)]))
    assert gen.usage["schema_errors"] == 1
