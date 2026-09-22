"""Gate B1: every number in the Amdahl fixture traces to a claim, and each drift is caught."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from content_factory.explainer.evidence import (
    check_calculations,
    check_datasets,
    check_evidence,
    check_narration,
    number_mentions,
)
from content_factory.explainer.validate import validate_episode
from content_factory.schemas.explainer import (
    EvidencePack,
    Quantity,
    ScriptPlan,
    VisualSpec,
    tokenize,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "explainer" / "amdahl"
GENERATOR = REPO / "scripts" / "make_explainer_fixtures.py"
FILES = ("pack.json", "script.json", "spec.json")

Trio = tuple[EvidencePack, ScriptPlan, VisualSpec]


@pytest.fixture(scope="module")
def trio() -> Trio:
    pack = EvidencePack.model_validate_json((FIXTURES / "pack.json").read_bytes())
    script = ScriptPlan.model_validate_json((FIXTURES / "script.json").read_bytes())
    spec = VisualSpec.model_validate_json((FIXTURES / "spec.json").read_bytes())
    return pack, script, spec


def _with_claim_value(pack: EvidencePack, claim_id: str, value: Quantity) -> EvidencePack:
    claims = tuple(
        c.model_copy(update={"value": value}) if c.claim_id == claim_id else c for c in pack.claims
    )
    return EvidencePack.model_validate(pack.model_copy(update={"claims": claims}).model_dump())


def _with_cell(pack: EvidencePack, row_key: str, column: str, value: float) -> EvidencePack:
    dataset = pack.datasets[0]
    index = [c.name for c in dataset.columns].index(column)
    rows = tuple(
        r.model_copy(update={"values": (*r.values[:index], value, *r.values[index + 1 :])})
        if r.key == row_key
        else r
        for r in dataset.rows
    )
    datasets = (dataset.model_copy(update={"rows": rows}),)
    return EvidencePack.model_validate(pack.model_copy(update={"datasets": datasets}).model_dump())


def _with_spoken(script: ScriptPlan, segment_id: str, old: str, new: str) -> ScriptPlan:
    segments = []
    for segment in script.segments:
        if segment.segment_id == segment_id:
            text = segment.spoken_text.replace(old, new)
            assert text != segment.spoken_text
            segment = segment.model_copy(update={"spoken_text": text, "tokens": tokenize(text)})
        segments.append(segment)
    copy = script.model_copy(update={"segments": tuple(segments)})
    return ScriptPlan.model_validate(copy.model_dump())


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_explainer_fixtures", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_explainer_fixtures"] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_trio_validates_and_every_number_traces_to_a_claim(trio: Trio) -> None:
    assert check_evidence(*trio) == []


def test_fixture_trio_passes_the_compiler_boundary(trio: Trio) -> None:
    validate_episode(*trio)


def test_wrong_total_is_one_arithmetic_issue_naming_the_calculation(trio: Trio) -> None:
    pack = _with_claim_value(trio[0], "clm_total_100ms", Quantity(magnitude=90, unit="ms"))
    issues = check_calculations(pack)
    assert [i.kind for i in issues] == ["arithmetic"]
    issue = issues[0]
    assert "calc_total_base" in issue.where
    assert "100 ms" in issue.message and "90 ms" in issue.message
    assert issue.fix.startswith("change the claim value to 100 ms")


def test_narration_drift_names_the_segment_and_token(trio: Trio) -> None:
    pack, script, _ = trio
    drifted = _with_spoken(script, "seg_change_variable", "60 milliseconds.", "50 milliseconds.")
    token = drifted.segment("seg_change_variable").tokens.index("50")
    issues = check_narration(pack, drifted)
    assert [i.kind for i in issues] == ["narration_mismatch"]
    issue = issues[0]
    assert issue.where == f"segment seg_change_variable token {token}"
    assert "seg_change_variable" in issue.ids
    assert "'50'" in issue.message and "60 ms (clm_total_60ms)" in issue.message
    assert issue.fix.endswith("or reference the claim that says 50 ms.")


def test_dataset_cell_drift_names_the_row_and_column(trio: Trio) -> None:
    pack = _with_cell(trio[0], "faster-other", "ms", 25.0)
    issues = check_datasets(pack)
    assert [i.kind for i in issues] == ["dataset_mismatch"]
    issue = issues[0]
    assert "'faster-other'" in issue.where and "column ms" in issue.where
    assert "25 ms" in issue.message and "20 ms" in issue.message
    assert issue.ids == ("ds_latency_split", "faster-other")


def test_claim_in_seconds_matches_narration_in_milliseconds(trio: Trio) -> None:
    pack, script, _ = trio
    pack = _with_claim_value(pack, "clm_total_60ms", Quantity(magnitude=0.06, unit="s"))
    text = "It still takes 60 milliseconds."
    segment = script.segment("seg_synthesis").model_copy(
        update={"spoken_text": text, "tokens": tokenize(text), "claim_ids": ("clm_total_60ms",)}
    )
    one_segment = ScriptPlan.model_validate(
        script.model_copy(update={"segments": (segment,)}).model_dump()
    )
    assert check_narration(pack, one_segment) == []
    assert check_calculations(pack) == []


def test_adding_milliseconds_to_terawatt_hours_is_a_unit_issue(trio: Trio) -> None:
    pack = _with_claim_value(trio[0], "clm_other_20ms", Quantity(magnitude=20, unit="TWh"))
    issues = check_calculations(pack)
    assert {i.kind for i in issues} == {"unit"}
    base = next(i for i in issues if "calc_total_base" in i.where)
    assert "time vs energy" in base.message


def test_time_basis_year_is_exempt_and_a_stray_year_is_flagged(trio: Trio) -> None:
    pack, script, _ = trio
    drifted = _with_spoken(script, "seg_show_limits", "hardware.", "hardware, not in 1999.")
    tokens = drifted.segment("seg_show_limits").tokens
    issues = check_narration(pack, drifted)
    assert [i.where for i in issues] == [f"segment seg_show_limits token {tokens.index('1999.')}"]
    assert issues[0].kind == "narration_mismatch"
    assert tokens.index("1967,") not in {int(i.where.rsplit(" ", 1)[1]) for i in issues}


def test_number_mentions_parse_every_written_form() -> None:
    twice = "2\N{MULTIPLICATION SIGN}"
    text = f"It took 1,200 hours, 40% more, {twice} the 80ms budget, or 60. Say 0.5 seconds."
    mentions = {m.raw: (m.value, m.unit) for m in number_mentions(tokenize(text))}
    assert mentions == {
        "1,200": (1200.0, "h"),
        "40%": (40.0, "%"),
        twice: (2.0, "ratio"),
        "80ms": (80.0, "ms"),
        "60.": (60.0, None),
        "0.5": (0.5, "s"),
    }


def test_fixture_generator_is_byte_stable(tmp_path: Path) -> None:
    generator = _load_generator()
    written = generator.write_fixtures(tmp_path)
    assert [p.name for p in written] == list(FILES)
    for path in written:
        assert path.read_bytes() == (FIXTURES / path.name).read_bytes(), path.name
