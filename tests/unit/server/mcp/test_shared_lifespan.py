"""Over streamable HTTP every client session enters the lifespan; the state it sets is shared."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from repowise.server.mcp_server import _server


@pytest.fixture
def lifespan_log(monkeypatch):
    log: list[str] = []

    @asynccontextmanager
    async def fake_lifespan(server):
        log.append("up")
        yield
        log.append("down")

    monkeypatch.setattr(_server, "_lifespan", fake_lifespan)
    monkeypatch.setattr(_server, "_lifespan_users", 0)
    return log


@pytest.mark.asyncio
async def test_a_closing_session_keeps_the_state_of_the_sessions_still_open(lifespan_log) -> None:
    first = _server._shared_lifespan(_server.mcp)
    second = _server._shared_lifespan(_server.mcp)
    await first.__aenter__()
    await second.__aenter__()
    await first.__aexit__(None, None, None)
    assert lifespan_log == ["up"]
    await second.__aexit__(None, None, None)
    assert lifespan_log == ["up", "down"]


@pytest.mark.asyncio
async def test_a_session_after_the_last_one_closed_brings_the_state_up_again(lifespan_log) -> None:
    async with _server._shared_lifespan(_server.mcp):
        pass
    async with _server._shared_lifespan(_server.mcp):
        pass
    assert lifespan_log == ["up", "down", "up", "down"]
