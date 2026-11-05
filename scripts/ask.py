#!/usr/bin/env python3
"""Run the LangGraph /ask agent directly; the HTTP adapter uses the same contracts."""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from src.agents.factory import open_ask_service
from src.common.agent_errors import AgentError
from src.common.config import DEFAULT_CONFIG_PATH
from src.common.search_errors import SearchError
from src.core.agent import AskErrorResponse, AskRequest
from src.core.search import SearchFilters


async def run() -> int:
    """Print exactly the public response/error body to stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--as-of-date", type=date.fromisoformat)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--document-id")
    parser.add_argument("--document-type")
    parser.add_argument("--issued-year", type=int)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = parser.parse_args()
    identifier = uuid4()
    try:
        try:
            request = AskRequest(
                question=args.question,
                as_of_date=args.as_of_date,
                top_k=args.top_k,
                filters=SearchFilters(
                    document_id=args.document_id,
                    document_type=args.document_type,
                    issued_year=args.issued_year,
                ),
            )
        except ValidationError:
            raise AgentError("INVALID_REQUEST", "Câu hỏi hoặc bộ lọc không hợp lệ.") from None
        async with open_ask_service(args.config_path, args.device) as service:
            response = await service.ask(request, identifier)
            print(response.model_dump_json(indent=2))
        return 0
    except (AgentError, SearchError) as exc:
        print(
            AskErrorResponse(
                request_id=identifier, code=exc.code, message=exc.message
            ).model_dump_json(indent=2)
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
