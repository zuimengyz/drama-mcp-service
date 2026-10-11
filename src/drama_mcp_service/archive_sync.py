"""Encrypted retryable archive outbox and read-only Ledger/Creative Authority export.

This module observes existing files. It has no Provider, budget, Runtime or Canon writer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from drama_plugin.long_memory.client import DramaArchiveClient, ArchiveUnavailable  # type: ignore[import-untyped]


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def record(raw: bytes, *, work_id: str, source_id: str, record_id: str, kind: str,
           revision: int = 1, evidence_class: str = "RAW_FACT", **scope: Any) -> dict[str, Any]:
    result = {"workId": work_id, "sourceId": source_id, "recordId": record_id,
        "sourceRevision": max(0, revision), "kind": kind, "evidenceClass": evidence_class,
        "fingerprint": hashlib.sha256(raw).hexdigest(), "rawBase64": base64.b64encode(raw).decode()}
    for key in ("sceneId", "shotId", "runId", "attemptId", "filmVersion", "subjectId", "locationId", "mediaId", "occurredAt"):
        if scope.get(key) is not None:
            result[key] = scope[key]
    return result


def ledger_records(root: Path, work_id: str) -> Iterator[dict[str, Any]]:
    """Export exact original JSON bytes, including separately retained real dispatch wires."""
    # Authority namespace, not a filesystem path: moving the same Ledger must not duplicate facts.
    source = "plugin-ledger:v1"
    ledger = root / "production.sqlite3"
    if not ledger.is_file():
        raise ValueError("Existing Ledger is required; export never creates one")
    with sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        scope_by_run: dict[str, dict[str, Any]] = {}
        for row in db.execute("SELECT * FROM production_run WHERE work_id=?", (work_id,)):
            scope = {"sceneId": row["scene_id"], "shotId": row["shot_id"], "runId": row["run_id"]}
            scope_by_run[row["run_id"]] = scope
            yield record(row["checkpoint_json"].encode(), work_id=work_id, source_id=source,
                record_id="run:" + row["run_id"], kind="RUN_SNAPSHOT", revision=row["revision"], **scope)
        for row in db.execute("SELECT * FROM immutable_artifact WHERE work_id=?", (work_id,)):
            decoded = json.loads(row["body_json"])
            payload = decoded if isinstance(decoded, dict) else {}
            yield record(row["body_json"].encode(), work_id=work_id, source_id=source,
                record_id=row["artifact_id"], kind=row["artifact_type"].upper().replace("-", "_"), revision=row["version"],
                sceneId=row["scene_id"], shotId=row["shot_id"], runId=payload.get("runId"),
                attemptId=payload.get("attemptId"), mediaId=payload.get("mediaId"), occurredAt=payload.get("createdAt"))
        for row in db.execute("SELECT o.* FROM production_operation o JOIN production_run r ON o.run_id=r.run_id WHERE r.work_id=?", (work_id,)):
            data = dict(row); raw = canonical(data)
            yield record(raw, work_id=work_id, source_id=source, record_id="operation:"+hashlib.sha256(raw).hexdigest(),
                kind="OPERATION_SNAPSHOT", attemptId=row["attempt_identity"], **scope_by_run[row["run_id"]])
        for row in db.execute("SELECT * FROM ledger_index WHERE work_id=?", (work_id,)):
            raw = canonical(dict(row))
            yield record(raw, work_id=work_id, source_id=source, record_id="index:"+hashlib.sha256(raw).hexdigest(),
                kind="SOURCE_SELECTION_SNAPSHOT", sceneId=row["scene_id"], shotId=row["shot_id"])
    # Dispatch filenames encode attempt identity. Relate them to the immutable attempt record.
    attempts: dict[str, dict[str, Any]] = {}
    for item in _read_attempts(ledger, work_id):
        attempts[item["client_identity"]] = item
    with sqlite3.connect(ledger.as_uri()+"?mode=ro", uri=True) as db:
        owned_operations = {r[0]: {"sceneId":r[1],"shotId":r[2],"runId":json.loads(r[3]).get("runId")}
            for r in db.execute("SELECT artifact_id,scene_id,shot_id,body_json FROM immutable_artifact WHERE work_id=? AND artifact_type='execution-operation'",(work_id,))}
    for path in sorted((root / "production.sqlite3.receipts").glob("*.json")):
        raw = path.read_bytes()
        try: data = json.loads(raw)
        except ValueError: continue
        attempt = attempts.get(path.name.split(".")[0], {})
        origin = data.get("identity", {}).get("operationRef", {}).get("artifactRef") if isinstance(data.get("identity"),dict) else None
        if not attempt and origin not in owned_operations: continue
        scope = owned_operations[origin] if not attempt else {"sceneId": attempt.get("scene_id"), "shotId": attempt.get("shot_id"),
                 "runId": data.get("runId") or attempt.get("run_id"), "attemptId": attempt.get("artifact_id")}
        kind = "REFERENCE_ORIGIN_EVIDENCE" if path.name.endswith(".reference-origin.json") else (
            "PROVIDER_REQUEST" if path.name.endswith(".wire.json") else "PROVIDER_RESPONSE")
        yield record(raw, work_id=work_id, source_id=source, record_id="receipt:"+path.name,
            kind=kind,
            occurredAt=data.get("createdAt"), **scope)
    for path, raw, data in _creative_sources(root, ledger, work_id):
        # Only explicitly owned originals; never attribute unrelated files to this Work.
        if not isinstance(data, dict):
            yield record(raw, work_id=work_id, source_id=source, record_id="creative:"+path.stem, kind="CREATIVE_ARTIFACT")
            continue
        status = data.get("status") or data.get("state", "")
        own_scope = data.get("scope", {})
        own_scope = own_scope if isinstance(own_scope, dict) else {}
        identity = data.get("identity") or data.get("id") or path.stem
        if not isinstance(identity, str): identity = path.stem
        classification = "OBSERVATION" if status == "OBSERVED_RESULT" else (
            "CANDIDATE" if status in ("CANDIDATE", "DRAFT", "REJECTED", "AUTHOR_CONCEPT", "REVIEWED") else "RAW_FACT")
        yield record(raw, work_id=work_id, source_id=source, record_id="creative:"+identity[:220],
            kind="CREATIVE_ARTIFACT", revision=data.get("version", 1) if isinstance(data.get("version", 1), int) else 1,
            evidence_class=classification,
            sceneId=data.get("sceneId") or own_scope.get("sceneId"), shotId=data.get("shotId") or own_scope.get("shotId"), occurredAt=data.get("createdAt"))


def ledger_work_ids(root: Path) -> list[str]:
    ledger = root / "production.sqlite3"
    if not ledger.is_file():
        return []
    with sqlite3.connect(ledger.resolve().as_uri()+"?mode=ro", uri=True) as db:
        return [r[0] for r in db.execute("SELECT DISTINCT work_id FROM immutable_artifact WHERE work_id IS NOT NULL UNION SELECT DISTINCT work_id FROM production_run WHERE work_id IS NOT NULL")]


def unscoped_creative_records(root: Path) -> Iterator[dict[str, Any]]:
    """Retain unowned originals without assigning them to a film by directory membership."""
    ledger = root / "production.sqlite3"
    owned = {path for work in ledger_work_ids(root) for path, _, _ in _creative_sources(root,ledger,work)}
    for path in sorted((root/"creative-authority/objects").glob("*.json")):
        if path not in owned:
            yield record(path.read_bytes(),work_id="__unscoped__",source_id="plugin-ledger:v1",
                record_id="unscoped-creative:"+path.stem,kind="UNSCOPED_CREATIVE_ARTIFACT")


def _read_attempts(ledger: Path, work: str) -> list[dict[str, Any]]:
    with sqlite3.connect(ledger.as_uri()+"?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        result = []
        for r in db.execute("SELECT * FROM immutable_artifact WHERE work_id=? AND artifact_type='provider-attempt'", (work,)):
            data = dict(r); body = json.loads(data["body_json"]); data["run_id"] = body.get("runId"); data["client_identity"] = body["clientIdentity"]
            result.append(data)
        return result


def _creative_sources(root: Path, ledger: Path, work: str) -> Iterator[tuple[Path, bytes, Any]]:
    """Ownership comes from an explicit scope or exact inbound reference, never folder presence alone."""
    objects = root / "creative-authority" / "objects"
    cache: dict[str, tuple[Path, bytes, Any]] = {}
    pending: list[Any] = []
    selected: set[str] = set()
    for path in objects.glob("*.json"):
        raw = path.read_bytes()
        try: data = json.loads(raw)
        except ValueError: continue
        cache[path.stem] = (path, raw, data)
        if isinstance(data, dict):
            scope = data.get("scope", {})
            owner = data.get("workId") or data.get("work_id") or (scope.get("workId") if isinstance(scope, dict) else None)
            if owner == work:
                selected.add(path.stem);pending.append(data)
    with sqlite3.connect(ledger.as_uri()+"?mode=ro",uri=True) as db:
        pending.extend(json.loads(r[0]) for r in db.execute("SELECT body_json FROM immutable_artifact WHERE work_id=?", (work,)))
        pending.extend(json.loads(r[0]) for r in db.execute("SELECT value_json FROM ledger_index WHERE work_id=?", (work,)))
    def fingerprints(value: Any) -> Iterator[str]:
        if isinstance(value, dict):
            if isinstance(value.get("fingerprint"), str): yield value["fingerprint"]
            for child in value.values(): yield from fingerprints(child)
        elif isinstance(value, list):
            for child in value: yield from fingerprints(child)
    while pending:
        for pin in fingerprints(pending.pop()):
            key = hashlib.sha256(canonical(pin)).hexdigest()
            if key in cache and key not in selected:
                data = cache[key][2]
                scope = data.get("scope", {}) if isinstance(data, dict) else {}
                declared = scope.get("workId") if isinstance(scope, dict) else None
                if declared and declared != work: continue
                selected.add(key); pending.append(data)
    for key in sorted(selected): yield cache[key]


class ArchiveOutbox:
    def __init__(self, path: Path, key: bytes) -> None:
        if len(key) != 32: raise ValueError("Outbox requires a 32-byte encryption key")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.cipher = path, AESGCM(key)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS archive_outbox (id TEXT PRIMARY KEY, sealed BLOB NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0)")
        path.chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def enqueue(self, records: list[dict[str, Any]]) -> int:
        added = 0
        with self.connect() as db:
            for item in records:
                raw = canonical(item); identity = hashlib.sha256(raw).hexdigest()
                if db.execute("SELECT 1 FROM archive_outbox WHERE id=?", (identity,)).fetchone():
                    continue
                nonce = os.urandom(12); sealed = nonce+self.cipher.encrypt(nonce, raw, identity.encode())
                added += db.execute("INSERT OR IGNORE INTO archive_outbox(id,sealed) VALUES (?,?)", (identity, sealed)).rowcount
        return added

    def pending(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM archive_outbox WHERE acknowledged=0").fetchone()[0])

    async def drain(self, client: DramaArchiveClient, batch_size: int = 25) -> dict[str, int | bool]:
        acknowledged = 0
        while True:
            with self.connect() as db:
                rows = db.execute("SELECT id,sealed FROM archive_outbox WHERE acknowledged=0 ORDER BY rowid LIMIT ?", (batch_size,)).fetchall()
            if not rows: return {"acknowledged": acknowledged, "pending": 0, "serviceAvailable": True}
            entries = []
            for identity, sealed in rows:
                entries.append(json.loads(self.cipher.decrypt(sealed[:12], sealed[12:], identity.encode())))
            try: await client.ingest(entries)
            except ArchiveUnavailable: return {"acknowledged": acknowledged, "pending": self.pending(), "serviceAvailable": False}
            with self.connect() as db:
                db.executemany("UPDATE archive_outbox SET acknowledged=1 WHERE id=?", [(x[0],) for x in rows])
            acknowledged += len(rows)


def tool_event(name: str, arguments: Any, result: Any, *, work_id: str, phase: str, call_id: str) -> dict[str, Any]:
    made = datetime.now(timezone.utc).isoformat()
    payload = {"tool": name, "callId": call_id, "phase": phase, "arguments": arguments, "result": result, "observedAt": made}
    return record(canonical(payload), work_id=work_id, source_id="mcp-observer", record_id="call:"+call_id+":"+phase,
        kind="MCP_CALL", occurredAt=made)
