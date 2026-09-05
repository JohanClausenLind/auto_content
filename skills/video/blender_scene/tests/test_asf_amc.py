"""ASF/AMC parsing and forward kinematics.

The synthetic cases pin the format rules that are easy to break silently: variable value counts per
bone, the axis conjugation, and the Y-up to Z-up conversion. The tests marked ``cmu`` additionally
check the kinematics against real CMU data, where the strongest available evidence that the
convention is right is that the figure stands still on the floor: a wrong convention drifts.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from mocap.asf_amc import (
    SCALE,
    MocapError,
    fk,
    parse_amc,
    parse_asf,
    rot_xyz,
    to_blender,
    trial_paths,
)

CMU_ROOT = Path("/mnt/fast/reference/CMU-Mocap/all_asfamc")
cmu = pytest.mark.skipif(not CMU_ROOT.is_dir(), reason="CMU mocap not on this host")

ASF = """\
:version 1.10
:name TEST
:units
  mass 1.0
  length 0.45
  angle deg
:root
   order TX TY TZ RX RY RZ
   axis XYZ
   position 0 0 0
   orientation 0 0 0
:bonedata
  begin
     id 1
     name upper
     direction 0 1 0
     length 10.0
     axis 0 0 0  XYZ
    dof rx ry rz
    limits (-160.0 20.0)
           (-70.0 70.0)
           (-60.0 70.0)
  end
  begin
     id 2
     name lower
     direction 0 1 0
     length 10.0
     axis 0 0 0  XYZ
    dof rx
    limits (-10.0 170.0)
  end
:hierarchy
  begin
    root upper
    upper lower
  end
