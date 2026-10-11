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
async def plugin_lifespan(server: Server[PluginToolAdapter], *, settings: Settings, runtime: dict[str, Any]) -> AsyncIterator[PluginToolAdapter]:
    del server
    async with DramaPlugin.load(settings.plugin_root, settings.plugin_config) as plugin:
        from drama_mcp_service.runtime_identity import capture_identity
        runtime.update(status="ready", identity=capture_identity(settings, plugin))
        adapter = PluginToolAdapter(plugin)
        archive = None
        worker = None
        try:
            import os
            if os.environ.get("DRAMA_MCP_ARCHIVE_ENABLED", "false") == "true":
                import asyncio
                import base64
                from pathlib import Path
                from drama_plugin.long_memory.client import DramaArchiveClient  # type: ignore[import-untyped]
                from drama_mcp_service.archive_adapter import ArchiveMemoryAdapter
                from drama_mcp_service.archive_sync import ArchiveOutbox
                try:
                    archive = DramaArchiveClient.from_environment(os.environ)
                    if archive is not None:
                        outbox = ArchiveOutbox(Path(os.environ["DRAMA_MCP_ARCHIVE_OUTBOX"]), base64.b64decode(os.environ["DRAMA_MCP_ARCHIVE_KEY"]))
                        raw_root = plugin.config.production_ledger_path
                        root = Path(raw_root).parent if raw_root else None
                        # An archive observer may read a retained Ledger without configuring
                        # that Ledger as the active Plugin production owner.
                        source_root = os.environ.get("DRAMA_MCP_ARCHIVE_SOURCE_ROOT")
                        if source_root:
                            root = Path(source_root)
                            if not root.is_absolute() or not (root / "production.sqlite3").is_file():
                                raise ValueError("Archive source requires an existing absolute Ledger directory")
                        wrapper = ArchiveMemoryAdapter(adapter, archive, outbox, ledger_root=root)
                        await wrapper.discover()
                        worker = asyncio.create_task(wrapper.synchronize())
                        adapter = wrapper  # type: ignore[assignment]
                        runtime["archive"] = "OPTIONAL_SOURCE_MIRROR"
                except Exception:
                    runtime["archive"] = "PENDING_CONFIGURATION"
            yield adapter
        finally:
            if worker is not None:
                import asyncio
                worker.cancel()
                try: await worker
                except asyncio.CancelledError: pass
            if archive is not None:
                await archive.aclose()
            runtime["status"] = "stopped"


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
    runtime = request.app.state.runtime_identity
    return JSONResponse({"service": "drama-mcp-service", **runtime},
                        status_code=200 if runtime["status"] == "ready" else 503)


def create_server(settings: Settings | None = None, *, runtime: dict[str, Any] | None = None) -> Server[PluginToolAdapter]:
    from functools import partial
    resolved = settings or Settings.from_environment()
    return Server(
        "drama-mcp-service",
        version="0.1.0",
        title="Drama MCP Service",
        description="MCP host adapter for the Drama Plugin tool registry.",
        lifespan=partial(plugin_lifespan, settings=resolved, runtime=runtime if runtime is not None else {"status": "starting"}),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def create_app(settings: Settings | None = None) -> Any:
    resolved = settings or Settings.from_environment()
    runtime: dict[str, Any] = {"status": "starting"}
    app = create_server(resolved, runtime=runtime).streamable_http_app(
        streamable_http_path="/mcp",
        host=resolved.host,
        max_request_body_size=resolved.max_request_bytes,
        custom_starlette_routes=[Route("/health", health, methods=["GET"])],
    )
    app.state.runtime_identity = runtime
    return app


def main() -> None:
    settings = Settings.from_environment()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
