from pathlib import Path
import hashlib
import json

from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from starlette.testclient import TestClient

from drama_mcp_service.server import create_app, create_server
from drama_mcp_service.settings import Settings

PLUGIN = Path(__file__).resolve().parents[2] / 'drama-plugin/plugin'


def configure(monkeypatch):
    for name in ('MEMORY', 'ASSET', 'RESEARCH', 'PRODUCTION', 'MEDIA', 'VOICE'):
        monkeypatch.setenv(f'DRAMA_PLUGIN_PROVIDER_{name}_MODE', 'mock')
    monkeypatch.setenv('DRAMA_PLUGIN_PROVIDER_CONTEXT_MODE', 'local')
    monkeypatch.setenv('DRAMA_PLUGIN_PROVIDER_AUDIO_SEMANTIC_MODE', 'off')
    monkeypatch.setenv('rhythm_speed', 'medium')
    monkeypatch.setenv('DRAMA_PLUGIN_SERVICE_MEMORY_API_TOKEN', 'SENTINEL_PRIVATE_TOKEN')


async def test_identity_is_from_actual_lifespan_and_fixed_settings(monkeypatch, tmp_path):
    configure(monkeypatch)
    settings = Settings(PLUGIN, None)
    runtime = {'status': 'starting'}
    server = create_server(settings, runtime=runtime)
    monkeypatch.setenv('DRAMA_PLUGIN_ROOT', str(tmp_path / 'wrong'))
    monkeypatch.setenv('DRAMA_PLUGIN_CONFIG', str(tmp_path / 'missing.yaml'))
    async with InMemoryTransport(server) as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            assert runtime['status'] == 'ready'
            identity = runtime['identity']
            assert identity['pluginRoot'] == str(PLUGIN.resolve())
            assert identity['pluginConfigPath'] is None
            assert identity['rhythmSource'] == 'environment:rhythm_speed'
            for record in identity['sourceFilesAtInitialization'].values():
                assert record['sha256'] == hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()
            assert 'SENTINEL_PRIVATE_TOKEN' not in json.dumps(runtime)
            old_snapshot = json.dumps(runtime)
            monkeypatch.setenv('rhythm_speed', 'fast')
            assert json.dumps(runtime) == old_snapshot
            listed = (await session.list_tools()).tools
            assert next(t for t in listed if t.name == 'production.generate_image').description.startswith('Retired raw-prompt API')
    assert runtime['status'] == 'stopped'


def test_health_exposes_loaded_identity_without_secrets(monkeypatch):
    configure(monkeypatch)
    app = create_app(Settings(PLUGIN, None))
    with TestClient(app) as client:
        response = client.get('/health')
        assert response.status_code == 200
        assert response.json()['identity']['pluginRoot'] == str(PLUGIN.resolve())
        assert 'SENTINEL_PRIVATE_TOKEN' not in response.text
    assert app.state.runtime_identity['status'] == 'stopped'


def test_health_cannot_claim_loaded_before_lifespan(monkeypatch):
    configure(monkeypatch)
    app = create_app(Settings(PLUGIN, None))
    client = TestClient(app)
    response = client.get('/health')
    assert response.status_code == 503
    assert response.json()['status'] == 'starting'
    assert 'identity' not in response.json()
    client.close()
