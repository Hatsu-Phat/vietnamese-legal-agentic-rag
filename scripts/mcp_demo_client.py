"""MCP demo client (P5/T-0501): start the stdio server once per role and call all three tools.

    uv run --locked python scripts/mcp_demo_client.py --roles legal,employee

The legal role finds a confidential chunk with search_legal and opens it with get_source; the
employee role must not see it in search results and gets SOURCE_NOT_FOUND from get_source.
`ask` runs twice per role to show the second call served from the cache. The report keeps
only IDs, access levels, decisions, flags and timings; no source text or answers.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client, StdioServerParameters

from src.common.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUERY = "khung lương bậc E3 và E4"
DEFAULT_QUESTION = "Thời hiệu khởi kiện tranh chấp hợp đồng là bao lâu?"


def role_key(role: str) -> str:
    secret = getattr(get_settings(), f"auth_api_key_{role}")
    if secret is None or not secret.get_secret_value().strip():
        raise SystemExit(f"AUTH_API_KEY_{role.upper()} is not set")
    return str(secret.get_secret_value())


def text_of(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


async def run_role(
    role: str, query: str, question: str, probe_chunk: str | None, timeout: float
) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "scripts" / "mcp_server.py")],
        env={"MCP_API_KEY": role_key(role), "PYTHONIOENCODING": "utf-8"},
        cwd=ROOT,
    )
    report: dict[str, Any] = {"role": role}
    started = time.perf_counter()
    async with Client(params, read_timeout_seconds=timeout) as client:
        report["startup_s"] = round(time.perf_counter() - started, 1)
        report["tools"] = sorted(tool.name for tool in (await client.list_tools()).tools)

        t0 = time.perf_counter()
        found = await client.call_tool("search_legal", {"query": query, "top_k": 10})
        hits = [] if found.is_error else found.structured_content["results"]
        report["search"] = {
            "is_error": found.is_error,
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "hits": [
                {"chunk_id": h["chunk_id"], "access_level": h.get("access_level")} for h in hits
            ],
        }
        confidential = [h["chunk_id"] for h in hits if h.get("access_level") == "confidential"]
        target = probe_chunk or (confidential[0] if confidential else None)
        if target is not None:
            opened = await client.call_tool("get_source", {"chunk_id": target})
            report["get_source"] = {
                "chunk_id": target,
                "is_error": opened.is_error,
                "error": text_of(opened) if opened.is_error else None,
                "access_level": None if opened.is_error else "opened",
            }
        report["probe_chunk"] = target

        asks = []
        for _ in range(2):
            t0 = time.perf_counter()
            result = await client.call_tool("ask", {"question": question})
            body = result.structured_content or {}
            asks.append(
                {
                    "is_error": result.is_error,
                    "error": text_of(result) if result.is_error else None,
                    "decision": body.get("decision"),
                    "citations": [c["chunk_id"] for c in body.get("citations", [])],
                    "cache_hit": "cache_hit" in body.get("trace", []),
                    "elapsed_s": round(time.perf_counter() - t0, 2),
                }
            )
        report["ask"] = asks
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roles", default="legal,employee")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds per MCP request")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/reports/p5/mcp_demo.json")
    args = parser.parse_args()

    reports: list[dict[str, Any]] = []
    probe: str | None = None
    for role in [r.strip() for r in args.roles.split(",") if r.strip()]:
        report = await run_role(role, args.query, args.question, probe, args.timeout)
        probe = probe or report.get("probe_chunk")
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"query": args.query, "question": args.question, "runs": reports},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
