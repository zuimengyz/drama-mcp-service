"""Explicit Batch 03 standard MCP client -> Plugin -> Java -> MySQL E2E.

Prerequisites: Drama Service and Drama MCP Service are running, and the MCP
server process owns the Plugin provider configuration and credentials.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


MCP_URL = os.environ.get("DRAMA_MCP_URL", "http://127.0.0.1:8765/mcp")
MEMORY_DOMAINS = {"work", "script", "episode", "scene", "shot", "asset", "media"}


async def run() -> None:
    suffix = uuid.uuid4().hex[:10]
    prefix = f"E2E_B03_{suffix}"
    source_ref = f"E2E_B03:{suffix}:standard-face"
    memory_smoke: set[str] = set()
    other_smoke: set[str] = set()

    async with streamable_http_client(MCP_URL) as streams:
        async with ClientSession(*streams[:2]) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            if len(tools) < 42:
                raise AssertionError(f"MCP discovery expected at least the Batch 03 baseline, got {len(tools)}")

            async def call(code: str, arguments: dict[str, Any] | None = None, *, smoke: bool = True) -> Any:
                result = await session.call_tool(code, arguments or {})
                if result.is_error:
                    raise AssertionError(f"{code} failed: {result.structured_content}")
                if smoke:
                    (memory_smoke if code.split(".", 1)[0] in MEMORY_DOMAINS else other_smoke).add(code)
                if result.structured_content is not None:
                    return result.structured_content
                if not result.content or result.content[0].type != "text":
                    raise AssertionError(f"{code} returned no JSON result")
                return json.loads(result.content[0].text)

            work = await call("work.create_work", {"title": f"{prefix}_神龙政变", "description": f"{prefix}_候选", "content": {"theme": prefix}})
            script = await call("script.create_script", {"work_id": work["id"], "title": f"{prefix}_剧本", "content": {"format": "short"}})
            episode = await call("episode.create_episode", {"script_id": script["id"], "episode_no": 1, "title": f"{prefix}_第一集", "content": {"arc": prefix}})
            scene = await call("scene.create_scene", {"episode_id": episode["id"], "order": 3, "title": f"{prefix}_张柬之书房", "location": f"{prefix}_洛阳", "content": {"character": "张柬之"}})
            shot = await call("shot.create_shot", {"scene_id": scene["id"], "shot_no": "3A", "title": f"{prefix}_密议特写", "shot_type": "CU", "content": {"character": "张柬之"}})

            assert (await call("work.get_work", {"work_id": work["id"]}))["id"] == work["id"]
            assert (await call("script.get_script", {"script_id": script["id"]}))["workId"] == work["id"]
            assert (await call("episode.get_episode", {"episode_id": episode["id"]}))["scriptId"] == script["id"]
            before_scene = await call("scene.get_scene", {"scene_id": scene["id"]})
            assert (await call("shot.get_shot", {"shot_id": shot["id"]}))["sceneId"] == scene["id"]
            assert work["id"] in [item["id"] for item in await call("work.list_works")]
            assert work["id"] in [item["id"] for item in await call("work.search_works", {"query": prefix})]
            assert script["id"] in [item["id"] for item in await call("script.list_scripts", {"work_id": work["id"]})]
            assert episode["id"] in [item["id"] for item in await call("episode.list_episodes", {"script_id": script["id"], "episode_no": 1, "title": "第一集"})]
            assert scene["id"] in [item["id"] for item in await call("scene.list_scenes", {"episode_id": episode["id"], "order": 3, "location": "洛阳", "character": "张柬之"})]
            assert scene["id"] in [item["id"] for item in await call("scene.search_scenes", {"query": "书房", "episode_id": episode["id"]})]
            assert shot["id"] in [item["id"] for item in await call("shot.list_shots", {"scene_id": scene["id"], "shot_no": "3A", "shot_type": "CU", "character": "张柬之"})]
            assert shot["id"] in [item["id"] for item in await call("shot.search_shots", {"query": "密议", "scene_id": scene["id"]})]

            work = await call("work.save_work", {"work_id": work["id"], "title": f"{prefix}_神龙政变修订", "description": f"{prefix}_候选", "content": {"revision": 2}})
            script = await call("script.save_script", {"script_id": script["id"], "title": f"{prefix}_剧本修订", "content": {"revision": 2}})
            episode = await call("episode.save_episode", {"episode_id": episode["id"], "episode_no": 1, "title": f"{prefix}_第一集修订", "content": {"revision": 2}})
            scene = await call("scene.save_scene", {"scene_id": scene["id"], "order": 3, "title": f"{prefix}_书房修订", "location": f"{prefix}_洛阳", "content": {"revision": 2}})
            shot = await call("shot.save_shot", {"shot_id": shot["id"], "shot_no": "3A", "title": f"{prefix}_密议修订", "shot_type": "CU", "content": {"revision": 2}})
            assert scene["episodeId"] == before_scene["episodeId"]

            a1 = await call("asset.create_asset", {"work_id": work["id"], "asset_type": "STANDARD_FACE", "name": f"{prefix}_A1标准脸", "content": {"identity": prefix}})
            m1 = await call("media.create_media", {"work_id": work["id"], "asset_id": a1["id"], "media_type": "IMAGE", "source_ref": source_ref, "content": {"kind": "face"}})
            a2 = await call("asset.create_asset", {"work_id": work["id"], "asset_type": "MASTER_CHARACTER_CARD", "name": f"{prefix}_A2人物卡", "reference_media_ids": [m1["id"]], "content": {"identity": prefix}})
            assert (await call("asset.get_asset", {"asset_id": a2["id"]}))["referenceMediaIds"] == [m1["id"]]
            assert (await call("media.get_media", {"media_id": m1["id"]}))["assetId"] == a1["id"] != a2["id"]
            assert a2["id"] in [item["id"] for item in await call("asset.list_assets", {"asset_type": "MASTER_CHARACTER_CARD"})]
            assert a2["id"] in [item["id"] for item in await call("asset.search_assets", {"query": prefix, "asset_type": "MASTER_CHARACTER_CARD"})]
            assert m1["id"] in [item["id"] for item in await call("media.list_media", {"media_type": "IMAGE"})]
            a2 = await call("asset.save_asset", {"asset_id": a2["id"], "name": f"{prefix}_A2人物卡修订", "reference_media_ids": [m1["id"]], "content": {"revision": 2}})
            m1 = await call("media.save_media", {"media_id": m1["id"], "purpose": "REFERENCE", "content": {"revision": 2}})
            retry = await call("media.create_media", {"work_id": work["id"], "asset_id": a1["id"], "media_type": "IMAGE", "source_ref": source_ref, "content": {"ignored": True}})
            assert retry["id"] == m1["id"]

            missing = await session.call_tool("scene.save_scene", {"scene_id": f"scene_missing_{suffix}", "order": 1, "title": "missing", "content": {}})
            assert missing.is_error and missing.structured_content["error"]["code"] == "NOT_FOUND"
            other = await call("work.create_work", {"title": f"{prefix}_冲突作品", "content": {"test": True}})
            conflict = await session.call_tool("media.create_media", {"work_id": other["id"], "media_type": "IMAGE", "source_ref": source_ref, "content": {}})
            assert conflict.is_error and conflict.structured_content["error"]["code"] == "CONFLICT"
            invalid = await session.call_tool("scene.create_scene", {})
            unknown = await session.call_tool("unknown.tool", {})
            assert invalid.is_error and invalid.structured_content["error"]["code"] == "INVALID_ARGUMENT"
            assert unknown.is_error and unknown.structured_content["error"]["code"] == "NOT_FOUND"

            for code in sorted(name for name in tools if name.startswith("research.")):
                await call(code, {"claim": prefix} if code == "research.verify_claim" else {"query": prefix})
            await call("production.generate_image", {"prompt": prefix})
            await call("production.generate_video", {"prompt": prefix})
            await call("production.generate_audio", {"prompt": prefix})
            context = await call("context.build_context", {"request": {"scope": "SHOT", "resourceId": shot["id"], "purpose": "SHOT_DESIGN", "options": {}}})
            patch = await call("context.refresh_context", {"request": {"scope": "SHOT", "resourceId": shot["id"], "purpose": "SHOT_DESIGN", "options": {}}, "current": context})
            assert patch["contextId"] == context["contextId"] and patch["newVersion"] == 2

            storage_tools = {"media.import_media", "media.resolve_media", "media.restore_media_object"}
            expected_memory = {name for name in tools if name.split(".", 1)[0] in MEMORY_DOMAINS} - storage_tools
            expected_other = set(tools) - expected_memory - storage_tools
            assert memory_smoke == expected_memory
            assert other_smoke == expected_other

            print(json.dumps({
                "result": "PASS",
                "protocol": {"server": initialized.server_info.name, "toolCount": len(tools)},
                "prefix": prefix,
                "memoryToolCalls": len(memory_smoke),
                "otherToolCalls": len(other_smoke),
                "ids": {"workId": work["id"], "scriptId": script["id"], "episodeId": episode["id"], "sceneId": scene["id"], "shotId": shot["id"], "assetA1Id": a1["id"], "mediaM1Id": m1["id"], "assetA2Id": a2["id"]},
                "referenceIndependence": {"assetA2ReferenceMediaIds": a2["referenceMediaIds"], "mediaM1AssetId": m1["assetId"]},
                "sourceRefIdempotentMediaId": retry["id"],
                "errors": ["NOT_FOUND", "CONFLICT", "INVALID_ARGUMENT", "UNKNOWN_TOOL"],
                "secrets": "REDACTED",
            }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
