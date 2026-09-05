"""One shared system instruction, one home for prompts, and figures that come off a row.

Two of the small gaps from the audit:

* **Prompts were f-strings inline** in whichever module needed one — three in `copywriter.py`, a
  sixty-line builder in `scriptwriter.py`, another in `shots/prompt_compile.py`. Nothing shared,
  nothing versioned, and — since Ollama's constrained decoding replaced the pasted-in schema —
  **no system message at all**, so the shared rules about inventing figures went to nobody.
* **A chart's columns were never checked against its dataset.** The renderer is total by design
  (`chartPoints` maps a missing cell to `0`, `resolveNumber` returns `null` for a row it cannot
  find), so a `ChartScene` whose `y` column is misspelled draws a flat line along zero, under a
  real dataset's name, and passed every check there was.
"""

from __future__ import annotations

import re

import pytest

from content_factory.models.gateway import GatewayOptions, _with_system_instruction
from content_factory.prompting import (
    PROMPTING_VERSION,
    REGISTRY,
    SYSTEM_INSTRUCTION,
    get,
)
from content_factory.qc.datarefs import chart_problems, key_column, plan_problems, resolve_ref
from content_factory.schemas import scenes as sc
from content_factory.schemas.fixtures import sample_dataset, sample_story_plan
from content_factory.schemas.render import DatasetTable


def test_the_shared_instruction_is_attached_by_the_gateway() -> None:
    """In the gateway, not at each call site, because a call site can forget — and one did: when
    constrained decoding replaced the pasted-in schema, the system message went with it."""
    messages = [{"role": "user", "content": "write a caption"}]
    out = _with_system_instruction(messages, None)
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[0]["content"] == SYSTEM_INSTRUCTION


def test_a_callers_own_system_message_is_kept_with_the_shared_text_in_front() -> None:
    """The shared part is the floor, not a replacement."""
    out = _with_system_instruction(
        [{"role": "system", "content": "BE TERSE"}, {"role": "user", "content": "x"}], None
    )
    assert len(out) == 2
    assert out[0]["content"].startswith(SYSTEM_INSTRUCTION)
    assert out[0]["content"].endswith("BE TERSE")


def test_a_probe_can_opt_out_entirely() -> None:
    """Measuring the raw model means sending it nothing of ours."""
    messages = [{"role": "user", "content": "x"}]
    assert _with_system_instruction(messages, "") == messages
    assert _with_system_instruction(messages, "CUSTOM")[0]["content"] == "CUSTOM"


def test_the_default_option_defers_to_the_module() -> None:
    """`None`, not a copy of the text: a frozen copy in a dataclass default would be a second
    place to edit and a first place to forget."""
    assert GatewayOptions().system_instruction is None


def test_the_shared_instruction_holds_only_what_is_true_for_every_role() -> None:
    """A rule for one role belongs in that role's template. These three are for all of them."""
    lowered = SYSTEM_INSTRUCTION.lower()
    assert "never invent" in lowered
    assert "checked against a cited source" in lowered
    # House style, here rather than per template because a film that sounds like an advertisement
    # sounds that way in the captions, the article and the narration alike.
    assert "delve" in lowered
    # And nothing role-specific: no scene kinds, no word budgets, no platform names.
    for word in ("carousel", "beat", "scene_kind", "instagram"):
        assert word not in lowered, word


def test_every_template_is_named_versioned_and_renders() -> None:
    assert REGISTRY
    for template_id, template in REGISTRY.items():
        assert template.template_id == template_id
        assert template.version and template.purpose
        assert template.system() == SYSTEM_INSTRUCTION
    assert PROMPTING_VERSION


def test_a_missing_render_field_fails_by_name() -> None:
    """`format`, not an f-string at the call site: a missing input is a KeyError naming the field
    rather than a silently empty string in the middle of a prompt."""
    with pytest.raises(KeyError):
        get("copy.caption").render(platform="x")


def test_an_unknown_template_id_lists_the_known_ones() -> None:
    with pytest.raises(KeyError, match=re.escape("copy.caption")):
        get("copy.nonexistent")


def test_the_copywriter_renders_from_the_registry() -> None:
    """So the registry has real consumers rather than being decoration."""
    from content_factory.models import copywriter

    assert copywriter.CAPTION.template_id == "copy.caption"
    assert copywriter.CARDS.template_id == "copy.cards"
    assert copywriter.CAPTION_MAX_CHARS > 0 and copywriter.CARD_MAX_CHARS > 0


# --- chart values -------------------------------------------------------------------------------


