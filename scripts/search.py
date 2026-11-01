#!/usr/bin/env python3
"""Run SearchService directly; the teammate's HTTP adapter uses the same contracts."""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from src.common.config import DEFAULT_CONFIG_PATH
from src.common.search_errors import InvalidSearchRequest, SearchError
from src.core.search import SearchErrorResponse, SearchFilters, SearchRequest
from src.retrieval.search_factory import open_search_service


async def run() -> int:
    """Print exactly the public response/error body to stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--document-id")
    parser.add_argument("--document-type")
    parser.add_argument("--issued-year", type=int)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda"], default="auto")
    args = parser.parse_args()
    identifier = uuid4()
    try:
        try:
            request = SearchRequest(
                query=args.query,
                top_k=args.top_k,
                filters=SearchFilters(
                    document_id=args.document_id,
                    document_type=args.document_type,
                    issued_year=args.issued_year,
                ),
            )
        except ValidationError:
            raise InvalidSearchRequest() from None
        async with open_search_service(args.config_path, args.device) as service:
            response = await service.search(request, identifier)
            print(response.model_dump_json(indent=2))
        return 0
    except SearchError as exc:
        print(
            SearchErrorResponse(
                request_id=identifier, code=exc.code, message=exc.message
            ).model_dump_json(indent=2)
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
