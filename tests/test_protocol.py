from __future__ import annotations

from pathlib import Path

from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from drama_mcp_service.server import create_server


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT.parent / "drama-plugin" / "plugin"


def force_mock_provider_modes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for service in ("MEMORY", "ASSET", "RESEARCH", "PRODUCTION", "MEDIA"):
        monkeypatch.setenv(f"DRAMA_PLUGIN_PROVIDER_{service}_MODE", "mock")
    monkeypatch.setenv("DRAMA_PLUGIN_PROVIDER_CONTEXT_MODE", "local")


async def test_standard_protocol_initialize_list_and_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    force_mock_provider_modes(monkeypatch)
    monkeypatch.setenv("DRAMA_PLUGIN_ROOT", str(PLUGIN_ROOT))
    monkeypatch.delenv("DRAMA_PLUGIN_CONFIG", raising=False)
    async with InMemoryTransport(create_server()) as streams:
        async with ClientSession(*streams[:2]) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "drama-mcp-service"
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            assert "scene.create_scene" in names
            assert {
                "media.import_media",
                "media.resolve_media",
                "media.restore_media_object",
            } <= names
            result = await session.call_tool("work.get_work", {"work_id": "work-1"})
            assert result.is_error is False
            assert result.structured_content["id"] == "work-1"
            imported = await session.call_tool("media.import_media", {"work_id":"work-1","media_type":"IMAGE","source_uri":"file:///not-read-by-mock.png","content":{}})
            assert imported.is_error is False
            resolved = await session.call_tool("media.resolve_media", {"media_id": imported.structured_content["id"]})
            assert resolved.is_error is False
            assert resolved.structured_content["mediaId"] == imported.structured_content["id"]


async def test_standard_protocol_unknown_and_invalid_calls_are_safe(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    force_mock_provider_modes(monkeypatch)
    monkeypatch.setenv("DRAMA_PLUGIN_ROOT", str(PLUGIN_ROOT))
    monkeypatch.delenv("DRAMA_PLUGIN_CONFIG", raising=False)
    async with InMemoryTransport(create_server()) as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            unknown = await session.call_tool("unknown.tool", {})
            invalid = await session.call_tool("scene.create_scene", {})
            assert unknown.is_error is True
            assert unknown.structured_content["error"]["code"] == "NOT_FOUND"
            assert invalid.is_error is True
            assert invalid.structured_content["error"]["code"] == "INVALID_ARGUMENT"