"""

AMC = """\
#!OML:ASF test.ASF
:FULLY-SPECIFIED
:DEGREES
1
root 0 0 0 0 0 0
upper 0 0 0
lower 0
2
root 0 0 0 0 0 0
upper 90 0 0
lower 0
"""


@pytest.fixture
def skeleton(tmp_path: Path):
    p = tmp_path / "test.asf"
    p.write_text(ASF)
    return parse_asf(p)


@pytest.fixture
def frames(tmp_path: Path):
    p = tmp_path / "test_01.amc"
    p.write_text(AMC)
    return parse_amc(p)


def test_scale_is_one_over_the_declared_length_unit_in_inches() -> None:
    assert SCALE == pytest.approx((1.0 / 0.45) * 0.0254)
    assert SCALE == pytest.approx(0.056444, abs=1e-6)


def test_parse_asf_reads_units_root_order_dof_and_limits(skeleton) -> None:
    assert skeleton.length_unit == 0.45
    assert skeleton.root_order == ("TX", "TY", "TZ", "RX", "RY", "RZ")
    assert set(skeleton.bones) == {"root", "upper", "lower"}
    assert skeleton.bones["upper"].dof == ("rx", "ry", "rz")
    assert skeleton.bones["upper"].limits == ((-160.0, 20.0), (-70.0, 70.0), (-60.0, 70.0))
    assert skeleton.bones["lower"].dof == ("rx",)
    assert skeleton.bones["lower"].limits == ((-10.0, 170.0),)
    assert skeleton.bones["lower"].parent == "upper"
    assert skeleton.bones["root"].parent is None


def test_topological_order_puts_parents_first(skeleton) -> None:
    order = skeleton.topological()
    assert order.index("root") < order.index("upper") < order.index("lower")


def test_parse_amc_keeps_one_value_per_declared_dof(frames) -> None:
    assert len(frames) == 2
    assert frames[0]["root"] == [0, 0, 0, 0, 0, 0]
    assert frames[0]["upper"] == [0, 0, 0]
    assert frames[0]["lower"] == [0]


def test_fk_chains_offsets_at_rest(skeleton, frames) -> None:
    posed = fk(skeleton, frames[0])
    unit = 10.0 * SCALE
    assert posed.tail["upper"] == pytest.approx([0.0, unit, 0.0])
    assert posed.head["lower"] == pytest.approx([0.0, unit, 0.0])
    assert posed.tail["lower"] == pytest.approx([0.0, 2 * unit, 0.0])
    assert posed.direction("upper") == pytest.approx([0.0, 1.0, 0.0])


def test_fk_rotation_propagates_to_the_child(skeleton, frames) -> None:
    # 90 deg about X takes +Y to +Z, and the child inherits it.
    posed = fk(skeleton, frames[1])
    unit = 10.0 * SCALE
    assert posed.tail["upper"] == pytest.approx([0.0, 0.0, unit], abs=1e-9)
    assert posed.tail["lower"] == pytest.approx([0.0, 0.0, 2 * unit], abs=1e-9)


def test_rot_xyz_applies_x_then_y_then_z() -> None:
    r = rot_xyz((90.0, 0.0, 0.0))
    assert r @ np.array([0.0, 1.0, 0.0]) == pytest.approx([0.0, 0.0, 1.0], abs=1e-12)
    r = rot_xyz((0.0, 0.0, 90.0))
    assert r @ np.array([1.0, 0.0, 0.0]) == pytest.approx([0.0, 1.0, 0.0], abs=1e-12)
    combined = rot_xyz((30.0, 20.0, 10.0))
    expected = rot_xyz((0.0, 0.0, 10.0)) @ rot_xyz((0.0, 20.0, 0.0)) @ rot_xyz((30.0, 0.0, 0.0))
    assert combined == pytest.approx(expected)


def test_root_translation_uses_the_declared_order(tmp_path: Path) -> None:
    asf = tmp_path / "t.asf"
    asf.write_text(ASF.replace("order TX TY TZ RX RY RZ", "order TZ TY TX RX RY RZ"))
    amc = tmp_path / "t_01.amc"
    amc.write_text(":DEGREES\n1\nroot 1 2 3 0 0 0\nupper 0 0 0\nlower 0\n")
    posed = fk(parse_asf(asf), parse_amc(amc)[0])
    # TZ=1, TY=2, TX=3 -> (x, y, z) = (3, 2, 1), scaled.
    assert posed.head["root"] == pytest.approx(np.array([3.0, 2.0, 1.0]) * SCALE)


def test_to_blender_is_a_rotation_not_a_mirror() -> None:
    assert to_blender(np.array([0.0, 1.0, 0.0])) == pytest.approx([0.0, 0.0, 1.0])
    assert to_blender(np.array([1.0, 0.0, 0.0])) == pytest.approx([1.0, 0.0, 0.0])
    assert to_blender(np.array([0.0, 0.0, 1.0])) == pytest.approx([0.0, -1.0, 0.0])
    basis = np.array([to_blender(e) for e in np.eye(3)]).T
    assert float(np.linalg.det(basis)) == pytest.approx(1.0)


def test_bad_files_are_rejected_with_a_reason(tmp_path: Path) -> None:
    p = tmp_path / "x.asf"
    p.write_text("nothing here")
    with pytest.raises(MocapError, match="not an ASF file"):
        parse_asf(p)
    p.write_text(ASF.replace("angle deg", "angle rad"))
    with pytest.raises(MocapError, match="angle"):
        parse_asf(p)
    p.write_text(ASF.replace("upper lower", "upper ghost"))
    with pytest.raises(MocapError, match="no :bonedata"):
        parse_asf(p)
    a = tmp_path / "x.amc"
    a.write_text(":DEGREES\nroot 0 0 0 0 0 0\n")
    with pytest.raises(MocapError, match="before the first frame"):
        parse_amc(a)


@cmu
def test_cmu_bone_lengths_are_adult_sized() -> None:
    asf, amc = trial_paths(CMU_ROOT, "18", "08")
    skeleton = parse_asf(asf)
    posed = fk(skeleton, parse_amc(amc)[0])
    femur = float(np.linalg.norm(posed.tail["lfemur"] - posed.head["lfemur"]))
    tibia = float(np.linalg.norm(posed.tail["ltibia"] - posed.head["ltibia"]))
    assert 0.35 < femur < 0.50, femur
    assert 0.35 < tibia < 0.50, tibia


@cmu
def test_cmu_variable_dof_counts_are_parsed() -> None:
    asf, amc = trial_paths(CMU_ROOT, "18", "08")
    skeleton = parse_asf(asf)
    frame = parse_amc(amc)[0]
    for bone, expected in (("root", 6), ("rclavicle", 2), ("rradius", 1), ("rfoot", 2)):
        declared = 6 if bone == "root" else len(skeleton.bones[bone].dof)
        assert declared == expected, bone
        assert len(frame[bone]) == expected, bone


@cmu
def test_cmu_figure_stands_on_a_stable_floor() -> None:
    """The convention check: a wrong axis order makes the figure sink, fly or wobble."""
    asf, amc = trial_paths(CMU_ROOT, "18", "08")
    skeleton = parse_asf(asf)
    frames = parse_amc(amc)
    lows = [fk(skeleton, frames[i]).lowest_foot() for i in range(0, len(frames), 40)]
    heads = [fk(skeleton, frames[i]).tail["head"][1] for i in range(0, len(frames), 40)]
    spread = max(lows) - min(lows)
    assert spread < 0.05, f"foot height varies by {spread:.3f} m over the trial"
    assert 0.0 < min(lows) < 0.20, min(lows)
    assert 1.4 < min(heads) and max(heads) < 2.0, (min(heads), max(heads))
    assert max(heads) - min(heads) < 0.20


@cmu
def test_cmu_two_person_trials_are_frame_synchronised_in_one_world_frame() -> None:
    """22_08 is 'hold hands, swing arms, walk'. A and B are the same take, so their frame counts
    match and their wrists come close enough to be holding hands."""
    a_asf, a_amc = trial_paths(CMU_ROOT, "22", "08")
    b_asf, b_amc = trial_paths(CMU_ROOT, "23", "08")
    sa, sb = parse_asf(a_asf), parse_asf(b_asf)
    fa, fb = parse_amc(a_amc), parse_amc(b_amc)
    assert len(fa) == len(fb)
    gaps = []
    roots = []
    for i in range(0, len(fa), 10):
        pa, pb = fk(sa, fa[i]), fk(sb, fb[i])
        roots.append(float(np.linalg.norm(pa.head["root"] - pb.head["root"])))
        gaps.append(
            min(
                float(np.linalg.norm(pa.tail[x] - pb.tail[y]))
                for x in ("lwrist", "rwrist")
                for y in ("lwrist", "rwrist")
            )
        )
    assert min(gaps) < 0.15, f"closest wrists {min(gaps):.3f} m - not holding hands"
    assert 0.3 < min(roots) and max(roots) < 1.5, (min(roots), max(roots))


@cmu
def test_cmu_frame_counts_are_plausible_across_the_two_person_set() -> None:
    for subject, trial in (("18", "01"), ("20", "02"), ("22", "04"), ("22", "03")):
        asf, amc = trial_paths(CMU_ROOT, subject, trial)
        frames = parse_amc(amc)
        assert 100 < len(frames) < 30000, (subject, trial, len(frames))
        posed = fk(parse_asf(asf), frames[len(frames) // 2])
        assert 1.2 < posed.tail["head"][1] < 2.1
        assert math.isfinite(posed.lowest_foot())
