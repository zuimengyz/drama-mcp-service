"""Verify local MCP runtime configuration without exposing credentials."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from drama_plugin.config.loader import load_config
from drama_plugin.exceptions import MediaImportSourceError
from drama_plugin.providers.http.media_source import allowed_media_roots, local_media_path
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from drama_mcp_service.settings import Settings


async def verify_mcp() -> dict[str, Any]:
    async with streamable_http_client("http://127.0.0.1:8765/mcp") as streams:
        async with ClientSession(*streams[:2]) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            result = await session.call_tool("work.list_works", {})
            return {
                "initialize": initialized.server_info.name == "drama-mcp-service",
                "server": initialized.server_info.name,
                "toolCount": len(names),
                "mediaImportDiscovered": "media.import_media" in names,
                "mediaResolveDiscovered": "media.resolve_media" in names,
                "mediaRestoreDiscovered": "media.restore_media_object" in names,
                "javaBearerReadCall": not result.is_error,
            }


def main() -> None:
    settings = Settings.from_environment()
    config = load_config(settings.plugin_config)
    roots = allowed_media_roots()
    try:
        path = local_media_path("file:///D:/home/AI/test.png")
        path_validation = {"passed": True, "path": str(path)}
    except MediaImportSourceError as exc:
        path_validation = {
            "passed": False,
            "errorCode": exc.error_code,
        }
    summary = {
        "providerModes": {
            "memory": config.providers.memory.mode,
            "asset": config.providers.asset.mode,
            "media": config.providers.media.mode,
            "research": config.providers.research.mode,
            "production": config.providers.production.mode,
            "context": config.providers.context.mode,
        },
        "baseUrlsConfigured": {
            "memory": bool(config.services.memory.base_url),
            "asset": bool(config.services.asset.base_url),
            "media": bool(config.services.media.base_url),
        },
        "tokensConfigured": {
            "memory": bool(config.services.memory.api_token),
            "asset": bool(config.services.asset.api_token),
            "media": bool(config.services.media.api_token),
        },
        "allowedRoots": [str(item) for item in roots],
        "pathValidation": path_validation,
        "mcp": asyncio.run(verify_mcp()),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
