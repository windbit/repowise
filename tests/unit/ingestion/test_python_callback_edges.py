"""A handler stored through a setter is reached from the method that calls it.

The tests weigh toward the refusals: an edge to the wrong handler is worse
than no edge, so every ambiguous link must emit nothing.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.python_callback_edges import CALLBACK_ORIGIN

_PARSER = ASTParser()

_ADAPTER = """\
class Adapter:
    def __init__(self):
        self._handler = None

    def set_handler(self, handler):
        self._handler = handler

    async def process(self, event):
        return await self._handler(event)
"""


def _build(tmp_path: Path, sources: dict[str, str]):
    builder = GraphBuilder(tmp_path)
    for rel, text in sources.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(abs_path),
            language="python",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(_PARSER.parse_file(info, text.encode("utf-8")))
    return builder.build()


def _callback_edges(graph) -> set[tuple[str, str]]:
    return {
        (u, v)
        for u, v, d in graph.edges(data=True)
        if d.get("resolution_origin") == CALLBACK_ORIGIN
    }


def test_a_method_reference_passed_to_a_setter_is_reached_from_the_invoker(tmp_path: Path) -> None:
    runner = """\
from adapter import Adapter


class Runner:
    async def handle(self, event):
        return event

    def start(self, adapter: Adapter):
        adapter.set_handler(self.handle)
"""
    graph = _build(tmp_path, {"adapter.py": _ADAPTER, "runner.py": runner})
    assert _callback_edges(graph) == {("adapter.py::Adapter::process", "runner.py::Runner::handle")}
    edge = graph["adapter.py::Adapter::process"]["runner.py::Runner::handle"]
    assert edge["edge_type"] == "calls"
    assert edge["confidence"] < 0.9


def test_a_factory_returning_a_method_reference_is_followed(tmp_path: Path) -> None:
    runner = """\
class Runner:
    async def handle(self, event):
        return event

    def _primary_handler(self):
        return self.handle

    def start(self, adapter):
        adapter.set_handler(self._primary_handler())
"""
    graph = _build(tmp_path, {"adapter.py": _ADAPTER, "runner.py": runner})
    assert ("adapter.py::Adapter::process", "runner.py::Runner::handle") in _callback_edges(graph)


def test_a_setter_name_two_classes_declare_links_nothing(tmp_path: Path) -> None:
    other = _ADAPTER.replace("class Adapter", "class OtherAdapter")
    runner = """\
class Runner:
    async def handle(self, event):
        return event

    def start(self, adapter):
        adapter.set_handler(self.handle)
"""
    graph = _build(tmp_path, {"adapter.py": _ADAPTER, "other.py": other, "runner.py": runner})
    assert _callback_edges(graph) == set()


def test_a_setter_storing_two_attributes_links_nothing(tmp_path: Path) -> None:
    adapter = """\
class Adapter:
    def set_handler(self, handler):
        self._handler = handler
        self._backup = handler

    async def process(self, event):
        return await self._handler(event)
"""
    runner = """\
class Runner:
    async def handle(self, event):
        return event

    def start(self, adapter):
        adapter.set_handler(self.handle)
"""
    graph = _build(tmp_path, {"adapter.py": adapter, "runner.py": runner})
    assert _callback_edges(graph) == set()


def test_a_written_call_is_left_as_it_was(tmp_path: Path) -> None:
    runner = """\
class Runner:
    async def handle(self, event):
        return event

    def start(self, adapter):
        adapter.set_handler(self.handle)
"""
    adapter = _ADAPTER + """\

    async def direct(self, runner):
        return await runner.handle(None)
"""
    graph = _build(tmp_path, {"adapter.py": adapter, "runner.py": runner})
    assert ("adapter.py::Adapter::direct", "runner.py::Runner::handle") not in _callback_edges(graph)
