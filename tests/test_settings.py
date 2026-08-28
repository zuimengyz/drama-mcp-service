from __future__ import annotations

from pathlib import Path

from drama_mcp_service.settings import Settings


def test_relative_plugin_paths_are_resolved_from_project_root(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    project_root = Path(__file__).resolve().parents[1]
    workspace_root = project_root.parent
    monkeypatch.chdir(project_root / "src")
    monkeypatch.setenv("DRAMA_PLUGIN_ROOT", "../drama-plugin/plugin")
    monkeypatch.setenv(
        "DRAMA_PLUGIN_CONFIG",
        "../drama-plugin/plugin/config/drama-service-http.example.yaml",
    )

    settings = Settings.from_environment()

    assert settings.plugin_root == workspace_root / "drama-plugin" / "plugin"
    assert settings.plugin_config == (
        workspace_root
        / "drama-plugin"
        / "plugin"
        / "config"
        / "drama-service-http.example.yaml"
    )
