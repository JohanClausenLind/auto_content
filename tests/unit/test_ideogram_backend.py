"""The Ideogram 4 backend refuses to pretend it can edit.

The sequence engine is hub-and-spoke: every frame is composed against the anchor, and drift is
measured against it. A backend that cannot see the anchor cannot participate in that, and the
failure mode worth preventing is the quiet one -- accepting `edit`, ignoring the anchor, returning
a fresh picture, and letting the drift gate call it a regression in the model rather than a
missing capability.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.schemas.sequences import GenerationLock
from content_factory.sequences.engine import ControlConditioning, SequenceError
from content_factory.sequences.ideogram_backend import Ideogram4Backend


def _lock() -> GenerationLock:
    return GenerationLock(
        workflow_package_id="ideo_test00001",
        workflow_package_version="1.0.0",
        model_revision="ideogram4",
        width=1024,
        height=576,
        seed=7,
        sampler="euler",
        steps=20,
        guidance=7.0,
        style_prompt="cinematic",
        camera_prompt="side view",
        lighting_prompt="overcast",
        background_prompt="a pavement",
        reference_asset_sha256="0" * 64,
    )


def test_edit_refuses_and_names_what_to_reach_for_instead(tmp_path: Path) -> None:
    backend = Ideogram4Backend(workdir=tmp_path)
    for call in (
        lambda: backend.edit(b"anchor", b"control", "move them", _lock(), attempt=1),
        lambda: backend.edit_conditioned(
            b"anchor", ControlConditioning(), "move them", _lock(), attempt=1
        ),
    ):
        with pytest.raises(SequenceError, match="text-to-image only"):
            call()


def test_generate_with_conditioning_refuses_rather_than_dropping_it(tmp_path: Path) -> None:
    """The quiet failure this prevents: references accepted, silently ignored, frame returned."""
    backend = Ideogram4Backend(workdir=tmp_path)
    with pytest.raises(SequenceError, match="text-to-image only"):
        backend.generate("{}", ControlConditioning(reference_pngs=(b"sheet",)), _lock(), seed=7)


def test_it_does_not_claim_to_carry_the_control_raster(tmp_path: Path) -> None:
    assert Ideogram4Backend(workdir=tmp_path).send_control_as_reference is False


def test_an_unreachable_server_is_not_reported_as_ready(tmp_path: Path) -> None:
    # Port 1 is reserved and never listening; `ready` must answer False, not raise.
    backend = Ideogram4Backend(workdir=tmp_path, endpoint="http://127.0.0.1:1")
    assert backend.ready() is False
