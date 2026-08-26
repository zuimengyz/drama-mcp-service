from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def load_project_environment(env_file: Path = ENV_FILE) -> None:
    """Load host-local configuration without replacing process variables."""
    load_dotenv(dotenv_path=env_file, override=False)


def resolve_project_path(value: str) -> Path:
    """Resolve runtime paths relative to the MCP project, independent of cwd."""
    path = Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


@dataclass(frozen=True)
class Settings:
    plugin_root: Path
    plugin_config: Path | None
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_environment(cls) -> "Settings":
        load_project_environment()
        default_plugin = PROJECT_ROOT.parent / "drama-plugin" / "plugin"
        raw_root = os.environ.get("DRAMA_PLUGIN_ROOT")
        raw_config = os.environ.get("DRAMA_PLUGIN_CONFIG")
        plugin_root = resolve_project_path(raw_root) if raw_root else default_plugin
        plugin_config = resolve_project_path(raw_config) if raw_config else None
        return cls(
            plugin_root=plugin_root.resolve(),
            plugin_config=plugin_config,
            host=os.environ.get("DRAMA_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("DRAMA_MCP_PORT", "8765")),
        )
