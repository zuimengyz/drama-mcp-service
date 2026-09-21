"""Allowlisted, startup-captured diagnostics; never expose environment or credentials."""
from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any

from drama_mcp_service.settings import Settings

MODULES = (
    'drama_mcp_service.server', 'drama_mcp_service.settings',
    'drama_mcp_service.runtime_identity', 'drama_mcp_service.adapter',
    'drama_plugin.plugin', 'drama_plugin.config.loader',
    'drama_plugin.config.video_route', 'drama_plugin.hosts.route_production',
    'drama_plugin.hosts.http_video', 'drama_plugin.visual.video_prompt',
    'drama_plugin.visual_medium', 'drama_plugin.contracts.visual_medium',
    'drama_plugin.tools.catalog',
    'drama_plugin.context.rhythm', 'drama_plugin.contracts.context',
    'drama_plugin.contracts.audio', 'drama_plugin.providers.speech.role_dubbing',
    'drama_plugin.exceptions',
)


def capture_identity(settings: Settings, plugin: Any) -> dict[str, Any]:
    files = {}
    for name in MODULES:
        module = importlib.import_module(name)
        assert module.__file__ is not None
        path = Path(module.__file__).resolve()
        files[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    config_path = settings.plugin_config.resolve() if settings.plugin_config else None
    return {
        'pid': os.getpid(), 'initializedAt': datetime.now(timezone.utc).isoformat(),
        'pythonExecutable': sys.executable, 'workingDirectory': str(Path.cwd()),
        'pluginRoot': str(plugin.root.resolve()),
        'settingsPluginRoot': str(settings.plugin_root.resolve()),
        'pluginConfigPath': str(config_path) if config_path else None,
        'configFileSha256': hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path else None,
        'mcpSourceRoot': str(Path(__file__).resolve().parent),
        'listeningHost': settings.host, 'listeningPort': settings.port,
        'sourceFilesAtInitialization': files,
        'fingerprintSemantics': 'Source files captured by this process at Plugin initialization; not live disk health checks.',
        'rhythmSource': plugin.config.rhythm_source,
        'rhythmSpeed': plugin.config.rhythm_speed,
        'videoRoutePolicy': plugin.config.video_route_policy.model_dump(mode='json'),
        'providerModes': plugin.config.providers.model_dump(mode='json'),
    }
