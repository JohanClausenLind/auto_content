from __future__ import annotations

import json
from pathlib import Path

import pytest
from spec import (
    SpecError,
    canonical_dumps,
    frames_to_render,
    load_spec,
    seg_assignments,
    spec_sha256,
    validate,
)

FIX = Path(__file__).parent / "fixtures" / "shot_cube_dolly.json"


def _spec() -> dict:
    return json.loads(FIX.read_text())


def test_fixture_validates_and_fills_defaults() -> None:
    s = load_spec(FIX)
    assert s["shot_id"] == "shot_cubedolly0001"
    assert frames_to_render(s) == [0, 1, 2, 3]
    assert s["render"]["engine"] == "workbench"
    assert s["camera"]["keyframes"][0]["easing_to_next"] == "linear"


def test_hash_is_canonical_and_stable() -> None:
    a, b = _spec(), _spec()
    b["render"], b["camera"] = a["render"], a["camera"]  # same content, different key order
    reordered = {k: b[k] for k in reversed(list(b))}
    assert spec_sha256(validate(a)) == spec_sha256(validate(reordered))
    assert canonical_dumps({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda s: s["camera"]["keyframes"].pop(0), "start at frame 0"),
        (
            lambda s: s["camera"]["keyframes"].append(dict(s["camera"]["keyframes"][-1])),
            "strictly increasing",
        ),
        (
            lambda s: s["camera"]["keyframes"][0].update({"rotation_euler_deg": [90, 0, 0]}),
            "exactly one of look_at",
        ),
        (lambda s: s.update({"width": 250}), "multiples of 32"),
        (lambda s: s["props"].append(dict(s["props"][0])), "ids must be unique"),
        (lambda s: s.update({"anchor_frames": [0, 4]}), "anchor_frames"),
        (lambda s: s["render"].update({"frames": [1, 3]}), "every anchor frame"),
        (lambda s: s["render"].update({"passes": ["hologram"]}), "unknown render passes"),
        (lambda s: s["render"].update({"engine": "octane"}), "render.engine"),
        (lambda s: s["props"][0].update({"seg_id": 300}), "seg_id"),
    ],
)
def test_structural_errors(mutate, match: str) -> None:
    s = _spec()
    mutate(s)
    with pytest.raises(SpecError, match=match):
        validate(s)


def test_seg_assignments_are_deterministic_and_honour_explicit_ids() -> None:
    s = _spec()
    assert seg_assignments(validate(s)) == {"cube": 1, "backdrop": 2}
    s["props"][0]["seg_id"] = 7
    s["props"].append({"id": "lamp", "seg": False})
    s["characters"].append({"id": "man", "asset": "man_01"})
    assigned = seg_assignments(validate(s))
    assert assigned == {
        "man": 1,
        "cube": 7,
        "backdrop": 2,
    }  # characters first, explicit kept, no id for seg=False
