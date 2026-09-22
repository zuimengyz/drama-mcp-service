"""MCP schema/coercion and the actual Plugin compiler; no remote calls."""
import json
from pathlib import Path
import pytest
from drama_plugin import DramaPlugin
from drama_mcp_service.adapter import PluginToolAdapter

ROOT=Path(__file__).resolve().parents[2]/'drama-plugin'/'plugin'

@pytest.mark.asyncio
async def test_literary_source_real_mcp_contract():
    source=json.loads((ROOT/'tests/fixtures/synthetic-literary.json').read_text())
    async with DramaPlugin.load(ROOT) as plugin:
        adapter=PluginToolAdapter(plugin)
        tool=next(t for t in adapter.list_tools() if t.name=='source.prepare_screenplay')
        assert 'LiteraryPackage' in tool.input_schema['$defs']
        result=await adapter.call_tool(tool.name,{'request':{'source':source,'jurisdiction':'TEST','intendedUse':'STUDY'}})
        assert not result.is_error
        output=result.structured_content
        assert output['sourceType']=='LITERARY' and output['sourceMap']
        assert output['rightsGate']['authorized'] is False
        source['cinema']['expressions'][0]['channels']=['VOICE_OVER']
        rejected=await adapter.call_tool(tool.name,{'request':{'source':source,'jurisdiction':'TEST'}})
        assert rejected.is_error
        assert rejected.structured_content['error']['code']=='INVALID_ARGUMENT'
