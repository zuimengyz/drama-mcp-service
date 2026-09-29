import json

import pytest
from starlette.testclient import TestClient

from drama_mcp_service.server import create_app
from drama_mcp_service.settings import Settings
from test_protocol import force_mock_provider_modes, PLUGIN_ROOT
from test_runtime_ownership import validate


@pytest.mark.parametrize('value', ['', 'invalid', '-1', '0', '1.5', 'unlimited'])
def test_invalid_limit_fails_startup(monkeypatch, value):
    monkeypatch.setenv('DRAMA_MCP_MAX_REQUEST_BYTES', value)
    with pytest.raises(ValueError):
        create_app()


def test_limit_is_host_owned(tmp_path):
    env = tmp_path / 'limit.env'
    env.write_text('DRAMA_MCP_MAX_REQUEST_BYTES=8388608\n')
    assert validate('mcp-host', env).returncode == 0
    assert validate('drama-plugin', env).returncode == 1
    assert validate('drama-service', env).returncode == 1


@pytest.mark.parametrize('configured,limit', [(None, 4194304), ('8388608', 8388608), ('33554432', 33554432)])
@pytest.mark.parametrize('streamed', [False, True])
def test_actual_http_parser_limit(monkeypatch, configured, limit, streamed):
    force_mock_provider_modes(monkeypatch)
    monkeypatch.setenv('DRAMA_PLUGIN_ROOT', str(PLUGIN_ROOT))
    monkeypatch.delenv('DRAMA_PLUGIN_CONFIG', raising=False)
    if configured is None:
        monkeypatch.delenv('DRAMA_MCP_MAX_REQUEST_BYTES', raising=False)
    else:
        monkeypatch.setenv('DRAMA_MCP_MAX_REQUEST_BYTES', configured)
    assert Settings.from_environment().max_request_bytes == limit
    with TestClient(create_app(), base_url='http://127.0.0.1:8765') as client:
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        response = client.post('/mcp', headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                       'clientInfo': {'name': 'body-limit-test', 'version': '1'}}})
        assert response.status_code == 200
        headers['mcp-session-id'] = response.headers['mcp-session-id']
        client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        base = json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}).encode()
        for size in (4235649, limit, limit + 1):
            body = base + b' ' * (size - len(base))
            content = (body[i:i+65536] for i in range(0, len(body), 65536)) if streamed else body
            result = client.post('/mcp', headers=headers, content=content)
            if streamed:
                assert 'content-length' not in result.request.headers
            if size > limit:
                assert result.status_code == 413
            else:
                assert result.status_code == 200
                assert 'work.save_work' in result.text


@pytest.mark.parametrize('payload_size', [128, 9 * 1024 * 1024])
def test_whole_work_save_roundtrip_with_bounded_headroom(monkeypatch, payload_size):
    # In-memory provider only: exercise actual HTTP framing and tool dispatch.
    force_mock_provider_modes(monkeypatch)
    monkeypatch.setenv('DRAMA_PLUGIN_ROOT', str(PLUGIN_ROOT))
    monkeypatch.setenv('DRAMA_MCP_MAX_REQUEST_BYTES', '33554432')
    monkeypatch.delenv('DRAMA_PLUGIN_CONFIG', raising=False)
    with TestClient(create_app(), base_url='http://127.0.0.1:8765') as client:
        headers = {'Content-Type':'application/json', 'Accept':'application/json, text/event-stream'}
        response = client.post('/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':'initialize',
            'params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'save-size-test','version':'1'}}})
        headers['mcp-session-id'] = response.headers['mcp-session-id']
        client.post('/mcp', headers=headers, json={'jsonrpc':'2.0','method':'notifications/initialized'})
        def call(name, arguments):
            response = client.post('/mcp', headers=headers, json={'jsonrpc':'2.0','id':2,'method':'tools/call',
                'params':{'name':name,'arguments':arguments}})
            assert response.status_code == 200
            body = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
            result = body['result']; assert not result.get('isError')
            return result['structuredContent']
        created = call('work.create_work', {'title':'offline size fixture','content':{}})
        content = {'historicalEvidence':'x' * payload_size, 'productionRoute':{'fixture':'retained'}}
        saved = call('work.save_work', {'work_id':created['id'],'title':'offline size fixture','content':content})
        assert saved['content'] == content
        assert call('work.get_work', {'work_id':created['id']})['content'] == content
