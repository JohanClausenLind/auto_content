"""Check an explainer plan against its schema and the planner prompt's own rules.

    uv run python scripts/check_explainer_plan.py PLAN.json [--schema SCHEMA.json] [--wps 2.4167]

The planner prompt (docs/prompts/explainer-planner/prompt.md) ends with a checklist the model is
asked to apply to itself. A model's self-check is not a check, so the rules that can be counted
are counted here: references, the transition chain, zoom direction, on-screen and in-view state,
update values, claim anchors, questions, pacing and duration. What cannot be counted (is the
explanation any good?) is left to the reviewer.

Prints a JSON report. Exit status 1 when there is any error, 0 otherwise; warnings do not fail.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / "docs/prompts/explainer-planner/schema.json"
# A structural move changes the diagram's structure (prompt <pacing>); reveal, highlight,
# update, trace and dismiss do not count.
STRUCTURAL_OPS = {"zoom_in", "zoom_out", "split", "morph", "merge", "branch", "compare", "fault"}
ENTERING_OPS = {"reveal", "split", "morph", "merge", "trace"}
NEEDS_VISIBLE = {"highlight", "update", "fault", "dismiss", "compare", "branch"}
SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
MARKUP = re.compile(r"[()\[\]*_`#<>{}]")
WPS = 145 / 60  # the repo's planning rate, 145 spoken words per minute
VALUE_OPS = {"update", "highlight", "fault", "trace"}
DATA_PRIMITIVES = {"memory_row", "register", "state_table", "graph_line", "graph_bar", "timeline"}
UPDATABLE = DATA_PRIMITIVES | {"data_packet", "code_block", "callout", "label"}
CELL_INDEX = re.compile(r"^[0-9]+(,[0-9]+)*$")
MULTI_TARGET_OPS = {"merge", "compare"}
FIELD_WORD_LIMIT = 20  # goal, question, mechanism and note stay under 20 words


def _words(text: str) -> int:
    return len(text.split())


def _first_key(content: str) -> str:
    """The name key of a data object's first cell ("count" in "count: 5"), or ""."""
    first = content.split(" | ")[0]
    return first.split(":", 1)[0].strip() if ":" in first else ""


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _ancestors(oid: str, parent: dict[str, str]) -> list[str]:
    out: list[str] = []
    seen = {oid}
    cur = parent.get(oid, "")
    while cur and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = parent.get(cur, "")
    return out


def check_references(plan: dict, r: Report) -> dict[str, dict]:
    layers = plan["layers"]
    colors = [c["role"] for c in plan["color_semantics"]]
    if len(set(colors)) != len(colors):
        r.err("color_semantics declares the same role twice")
    for step in plan["explanation_spine"]:
        if step["layer"] not in layers:
            r.err(f"explanation_spine layer {step['layer']!r} is not in layers")
    objects: dict[str, dict] = {}
    for obj in plan["objects"]:
        oid = obj["id"]
        if oid in objects:
            r.err(f"object id {oid!r} declared twice")
        objects[oid] = obj
        if not SNAKE.match(oid):
            r.err(f"object id {oid!r} is not snake_case")
    parent = {oid: o["parent"] for oid, o in objects.items()}
    labels: dict[str, str] = {}
    for oid, obj in objects.items():
        if obj["parent"] and obj["parent"] not in objects:
            r.err(f"object {oid}: parent {obj['parent']!r} is not declared")
        if oid in _ancestors(oid, parent) or obj["parent"] == oid:
            r.err(f"object {oid}: parent chain is a cycle")
        if obj["layer"] not in layers:
            r.err(f"object {oid}: layer {obj['layer']!r} is not in layers")
        if obj["color_role"] not in colors:
            r.err(f"object {oid}: color_role {obj['color_role']!r} is not in color_semantics")
        if obj["primitive"] == "arrow":
            for end in ("from", "to"):
                if obj[end] not in objects:
                    r.err(f"arrow {oid}: {end} {obj[end]!r} is not a declared object")
        elif obj["from"] or obj["to"]:
            r.err(f"object {oid}: from/to are only for arrows")
        label = obj["label"].strip().lower()
        if label and label in labels and obj["primitive"] != "arrow":
            r.warn(f"objects {labels[label]} and {oid} share the label {obj['label']!r}")
        labels.setdefault(label, oid)
    return objects


