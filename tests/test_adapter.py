from __future__ import annotations

from pathlib import Path

import pytest
from drama_plugin import DramaPlugin
from drama_plugin.exceptions import ProviderResultUnknown, RoleDubbingError, SpeechProviderError

from drama_mcp_service.adapter import PluginToolAdapter, to_json_value


PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "drama-plugin" / "plugin"


@pytest.fixture
async def adapter() -> PluginToolAdapter:
    async with DramaPlugin.load(PLUGIN_ROOT) as plugin:
        yield PluginToolAdapter(plugin)


def test_tool_projection_is_dynamic_and_lossless(adapter: PluginToolAdapter) -> None:
    plugin_tools = adapter.plugin.tools.list()
    mcp_tools = adapter.list_tools()
    assert len(plugin_tools) == len(mcp_tools)
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


def test_voice_and_role_dubbing_tools_are_automatically_projected(adapter: PluginToolAdapter) -> None:
    projected = {tool.name: tool for tool in adapter.list_tools()}
    for code in ("voice.import_voice", "voice.get_voice", "voice.save_voice",
                 "voice.resolve_voice", "production.generate_role_dubbing"):
        source = adapter.plugin.tools.get(code)
        assert projected[code].description == source.description
        assert projected[code].input_schema == source.input_schema


@pytest.mark.parametrize("code", ["VOICE_CASTING_FAILED", "INTELLIGIBILITY_QC_FAILED",
                                   "VOICE_ARTISTIC_REVIEW_REQUIRED",
                                   "VOICE_ARTISTIC_APPROVAL_INVALID",
                                  "VOICE_NOT_FOUND", "VOICE_REFERENCE_UNAVAILABLE"])
async def test_role_dubbing_errors_remain_high_level_and_provider_neutral(
    adapter: PluginToolAdapter, monkeypatch: pytest.MonkeyPatch, code: str,
) -> None:
    async def fail(*args: object, **kwargs: object) -> object:
        raise RoleDubbingError(code, "private provider detail")

    monkeypatch.setattr(adapter.plugin.tools, "invoke", fail)
    result = await adapter.call_tool("work.get_work", {"work_id": "work-1"})
    assert result.is_error is True
    assert result.structured_content["error"] == {
        "code": code,
        "message": "Role dubbing operation failed safely",
    }
    assert "private provider detail" not in result.content[0].text  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("raised", "code"),
    [
        (
            ProviderResultUnknown("result cannot be confirmed"),
            "AMBIGUOUS_RESULT",
        ),
        (
            SpeechProviderError("request rejected", status_code=400),
            "PROVIDER_REJECTED",
        ),
        (
            SpeechProviderError("safe retries exhausted", retryable=True),
            "TRANSIENT_RETRY_EXHAUSTED",
        ),
    ],
)
async def test_speech_errors_preserve_paid_retry_safety(
    adapter: PluginToolAdapter,
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    code: str,
) -> None:
    async def fail(*args: object, **kwargs: object) -> object:
        raise raised

    monkeypatch.setattr(adapter.plugin.tools, "invoke", fail)
    result = await adapter.call_tool("work.get_work", {"work_id": "work-1"})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == code
    assert str(raised) not in result.content[0].text  # type: ignore[union-attr]


async def test_provider_rejection_propagates_only_safe_diagnostics(
    adapter: PluginToolAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raised = SpeechProviderError(
        "raw exception must remain private",
        status_code=400,
        provider_error_code="InvalidParameter",
        provider_error_message=(
            "input length invalid; Authorization: Bearer hidden-secret; "
            "api_key=sk-hidden-secret; url=https://signed.invalid/x?token=hidden"
        ),
        provider_request_id="request-safe-123",
        rejection_reason="INVALID_REQUEST",
    )

    async def fail(*args: object, **kwargs: object) -> object:
        raise raised

    monkeypatch.setattr(adapter.plugin.tools, "invoke", fail)
    result = await adapter.call_tool("work.get_work", {"work_id": "work-1"})

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error == {
        "code": "PROVIDER_REJECTED",
        "message": "Speech provider rejected the request",
        "rejectionReason": "INVALID_REQUEST",
        "httpStatus": 400,
        "providerErrorCode": "InvalidParameter",
        "providerErrorMessage": (
            "input length invalid; Authorization=[REDACTED] [REDACTED]; "
            "api_key=[REDACTED]; url=[REDACTED_URL]"
        ),
        "providerRequestId": "request-safe-123",
    }
    serialized = result.content[0].text  # type: ignore[union-attr]
    assert "hidden-secret" not in serialized
    assert "signed.invalid" not in serialized
    assert "raw exception must remain private" not in serialized
