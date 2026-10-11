"""Real retained-film corpus -> isolated Java/H2 -> real MCP SDK offline replay.

No model, generation, adoption, upload or live database endpoint is called.
The local S3 GET/HEAD fixture streams existing original bytes without copying media.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp

import httpx
from drama_plugin.long_memory.client import ArchiveUnavailable, DramaArchiveClient
from drama_mcp_service.archive_sync import ArchiveOutbox, ledger_records, record, unscoped_creative_records
from drama_mcp_service.server import create_server
from drama_mcp_service.settings import Settings
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "production/ridiculous-man-pre-video-498fbb74-f925-40e1-b1f0-0487e7f8940f"
WORK = "ridiculous-man-498fbb74-f925-40e1-b1f0-0487e7f8940f"
SCENE = "scene-c7c6d02d6ab5439f336730df"
SHOT = "shot-4379937970a26314204a4f2a"
OUT = ROOT / "artifacts/drama-service-foundation-20261011"
JAVA = ROOT / "drama-service/server"
PROOF: dict[str, object] = {}


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): value.update(chunk)
    return value.hexdigest()


def port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); return int(probe.getsockname()[1])


def companions() -> list[dict]:
    result = []
    for directory in ("scene3-reference-delivery-recovery-20261011", "scene3-shot01-targeted-retake-20261011"):
        folder = ROOT / "artifacts" / directory
        archive = json.loads((folder / "shot01-archived-result.json").read_bytes())
        for name, kind in (("shot01-archived-result.json", "ARCHIVE_RECEIPT"),
                           ("shot01-financial-terms.json", "FINANCIAL_TERMS"),
                           ("shot01-query-status.json", "RECOVERY_QUERY_RESULT"),
                           ("human-retake-authorization.json", "HUMAN_AUTHORIZATION"),
                           ("mcp-original-verification.json", "MEDIA_READBACK_VERIFICATION")):
            path = folder / name
            if path.exists():
                result.append(record(path.read_bytes(), work_id=WORK, source_id="retained-companion-artifacts",
                    record_id=f"{directory}:{name}", kind=kind, sceneId=SCENE, shotId=SHOT,
                    runId=archive["runId"], occurredAt=archive.get("recordedAt") if name == "shot01-archived-result.json" else None))
    path = ROOT / "artifacts/film-look-reconciliation-20261011/human-art-direction.json"
    result.append(record(path.read_bytes(), work_id=WORK, source_id="retained-companion-artifacts",
        record_id="human-art-direction:20261011", kind="USER_ART_DIRECTION", sceneId=SCENE, shotId=SHOT))
    return result


def registrations() -> list[dict]:
    with sqlite3.connect((SOURCE / "production.sqlite3").as_uri()+"?mode=ro", uri=True) as db:
        return [json.loads(r[0]) for r in db.execute(
            "SELECT body_json FROM immutable_artifact WHERE artifact_type='formal-media-registration' AND work_id=?", (WORK,))]


class ExistingBytes(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self): self.deliver(True)
    def do_HEAD(self): self.deliver(False)
    def do_PUT(self): self.send_error(405, "Read-only original replay")
    def do_POST(self): self.send_error(405, "Read-only original replay")
    def deliver(self, body: bool):
        name = self.path.split("?")[0].rsplit("/", 1)[-1]
        path = SOURCE / "media" / name
        if len(name) != 64 or not all(x in "0123456789abcdef" for x in name) or not path.is_file():
            self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(path.stat().st_size)); self.end_headers()
        if body:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""): self.wfile.write(chunk)


async def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    private = Path(mkdtemp(prefix="offline-replay-", dir=OUT)); private.chmod(0o700)
    key, token = secrets.token_bytes(32), secrets.token_urlsafe(32)
    # Durable test recovery material is private and independent of the live account.
    (private / "replay-key").write_bytes(key); (private / "replay-key").chmod(0o600)
    baseline = json.loads((OUT / "production-baseline.json").read_bytes())
    work_items = list(ledger_records(SOURCE, WORK)) + companions()
    unscoped = list(unscoped_creative_records(SOURCE))
    items = work_items + unscoped
    kinds = Counter(r["kind"] for r in items); classes = Counter(r["evidenceClass"] for r in items)
    assert classes["OBSERVATION"] > 0, "Actual observed-result source must remain an observation"
    shot_requests = [r for r in items if r["kind"] == "PROVIDER_REQUEST" and r.get("shotId") == SHOT]
    assert len(shot_requests) == 2
    prompt_sizes = []
    for item in shot_requests:
        raw = json.loads(base64.b64decode(item["rawBase64"]))
        payload = raw.get("payload", raw.get("request", raw))
        # The retained native wire may have an outer dispatch envelope.
        def text_values(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k == "text" and isinstance(v, str): yield v
                    else: yield from text_values(v)
            elif isinstance(value, list):
                for v in value: yield from text_values(v)
        prompt_sizes.append(max(map(len, text_values(payload))))
    assert sorted(prompt_sizes) == [2822, 3014]
    PROOF.update(sourceRecords=len(items), filmRecords=len(work_items), unscopedRecords=len(unscoped), kinds=dict(kinds), evidenceClasses=dict(classes),
        scene3ActualPromptCharacters=sorted(prompt_sizes), paidCalls=0, productionSubmissions=0,
        sourceRoot=str(SOURCE), privateReplayDirectory=str(private), historicalArchiveOwner="PLUGIN_SOURCE_MIRROR")
    owned = sum(r["kind"] == "CREATIVE_ARTIFACT" for r in items)
    PROOF["creativeOwnership"] = {"provenOwned":owned, "archivedWithoutFilmAssignment":len(unscoped)}
    s3 = ThreadingHTTPServer(("127.0.0.1", 0), ExistingBytes)
    threading.Thread(target=s3.serve_forever, daemon=True).start()
    service_port = port(); base = f"http://127.0.0.1:{service_port}"
    jdbc = f"jdbc:h2:file:{private/'archive'};MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1"
    classpath = os.pathsep.join([str(JAVA/"target/test-classes"), str(JAVA/"target/classes"),
        (JAVA/"target/archive-test-classpath.txt").read_text().strip()])
    child_env = dict(os.environ)
    child_env.update(DRAMA_ARCHIVE_ENCRYPTION_KEY=base64.b64encode(key).decode(), DRAMA_TOOL_SECRET=token, ARCHIVE_REPLAY_JDBC=jdbc)
    process = None
    log = (private/"java-service.log").open("ab")
    def start(initial: bool):
        return subprocess.Popen(["/usr/bin/java", "-cp", classpath, "com.drama.DramaApplication",
            "--spring.profiles.active=test", "--server.address=127.0.0.1", f"--server.port={service_port}", f"--spring.datasource.url={jdbc}",
            "--drama.archive.encryption-key=${DRAMA_ARCHIVE_ENCRYPTION_KEY}", "--drama.tool.secret=${DRAMA_TOOL_SECRET}",
            f"--drama.media.storage.endpoint=http://127.0.0.1:{s3.server_port}",
            "--spring.sql.init.mode=" + ("always" if initial else "never")], env=child_env, stdout=log, stderr=log, cwd=JAVA)
    async def ready(p):
        async with httpx.AsyncClient(trust_env=False) as h:
            for _ in range(120):
                if p.poll() is not None: raise RuntimeError("Isolated Java service exited; inspect private replay log")
                try:
                    r = await h.get(base+"/api/tool/archive/tools", headers={"Authorization":"Bearer "+token})
                    if r.status_code == 200: return
                except httpx.HTTPError: pass
                await asyncio.sleep(.25)
        raise RuntimeError("Isolated Java startup timeout")
    def stop(p):
        p.terminate()
        try: p.wait(20)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    client = DramaArchiveClient(base, token, timeout=60)
    try:
        process = start(True); await ready(process)
        box = ArchiveOutbox(private/"outbox.sqlite3", key); assert box.enqueue(items) == len(items)
        result = await box.drain(client)
        assert result["pending"] == 0
        repeated = 0
        for offset in range(0, len(items), 25):
            ack = await client.ingest(items[offset:offset+25]); assert ack["inserted"] == 0; repeated += ack["repeated"]
        PROOF["duplicateReplay"] = {"repeated":repeated,"additionalProductionFacts":0}
        # Real service commits a request, then the acknowledgement is deliberately dropped in transit.
        lost = True
        async def drop_ack(request):
            nonlocal lost
            async with httpx.AsyncClient(trust_env=False) as forwarding:
                response = await forwarding.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
            if lost: lost=False; raise httpx.ReadTimeout("Controlled lost acknowledgement")
            return response
        ack_client = DramaArchiveClient(base, token, transport=httpx.MockTransport(drop_ack))
        recovery = ArchiveOutbox(private/"lost-ack.sqlite3", key); recovery.enqueue([shot_requests[0]])
        assert (await recovery.drain(ack_client))["pending"] == 1
        recovery = ArchiveOutbox(private/"lost-ack.sqlite3", key)
        assert (await recovery.drain(ack_client))["pending"] == 0
        await ack_client.aclose(); PROOF["lostAcknowledgementRecovery"] = "PASS_WITH_REAL_SERVICE_AND_REOPENED_OUTBOX"
        await client.request("/api/tool/archive/checkpoint", method="POST", body={"workId":WORK,
            "sourceId":items[0]["sourceId"],"cursor":"readonly-full-snapshot", "coverage":{
                "immutableOriginals":990, "retainedNativeRequests":30,"currentRunSnapshots":79,
                "historicalOverwrittenRunStatesComplete":False,"unloggedHistoricalDirectQueriesComplete":False}})
        # Internal-only exact decryption proof; no raw-payload HTTP/MCP endpoint is introduced.
        media = [r for r in registrations() if (SOURCE/"media"/r["identity"]["contentHash"]).is_file()]
        manifest = {"mediaRegistrations":media,"records":[{"entryId":hashlib.sha256(
            (r["workId"]+"\0"+r["sourceId"]+"\0"+r["recordId"]+"\0"+str(r["sourceRevision"])).encode()).hexdigest(),
            "fingerprint":r["fingerprint"]} for r in items]}
        manifest_path = private/"verification-manifest.json"; manifest_path.write_text(json.dumps(manifest));manifest_path.chmod(0o600)
        stop(process); process = None
        with (private/"internal-verification.log").open("wb") as verification:
            checked = subprocess.run(["/usr/bin/java","-cp",classpath,"com.drama.ArchiveReplayDatabase",str(manifest_path)],
                env=child_env,stdout=verification,stderr=verification,cwd=JAVA,timeout=120)
        if checked.returncode: raise RuntimeError("Internal exact-byte replay verification failed")
        lines=(private/"internal-verification.log").read_text().splitlines()
        PROOF["exactByteRecovery"]={k:int(v) for k,v in (line.split("=",1) for line in lines if line.startswith(("EXACT_ORIGINAL_RECORDS=","SOURCE_LINKS=","VERSIONED_SOURCE_LINKS=")))}
        assert PROOF["exactByteRecovery"]["EXACT_ORIGINAL_RECORDS"] == len(items)
        # Service outage preserves durable sync queue, and the restarted Java service preserves history.
        outage = ArchiveOutbox(private/"outage.sqlite3", key);outage.enqueue([shot_requests[1]])
        assert (await outage.drain(client))["pending"] == 1
        process = start(False); await ready(process)
        assert (await ArchiveOutbox(private/"outage.sqlite3",key).drain(client))["pending"] == 0
        context = await client.request("/api/tool/archive/context",params={"workId":WORK,"sceneId":SCENE,"limit":30})
        assert len(context["sourceRecords"]) <= 30
        assert context["canonicalWorkStatus"] == "NOT_PRESENT_IN_THIS_DATABASE"
        assert "scene12-actual-continuity" in json.dumps(context["observations"],ensure_ascii=False)
        observed = await client.request("/api/tool/archive/query",params={"workId":WORK,"evidenceClass":"OBSERVATION"})
        assert "scene12-actual-continuity" in json.dumps(observed,ensure_ascii=False)
        for task in ("cgt-20261011012944-mpfvj","cgt-20261011102732-7hwxg"):
            history = await client.request("/api/tool/archive/history",params={"workId":WORK,"query":task,"limit":100})
            assert history["total"] > 0
        cost = await client.request("/api/tool/archive/query",params={"workId":WORK,"shotId":SHOT,"kind":"FINANCIAL_TERMS"})
        assert cost["total"] == 2
        public = json.dumps(await client.request("/api/tool/archive/query",params={"workId":WORK,"kind":"PROVIDER_REQUEST","limit":100}))
        assert "https://" not in public and "http://" not in public
        executable=await client.request("/api/tool/archive/query",params={"workId":WORK,"shotId":SHOT,"kind":"PROVIDER_REQUEST"})
        assert sorted(max(map(len,text_values(r["payload"]))) for r in executable["records"]) == [2822,3014]
        PROOF["historyRecovery"]={"twoFailedArtisticTrials":True,"actualPromptsAndReceipts":True,
            "quotesAndBudgetAuthorizationsPreserved":True,"actualContinuityObservationSeparate":True,"canonicalWorkStatus":context["canonicalWorkStatus"],
            "serviceRestartAndPendingSync":"PASS","ordinaryQueriesRedacted":True}
        # Real MCP SDK, real discovered Java memory contract, frozen existing media HTTP/S3 implementation.
        for capability in ("MEMORY","ASSET","RESEARCH","PRODUCTION","VOICE"):
            os.environ[f"DRAMA_PLUGIN_PROVIDER_{capability}_MODE"]="mock"
        os.environ.update(DRAMA_PLUGIN_PROVIDER_MEDIA_MODE="http",DRAMA_PLUGIN_PROVIDER_CONTEXT_MODE="local",
            DRAMA_SERVICE_BASE_URL=base,DRAMA_SERVICE_API_TOKEN=token,DRAMA_MCP_ARCHIVE_ENABLED="true",
            DRAMA_PLUGIN_SERVICE_MEMORY_BASE_URL=base,DRAMA_PLUGIN_SERVICE_MEMORY_API_TOKEN=token,
            DRAMA_PLUGIN_SERVICE_MEDIA_BASE_URL=base,DRAMA_PLUGIN_SERVICE_MEDIA_API_TOKEN=token,NO_PROXY="127.0.0.1,localhost",
            DRAMA_MCP_ARCHIVE_KEY=base64.b64encode(key).decode(),DRAMA_MCP_ARCHIVE_OUTBOX=str(private/"mcp-outbox.sqlite3"))
        os.environ.pop("DRAMA_PLUGIN_CONFIG",None)
        downloaded=[]
        async with InMemoryTransport(create_server(Settings(plugin_root=ROOT/"drama-plugin/plugin",
            plugin_config=ROOT/"drama-plugin/plugin/config/drama-service-http.example.yaml"))) as streams:
            async with ClientSession(*streams[:2]) as session:
                await session.initialize()
                names=[t.name for t in (await session.list_tools()).tools]
                assert "memory.restore_context" in names and "memory.production_history" in names and "media.resolve_media" in names
                status=await session.call_tool("memory.archive_status",{"workId":WORK});assert not status.is_error
                recovered=await session.call_tool("memory.restore_context",{"workId":WORK,"sceneId":SCENE,"limit":30});assert not recovered.is_error
                query=await session.call_tool("memory.production_history",{"workId":WORK,"shotId":SHOT,"query":"cgt-20261011102732-7hwxg"});assert not query.is_error
                for selected in ("media_00e3c2fdcc1147a2b448b046a750a3e1", "media_f996694c1ca94b269f5d2b5748e1de03",
                    "media_cae582e8be4645eeb531162baff3a106", "media_8cc90e9de05242eb8b1e3e8c09d0f3c5", "media_589040e2ef174b489f7f2dbc0eaa91a8"):
                    metadata=await session.call_tool("media.get_media",{"media_id":selected});assert not metadata.is_error
                    resolved=await session.call_tool("media.resolve_media",{"media_id":selected});assert not resolved.is_error
                    async with httpx.AsyncClient(trust_env=False) as download:
                        response=await download.get(resolved.structured_content["url"]);response.raise_for_status()
                    original=next(r for r in media if r["canonicalMediaRef"]["artifactRef"]==selected)
                    assert hashlib.sha256(response.content).hexdigest()==original["identity"]["contentHash"]
                    assert len(response.content)==original["identity"]["byteCount"]
                    downloaded.append({"mediaId":selected,"sha256":original["identity"]["contentHash"],"originalBytes":True})
        PROOF["mcp"]={"protocol":"MCP_SDK_STANDARD_INITIALIZE_LIST_CALL","discoveredMemoryTools":[n for n in names if n.startswith("memory.")],
            "mediaReadback":downloaded,"storageRoute":"EXISTING_JAVA_S3_ADAPTER_WITH_READ_ONLY_LOCAL_ORIGINAL_BYTES",
            "liveMinioVerifiedThisRun":False,"existingMediaOwnerUnchanged":True}
        PROOF["scene12MediaDistinction"]={"scene1CanonicalAndDesignatedBytesIdentical":True,
            "scene2CanonicalSha256":"096c0ed1214fc5e7dc618ab6026a4547f9d92547a2cf2845601ced76501a8ac3",
            "scene2DesignatedFileSha256":"134a0a93dfc7454c6879542699800567066cc82dbae9d9b8e67ca91cee36b73b",
            "differentContainersNotDeclaredIdentical":True,"noAdoptionWrites":True}
        PROOF["coverageGaps"]=["Live Canon/MinIO and live MySQL deployment have not been inspected or migrated in this isolated replay.",
            "Ledger only retains current mutable Run/index snapshots; overwritten historical revisions cannot be reconstructed.",
            "Unlogged historical direct Provider status queries cannot be invented; future MCP calls are passively buffered.",
            "54 creative objects lack provable Work ownership; archived as __unscoped__, not silently assigned to this film.",
            "No new paid character images exist; board/independent-image capability is a schema and template, not adopted casting."]
    finally:
        await client.aclose()
        if process is not None: stop(process)
        s3.shutdown();s3.server_close();log.close()
    (OUT/"offline-replay-proof.json").write_text(json.dumps(PROOF,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":"PASS","sourceRecords":len(items),"proof":str(OUT/"offline-replay-proof.json")},ensure_ascii=False))


if __name__ == "__main__": asyncio.run(run())
