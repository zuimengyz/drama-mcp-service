from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from drama_plugin.long_memory.client import DramaArchiveClient, ArchiveUnavailable  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from mcp import types

from drama_mcp_service.adapter import PluginToolAdapter
from drama_mcp_service.archive_sync import ArchiveOutbox, ledger_records, ledger_work_ids, unscoped_creative_records, tool_event


def work_scope(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("workId", "work_id"):
            if isinstance(value.get(key), str): return str(value[key])
        for child in value.values():
            found = work_scope(child)
            if found: return found
    elif isinstance(value, list):
        for child in value:
            found = work_scope(child)
            if found: return found
    return None


class ArchiveMemoryAdapter:
    """Service-owned memory tools plus passive observation of unchanged Plugin calls."""
    def __init__(self, plugin: PluginToolAdapter, client: DramaArchiveClient, outbox: ArchiveOutbox,
                 *, ledger_root: Path | None = None) -> None:
        self.plugin, self.client, self.outbox, self.ledger_root = plugin, client, outbox, ledger_root
        self.memory_tools: list[dict[str, Any]] = []
        self.discovered = False
        self.pending_error: str | None = None

    async def discover(self) -> None:
        try:
            tools = await self.client.tools()
            for tool in tools:
                if not tool["name"].startswith("memory.") or not tool["path"].startswith("/api/tool/archive/") or tool["method"] != "GET":
                    raise ArchiveUnavailable("Incompatible archive tool discovery")
                Draft202012Validator.check_schema(tool["inputSchema"])
            self.memory_tools, self.discovered = tools, True
        except Exception:
            self.pending_error = "ARCHIVE_DISCOVERY_PENDING"

    def list_tools(self) -> list[types.Tool]:
        return self.plugin.list_tools() + [types.Tool(name=t["name"], description=t["description"], input_schema=t["inputSchema"]) for t in self.memory_tools]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        selected = next((t for t in self.memory_tools if t["name"] == name), None)
        if selected:
            try:
                Draft202012Validator(selected["inputSchema"]).validate(arguments or {})
                result = await self.client.request(selected["path"], params=arguments or {})
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))], structured_content=result)
            except Exception:
                return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text="Archive query unavailable; production state is unchanged")])
        call_id = uuid.uuid4().hex
        scope = work_scope(arguments)
        try:
            self.outbox.enqueue([tool_event(name, arguments, None, work_id=scope or "__unscoped__", phase="REQUEST", call_id=call_id)])
        except Exception:
            self.pending_error = "ARCHIVE_LOCAL_BUFFER_PENDING"
        # This exact Plugin result is returned even when archive writes or synchronization fail.
        result = await self.plugin.call_tool(name, arguments)
        try:
            serialized = result.model_dump(mode="json", by_alias=True)
            resolved_scope = scope or work_scope(serialized)
            self.outbox.enqueue([tool_event(name, arguments, serialized, work_id=resolved_scope or "__unscoped__", phase="RESPONSE", call_id=call_id)])
            if self.ledger_root and resolved_scope:
                self.outbox.enqueue(list(ledger_records(self.ledger_root, resolved_scope)))
        except Exception:
            self.pending_error = "ARCHIVE_SOURCE_CAPTURE_PENDING"
        return result

    async def synchronize(self, interval: float = 10) -> None:
        while True:
            if not self.discovered:
                await self.discover()
            try:
                # Includes durable originals produced by direct CLI calls, not only MCP traffic.
                # Mutable revisions overwritten between snapshots remain an explicit coverage gap.
                if self.ledger_root:
                    root = self.ledger_root
                    for work in ledger_work_ids(root):
                        records = await asyncio.to_thread(lambda: list(ledger_records(root, work)))
                        await asyncio.to_thread(self.outbox.enqueue, records)
                    unscoped = await asyncio.to_thread(lambda: list(unscoped_creative_records(root)))
                    await asyncio.to_thread(self.outbox.enqueue, unscoped)
                result = await self.outbox.drain(self.client)
                self.pending_error = None if result["serviceAvailable"] else "ARCHIVE_SYNC_PENDING"
            except Exception:
                self.pending_error = "ARCHIVE_SYNC_PENDING"
            await asyncio.sleep(interval)
