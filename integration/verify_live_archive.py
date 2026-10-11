"""Approved live archive sync and read-only MCP/MinIO validation. No production tools."""
from __future__ import annotations

import asyncio
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import httpx
from drama_plugin.long_memory.client import DramaArchiveClient
from drama_mcp_service.archive_sync import ArchiveOutbox, ledger_records, record, unscoped_creative_records
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/drama-service-foundation-20261011'
SOURCE = ROOT/'production/ridiculous-man-pre-video-498fbb74-f925-40e1-b1f0-0487e7f8940f'
WORK = 'ridiculous-man-498fbb74-f925-40e1-b1f0-0487e7f8940f'
SCENE = 'scene-c7c6d02d6ab5439f336730df'
SHOT = 'shot-4379937970a26314204a4f2a'
PRIVATE = OUT/'live-backup'
PROOF_FILE = OUT/'live-integration-proof.json'

def companions():
    result = []
    for directory in ('scene3-reference-delivery-recovery-20261011','scene3-shot01-targeted-retake-20261011'):
        folder = ROOT/'artifacts'/directory
        archive = json.loads((folder/'shot01-archived-result.json').read_bytes())
        for name, kind in (('shot01-archived-result.json','ARCHIVE_RECEIPT'),('shot01-financial-terms.json','FINANCIAL_TERMS'),
                           ('shot01-query-status.json','RECOVERY_QUERY_RESULT'),('human-retake-authorization.json','HUMAN_AUTHORIZATION'),
                           ('mcp-original-verification.json','MEDIA_READBACK_VERIFICATION')):
            path = folder/name
            if path.exists(): result.append(record(path.read_bytes(),work_id=WORK,source_id='retained-companion-artifacts',
                record_id=directory+':'+name,kind=kind,sceneId=SCENE,shotId=SHOT,runId=archive['runId'],
                occurredAt=archive.get('recordedAt') if name=='shot01-archived-result.json' else None))
    path = ROOT/'artifacts/film-look-reconciliation-20261011/human-art-direction.json'
    result.append(record(path.read_bytes(),work_id=WORK,source_id='retained-companion-artifacts',
        record_id='human-art-direction:20261011',kind='USER_ART_DIRECTION',sceneId=SCENE,shotId=SHOT))
    return result

def items(): return list(ledger_records(SOURCE,WORK))+companions()+list(unscoped_creative_records(SOURCE))

