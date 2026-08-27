from __future__ import annotations

import inspect
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any, get_type_hints

from drama_plugin import DramaPlugin  # type: ignore[import-untyped]
from drama_plugin.exceptions import (  # type: ignore[import-untyped]
    ConfigurationError,
    ContextBuildError,
    ContractValidationError,
    DramaPluginError,
    ProviderError,
    ProviderResultUnknown,
    RemoteServiceError,
    SpeechProviderError,
    ToolNotFoundError,
)
from drama_plugin.tools import ToolDefinition  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]
from mcp import types
from pydantic import BaseModel, TypeAdapter


def to_json_value(value: Any) -> Any:
    """Recursively convert Plugin results without knowing domain DTOs."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set):
        return [to_json_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError("Plugin returned a value that is not JSON serializable")


class PluginToolAdapter:
    """Generic MCP projection over a Plugin registry; no domain dispatch lives here."""

    def __init__(self, plugin: DramaPlugin) -> None:
        self.plugin = plugin

    def list_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=definition.code,
                description=definition.description,
                input_schema=definition.input_schema,
                # MCP 2.0 limits outputSchema to an object root. Plugin list
                # contracts remain authoritative and are returned as structured
                # arrays; only the unsupported MCP declaration is omitted.
                output_schema=(
                    definition.output_schema
                    if definition.output_schema.get("type") == "object"
                    else None
                ),
            )
            for definition in self.plugin.tools.list()
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        try:
            definition = self.plugin.tools.get(name)
            raw_arguments = arguments or {}
            self._validate(definition, raw_arguments)
            typed_arguments = self._coerce(definition, raw_arguments)
            result = await self.plugin.tools.invoke(name, **typed_arguments)
            public = to_json_value(result)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(public, ensure_ascii=False))],
                structured_content=public if isinstance(public, dict) else None,
            )
        except ToolNotFoundError:
            return self._error("NOT_FOUND", "Unknown MCP tool")
        except (ValidationError, SchemaError, TypeError, ValueError):
            return self._error("INVALID_ARGUMENT", "Tool arguments do not match the Plugin contract")
        except RemoteServiceError as exc:
            return self._error(exc.error_code or "PROVIDER_ERROR", self._remote_message(exc))
        except ConfigurationError:
            return self._error("CONFIGURATION_ERROR", "Plugin provider configuration is invalid")
        except ContractValidationError:
            return self._error("INVALID_ARGUMENT", "Plugin contract validation failed")
        except ContextBuildError:
            return self._error("CONTEXT_ERROR", "Plugin context construction failed")
        except ProviderResultUnknown:
            return self._error(
                "AMBIGUOUS_RESULT",
                "Provider submission may have succeeded; paid retry is unsafe",
            )
        except SpeechProviderError as exc:
            if (
                exc.status_code is not None and 400 <= exc.status_code <= 499
            ) or exc.rejection_reason is not None:
                return self._error(
                    "PROVIDER_REJECTED",
                    "Speech provider rejected the request",
                    details=self._speech_rejection_details(exc),
                )
            if exc.retryable:
                return self._error(
                    "TRANSIENT_RETRY_EXHAUSTED",
                    "Speech provider exhausted its safe transient retry policy",
                )
            return self._error("PROVIDER_ERROR", "Speech provider operation failed")
        except ProviderError as exc:
            return self._error(getattr(exc, "error_code", "PROVIDER_ERROR"), "Plugin provider operation failed")
        except DramaPluginError:
            return self._error("PLUGIN_ERROR", "Plugin operation failed")
        except Exception:
            return self._error("INTERNAL_ERROR", "Internal MCP adapter error")

    @staticmethod
    def _validate(definition: ToolDefinition, arguments: dict[str, Any]) -> None:
        Draft202012Validator.check_schema(definition.input_schema)
        Draft202012Validator(definition.input_schema).validate(arguments)

    @staticmethod
    def _coerce(definition: ToolDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        signature = inspect.signature(definition.handler)
        hints = get_type_hints(definition.handler)
        result: dict[str, Any] = {}
        for name, value in arguments.items():
            parameter = signature.parameters.get(name)
            if parameter is None:
                raise TypeError("Unexpected argument")
            annotation = hints.get(name, parameter.annotation)
            result[name] = value if annotation is inspect.Parameter.empty else TypeAdapter(annotation).validate_python(value)
        return result

    @staticmethod
    def _remote_message(exc: RemoteServiceError) -> str:
        if exc.error_code in {"INVALID_ARGUMENT", "UNAUTHORIZED", "NOT_FOUND", "CONFLICT"}:
            return f"Downstream provider returned {exc.error_code}"
        return "Downstream provider operation failed"

    @staticmethod
    def _speech_rejection_details(exc: SpeechProviderError) -> dict[str, Any]:
        details: dict[str, Any] = {
            "rejectionReason": exc.rejection_reason or "UNKNOWN_REJECTION"
        }
        if exc.status_code is not None:
            details["httpStatus"] = exc.status_code
        provider_error_code = PluginToolAdapter._safe_identifier(
            exc.provider_error_code
        )
        provider_request_id = PluginToolAdapter._safe_identifier(
            exc.provider_request_id
        )
        provider_error_message = PluginToolAdapter._safe_message(
            exc.provider_error_message
        )
        if provider_error_code:
            details["providerErrorCode"] = provider_error_code
        if provider_error_message:
            details["providerErrorMessage"] = provider_error_message
        if provider_request_id:
            details["providerRequestId"] = provider_request_id
        return details

    @staticmethod
    def _safe_identifier(value: str | None) -> str | None:
        if value is None or len(value) > 200:
            return None
        return value if re.fullmatch(r"[A-Za-z0-9_.:-]+", value) else None

    @staticmethod
    def _safe_message(value: str | None) -> str | None:
        if value is None:
            return None
        rendered = " ".join(value.replace("\x00", " ").split())
        rendered = re.sub(
            r"https?://[^\s\"'<>]+", "[REDACTED_URL]", rendered, flags=re.I
        )
        rendered = re.sub(
            r"\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", rendered, flags=re.I
        )
        rendered = re.sub(
            r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED_API_KEY]", rendered
        )
        rendered = re.sub(
            r"(?i)\b(authorization|api[_ -]?key|access[_ -]?token|cookie|credential)"
            r"\s*[:=]\s*([^\s,;]+)",
            lambda match: f"{match.group(1)}=[REDACTED]",
            rendered,
        )
        return rendered[:500] or None

    @staticmethod
    def _error(
        code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> types.CallToolResult:
        error: dict[str, Any] = {"code": code, "message": message}
        if details:
            error.update(details)
        payload = {"error": error}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
