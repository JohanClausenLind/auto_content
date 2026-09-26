"""Locating the subject that moves, against a stub gateway -- no vision model, no GPU."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from PIL import Image

from content_factory.schemas.sequences import Box
from content_factory.sequences.subject_locate import (
    SubjectLocationError,
    locate_subject,
    pad_box,
    to_box,
)

if TYPE_CHECKING:
    from content_factory.models.gateway import ModelGateway


@dataclass
class _Result:
    value: object


class _StubGateway:
    """Answers with one canned location and records what it was asked."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.messages: list = []

    def complete_structured(self, skill, policy, response_model, messages, **kw):
        self.messages = messages
        return _Result(value=self.value)


def _png(size=(640, 384)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_grid_box_becomes_a_relative_box() -> None:
    assert to_box([100, 200, 400, 700]) == Box(x=0.1, y=0.2, w=0.3, h=0.5)


def test_reversed_or_empty_box_is_refused() -> None:
    with pytest.raises(SubjectLocationError, match="empty once clamped"):
        to_box([400, 200, 100, 700])


def test_box_is_clamped_to_the_grid() -> None:
    assert to_box([-50, -10, 2000, 1200]) == Box(x=0.0, y=0.0, w=1.0, h=1.0)


def test_pad_grows_outward_but_stays_in_frame() -> None:
    padded = pad_box(Box(x=0.0, y=0.5, w=0.2, h=0.5), 0.05)
    assert padded.x == 0.0
    assert padded.y == pytest.approx(0.45)
    assert padded.y + padded.h <= 1.0


def test_locate_returns_the_box_it_was_given() -> None:
    from content_factory.sequences import subject_locate

    gateway = _StubGateway(subject_locate._Located(present=True, box=[200, 100, 600, 900]))
    box = locate_subject(_png(), "the walking man", gateway=cast("ModelGateway", gateway), pad=0.0)
    assert box == Box(x=0.2, y=0.1, w=0.4, h=0.8)


def test_absent_subject_raises_rather_than_boxing_nothing() -> None:
    """A confident box round empty background would freeze the subject and repaint the sky."""
    from content_factory.sequences import subject_locate

    gateway = _StubGateway(subject_locate._Located(present=False, box=[0, 0, 10, 10]))
    with pytest.raises(SubjectLocationError, match="did not find"):
        locate_subject(_png(), "the walking man", gateway=cast("ModelGateway", gateway))


def test_empty_subject_is_refused_before_the_model_is_asked() -> None:
    gateway = _StubGateway(None)
    with pytest.raises(SubjectLocationError, match="must name what to find"):
        locate_subject(_png(), "   ", gateway=cast("ModelGateway", gateway))
    assert gateway.messages == []


def test_the_image_is_attached_and_downscaled() -> None:
    from content_factory.sequences import subject_locate

    gateway = _StubGateway(subject_locate._Located(present=True, box=[10, 10, 900, 900]))
    locate_subject(_png((4096, 2048)), "the man", gateway=cast("ModelGateway", gateway))
    parts = gateway.messages[0]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
