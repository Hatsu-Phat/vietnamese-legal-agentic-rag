"""Run the MCP server over stdio (P5/T-0501).

    MCP_API_KEY=<one of AUTH_API_KEY_*> uv run --locked python scripts/mcp_server.py

The role is fixed by MCP_API_KEY for the whole process. Logs go to stderr only: stdout
carries the MCP protocol. Startup loads the search release (~2 minutes on CPU) before the
server answers the client's initialize request.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.dependencies import AccessResolver
from src.api.main import open_services
from src.common.config import DEFAULT_CONFIG_PATH, get_settings, load_yaml_config
from src.common.logger import configure_logging, logger
from src.core.access import AccessPolicy
from src.mcp_server.server import McpUnauthorized, build_mcp_server, resolve_role


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_format, settings.log_level)
    credential = settings.mcp_api_key.get_secret_value() if settings.mcp_api_key else None
    # Check the credential before the slow model/release load; same key table as the API.
    policy = AccessPolicy.from_config(load_yaml_config(DEFAULT_CONFIG_PATH))
    try:
        role, scope = resolve_role(AccessResolver.from_settings(policy, settings), credential)
    except McpUnauthorized as exc:
        logger.error("MCP server refused to start: {}", exc)
        return 2
    async with open_services() as services:
        await build_mcp_server(services, role, scope).run_stdio_async()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
