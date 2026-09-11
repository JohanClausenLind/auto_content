"""Which of a node's declared widgets its stage executor actually reads.

A widget that looks bound and does nothing is a defect class in this repo, not a one-off. It has
been found three times by rendering a film and noticing the knob did nothing: ``upscale_video``'s
resolution (a lane asking for 2160 got whatever the host was configured for), and
``generate_keyframes``' two, which were removed because the frame count comes from the control plan
and the seed from the generation lock. Each cost a render to find. The check that caught the second
pair was two hand-written assertions naming those nodes, which cannot catch the fourth.

So this reads ``workflows/stages.py`` as a syntax tree and answers the question for every node type
at once: for each stage, which widget keys reach a ``_param``-family call, or a
``ctx.params.get(...)``, anywhere in the executor or in a module-level helper it calls. Static
rather than dynamic, because a widget's whole problem is that the code path reading it may never
run in a test.

Three indirections are followed, because stages use all three:

* module-level helpers (``_anchor_lock``, ``_tts_executor``, ``_video_backend``);
* derived readers — a helper that forwards its own ``key`` parameter to a primitive, found to a
  fixpoint rather than listed, which is how ``_param_list`` and ``_param_int_list`` are picked up;
* nested closures like ``_tts_executor``'s ``param(key, default)``;
* table-driven loops: ``_control_passes`` reads six toggles by iterating a module constant, so the
  strings of that constant count as read.

``WIDGET_EXEMPTIONS`` is the escape hatch, and it takes a reason. A widget listed there is declared
on the canvas and read by nothing, on purpose and with the purpose written down — always because
the executor behind it is still a fixture stand-in, so the knob will bind when the executor is
real. It is not a place to park a knob that is simply broken, and :func:`stale_exemptions` fails
the entry once it stops applying.
"""

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
    """``module constant -> every string literal inside it``.

    For the table-driven stages: ``_control_passes`` reads six toggles by looping over
    ``_CONTROL_PASS_WIDGETS``, so the widget names live in a module constant rather than in the
    call. Taking every string in the table over-approximates within that one table, which is the
    right trade — the table *is* the declaration of what the stage reads, and a widget name absent
    from it is still reported.
    """
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
    """``function name -> which positional argument is the widget key``.

    Seeded with :data:`BASE_READERS` and grown to a fixpoint: a module-level function that forwards
    one of its own parameters to a reader as the key is itself a reader.
    """
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
    """Nested defs that forward to a reader, so their first argument is the key.

    ``_tts_executor`` does exactly this: a closure ``param(key, default)`` so the whole function
    reads either the context or the settings without repeating the condition eight times.
    """
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
    """Exemptions that no longer apply, so the table cannot rot into a list of stale excuses.

    An entry is stale when the widget is now read, or when it is no longer declared at all.
    """
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
    """``stage value -> keys the executor reads that its node never declares``.

    The mirror of :func:`unread_widgets`, and the half that was missing. A declared widget nothing
    reads is a control that looks bound and does nothing. A read key nothing declares is the
    opposite: reachable from `--set` and from nowhere else — invisible on the canvas, and rejected
    by `workflows/catalog.py` if a lane tries to freeze it.

    `generate_keyframes` is how this was found. `drift_profile`, `locked_min` and
    `style_delta_max` had always been read, the stage's own comment told the operator to reach for
    `--set spokes.drift_profile=uncalibrated`, and none of the three was declared anywhere. A run
    resumed without that flag met the mock-calibrated 0.92 that no real frame reaches and was
    BLOCKED at a measured 0.8491 (2026-09-10). They are declared now.

    **This is a report, not a gate, and the remaining entries are untriaged.** Two legitimate
    reasons to read an undeclared key are already visible in the code, and telling them apart from
    a real gap needs reading each one rather than a rule:

    - the runner injects it from a dedicated flag (`resolved_steps` writes `story`, `subject`,
      `planner` and `fixture_path` from `--story`, `--subject` and `--shots`);
    - the stage injects it itself before reading it back through the same path
      (`stage_lock_generation` writes `style` and `model` from the anchor's recorded marker, so
      that a lock can never name art direction the anchor was not drawn in).

    The test pins whatever this currently returns, so the list can shrink deliberately but cannot
    grow by accident.
    """
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
