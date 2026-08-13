from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_project_environment(env_file: Path = ENV_FILE) -> None:
    """Load host-local configuration without replacing process variables."""
    load_dotenv(dotenv_path=env_file, override=False)


@dataclass(frozen=True)
class Settings:
    plugin_root: Path
    plugin_config: Path | None
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_environment(cls) -> "Settings":
        load_project_environment()
        project_root = Path(__file__).resolve().parents[3]
        default_plugin = project_root / "drama-plugin" / "plugin"
        raw_root = os.environ.get("DRAMA_PLUGIN_ROOT")
        raw_config = os.environ.get("DRAMA_PLUGIN_CONFIG")
        plugin_root = Path(raw_root).expanduser() if raw_root else default_plugin
        plugin_config = Path(raw_config).expanduser() if raw_config else None
        return cls(
            plugin_root=plugin_root.resolve(),
            plugin_config=plugin_config.resolve() if plugin_config else None,
            host=os.environ.get("DRAMA_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("DRAMA_MCP_PORT", "8765")),
        )

