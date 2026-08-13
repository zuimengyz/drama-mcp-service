from __future__ import annotations

from pathlib import Path

import pytest
from drama_plugin import DramaPlugin

from drama_mcp_service.adapter import PluginToolAdapter, to_json_value


PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "drama-plugin" / "plugin"


@pytest.fixture
async def adapter() -> PluginToolAdapter:
    async with DramaPlugin.load(PLUGIN_ROOT) as plugin:
        yield PluginToolAdapter(plugin)


def test_tool_projection_is_dynamic_and_lossless(adapter: PluginToolAdapter) -> None:
    plugin_tools = adapter.plugin.tools.list()
    mcp_tools = adapter.list_tools()
    assert len(plugin_tools) == len(mcp_tools) == 44
    assert [tool.code for tool in plugin_tools] == [tool.name for tool in mcp_tools]
    for source, projected in zip(plugin_tools, mcp_tools, strict=True):
        assert projected.description == source.description
        assert projected.input_schema == source.input_schema
        expected_output = source.output_schema if source.output_schema.get("type") == "object" else None
        assert projected.output_schema == expected_output
        assert "." in projected.name


async def test_generic_invocation_serializes_structured_content(adapter: PluginToolAdapter) -> None:
    result = await adapter.call_tool("work.get_work", {"work_id": "work-1"})
    assert result.is_error is False
    assert result.structured_content["id"] == "work-1"
    assert result.structured_content == to_json_value(adapter.plugin.providers.memory.data.work)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("unknown.tool", {}, "NOT_FOUND"),
        ("work.get_work", {}, "INVALID_ARGUMENT"),
        ("work.get_work", {"work_id": 123}, "INVALID_ARGUMENT"),
        ("work.get_work", {"work_id": "work-1", "unexpected": True}, "INVALID_ARGUMENT"),
    ],
)
async def test_safe_client_errors(
    adapter: PluginToolAdapter,
    name: str,
    arguments: dict[str, object],
    code: str,
) -> None:
    result = await adapter.call_tool(name, arguments)
    assert result.is_error is True
    assert result.structured_content == {
        "error": {
            "code": code,
            "message": result.structured_content["error"]["message"],
        }
    }
    rendered = result.content[0].text  # type: ignore[union-attr]
    assert "Traceback" not in rendered
    assert "DRAMA_TOOL_SECRET" not in rendered


async def test_nested_contract_arguments_are_coerced_generically(adapter: PluginToolAdapter) -> None:
    result = await adapter.call_tool(
        "context.build_context",
        {
            "request": {
                "scope": "SHOT",
                "resourceId": "shot-1",
                "purpose": "SHOT_DESIGN",
                "options": {},
            }
        },
    )
    assert result.is_error is False
    assert result.structured_content["scope"] == "SHOT"
    assert result.structured_content["shot"]["id"] == "shot-1"


async def test_list_results_preserve_array_shape_in_text(adapter: PluginToolAdapter) -> None:
    result = await adapter.call_tool("work.list_works", {})
    assert result.is_error is False
    assert result.structured_content is None
    assert result.content[0].type == "text"
    assert result.content[0].text.startswith("[")


def test_media_tools_are_automatically_projected(adapter: PluginToolAdapter) -> None:
    projected = {tool.name: tool for tool in adapter.list_tools()}
    for code in ("media.import_media", "media.resolve_media"):
        source = adapter.plugin.tools.get(code)
        assert projected[code].description == source.description
        assert projected[code].input_schema == source.input_schema
