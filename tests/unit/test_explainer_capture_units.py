"""Pure helpers behind the capture gates: ids, normalising, tiles, issues, signatures."""

from __future__ import annotations

import json
import re
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from content_factory.explainer.capture import CaptureError, read_signature
from content_factory.explainer.ocr import (
    compare_texts,
    crop_rect,
    judge,
    normalize_text,
    tile_spans,
)
from content_factory.explainer.passages import (
    QuoteRequest,
    Tile,
    ambiguous_issue,
    closest_text,
    not_found_issue,
    quote_id,
    section_id,
    text_sha256,
)
from content_factory.explainer.sources import capture_id
from content_factory.schemas.explainer import PageRect

OPAQUE_ID = re.compile(r"^[a-z][a-z0-9]{1,7}_[A-Za-z0-9_-]{8,32}$")
PAGE_TEXT = (
    "A tram trip has only two kinds of time in it.\n"
    "Late trams get later; this is the bunching effect, and every operator knows it. "
    "Operators tend to reach for the moving-time remedy first."
)


def test_ids_are_prefixed_short_hashes_and_stable() -> None:
    digest = "ab" * 32
    assert capture_id(digest) == "cap_" + "ab" * 6
    assert section_id("Model", 1) == section_id("Model", 1)
    assert section_id("Model", 1) != section_id("Model", 2)
    assert quote_id("dwell time", 0) != quote_id("dwell time", 2)
    for value in (capture_id(digest), section_id("", 0), quote_id("x", 0)):
        assert OPAQUE_ID.match(value), value


def test_normalize_text_folds_quotes_dashes_case_and_whitespace() -> None:
    assert normalize_text("“Dwell  time” — not\nspeed…") == '"dwell time" - not speed...'


def test_compare_texts_fails_a_removed_word_despite_high_similarity() -> None:
    expected = "The measurements do support the claim that a faster tram shortens the trip."
    ocr = "The measurements do not support the claim that a faster tram shortens the trip."
    comparison = compare_texts(expected, ocr)
    assert comparison.similarity > 0.9
    assert comparison.only_ocr == ("not",)
    assert comparison.only_expected == ()
    assert not comparison.passed


def test_compare_texts_tolerates_ocr_glyph_noise() -> None:
    comparison = compare_texts("dwell time sets the timetable", "dwell tlme sets the timetab1e")
    assert comparison.passed
    assert comparison.similarity >= 0.9


def test_judge_reports_blank_ocr_as_a_mismatch() -> None:
    verdict = judge("dwell time", "")
    assert not verdict.passed
    assert verdict.similarity == 0.0
    assert verdict.reason.startswith("OCR read '' but the manifest says 'dwell time'")


def test_tile_spans_stitch_across_an_overlapping_last_tile() -> None:
    tiles = [(0, 800), (800, 800), (1200, 800)]
    # Overlapping rows come from the first tile that has them; the last tile is only a fallback.
    assert tile_spans(tiles, 790, 1210) == [(0, 790, 800, 790), (1, 800, 1210, 800)]
    assert tile_spans(tiles, 1650, 1900) == [(2, 1650, 1900, 1650)]
    assert tile_spans(tiles, 10, 20) == [(0, 10, 20, 10)]


def test_tile_spans_reject_uncovered_rows() -> None:
    with pytest.raises(ValueError, match="not covered"):
        tile_spans([(0, 800)], 700, 900)


def test_crop_rect_stitches_pixels_from_both_tiles(tmp_path: Path) -> None:
    Image.new("RGB", (100, 100), (255, 0, 0)).save(tmp_path / "a.png")
    Image.new("RGB", (100, 100), (0, 0, 255)).save(tmp_path / "b.png")
    tiles = [Tile(tmp_path / "a.png", 0), Tile(tmp_path / "b.png", 100)]
    crop = crop_rect(tiles, PageRect(x=10, y=90, width=20, height=20), padding_px=0)
    assert crop.size == (20, 20)
    assert crop.getpixel((0, 0)) == (255, 0, 0)
    assert crop.getpixel((19, 19)) == (0, 0, 255)


def test_closest_text_picks_the_nearest_sentence() -> None:
    needle = "Late trams get later; this is the bunching effect, and every driver knows it."
    assert closest_text(needle, PAGE_TEXT).endswith("every operator knows it.")


def test_not_found_issue_names_the_closest_sentence() -> None:
    request = QuoteRequest("Late trams get later, and every driver knows it.")
    issue = not_found_issue(request, 0, 0, PAGE_TEXT)
    assert issue.kind == "invalid_reference"
    assert "passage_not_found" in issue.message
    assert "every operator knows it" in issue.message
    assert "re-capture" in issue.fix


def test_out_of_range_occurrence_is_not_found_with_the_count() -> None:
    issue = not_found_issue(QuoteRequest("dwell", occurrence_index=5), 1, 3, PAGE_TEXT)
    assert "out of range" in issue.message
    assert "3 time(s)" in issue.message


def test_ambiguous_issue_names_the_count_and_the_index_range() -> None:
    issue = ambiguous_issue(QuoteRequest("dwell"), 2, 3)
    assert issue.kind == "invalid_reference"
    assert "passage_ambiguous" in issue.message
    assert "3 times" in issue.message
    assert "occurrence_index (0..2)" in issue.fix


def test_text_sha256_ignores_whitespace_runs() -> None:
    assert text_sha256("a  b\n c") == text_sha256("a b c")
    assert text_sha256("a b") != text_sha256("a c")


def _wacz(
    path: Path, signed_data: dict[str, str] | None, *, hash_override: str | None = None
) -> Path:
    package = b'{"profile": "data-package"}'
    digest: dict[str, object] = {
        "path": "datapackage.json",
        "hash": hash_override or "sha256:" + sha256(package).hexdigest(),
    }
    if signed_data is not None:
        digest["signedData"] = {"hash": digest["hash"], **signed_data}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("datapackage.json", package)
        archive.writestr("datapackage-digest.json", json.dumps(digest))
    return path


def test_read_signature_reports_unsigned_and_anonymous_signatures(tmp_path: Path) -> None:
    assert read_signature(_wacz(tmp_path / "plain.wacz", None)) is None
    anonymous = read_signature(
        _wacz(
            tmp_path / "anon.wacz",
            {"software": "signer 1", "signature": "AA==", "publicKey": "BB=="},
        )
    )
    assert anonymous is not None
    assert anonymous.anonymous is True
    assert anonymous.domain == ""
    assert anonymous.software == "signer 1"
    domain = read_signature(
        _wacz(tmp_path / "domain.wacz", {"signature": "AA==", "domain": "signer.example"})
    )
    assert domain is not None
    assert domain.anonymous is False
    assert domain.domain == "signer.example"


def test_read_signature_rejects_a_digest_that_does_not_cover_the_package(tmp_path: Path) -> None:
    bad = _wacz(tmp_path / "bad.wacz", {"signature": "AA=="}, hash_override="sha256:" + "0" * 64)
    with pytest.raises(CaptureError, match="does not cover"):
        read_signature(bad)