def _table(**kwargs) -> DatasetTable:
    base = {
        "dataset_id": "ds_wind00000001",
        "classification": "SOURCE_DATA",
        "columns": ("year", "share_pct"),
        "rows": ({"year": "2018", "share_pct": 11}, {"year": "2025", "share_pct": 21}),
        "unit": "%",
        "label": "Wind share",
    }
    return DatasetTable.model_validate(base | kwargs)


def _chart(**kwargs) -> sc.ChartScene:
    base = {
        "scene_id": "scn_chart000001",
        "beat_id": "beat_000000001",
        "chart": sc.ChartKind.line,
        "data": sc.DataRef(dataset_id="ds_wind00000001"),
        "x": "year",
        "y": ("share_pct",),
        "title": sc.TextRef(text="Wind share"),
    }
    return sc.ChartScene.model_validate(base | kwargs)


def test_a_sound_chart_reports_nothing() -> None:
    assert chart_problems(_chart(), {"ds_wind00000001": _table()}) == []


def test_a_misspelled_series_column_is_the_flat_line_at_zero() -> None:
    """The defect this exists for: the renderer maps a missing cell to 0, so the chart draws a
    flat line along the axis under a real dataset's name and nothing said so."""
    problems = chart_problems(_chart(y=("share_pcnt",)), {"ds_wind00000001": _table()})
    assert len(problems) == 1
    assert "share_pcnt" in problems[0].detail
    assert "drawn as zero" in problems[0].detail


def test_a_misspelled_x_column_is_reported_too() -> None:
    problems = chart_problems(_chart(x="yr"), {"ds_wind00000001": _table()})
    assert any("unlabelled bars" in p.detail for p in problems)


def test_a_series_of_non_numbers_is_reported() -> None:
    """A column that exists and holds text draws the same flat line."""
    table = _table(
        columns=("year", "share_pct"),
        rows=({"year": "2018", "share_pct": "n/a"}, {"year": "2025", "share_pct": "n/a"}),
    )
    problems = chart_problems(_chart(), {"ds_wind00000001": table})
    assert any("no numbers at all" in p.detail for p in problems)


def test_a_missing_dataset_is_reported_once() -> None:
    problems = chart_problems(_chart(), {})
    assert len(problems) == 1 and "not in the bundle" in problems[0].detail


def test_the_resolution_rules_mirror_the_renderer() -> None:
    """`row_key` matches the first column (or one named key/row_key); `column` defaults to the
    LAST. Mirrored from content-ui/src/format/number.ts, and pinned here because a mirrored rule
    that drifts would report figures as sound that the renderer draws as dashes."""
    table = _table()
    assert key_column(table) == "year"
    assert key_column(_table(columns=("row_key", "v"), rows=({"row_key": "a", "v": 1},))) == (
        "row_key"
    )
    # No row_key: the first row. No column: the last column.
    value, reason = resolve_ref(sc.DataRef(dataset_id=table.dataset_id), {table.dataset_id: table})
    assert reason is None and value == 11
    value, reason = resolve_ref(
        sc.DataRef(dataset_id=table.dataset_id, row_key="2025"), {table.dataset_id: table}
    )
    assert reason is None and value == 21


def test_a_row_that_is_not_there_names_the_rows_that_are() -> None:
    table = _table()
    _value, reason = resolve_ref(
        sc.DataRef(dataset_id=table.dataset_id, row_key="1999"), {table.dataset_id: table}
    )
    assert reason and "1999" in reason and "2018" in reason


def test_a_big_number_that_would_render_as_a_dash_is_reported() -> None:
    """A big number is the whole point of its scene; an em dash there is a hole."""
    plan = sample_story_plan()
    problems = plan_problems(plan, {sample_dataset().dataset_id: sample_dataset()})
    assert problems == []
    # Point it at a row that is not in the table.
    broken = plan.model_copy(
        update={
            "scenes": tuple(
                s.model_copy(
                    update={"value": sc.DataRef(dataset_id="ds_wind00000001", row_key="9999")}
                )
                if s.kind == "big_number"
                else s
                for s in plan.scenes
            )
        }
    )
    problems = plan_problems(broken, {sample_dataset().dataset_id: sample_dataset()})
    assert any(p.kind == "big_number" and "9999" in p.detail for p in problems)


def test_the_demo_plan_and_dataset_agree() -> None:
    """The fixture every lane runs on. If this ever fails, the demo ships a chart of nothing."""
    assert plan_problems(sample_story_plan(), {sample_dataset().dataset_id: sample_dataset()}) == []
