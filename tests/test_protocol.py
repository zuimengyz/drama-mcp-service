from __future__ import annotations

from pathlib import Path

from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from drama_mcp_service.server import create_server


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT.parent / "drama-plugin" / "plugin"


def force_mock_provider_modes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for service in ("MEMORY", "ASSET", "RESEARCH", "PRODUCTION", "MEDIA", "VOICE"):
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
                "voice.import_voice",
                "voice.get_voice",
                "production.generate_role_dubbing",
            } <= names
            empty = await session.call_tool("work.list_works", {})
            assert empty.is_error is False and empty.content[0].text.strip() == "[]"
            # In-memory fixture only: no formal service or storage is configured.
            created = await session.call_tool("work.create_work", {"title":"Protocol fixture", "content":{}})
            assert created.is_error is False
            result = await session.call_tool("work.get_work", {"work_id": "work-new"})
            assert result.is_error is False
            assert result.structured_content["id"] == "work-new"
            imported = await session.call_tool("media.import_media", {"work_id":"work-new","media_type":"IMAGE","source_uri":"file:///not-read-by-mock.png","content":{}})
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


async def test_explicit_server_settings_are_used_by_plugin_lifespan(monkeypatch, tmp_path):
    from drama_mcp_service.settings import Settings
    force_mock_provider_modes(monkeypatch)
    monkeypatch.setenv('DRAMA_PLUGIN_ROOT', str(tmp_path / 'wrong-env-plugin'))
    settings = Settings(plugin_root=PLUGIN_ROOT, plugin_config=None, host='127.0.0.1', port=8765)
    server = create_server(settings)
    # Later ENV changes cannot replace the already selected external Settings.
    async with InMemoryTransport(server) as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            assert (await session.call_tool('work.list_works', {})).is_error is False


async def test_mcp_nested_contract_fields_survive_roundtrip(monkeypatch):
    force_mock_provider_modes(monkeypatch)
    monkeypatch.setenv('DRAMA_PLUGIN_ROOT', str(PLUGIN_ROOT))
    content = {'authorityFixture': {'intent': {'owner':'director', 'unknownFutureField':[1, False, None]},
                                   'source': {'compilerVersion':'test', 'gateStatus':'NOT_RUN'}}}
    async with InMemoryTransport(create_server()) as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            result = await session.call_tool('work.create_work', {'title':'fixture', 'content':content})
            assert result.structured_content['content'] == content
            result = await session.call_tool('work.get_work', {'work_id':result.structured_content['id']})
            assert result.structured_content['content'] == content
            invalid = await session.call_tool('scene.create_scene',
                {'episode_id':'e', 'order':-1, 'title':'fixture', 'content':{}})
            assert invalid.is_error is True


async def test_actual_context_response_matches_advertised_schema_and_rhythm_conflicts(monkeypatch):
    from jsonschema import Draft202012Validator
    force_mock_provider_modes(monkeypatch)
    monkeypatch.setenv('DRAMA_PLUGIN_ROOT', str(PLUGIN_ROOT))
    monkeypatch.delenv('DRAMA_PLUGIN_CONFIG', raising=False)
    monkeypatch.setenv('rhythm_speed', 'medium')
    async with InMemoryTransport(create_server()) as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            catalog = (await session.list_tools()).tools
            schema = next(t.output_schema for t in catalog if t.name == 'context.build_context')
            validator = Draft202012Validator(schema)
            for options in ({'newWork':True}, {'newWork':True,'rhythm_speed':'medium'},
                            {'newWork':True,'creativeRhythm':{'rhythmSpeed':'medium'}}):
                result = await session.call_tool('context.build_context', {'request':{
                    'scope':'WORK','purpose':'WORK_CREATION','resourceId':'protocol-no-work','options':options}})
                assert result.is_error is False
                assert list(validator.iter_errors(result.structured_content)) == []
                assert result.structured_content['creativeRhythm']['rhythm_speed'] == 'medium'
                assert 'rhythmSpeed' not in result.structured_content['creativeRhythm']
                assert result.structured_content['work'] is None
            for options in ({'newWork':True,'rhythm_speed':'fast'},
                            {'newWork':True,'creativeRhythm':{'rhythmSpeed':'fast'}}):
                result = await session.call_tool('context.build_context', {'request':{
                    'scope':'WORK','purpose':'WORK_CREATION','resourceId':'protocol-no-work','options':options}})
                assert result.is_error is True
                assert result.structured_content['error']['code'] == 'RHYTHM_AUTHORITY_CONFLICT'
