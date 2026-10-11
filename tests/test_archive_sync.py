from __future__ import annotations

import hashlib
import asyncio
import json
import sqlite3
import shutil
from pathlib import Path

import httpx
import pytest
from drama_plugin import DramaPlugin
from drama_plugin.long_memory.client import DramaArchiveClient
from drama_mcp_service.adapter import PluginToolAdapter
from drama_mcp_service.archive_adapter import ArchiveMemoryAdapter
from drama_mcp_service.archive_sync import ArchiveOutbox, canonical, record, ledger_records, unscoped_creative_records


async def test_remote_acceptance_then_lost_ack_is_idempotent_after_restart(tmp_path: Path) -> None:
    accepted: dict[str, object] = {}
    calls = 0
    def service(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        records = json.loads(request.content)["records"]
        for value in records: accepted[value["recordId"]] = value
        if calls == 1: raise httpx.ReadTimeout("simulated lost acknowledgement")
        return httpx.Response(200, json={"inserted": 0, "repeated": len(records)})
    client = DramaArchiveClient("https://archive.invalid", "test", transport=httpx.MockTransport(service))
    path = tmp_path / "outbox.sqlite3"
    box = ArchiveOutbox(path, b"1"*32)
    raw = canonical({"prompt": "technical fixture", "url": "https://storage.invalid/path?signature=secret"})
    item = record(raw, work_id="technical-work", source_id="test", record_id="request", kind="PROVIDER_REQUEST")
    assert box.enqueue([item, item]) == 1
    assert (await box.drain(client))["pending"] == 1
    assert b"signature=secret" not in path.read_bytes()
    resumed = ArchiveOutbox(path, b"1"*32)
    assert (await resumed.drain(client))["pending"] == 0
    assert len(accepted) == 1
    await client.aclose()


async def test_periodic_read_only_sync_captures_direct_ledger_writes(tmp_path: Path) -> None:
    from drama_plugin.persistence.ledger import ProductionLedger
    from drama_plugin.runtime.contracts import RuntimeRun, RuntimeScope, RunMode
    received = asyncio.Event()
    def service(request: httpx.Request) -> httpx.Response:
        if request.method == "GET": return httpx.Response(200, json=[])
        records = json.loads(request.content)["records"]
        if any(r.get("runId") == "direct-ledger-run" for r in records): received.set()
        return httpx.Response(200, json={"inserted":len(records),"repeated":0})
    ledger = ProductionLedger(tmp_path/"production.sqlite3")
    ledger.create_run(RuntimeRun(run_id="direct-ledger-run", scope=RuntimeScope(work_id="technical-work"), mode=RunMode.EXPERIMENT,
        policy_id="technical-test", workflow_id="technical-fixture", workflow_fingerprint="0"*64))
    before = ledger.path.read_bytes()
    client = DramaArchiveClient("https://archive.invalid", "test", transport=httpx.MockTransport(service))
    async with DramaPlugin.load(Path(__file__).resolve().parents[2]/"drama-plugin/plugin") as plugin:
        adapter = ArchiveMemoryAdapter(PluginToolAdapter(plugin),client,ArchiveOutbox(tmp_path/"observer.sqlite3",b"3"*32),ledger_root=tmp_path)
        observer=asyncio.create_task(adapter.synchronize(.01))
        try: await asyncio.wait_for(received.wait(),2)
        finally:
            observer.cancel()
            with pytest.raises(asyncio.CancelledError): await observer
    assert ledger.path.read_bytes() == before
    await client.aclose()


async def test_service_outage_cannot_change_plugin_result_or_registry(tmp_path: Path) -> None:
    def unavailable(request: httpx.Request) -> httpx.Response: return httpx.Response(503)
    client = DramaArchiveClient("https://archive.invalid", "test", transport=httpx.MockTransport(unavailable))
    root = Path(__file__).resolve().parents[2] / "drama-plugin/plugin"
    async with DramaPlugin.load(root) as plugin:
        ordinary = PluginToolAdapter(plugin)
        wrapped = ArchiveMemoryAdapter(ordinary, client, ArchiveOutbox(tmp_path/"queue.sqlite3", b"2"*32))
        await wrapped.discover()
        assert wrapped.list_tools() == ordinary.list_tools()
        before = await ordinary.call_tool("work.get_work", {"work_id": "work-1"})
        after = await wrapped.call_tool("work.get_work", {"work_id": "work-1"})
        assert before == after
        assert wrapped.outbox.pending() == 2
        await wrapped.outbox.drain(client)
        assert wrapped.outbox.pending() == 2
    await client.aclose()


def test_real_ledger_shape_export_is_read_only_and_keeps_attempt_identity(tmp_path: Path) -> None:
    from drama_plugin.persistence.ledger import ProductionLedger
    from drama_plugin.runtime.contracts import RuntimeRun, RuntimeScope, RunMode
    ledger = ProductionLedger(tmp_path/"production.sqlite3")
    ledger.create_run(RuntimeRun(run_id="technical-run", scope=RuntimeScope(work_id="technical-work"), mode=RunMode.EXPERIMENT,
        policy_id="technical-test", workflow_id="technical-fixture", workflow_fingerprint="0"*64))
    original = ledger.path.read_bytes()
    records = list(ledger_records(tmp_path, "technical-work"))
    assert len(records) == 1
    assert records[0]["runId"] == "technical-run"
    assert ledger.path.read_bytes() == original
    relocated=tmp_path/"relocated";relocated.mkdir()
    shutil.copyfile(ledger.path,relocated/"production.sqlite3")
    assert list(ledger_records(relocated,"technical-work")) == records
    originals=tmp_path/"creative-authority/objects";originals.mkdir(parents=True)
    (originals/"unowned.json").write_text('{"finding":"technical source with unknown ownership"}')
    quarantine=list(unscoped_creative_records(tmp_path))
    assert len(quarantine)==1 and quarantine[0]["workId"]=="__unscoped__"
    assert list(ledger_records(tmp_path,"technical-work")) == records


async def test_archive_client_cannot_submit_provider_or_adopt_media() -> None:
    client = DramaArchiveClient("https://archive.invalid", "test", transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    with pytest.raises(ValueError): await client.request("/api/tool/production/generate", method="POST")
    with pytest.raises(ValueError): await client.request("/api/tool/archive/../../production/generate", method="POST")
    with pytest.raises(ValueError): await client.request("/api/tool/archive/history", method="POST")
    await client.aclose()
