from __future__ import annotations

import os
from pathlib import Path

from drama_mcp_service.settings import load_project_environment


def test_dotenv_load_preserves_windows_path_and_process_precedence(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DRAMA_MCP_HOST=from-dotenv\n"
        "DRAMA_PLUGIN_MEDIA_IMPORT_ALLOWED_ROOTS=D:\\home\\AI\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DRAMA_MCP_HOST", "from-process")
    monkeypatch.delenv("DRAMA_PLUGIN_MEDIA_IMPORT_ALLOWED_ROOTS", raising=False)

    load_project_environment(env_file)

    assert os.environ["DRAMA_MCP_HOST"] == "from-process"
    assert os.environ["DRAMA_PLUGIN_MEDIA_IMPORT_ALLOWED_ROOTS"] == r"D:\home\AI"


def test_missing_dotenv_is_optional(tmp_path: Path) -> None:
    load_project_environment(tmp_path / "missing.env")