def _check_cells(
    where: str, op: str, targets: list[str], value: str, content: dict[str, str], r: Report
) -> None:
    """A cell value on highlight, fault or trace: 0-based indices into one data object."""
    if op == "trace":
        cell_obj = targets[-1] if targets[-1] in content else None
        if cell_obj is None:
            r.err(f"{where}: a trace value needs a data object as the last target")
    else:
        data_targets = [t for t in targets if t in content]
        cell_obj = data_targets[0] if len(data_targets) == 1 else None
        if cell_obj is None:
            r.err(f"{where}: a {op} value needs exactly one data-object target")
    if not CELL_INDEX.match(value):
        r.err(f"{where}: value {value!r} is not a 0-based cell index list such as '3' or '2,5'")
        return
    if cell_obj is not None:
        cells = len(content[cell_obj].split(" | "))
        bad = [i for i in map(int, value.split(",")) if i >= cells]
        if bad:
            r.err(f"{where}: cell index {bad} is out of range; {cell_obj!r} has {cells} cells")


def check_claims(plan: dict, objects: dict[str, dict], r: Report) -> None:
    sentences = {b["id"]: len(b["narration"]) for b in plan["beats"]}
    for k, c in enumerate(plan["claims_to_verify"]):
        where = f"claim {k} ({c['claim'][:40]!r})"
        if c["beat"] not in sentences:
            r.err(f"{where}: beat {c['beat']!r} does not exist")
            continue
        if c["sentence"] == -1:
            if c["object"] not in objects:
                r.err(f"{where}: an on-screen claim needs a declared object, got {c['object']!r}")
        elif c["sentence"] >= sentences[c["beat"]]:
            r.err(f"{where}: sentence {c['sentence']} is out of range for {c['beat']}")
        elif c["object"]:
            r.err(f'{where}: a spoken claim has object ""; use sentence -1 for on-screen facts')


