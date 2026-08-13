# Drama MCP Service

`drama-mcp-service` is the thin Host Protocol Adapter between an MCP host and
the independent Drama Plugin. The Plugin tool registry remains the only source
of tool names, descriptions, input schemas, handlers, and provider routing.

## Local setup

Python 3.12 is required. From this directory:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ../drama-plugin/plugin -e '.[dev]'
```

The local Python interpreter may be supplied by the existing Plugin virtual
environment when `python3.12` is not installed globally.

## Run the complete stack

1. Start Java Drama Service with `DRAMA_TOOL_SECRET` configured.
2. Export the same value only as the Plugin provider tokens:
   `DRAMA_PLUGIN_SERVICE_MEMORY_API_TOKEN`,
   `DRAMA_PLUGIN_SERVICE_ASSET_API_TOKEN`, and
   `DRAMA_PLUGIN_SERVICE_MEDIA_API_TOKEN`.
3. Set `DRAMA_PLUGIN_ROOT` and `DRAMA_PLUGIN_CONFIG` as shown in
   `.env.example`.
4. Run `.venv/bin/drama-mcp-service`.
5. Point the MCP host at `http://127.0.0.1:8765/mcp` and run `tools/list`.
6. Call a canonical tool such as `work.list_works`.

The checked-in Plugin Host configuration is
`../drama-plugin/plugin/.mcp.json`; it names the formal server `drama-tools`
and uses the URL above. Restart/reinstall the local Plugin snapshot after a
source checkout change because an already installed Codex cache will retain
its previous MCP server name until refreshed.

The MCP schemas never expose Java URLs, endpoint paths, or provider secrets.
The browser/frontend must never receive provider tokens. Unit tests use the
Plugin's default Mock/Local routing and require no Java or MySQL service.
New Plugin tools, including `media.import_media` and `media.resolve_media`, are
discovered automatically; this project contains no media-specific wrapper.

## Verify

```bash
.venv/bin/python -m pytest -ra
.venv/bin/python -m mypy src/drama_mcp_service
```

With the full stack running, execute
`.venv/bin/python integration/run_mcp_e2e.py`. It is intentionally excluded
from regular pytest because it creates temporary `E2E_B03_` cloud data.
