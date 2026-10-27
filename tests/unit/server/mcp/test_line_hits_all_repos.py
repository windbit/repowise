"""repo="all" reads lines from every workspace repo, tagged with the repo they came from."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import _line_hits, _state


def _registry(monkeypatch, aliases):
    monkeypatch.setattr(_state, "_registry", SimpleNamespace(get_all_aliases=lambda: aliases))


@pytest.mark.asyncio
async def test_lines_from_each_repo_carry_its_alias(monkeypatch) -> None:
    _registry(monkeypatch, ["console", "runtime"])
    found = {"console": [], "runtime": [{"path": "gateway/env.py", "line": 7, "kind": "reference", "text": "X"}]}

    async def one_repo(part, query, mode, names, repo):
        part["lines"] = found[repo]
        part["complete"] = True

    monkeypatch.setattr(_line_hits, "_attach", one_repo)
    response: dict = {"results": [], "grep_hint": "No indexed match"}
    await _line_hits._attach_all(response, "TELEGRAM_ALLOWED_USERS", "symbol", set())
    assert response["lines"] == [{"repo": "runtime", "path": "gateway/env.py", "line": 7, "kind": "reference", "text": "X"}]
    assert response["complete"] is True
    assert "grep_hint" not in response


@pytest.mark.asyncio
async def test_a_failing_repo_marks_the_lines_incomplete(monkeypatch) -> None:
    _registry(monkeypatch, ["console", "runtime"])

    async def one_repo(part, query, mode, names, repo):
        if repo == "runtime":
            raise OSError("gone")
        part["lines"] = [{"path": "a.ts", "line": 1, "kind": "reference", "text": "X"}]

    monkeypatch.setattr(_line_hits, "_attach", one_repo)
    response: dict = {"results": []}
    await _line_hits._attach_all(response, "X", "symbol", set())
    assert response["complete"] is False
    assert response["reasons"] == ["runtime: line search failed"]


@pytest.mark.asyncio
async def test_no_lines_anywhere_leaves_the_response_untouched(monkeypatch) -> None:
    _registry(monkeypatch, ["console"])

    async def one_repo(part, query, mode, names, repo):
        return None

    monkeypatch.setattr(_line_hits, "_attach", one_repo)
    response: dict = {"results": []}
    await _line_hits._attach_all(response, "X", "symbol", set())
    assert response == {"results": []}
