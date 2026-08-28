from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from drama_mcp_service.settings import Settings

WORKSPACE = Path(__file__).resolve().parents[2]
VALIDATOR = WORKSPACE / "scripts" / "runtime-env-ownership.py"


def validate(owner: str, path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VALIDATOR), owner, str(path)], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def test_component_env_allowlists_reject_cross_boundary_variables(tmp_path: Path) -> None:
    valid = {
        "mcp-host": "DRAMA_MCP_PORT=8765\nDRAMA_PLUGIN_ROOT=/plugin\n",
        "drama-plugin": "DRAMA_PLUGIN_PROVIDER_MEDIA_MODE=http\nFISH_TTS_MODEL=s2-pro\n",
        "drama-service": "DB_HOST=db.internal\nDRAMA_MEDIA_STORAGE_ENDPOINT=http://storage.internal\n",
    }
    forbidden = {
        "mcp-host": "DRAMA_MEDIA_STORAGE_ENDPOINT=http://storage.internal\n",
        "drama-plugin": "DB_PASSWORD=forbidden\n",
        "drama-service": "FISH_AUDIO_API_KEY=forbidden\n",
    }
    for owner, body in valid.items():
        path = tmp_path / f"{owner}.env"
        path.write_text(body, encoding="utf-8")
        assert validate(owner, path).returncode == 0
    for owner, body in forbidden.items():
        path = tmp_path / f"{owner}-forbidden.env"
        path.write_text(body, encoding="utf-8")
        assert validate(owner, path).returncode == 1


def test_mcp_settings_and_plugin_location_need_no_storage_or_database_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in list(os.environ):
        if name.startswith(("DB_", "MYSQL_", "MINIO_", "S3_", "DRAMA_MEDIA_STORAGE_")):
            monkeypatch.delenv(name, raising=False)
    settings = Settings.from_environment()
    assert settings.plugin_root.is_dir()
    assert settings.host == os.environ.get("DRAMA_MCP_HOST", "127.0.0.1")


def test_mcp_active_source_has_no_database_or_storage_configuration_reads() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
    for forbidden in ("MINIO_", "MYSQL_", "S3_ENDPOINT", "S3_ACCESS", "S3_SECRET",
                      "DRAMA_MEDIA_STORAGE_", "DB_HOST", "DB_PASSWORD"):
        assert forbidden not in source
