from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from drama_plugin import DramaPlugin  # type: ignore[import-untyped]
from mcp import types
from mcp.server import Server, ServerRequestContext
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from drama_mcp_service.adapter import PluginToolAdapter
from drama_mcp_service.settings import Settings


@asynccontextmanager
async def plugin_lifespan(server: Server[PluginToolAdapter]) -> AsyncIterator[PluginToolAdapter]:
    del server
    settings = Settings.from_environment()
    async with DramaPlugin.load(settings.plugin_root, settings.plugin_config) as plugin:
        yield PluginToolAdapter(plugin)


async def list_tools(
    context: ServerRequestContext[PluginToolAdapter],
    params: types.PaginatedRequestParams | None,
) -> types.ListToolsResult:
    del params
    return types.ListToolsResult(tools=context.lifespan_context.list_tools())


async def call_tool(
    context: ServerRequestContext[PluginToolAdapter],
    params: types.CallToolRequestParams,
) -> types.CallToolResult:
    return await context.lifespan_context.call_tool(params.name, params.arguments)


async def health(request: Request) -> JSONResponse:
    del request
    return JSONResponse({"status": "ok", "service": "drama-mcp-service"})


def create_server() -> Server[PluginToolAdapter]:
    return Server(
        "drama-mcp-service",
        version="0.1.0",
        title="Drama MCP Service",
        description="MCP host adapter for the Drama Plugin tool registry.",
        lifespan=plugin_lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def create_app(settings: Settings | None = None) -> Any:
    resolved = settings or Settings.from_environment()
    return create_server().streamable_http_app(
        streamable_http_path="/mcp",
        host=resolved.host,
        custom_starlette_routes=[Route("/health", health, methods=["GET"])],
    )


def main() -> None:
    settings = Settings.from_environment()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