def check_beats(plan: dict, objects: dict[str, dict], r: Report, wps: float) -> dict:
    beats = plan["beats"]
    layers = plan["layers"]
    parent = {oid: o["parent"] for oid, o in objects.items()}
    content = {oid: o["label"] for oid, o in objects.items() if o["primitive"] in DATA_PRIMITIVES}
    used: set[str] = set()
    types = [b["type"] for b in beats]
    if types[:2] != ["question_hook", "common_assumption"]:
        r.err(f"the first two beats must be question_hook, common_assumption; got {types[:2]}")
    if types[-1] != "takeaway":
        r.err(f"the last beat must be takeaway; got {types[-1]}")
    if "resolution" not in types:
        r.err("no resolution beat")
    elif types.index("resolution") > len(types) - 2:
        r.err("resolution must come before the takeaway")
    if "system_overview" not in types:
        r.err("no system_overview beat")
    if "edge_case" not in types:
        r.err("no edge_case beat")
    for t in ("question_hook", "common_assumption", "resolution", "takeaway"):
        if types.count(t) > 1:
            r.err(f"{t} appears {types.count(t)} times; the opening and closing happen once")

    visible: set[str] = set()
    # A fault mark lasts until its object is dismissed, replaced or updated (prompt <operations>).
    faulted: set[str] = set()
    sentence_cursor = 0
    word_cursor = 0
    change_sentences: list[int] = []
    structural_words: list[int] = [0]
    question_words: int | None = None
    overview_start: int | None = None
    total_est = 0
    for i, beat in enumerate(beats):
        bid = beat["id"]
        if bid != f"b{i + 1:02d}":
            r.err(f"beat {i + 1} has id {bid!r}, expected b{i + 1:02d}")
        if beat["layer"] not in layers:
            r.err(f"{bid}: layer {beat['layer']!r} is not in layers")
        frame = beat["frame"]
        if frame not in objects:
            r.err(f"{bid}: frame {frame!r} is not a declared object")
        used.add(frame)
        for field in ("goal", "question"):
            if not beat[field].strip():
                r.err(f"{bid}: {field} is empty")
        for field in ("goal", "question", "mechanism"):
            if _words(beat[field]) >= FIELD_WORD_LIMIT:
                r.warn(f"{bid}: {field} is {_words(beat[field])} words; keep it under 20")
        opening = beat["type"] in {"question_hook", "common_assumption"}
        if not opening and not beat["mechanism"].strip():
            r.err(f"{bid}: mechanism is empty on a {beat['type']} beat")
        narration = beat["narration"]
        for j, sentence in enumerate(narration):
            if MARKUP.search(sentence):
                r.warn(f"{bid} sentence {j}: brackets or markup in narration: {sentence[:60]!r}")
            if re.search(r"0x[0-9a-fA-F]+|\b[0-9A-F]{2}( [0-9A-F]{2})+\b", sentence):
                r.warn(f"{bid} sentence {j}: hex read aloud; it belongs in an on-screen object")
        n = len(narration)
        if not 2 <= n <= 6 and beat["type"] not in {"question_hook", "takeaway"}:
            r.warn(f"{bid}: {n} sentences (typical is 2-6)")
        words = sum(_words(s) for s in narration)
        spoken_s = words / wps
        if beat["est_seconds"] and abs(beat["est_seconds"] - spoken_s) > max(3, 0.3 * spoken_s):
            r.warn(
                f"{bid}: est_seconds {beat['est_seconds']} but {words} words is ~{spoken_s:.0f}s"
            )
        total_est += beat["est_seconds"]

        # Transition chain and zoom direction.
        t_in, t_out = beat["transition_in"], beat["transition_out"]
        if i == 0 and t_in != "cut":
            r.err(f"{bid}: the first beat's transition_in must be cut")
        if i == len(beats) - 1 and t_out != "cut":
            r.err(f"{bid}: the last beat's transition_out must be cut")
        if i > 0 and t_in == "cut":
            r.err(f"{bid}: cut is only for the first beat's transition_in")
        if i < len(beats) - 1 and t_out == "cut":
            r.err(f"{bid}: cut is only for the last beat's transition_out")
        if i > 0 and t_in != beats[i - 1]["transition_out"]:
            r.err(
                f"{bid}: transition_in {t_in!r} != previous transition_out"
                f" {beats[i - 1]['transition_out']!r}"
            )
        if i > 0 and frame in objects:
            prev = beats[i - 1]["frame"]
            if t_in == "zoom_in" and prev not in _ancestors(frame, parent):
                r.err(f"{bid}: zoom_in to {frame!r}, which is not inside {prev!r}")
            if t_in == "zoom_out" and frame not in _ancestors(prev, parent):
                r.err(f"{bid}: zoom_out to {frame!r}, which does not contain {prev!r}")
            if t_in not in {"zoom_in", "zoom_out", "cut"} and frame != prev:
                r.warn(f"{bid}: frame changes {prev!r} -> {frame!r} without a zoom")
        updates = beat["visual_updates"]
        in_view = {frame, *(o for o in objects if frame in _ancestors(o, parent))}
        if t_in not in {"zoom_in", "zoom_out", "cut"}:
            first = updates[0] if updates else None
            if not first or first["at_sentence"] != 0 or first["op"] != t_in:
                r.err(f"{bid}: transition_in {t_in!r} needs a first update at sentence 0 with it")
        if t_in in {"zoom_in", "zoom_out", "cut"}:
            visible.add(frame)
        if t_in in STRUCTURAL_OPS:
            structural_words.append(word_cursor)

        # Updates: anchors, references, on-screen state.
        last_at = -1
        change_sentences.append(sentence_cursor)
        for u in updates:
            at = u["at_sentence"]
            op, targets, into = u["op"], u["targets"], u["into"]
            where = f"{bid} update@{at} {op}"
            if not 0 <= at < n:
                r.err(f"{where}: at_sentence out of range (beat has {n} sentences)")
                continue
            if at < last_at:
                r.warn(f"{where}: updates are not in sentence order")
            last_at = at
            missing = [t for t in targets if t not in objects]
            if missing:
                r.err(f"{where}: targets not declared: {missing}")
            if op in {"morph", "merge"}:
                if into not in objects:
                    r.err(f"{where}: into {into!r} is not a declared object")
            elif into:
                r.err(f"{where}: into is only for morph and merge")
            used.update(targets)
            if into:
                used.add(into)
            if op == "update" and not u["value"].strip():
                r.err(f"{where}: update needs the new on-screen text in value")
            if u["value"] and op not in VALUE_OPS:
                r.err(f"{where}: value is only for update, highlight, fault and trace")
            if op == "update":
                for t in targets:
                    if t in objects and objects[t]["primitive"] not in UPDATABLE:
                        r.err(
                            f"{where}: {t!r} is a {objects[t]['primitive']}; update changes only"
                            " data objects, data_packets, code_blocks, callouts and labels"
                            " (status: a callout)"
                        )
                    if t in content:
                        old_key = _first_key(content[t])
                        if old_key and _first_key(u["value"]) != old_key:
                            r.warn(f"{where}: {t!r} loses its name key {old_key!r}")
                        content[t] = u["value"]
            elif u["value"] and op in VALUE_OPS:
                _check_cells(where, op, targets, u["value"], content, r)
            if op in MULTI_TARGET_OPS and len(targets) < 2:
                r.err(f"{where}: {op} needs two or more targets")
            outside = [t for t in [*targets, into] if t and t in objects and t not in in_view]
            if outside:
                r.err(f"{where}: {outside} are outside the frame {frame!r}, so not in view")
            if not u["note"].strip():
                r.warn(f"{where}: empty note")
            elif _words(u["note"]) >= FIELD_WORD_LIMIT:
                r.warn(f"{where}: note is {_words(u['note'])} words; keep it under 20")
            if op == "fault":
                again = [t for t in targets if t in faulted]
                if again:
                    r.warn(
                        f"{where}: {again} still carry a fault mark; it lasts until the object"
                        " is dismissed, replaced or updated"
                    )
                faulted.update(targets)
            elif op in {"update", "dismiss"}:
                faulted.difference_update(targets)
            if op == "morph" and into in objects:
                roles = {objects[t]["color_role"] for t in targets if t in objects}
                if roles and roles != {objects[into]["color_role"]}:
                    r.warn(
                        f"{where}: into {into!r} is {objects[into]['color_role']!r} but the"
                        f" morphed concept is {sorted(roles)}; a morph keeps its color_role"
                    )
            if op in NEEDS_VISIBLE:
                need = targets[:1] if op == "branch" else targets
                for t in need:
                    if t in objects and t not in visible:
                        r.err(f"{where}: {t!r} is not on screen yet")
            entering = {
                "split": targets[1:],
                "trace": targets[:1],
                "reveal": targets,
                "branch": targets[1:],
                "morph": [into],
                "merge": [into],
            }.get(op, [])
            for t in entering:
                up = parent.get(t, "")
                if up and up not in visible and up not in entering:
                    r.err(f"{where}: {t!r} enters before its parent {up!r} is on screen")
            if op == "split":
                if targets[0] in objects and targets[0] not in visible:
                    r.err(f"{where}: {targets[0]!r} is split before it is on screen")
                for t in targets[1:]:
                    if t in objects and parent.get(t) != targets[0]:
                        r.err(f"{where}: split child {t!r} must have {targets[0]!r} as parent")
                visible.update(targets[1:])
            elif op == "trace":
                visible.add(targets[0])
                for t in targets[1:]:
                    if t in objects and t not in visible:
                        r.err(f"{where}: trace passes through {t!r}, which is not on screen")
            elif op == "reveal":
                visible.update(targets)
            elif op in {"morph", "merge"}:
                for t in targets:
                    if t in objects and t not in visible:
                        r.err(f"{where}: {t!r} is not on screen yet")
                # morph and merge replace their targets: they and their contents leave, and
                # arrows that ended at a target now end at into (endpoints are not tracked).
                gone = {o for o in visible if set(targets) & {o, *_ancestors(o, parent)}}
                visible.difference_update(gone)
                faulted.difference_update(gone)
                if into:
                    visible.add(into)
            elif op == "branch":
                visible.update(targets[1:])
            elif op == "dismiss":
                visible.difference_update(targets)
            change_sentences.append(sentence_cursor + at)
            if op in STRUCTURAL_OPS:
                structural_words.append(word_cursor + sum(_words(s) for s in narration[:at]))
        if not opening and not updates and t_in not in {"zoom_in", "zoom_out"}:
            r.warn(f"{bid}: no visual updates")

        # Pacing landmarks.
        running = word_cursor
        asked = False
        for j, s in enumerate(narration):
            running += _words(s)
            if not s.rstrip().endswith("?"):
                continue
            asked = True
            if question_words is None:
                question_words = running
            elif beat["type"] != "resolution":
                r.err(f"{bid} sentence {j}: a question other than the title question: {s[:60]!r}")
        if beat["type"] == "resolution" and not asked:
            r.warn(f"{bid}: the resolution does not ask the title question again")
        if beat["type"] == "system_overview" and overview_start is None:
            overview_start = word_cursor
        sentence_cursor += n
        word_cursor += words

    change_sentences = sorted(set(change_sentences))
    for a, b in itertools.pairwise([*change_sentences, sentence_cursor]):
        if b - a > 3:
            r.err(f"sentences {a}-{b - 1} (global) go {b - a} sentences without a visual change")
    structural_words.append(word_cursor)
    structural_words.sort()
    for a, b in itertools.pairwise(structural_words):
        if b - a > 100:
            r.err(f"words {a}-{b} go {b - a} words without a structural move")
    if question_words is None:
        r.err("no narration sentence ends with a question mark: the title question is never asked")
    elif question_words > 29:
        r.err(f"the first question lands at word {question_words}; it must land within 29")
    if overview_start is not None and overview_start > 85:
        r.err(f"system_overview starts at word {overview_start}; it must start by word 85")
    check_claims(plan, objects, r)

    target = plan["target_duration_s"]
    if total_est > target * 1.1:
        r.err(f"est_seconds sum {total_est}s is over 110% of the target {target}s")
    elif total_est < target * 0.9:
        if plan["notes"].strip():
            r.warn(f"est_seconds sum {total_est}s is under 90% of {target}s (notes present)")
        else:
            r.err(f"est_seconds sum {total_est}s is under 90% of {target}s and notes say nothing")
    spoken_total = word_cursor / wps
    if spoken_total > target * 1.1:
        r.err(f"narration is ~{spoken_total:.0f}s at {wps:.3f} w/s, over 110% of {target}s")
    elif spoken_total < target * 0.9 and not plan["notes"].strip():
        r.err(f"narration is ~{spoken_total:.0f}s, under 90% of {target}s and notes say nothing")
    if abs(spoken_total - total_est) > 0.15 * max(total_est, 1):
        r.warn(f"narration is ~{spoken_total:.0f}s at {wps} w/s but est_seconds sum {total_est}s")
    unused = sorted(set(objects) - used)
    if unused:
        r.warn(f"declared objects never shown or used: {unused}")
    return {
        "beats": len(beats),
        "objects": len(objects),
        "sentences": sentence_cursor,
        "words": word_cursor,
        "spoken_s": round(spoken_total),
        "est_seconds": total_est,
        "target_s": target,
        "question_at_word": question_words,
        "overview_at_word": overview_start,
    }


def check(plan: dict, schema: dict, wps: float = WPS) -> dict:
    r = Report()
    schema_errors = sorted(
        Draft202012Validator(schema).iter_errors(plan), key=lambda e: list(e.absolute_path)
    )
    for e in schema_errors[:40]:
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        r.err(f"schema: {path}: {e.message[:200]}")
    stats: dict = {}
    if not schema_errors:
        objects = check_references(plan, r)
        stats = check_beats(plan, objects, r, wps)
    return {"ok": not r.errors, "errors": r.errors, "warnings": r.warnings, "stats": stats}


def main() -> int:
    ap = argparse.ArgumentParser(description="Check an explainer plan.")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    ap.add_argument("--wps", type=float, default=WPS, help="spoken words per second")
    args = ap.parse_args()
    try:
        plan = json.loads(args.plan.read_text())
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "errors": [f"not JSON: {exc}"], "warnings": []}, indent=1))
        return 1
    report = check(plan, json.loads(args.schema.read_text()), args.wps)
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
