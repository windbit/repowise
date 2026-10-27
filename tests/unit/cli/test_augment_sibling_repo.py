"""A hook resolves the index of the repo a tool call touched, not only the session cwd's."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands.augment_cmd._shared import _cwd_for_tool_target


def _indexed(path: Path) -> Path:
    (path / ".repowise").mkdir(parents=True)
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    return path


def test_a_read_in_a_sibling_repo_moves_the_lookup_there(tmp_path: Path) -> None:
    console, gateway = _indexed(tmp_path / "console"), _indexed(tmp_path / "gateway")
    tool_input = {"file_path": str(gateway / "src" / "app.py")}
    assert _cwd_for_tool_target(tool_input, str(console)) == str(gateway.resolve())


def test_a_relative_grep_path_into_a_sibling_resolves_from_cwd(tmp_path: Path) -> None:
    console, gateway = _indexed(tmp_path / "console"), _indexed(tmp_path / "gateway")
    assert _cwd_for_tool_target({"path": "../gateway/src"}, str(console)) == str(gateway.resolve())


def test_a_target_in_the_session_repo_keeps_the_cwd(tmp_path: Path) -> None:
    console = _indexed(tmp_path / "console")
    cwd = str(console / "src")
    assert _cwd_for_tool_target({"file_path": str(console / "src" / "app.py")}, cwd) == cwd


def test_an_unindexed_or_missing_target_keeps_the_cwd(tmp_path: Path) -> None:
    console = _indexed(tmp_path / "console")
    (tmp_path / "plain").mkdir()
    assert _cwd_for_tool_target({"file_path": str(tmp_path / "plain" / "a.py")}, str(console)) == str(console)
    assert _cwd_for_tool_target({"file_path": str(tmp_path / "nope" / "a.py")}, str(console)) == str(console)
    assert _cwd_for_tool_target({"pattern": "x"}, str(console)) == str(console)
