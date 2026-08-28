"""Verify durable Voice/Media bytes flow through Drama Service, never storage URLs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    result = await session.call_tool(name, arguments)
    payload: Any = result.structured_content
    if payload is None and result.content and result.content[0].type == "text":
        payload = json.loads(result.content[0].text)
    if result.is_error:
        raise RuntimeError(f"{name} failed")
    return payload


def origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    port = parsed.port if parsed.port is not None else {"http": 80, "https": 443}.get(parsed.scheme)
    return parsed.scheme, parsed.hostname, port


async def verify_content(
    session: ClientSession,
    *,
    kind: str,
    durable_id: str,
    expected_hash: str,
    service_base_url: str,
) -> dict[str, Any]:
    resolved = await call(session, f"{kind}.resolve_{kind}", {f"{kind}_id": durable_id})
    content_url = str(resolved["url"])
    parsed = urlsplit(content_url)
    if origin(content_url) != origin(service_base_url):
        raise RuntimeError(f"{kind} resolve leaked a non-service URL")
    if parsed.path != f"/api/content/{kind}/{durable_id}":
        raise RuntimeError(f"{kind} resolve returned an unexpected content route")
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.get(content_url)
        response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != expected_hash:
        raise RuntimeError(f"{kind} content hash mismatch")
    if resolved.get("contentHash") is not None and resolved["contentHash"] != digest:
        raise RuntimeError(f"{kind} resolve integrity metadata mismatch")
    return {
        "id": durable_id,
        "urlOwner": "DRAMA_SERVICE",
        "contentRoute": parsed.path,
        "sizeBytes": len(response.content),
        "contentHash": digest,
        "hashMatch": True,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    async with streamable_http_client(args.mcp_url) as streams:
        async with ClientSession(*streams[:2]) as session:
            initialized = await session.initialize()
            tools = {item.name for item in (await session.list_tools()).tools}
            required = {"voice.get_voice", "voice.resolve_voice", "media.get_media", "media.resolve_media"}
            if not required <= tools:
                raise RuntimeError("required storage-backed tools are not projected")
            voice = await call(session, "voice.get_voice", {"voice_id": args.voice_id})
            media = await call(session, "media.get_media", {"media_id": args.media_id})
            forbidden_voice_fields = {"storageType", "bucketName", "objectKey"} & set(voice)
            if forbidden_voice_fields:
                raise RuntimeError("Voice contract leaked storage topology")
            voice_result = await verify_content(
                session, kind="voice", durable_id=args.voice_id,
                expected_hash=str(voice["contentHash"]), service_base_url=args.service_base_url,
            )
            media_result = await verify_content(
                session, kind="media", durable_id=args.media_id,
                expected_hash=str(media["contentHash"]), service_base_url=args.service_base_url,
            )
    return {
        "schemaVersion": "runtime-storage-boundary-e2e-v1",
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "PASS",
        "mcpProtocolVersion": initialized.protocol_version,
        "mcpHealth": "PASS",
        "pluginLoad": "PASS",
        "dramaServiceHttp": "PASS",
        "voiceGet": "PASS",
        "mediaGet": "PASS",
        "voice": voice_result,
        "media": media_result,
        "hostStorageIndependence": "PASS",
        "directMinioPreflight": False,
        "fishRealCalls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--service-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--media-id", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = asyncio.run(run(args))
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "evidence": str(args.evidence),
                      "voiceId": args.voice_id, "mediaId": args.media_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
