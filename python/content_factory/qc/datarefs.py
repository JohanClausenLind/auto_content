"""Do the figures a plan puts on screen actually come out of the datasets it cites?

The renderer is deliberately **total**: `chartPoints` maps a non-numeric or missing cell to `0`
("bad cells become 0, never a crash"), and `resolveNumber` returns `null` for a row or column it
cannot find, which `formatNumber` draws as an em dash. Both are right for a renderer — a bad row
must not take a whole film down — and together they mean a `ChartScene` whose `y` column is
misspelled draws a **flat line along zero** and passes every check there was.

That is the worst kind of wrong output: a plausible chart, of nothing, with a real dataset's name
under it. This module resolves every `DataRef` in a plan the way the TypeScript does and reports
the ones that do not land, so QC can fail the film instead of shipping it.

The resolution rules are mirrored from `content-ui/src/format/number.ts:resolveDataRef` — the same
two conventions, stated once here in Python:

* ``row_key`` matches the value in the first column, or in a column literally named ``key`` or
  ``row_key``; ``None`` means the first row.
* ``column`` defaults to the **last** column, which is where a single-measure table puts its
  measure.

A cross-language test (`tests/unit/test_chart_values.py`) pins them against the TS source, because
a mirrored rule that drifts is worse than no rule: it would report figures as sound that the
renderer draws as dashes.
"""

from __future__ import annotations

from dataclasses import dataclass

from content_factory.schemas.base import Severity
from content_factory.schemas.render import DatasetTable
from content_factory.schemas.scenes import DataRef, StoryPlan


@dataclass(frozen=True)
class RefProblem:
    """One figure that does not come out of the data it claims."""

    scene_id: str
    kind: str
    detail: str
    severity: Severity = Severity.critical


def key_column(table: DatasetTable) -> str:
    """The column `row_key` is matched against. Mirrors `resolveDataRef`."""
    for name in table.columns:
        if name in ("key", "row_key"):
            return name
    return table.columns[0]


def resolve_ref(ref: DataRef, datasets: dict[str, DatasetTable]) -> tuple[object, str | None]:
    """The cell a DataRef points at, and why it does not resolve when it does not.

    Returns ``(value, None)`` on success and ``(None, reason)`` otherwise, so a caller can report
    the reason rather than guessing from a `None` that also means "the cell is empty".
    """
    table = datasets.get(ref.dataset_id)
    if table is None:
        return None, f"dataset {ref.dataset_id} is not in the bundle"
    keyed = key_column(table)
    if ref.row_key is None:
        row = table.rows[0]
    else:
        row = next((r for r in table.rows if str(r.get(keyed, "")) == ref.row_key), None)
        if row is None:
            available = [str(r.get(keyed, "")) for r in table.rows][:5]
            return None, (
                f"row {ref.row_key!r} is not in {ref.dataset_id} (column {keyed!r} has {available})"
            )
    column = ref.column or table.columns[-1]
    if column not in table.columns:
        return None, (f"column {column!r} is not in {ref.dataset_id} (has {list(table.columns)})")
    value = row.get(column)
    if value is None:
        return None, f"{ref.dataset_id}[{ref.row_key or 'first row'}][{column}] is empty"
    return value, None


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def chart_problems(scene, datasets: dict[str, DatasetTable]) -> list[RefProblem]:
    """A chart's columns have to exist and its series have to carry numbers.

    The zero-fill is the whole point: a misspelled `y` column draws a flat line at zero, and a
    flat line is a *finding* about the data if the data is flat and a **lie** if the column is
    simply not there. Only the second is reported.
    """
    problems: list[RefProblem] = []
    table = datasets.get(scene.data.dataset_id)
    if table is None:
        return [
            RefProblem(
                scene.scene_id,
                "chart",
                f"dataset {scene.data.dataset_id} is not in the bundle",
            )
        ]
    if scene.x not in table.columns:
        problems.append(
            RefProblem(
                scene.scene_id,
                "chart",
                f"x column {scene.x!r} is not in {table.dataset_id}"
                f" (has {list(table.columns)}); the chart would plot unlabelled bars",
            )
        )
    for column in scene.y:
        if column not in table.columns:
            problems.append(
                RefProblem(
                    scene.scene_id,
                    "chart",
                    f"series column {column!r} is not in {table.dataset_id}"
                    f" (has {list(table.columns)}); every value would be drawn as zero",
                )
            )
            continue
        values = [_numeric(row.get(column)) for row in table.rows]
        if all(v is None for v in values):
            problems.append(
                RefProblem(
                    scene.scene_id,
                    "chart",
                    f"series column {column!r} in {table.dataset_id} holds no numbers at all"
                    f" ({[row.get(column) for row in table.rows][:3]}); the chart would be flat",
                )
            )
    return problems


def plan_problems(plan: StoryPlan, datasets: dict[str, DatasetTable]) -> list[RefProblem]:
    """Every figure in a plan, resolved. Empty means every number on screen came off a row."""
    problems: list[RefProblem] = []
    for scene in plan.scenes:
        if scene.kind == "chart":
            problems.extend(chart_problems(scene, datasets))
            continue
        # Every other scene kind carries plain DataRefs: a big number's `value`, a comparison's
        # two sides, a map's optional choropleth column, a table's `data`.
        for attribute in ("value", "data", "left_value", "right_value"):
            ref = getattr(scene, attribute, None)
            if not isinstance(ref, DataRef):
                continue
            value, reason = resolve_ref(ref, datasets)
            if reason is not None:
                problems.append(RefProblem(scene.scene_id, scene.kind, reason))
                continue
            # A big number that renders as an em dash is a hole where the point of the scene was.
            if scene.kind in ("big_number", "comparison") and _numeric(value) is None:
                problems.append(
                    RefProblem(
                        scene.scene_id,
                        scene.kind,
                        f"{ref.dataset_id}[{ref.column or 'last column'}] is {value!r}, not a"
                        " number; it would be drawn as an em dash",
                    )
                )
    return problems
