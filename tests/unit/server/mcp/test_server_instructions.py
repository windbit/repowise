"""The server ``instructions`` string decides whether an agent reaches for the tools.

Hosts that defer tool schemas show only tool names plus this string, and some
truncate long instructions, so the guidance that matters is pinned here.
"""

from __future__ import annotations

from repowise.server.mcp_server import mcp

# Well under the 2,000 chars some hosts keep.
_MAX_INSTRUCTIONS_CHARS = 1500


def test_instructions_fit_the_length_cap() -> None:
    assert len(mcp.instructions) < _MAX_INSTRUCTIONS_CHARS


def test_instructions_say_when_each_tool_replaces_a_text_search() -> None:
    text = mcp.instructions
    assert "search_codebase" in text and "`lines`" in text
    assert 'include=["references"]' in text
    assert "get_answer" in text
    assert "get_risk" in text and "get_change_risk" in text and "get_why" in text
    assert "repowise update --working-tree" in text


def test_instructions_keep_the_no_index_and_workspace_guidance() -> None:
    text = mcp.instructions
    assert "repowise init --yes" in text
    assert "do not run it yourself" in text
    assert "get_architecture" in text and "get_blast_radius" in text


def test_handshake_reports_the_repowise_version_not_the_sdk_version() -> None:
    from repowise.server import __version__

    options = mcp._mcp_server.create_initialization_options()
    assert options.server_name == "repowise"
    assert options.server_version == __version__


def test_workspace_instructions_name_the_repos_and_the_cross_repo_entry(monkeypatch) -> None:
    from types import SimpleNamespace

    from repowise.server.mcp_server import _server

    config = SimpleNamespace(repos=[SimpleNamespace(alias="console"), SimpleNamespace(alias="litellm")])
    monkeypatch.setattr(_server, "_detect_workspace", lambda _path: ("/ws", config, "console"))
    try:
        _server._describe_served_repos("/ws/console", workspace_mode=True)
        text = mcp.instructions
        assert "2 repositories of one workspace: console, litellm" in text
        assert "cross_repo_impact" in text and 'repo="all"' in text
    finally:
        _server._describe_served_repos(None, workspace_mode=False)
    assert mcp.instructions == _server._INSTRUCTIONS