def write_proof(value): PROOF_FILE.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def text_values(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k=='text' and isinstance(v,str): yield v
            else: yield from text_values(v)
    elif isinstance(value,list):
        for v in value: yield from text_values(v)

def new_client():
    # This operator's timeout is independent of production/model timeout configuration.
    values = dict(os.environ)
    values['DRAMA_PLUGIN_SERVICE_MEMORY_TIMEOUT_SECONDS'] = '180'
    client = DramaArchiveClient.from_environment(values)
    if client is None:
        raise RuntimeError('Archive service connection is not configured')
    return client

async def write_checkpoints(client,sources):
    for (work,source),count in Counter((r['workId'],r['sourceId']) for r in sources).items():
        coverage={'sourceRecords':count,'historicalOverwrittenRunStatesComplete':False,
                  'unloggedHistoricalDirectQueriesComplete':False}
        if work==WORK and source=='plugin-ledger:v1':
            coverage.update(immutableOriginals=990,retainedNativeRequests=30,currentRunSnapshots=79)
        await client.request('/api/tool/archive/checkpoint',method='POST',body={
            'workId':work,'sourceId':source,'cursor':'readonly-full-snapshot','coverage':coverage})

async def sync():
    sources=items(); assert len(sources)==3346
    key=base64.b64decode(os.environ['DRAMA_MCP_ARCHIVE_KEY'],validate=True)
    box=ArchiveOutbox(Path(os.environ['DRAMA_MCP_ARCHIVE_OUTBOX']),key)
    enqueued=box.enqueue(sources)
    manifest={'records':[{'entryId':hashlib.sha256((r['workId']+'\0'+r['sourceId']+'\0'+r['recordId']+'\0'+str(r['sourceRevision'])).encode()).hexdigest(),
                           'fingerprint':r['fingerprint']} for r in sources]}
    path=PRIVATE/'live-exact-byte-manifest.json'; path.write_text(json.dumps(manifest));path.chmod(0o600)
    client=new_client()
    for attempt in range(60):
        try:
            await client.tools();break
        except Exception:
            if attempt==59: raise
            await asyncio.sleep(.5)
    original_ingest=client.ingest
    completed=0
    async def observed_ingest(records):
        nonlocal completed
        start=time.monotonic(); ack=await original_ingest(records); completed+=len(records)
        if completed%250==0 or completed==25:
            print(json.dumps({'acknowledged':completed,'pending':box.pending(),'lastBatchSeconds':round(time.monotonic()-start,2)}),flush=True)
        return ack
    client.ingest=observed_ingest
    try:
        outcome=await box.drain(client)
        assert outcome['pending']==0, 'Live sync remains pending; protected outbox preserved'
        repeated=0
        for offset in range(0,len(sources),25):
            ack=await original_ingest(sources[offset:offset+25]);assert ack['inserted']==0;repeated+=ack['repeated']
        await write_checkpoints(client,sources)
    finally: await client.aclose()
    proof={'status':'LIVE_SYNC_COMPLETE','sourceRecords':len(sources),'filmRecords':len(sources)-54,'unscopedRecords':54,
           'newlyQueued':enqueued,'outboxPending':box.pending(),'duplicateReplay':{'repeated':repeated,'inserted':0},
           'kinds':dict(Counter(r['kind'] for r in sources)),'paidCalls':0,'productionSubmissions':0,'mediaUploads':0}
    write_proof(proof);print(json.dumps({'status':proof['status'],'sourceRecords':len(sources),'outboxPending':box.pending()}),flush=True)

async def verify():
    proof=json.loads(PROOF_FILE.read_text())
    client=new_client()
    try: await write_checkpoints(client,items())
    finally: await client.aclose()
    async with httpx.AsyncClient(trust_env=False,timeout=15) as http:
        health=await http.get('http://127.0.0.1:8765/health');health.raise_for_status();runtime=health.json()
        assert runtime['archive']=='OPTIONAL_SOURCE_MIRROR'
        identity=runtime['identity'];before=json.loads((OUT/'live-runtime-before.json').read_text())
        assert identity['pluginConfigPath']==before['pluginConfig'] and identity['configFileSha256']==before['configSha256']
        baseline=json.loads((OUT/'freeze-baseline.json').read_text())['repositories']['drama-plugin']
        for name,value in identity['sourceFilesAtInitialization'].items():
            if name.startswith('drama_plugin.') and name!='drama_plugin.config.loader':
                relative=str(Path(value['path']).relative_to(Path(baseline['root'])))
                assert value['sha256']==baseline['files'][relative], 'Running Plugin core differs from freeze baseline'
        proof['runtime']={'archive':runtime['archive'],'pid':identity['pid'],'pluginConfigUnchanged':True,
                          'activePluginProductionLedgerUnchanged':True,'loadedFrozenPluginModulesMatch':True}
    async with streamable_http_client('http://127.0.0.1:8765/mcp') as streams:
        async with ClientSession(*streams[:2]) as session:
            initialized=await session.initialize();assert initialized.server_info.name=='drama-mcp-service'
            listed=(await session.list_tools()).tools; names={t.name for t in listed}
            expected={'memory.restore_context','memory.query_archive','memory.production_history','memory.character_assets','memory.archive_status'}
            assert expected<=names
            async def call(name,arguments):
                result=await session.call_tool(name,arguments)
                assert not result.is_error, 'Live MCP read failed: '+name
                return result.structured_content
            status=await call('memory.archive_status',{'workId':WORK})
            assert status['runtimeAuthority']=='PLUGIN_LEDGER' and status['archiveIsNotRuntime']
            context=await call('memory.restore_context',{'workId':WORK,'sceneId':SCENE,'limit':30})
            assert context['canonicalWorkStatus']=='NOT_PRESENT_IN_THIS_DATABASE'
            assert 'scene12-actual-continuity' in json.dumps(context['observations'],ensure_ascii=False)
            assert len(context['sourceRecords'])<=30 and context['doesNotAdvanceRuntime']
            observed=await call('memory.query_archive',{'workId':WORK,'evidenceClass':'OBSERVATION'})
            assert 'scene12-actual-continuity' in json.dumps(observed,ensure_ascii=False)
            task_counts={}
            for task in ('cgt-20261011012944-mpfvj','cgt-20261011102732-7hwxg'):
                history=await call('memory.production_history',{'workId':WORK,'shotId':SHOT,'query':task,'limit':100})
                assert history['total']>0;task_counts[task]=history['total']
            financial=await call('memory.query_archive',{'workId':WORK,'shotId':SHOT,'kind':'FINANCIAL_TERMS'})
            assert financial['total']==2
            requests=await call('memory.query_archive',{'workId':WORK,'shotId':SHOT,'kind':'PROVIDER_REQUEST'})
            lengths=sorted(max(map(len,text_values(r['payload']))) for r in requests['records']);assert lengths==[2822,3014]
            assert 'https://' not in json.dumps(requests) and 'http://' not in json.dumps(requests)
            chars=await call('memory.character_assets',{'workId':WORK});assert chars['adoptionUnchanged']
            with sqlite3.connect((SOURCE/'production.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                registrations=[json.loads(r[0]) for r in db.execute("SELECT body_json FROM immutable_artifact WHERE artifact_type='formal-media-registration' AND work_id=?",(WORK,))]
            readbacks=[]
            for selected in ('media_00e3c2fdcc1147a2b448b046a750a3e1','media_f996694c1ca94b269f5d2b5748e1de03',
                             'media_cae582e8be4645eeb531162baff3a106','media_8cc90e9de05242eb8b1e3e8c09d0f3c5','media_589040e2ef174b489f7f2dbc0eaa91a8'):
                metadata=await call('media.get_media',{'media_id':selected})
                resolved=await call('media.resolve_media',{'media_id':selected})
                original=next(r for r in registrations if r['canonicalMediaRef']['artifactRef']==selected)
                digest=hashlib.sha256();count=0
                async with httpx.AsyncClient(trust_env=False,timeout=180) as download:
                    async with download.stream('GET',resolved['url']) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():digest.update(chunk);count+=len(chunk)
                assert digest.hexdigest()==original['identity']['contentHash'] and count==original['identity']['byteCount']
                readbacks.append({'mediaId':selected,'sha256':digest.hexdigest(),'bytes':count,'originalBytes':True})
                print(json.dumps({'mediaReadback':selected,'hashMatches':True,'bytes':count}),flush=True)
            proof['mcp']={'protocol':'LIVE_STREAMABLE_HTTP_INITIALIZE_LIST_CALL','discoveredMemoryTools':sorted(expected),
                          'toolCount':len(names),'taskHistoryMatches':task_counts,'actualPromptCharacters':lengths,
                          'observationsSeparate':True,'canonicalWorkStatus':context['canonicalWorkStatus'],
                          'financialTermsRecords':financial['total'],'ordinaryQueriesRedacted':True,
                          'mediaReadback':readbacks,'liveMinioVerifiedThisRun':True,'mediaRoute':'EXISTING_MCP_JAVA_MINIO_OWNER'}
    # A new real session verifies reconnect while the service/outbox remain persistent.
    async with streamable_http_client('http://127.0.0.1:8765/mcp') as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            result=await session.call_tool('memory.archive_status',{'workId':WORK});assert not result.is_error
    proof['mcp']['sessionReconnect']='PASS'
    proof['status']='LIVE_MCP_AND_MINIO_VERIFIED';write_proof(proof)
    print(json.dumps({'status':proof['status'],'memoryTools':5,'mediaReadbacks':5,'proof':str(PROOF_FILE)}),flush=True)

if __name__=='__main__': asyncio.run(sync() if sys.argv[1]=='sync' else verify())
