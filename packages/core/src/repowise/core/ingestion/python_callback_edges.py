"""Python handlers a class stores through a setter and calls later.

``adapter.set_message_handler(runner.handle)`` stores ``runner.handle`` in
``self._message_handler``, and ``await self._message_handler(event)`` inside the
adapter runs it. Neither line names the other, so the call graph stops at the
attribute. This post-pass joins the method that calls the attribute to the
function the setter received, as a ``calls`` edge with
``resolution_origin="callback_binding"`` and a confidence below any written call.

Every link must be unambiguous, or no edge is emitted:

- the setter is a method whose body stores one parameter in ``self.<attr>``;
- the class that declares the setter calls ``self.<attr>(...)`` in a method;
- the setter name belongs to exactly one class, unless the call site's own
  graph edge names the setter;
- the argument is a function reference (``obj.method`` / ``name``), or a call to
  a method of the caller's class that returns ``self.<method>``, and the name
  resolves to exactly one symbol the call site already references or calls.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Any

import structlog

from .python_local_refs import parse_python_module

log = structlog.get_logger(__name__)

CALLBACK_ORIGIN = "callback_binding"
#: Below every written call (0.9+): the edge is inferred from a store, not read.
_CALLBACK_CONFIDENCE = 0.6

# (class id, attribute) -> setter names / invoker method ids
_Key = tuple[str, str]


def resolve_callback_bindings(graph: Any, parsed_files: dict[str, Any]) -> int:
    """Add ``calls`` edges from attribute invokers to the functions their setters received."""
    modules = _python_modules(parsed_files)
    test_paths = {
        path for path, parsed in parsed_files.items() if getattr(getattr(parsed, "file_info", None), "is_test", False)
    }
    setters: dict[str, list[_Key]] = defaultdict(list)
    invokers: dict[_Key, list[tuple[str, int]]] = defaultdict(list)
    returns_ref: dict[str, set[str]] = {}
    for path, tree in modules.items():
        _collect_class_facts(path, tree, setters, invokers, returns_ref)

    added = 0
    for path, tree in modules.items():
        for caller_id, setter_name, arg_names in _setter_calls(path, tree, setters, returns_ref):
            key = _setter_key(graph, caller_id, setter_name, setters, test_paths, path in test_paths)
            if key is None or key not in invokers:
                continue
            for arg_name in arg_names:
                target = _referenced_target(graph, caller_id, arg_name)
                if target is None:
                    continue
                for invoker_id, line in invokers[key]:
                    if invoker_id not in graph or invoker_id == target or graph.has_edge(invoker_id, target):
                        continue
                    graph.add_edge(
                        invoker_id,
                        target,
                        edge_type="calls",
                        call_lines=[line],
                        confidence=_CALLBACK_CONFIDENCE,
                        resolution_origin=CALLBACK_ORIGIN,
                    )
                    added += 1
    log.info("callback_binding_edges", added=added, setters=sum(len(v) for v in setters.values()))
    return added


def _python_modules(parsed_files: dict[str, Any]) -> dict[str, ast.Module]:
    modules: dict[str, ast.Module] = {}
    for path, parsed in parsed_files.items():
        info = getattr(parsed, "file_info", None)
        if info is None or info.language != "python":
            continue
        try:
            source = Path(info.abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tree = parse_python_module(source)
        if tree is not None:
            modules[path] = tree
    return modules


def _collect_class_facts(
    path: str,
    tree: ast.Module,
    setters: dict[str, list[_Key]],
    invokers: dict[_Key, list[tuple[str, int]]],
    returns_ref: dict[str, set[str]],
) -> None:
    for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        class_id = f"{path}::{cls.name}"
        for method in _methods(cls):
            method_id = f"{class_id}::{method.name}"
            attr = _stored_parameter(method)
            if attr is not None:
                setters[method.name].append((class_id, attr))
            for attr_name, line in _self_attribute_calls(method):
                invokers[(class_id, attr_name)].append((method_id, line))
            refs = _returned_self_methods(method)
            if refs:
                returns_ref[method_id] = refs


def _methods(cls: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _stored_parameter(method: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """``self.<attr>`` that the method assigns from one of its parameters, when it is the only store."""
    params = {arg.arg for arg in method.args.args[1:]}
    stores: set[str] = set()
    for node in ast.walk(method):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if not (isinstance(value, ast.Name) and value.id in params):
            continue
        stores.update(
            target.attr
            for target in targets
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"
        )
    return next(iter(stores)) if len(stores) == 1 else None


def _self_attribute_calls(method: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, int]]:
    return [
        (node.func.attr, node.lineno)
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    ]


def _returned_self_methods(method: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names in ``return self.<name>`` statements of the method's own body (nested functions excluded)."""
    found: set[str] = set()
    stack: list[ast.AST] = list(method.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        ):
            found.add(node.value.attr)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _setter_calls(
    path: str,
    tree: ast.Module,
    setters: dict[str, list[_Key]],
    returns_ref: dict[str, set[str]],
) -> list[tuple[str, str, list[str]]]:
    """``(caller id, setter name, argument names)`` per setter call site."""
    found: list[tuple[str, str, list[str]]] = []
    for scope_id, scope, class_id in _scopes(path, tree):
        for node in ast.walk(scope):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in setters
                and len(node.args) == 1
            ):
                continue
            arg = node.args[0]
            names: list[str] = []
            if isinstance(arg, ast.Attribute):
                names = [arg.attr]
            elif isinstance(arg, ast.Name):
                names = [arg.id]
            elif (
                isinstance(arg, ast.Call)
                and class_id is not None
                and isinstance(arg.func, ast.Attribute)
                and isinstance(arg.func.value, ast.Name)
                and arg.func.value.id == "self"
            ):
                names = sorted(returns_ref.get(f"{class_id}::{arg.func.attr}", ()))
            if names:
                found.append((scope_id, node.func.attr, names))
    return found


def _scopes(path: str, tree: ast.Module):
    """``(symbol id, node, class id)`` for module functions and class methods."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield f"{path}::{node.name}", node, None
        elif isinstance(node, ast.ClassDef):
            class_id = f"{path}::{node.name}"
            for method in _methods(node):
                yield f"{class_id}::{method.name}", method, class_id


def _setter_key(
    graph: Any,
    caller_id: str,
    setter_name: str,
    setters: dict[str, list[_Key]],
    test_paths: set[str],
    caller_is_test: bool,
) -> _Key | None:
    candidates = setters.get(setter_name, [])
    if not caller_is_test:
        # A test double that redeclares the setter must not make production wiring ambiguous.
        candidates = [key for key in candidates if key[0].split("::", 1)[0] not in test_paths]
    if caller_id in graph:
        named = [
            key for key in candidates if graph.has_edge(caller_id, f"{key[0]}::{setter_name}")
        ]
        if len(named) == 1:
            return named[0]
    return candidates[0] if len(candidates) == 1 else None


def _referenced_target(graph: Any, caller_id: str, name: str) -> str | None:
    """The one symbol named *name* that *caller_id* (or the factory it calls) already reaches."""
    if caller_id not in graph:
        return None
    hits = {
        target
        for _, target, data in graph.out_edges(caller_id, data=True)
        if data.get("edge_type") in ("references", "calls") and target.rsplit("::", 1)[-1] == name
    }
    if not hits:
        class_id = caller_id.rsplit("::", 1)[0]
        own = f"{class_id}::{name}"
        hits = {own} if own in graph else set()
    return next(iter(hits)) if len(hits) == 1 else None
