from __future__ import annotations

from layout import bbox_from_points, hidream_boxes, visible_fraction, xxyy


def test_bbox_clips_and_rounds() -> None:
    box = bbox_from_points([(0.2, 0.3), (0.6, 0.9), (float("nan"), 0.1)])
    assert box == (0.2, 0.3, 0.4, 0.6)
    assert bbox_from_points([(-0.5, -0.5), (0.5, 0.5)]) == (0.0, 0.0, 0.5, 0.5)
    assert bbox_from_points([(1.5, 0.5), (2.0, 0.9)]) is None
    assert bbox_from_points([]) is None


def test_visible_fraction() -> None:
    assert visible_fraction([(0.2, 0.2), (0.4, 0.4)]) == 1.0
    assert visible_fraction([(-0.2, 0.0), (0.2, 0.4)]) == 0.5
    assert visible_fraction([(1.2, 1.2), (1.5, 1.5)]) == 0.0
    assert visible_fraction([]) == 0.0


def test_xxyy_and_hidream_cap() -> None:
    assert xxyy((0.1, 0.2, 0.3, 0.4)) == [0.1, 0.4, 0.2, 0.6]
    objects = [
        {"object_id": str(i), "box": {"x": 0.0, "y": 0.0, "w": 0.1 * (i + 1), "h": 0.1}}
        for i in range(7)
    ]
    objects.append({"object_id": "offscreen", "box": None})
    boxes = hidream_boxes(objects)
    assert len(boxes) == 5
    # order is preserved for the survivors; the two smallest were dropped
    assert boxes[0] == xxyy((0.0, 0.0, 0.3, 0.1)) and boxes[-1] == xxyy((0.0, 0.0, 0.7, 0.1))
