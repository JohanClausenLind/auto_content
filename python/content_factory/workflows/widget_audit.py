"""Which of a node's declared widgets its stage executor actually reads."""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

from content_factory.workflows.catalog import node_catalog

STAGES_PATH = Path(__file__).resolve().parent / "stages.py"

BASE_READERS: frozenset[str] = frozenset(
    {"_param", "_param_int", "_param_float", "_param_bool", "_param_size"}
)
"""The primitives, each taking the widget key as its second positional argument. Everything else
that reads a widget is derived from these rather than listed here."""

WIDGET_EXEMPTIONS: dict[str, dict[str, str]] = {
    "research": {
        "depth": "the research executor returns a committed fixture, and no depth of search"
        " changes a fixture. Binds when the real ingest/search executor lands (priority 2)."
    },
    "compile_artboards": {
        "format": "the artboard comes from a committed fixture bundle whose size is already the"
        " deliverable's aspect. Binds when artboards are compiled from the story rather than"
        " loaded."
    },
}
"""``stage -> widget -> why it is declared and read by nothing.``"""

_Func = ast.FunctionDef | ast.AsyncFunctionDef


@lru_cache(maxsize=1)
def _functions() -> dict[str, _Func]:
    tree = ast.parse(STAGES_PATH.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, _Func)}


@lru_cache(maxsize=1)
def _module_tables() -> dict[str, frozenset[str]]:
    """``module constant -> every string literal inside it``."""
    tree = ast.parse(STAGES_PATH.read_text(encoding="utf-8"))
    out: dict[str, frozenset[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        else:
            continue
        if value is None:
            continue
        strings = {
            n.value
            for n in ast.walk(value)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = frozenset(strings)
    return out


def _positional(fn: _Func) -> list[str]:
    return [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]


@lru_cache(maxsize=1)
def readers() -> dict[str, int]:
    """``function name -> which positional argument is the widget key``."""
    funcs = _functions()
    found: dict[str, int] = dict.fromkeys(BASE_READERS, 1)
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in found:
                continue
            params = _positional(fn)
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                    continue
                index = found.get(call.func.id)
                if index is None or len(call.args) <= index:
                    continue
                key = call.args[index]
                if isinstance(key, ast.Name) and key.id in params:
                    found[name] = params.index(key.id)
                    changed = True
                    break
    return found


def _closure_readers(fn: ast.AST) -> frozenset[str]:
    """Nested defs that forward to a reader, so their first argument is the key."""
    known = readers()
    out: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, _Func) or node is fn:
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in known
            ):
                out.add(node.name)
    return frozenset(out)


def _loop_table(fn: ast.AST, variable: str) -> frozenset[str]:
    """The strings of the module constant a ``for`` loop binding ``variable`` iterates over."""
    tables = _module_tables()
    for node in ast.walk(fn):
        if not isinstance(node, ast.For):
            continue
        bound = {t.id for t in ast.walk(node.target) if isinstance(t, ast.Name)}
        if variable in bound and isinstance(node.iter, ast.Name):
            return tables.get(node.iter.id, frozenset())
    return frozenset()


def _keys_read(name: str, seen: frozenset[str] = frozenset()) -> frozenset[str]:
    funcs = _functions()
    if name in seen or name not in funcs:
        return frozenset()
    fn = funcs[name]
    seen = seen | {name}
    closures = _closure_readers(fn)
    known = readers()
    out: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            index = known.get(func.id)
            if index is not None and len(node.args) > index:
                key = node.args[index]
                if isinstance(key, ast.Constant):
                    out.add(str(key.value))
                elif isinstance(key, ast.Name):
                    out |= _loop_table(fn, key.id)
            elif func.id in closures and node.args and isinstance(node.args[0], ast.Constant):
                out.add(str(node.args[0].value))
            elif func.id in funcs:
                out |= _keys_read(func.id, seen)
        elif isinstance(func, ast.Attribute) and func.attr == "get":
            owner = func.value
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "params"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                out.add(str(node.args[0].value))
    return frozenset(out)


@lru_cache(maxsize=1)
def executor_names() -> dict[str, str]:
    """``stage value -> executor function name``, read off the ``STAGE_EXECUTORS`` table."""
    source = STAGES_PATH.read_text(encoding="utf-8")
    block = re.search(r"STAGE_EXECUTORS = \{(.*?)\n\}", source, re.S)
    if block is None:  # pragma: no cover - the table is not optional
        msg = f"{STAGES_PATH} has no STAGE_EXECUTORS table"
        raise RuntimeError(msg)
    return dict(re.findall(r"\n\s*Stage\.(\w+): (\w+),", block.group(1)))


def widget_reads() -> dict[str, frozenset[str]]:
    """``stage value -> every widget key its executor reads`` (including through helpers)."""
    return {stage: _keys_read(fn) for stage, fn in executor_names().items()}


def unread_widgets() -> dict[str, tuple[str, ...]]:
    """``stage value -> declared widgets nothing reads``, exemptions removed. Empty is the pass."""
    reads = widget_reads()
    catalog = node_catalog()
    out: dict[str, tuple[str, ...]] = {}
    for stage, read in reads.items():
        declared = set(catalog.get(stage, {}).get("widgets", ()))
        dead = declared - read - set(WIDGET_EXEMPTIONS.get(stage, {}))
        if dead:
            out[stage] = tuple(sorted(dead))
    return out


def stale_exemptions() -> dict[str, tuple[str, ...]]:
    """Exemptions that no longer apply, so the table cannot rot into a list of stale excuses."""
    reads = widget_reads()
    catalog = node_catalog()
    out: dict[str, tuple[str, ...]] = {}
    for stage, exempt in WIDGET_EXEMPTIONS.items():
        declared = set(catalog.get(stage, {}).get("widgets", ()))
        stale = {k for k in exempt if k in reads.get(stage, frozenset()) or k not in declared}
        if stale:
            out[stage] = tuple(sorted(stale))
    return out


def undeclared_reads() -> dict[str, tuple[str, ...]]:
    """``stage value -> keys the executor reads that its node never declares``."""
    reads = widget_reads()
    catalog = node_catalog()
    out: dict[str, tuple[str, ...]] = {}
    for stage, read in reads.items():
        node = catalog.get(stage)
        if node is None:
            continue  # not a canvas node; nothing declares its keys by design
        missing = read - set(node.get("widgets", ()))
        if missing:
            out[stage] = tuple(sorted(missing))
    return out
