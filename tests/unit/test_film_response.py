"""The finisher that puts a noise floor back, and the properties that make it safe to cache."""

from __future__ import annotations

import io

from PIL import Image

from content_factory.imaging import FilmResponse, apply_film_response
from content_factory.qc.frame_review import dead_flat_fraction


def _flat_png(size=(256, 256), colour=(128, 128, 128)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _gradient_png(size=(256, 256)) -> bytes:
    w, h = size
    img = Image.new("L", size)
    img.putdata([int(255 * (x / w)) for _ in range(h) for x in range(w)])
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_it_is_deterministic_so_it_can_sit_in_a_cache_key() -> None:
    """Byte-identical for one seed."""
    png = _gradient_png()
    assert apply_film_response(png, FilmResponse(seed=7)) == apply_film_response(
        png, FilmResponse(seed=7)
    )


def test_the_seed_actually_changes_the_grain_field() -> None:
    """Frames sharing a field read as a static overlay on the film rather than grain in it."""
    png = _gradient_png()
    assert apply_film_response(png, FilmResponse(seed=1)) != apply_film_response(
        png, FilmResponse(seed=2)
    )


def test_all_zero_is_the_identity() -> None:
    """Expressible off, so a lane can carry the setting and mean none of it."""
    png = _gradient_png()
    off = FilmResponse(grain=0.0, chroma_grain=0.0, halation=0.0, vignette=0.0)
    before = Image.open(io.BytesIO(png)).convert("RGB")
    after = Image.open(io.BytesIO(apply_film_response(png, off))).convert("RGB")
    assert before.tobytes() == after.tobytes()


def test_it_moves_the_measurement_it_exists_to_move() -> None:
    png = _gradient_png()
    flat_before, detail_before = dead_flat_fraction(png)
    flat_after, detail_after = dead_flat_fraction(apply_film_response(png))
    assert flat_before > 0.9, "a clean gradient is what a render looks like to this measurement"
    assert flat_after < 0.05, "and afterwards every tile carries a noise floor"
    assert detail_after > detail_before


def test_grain_is_exposure_weighted_not_a_flat_field() -> None:
    """A uniform field over a black sky is the most obvious tell of added noise."""
    mid = dead_flat_fraction(apply_film_response(_flat_png(colour=(128, 128, 128))))[1]
    black = dead_flat_fraction(apply_film_response(_flat_png(colour=(2, 2, 2))))[1]
    assert black < mid / 2


def test_it_never_leaves_the_valid_range() -> None:
    """Applied to solid white and solid black, which is where clipping would show."""
    for colour in ((255, 255, 255), (0, 0, 0)):
        out = Image.open(io.BytesIO(apply_film_response(_flat_png(colour=colour)))).convert("RGB")
        values = out.tobytes()
        assert min(values) >= 0 and max(values) <= 255


def test_the_output_is_the_same_size_and_mode() -> None:
    out = Image.open(io.BytesIO(apply_film_response(_gradient_png(size=(320, 180)))))
    assert out.size == (320, 180) and out.mode == "RGB"
