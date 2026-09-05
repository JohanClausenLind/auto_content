"""What ships is measured on the right file, judged against a real promise, and listed with digests.

Four defects in the last two stages before a destination:

* the delivery-promise check measured `exports/bnd_run000000001.mp4` — the Remotion bundle. On the
  hybrid path that is only the typeset half; the picture that ships is `composed.mp4`, which
  interleaves the generated clips. So it read a file with none of the generated motion in it and
  then reported on the motion.
* the promise came from `spec.intent.startswith("animated")` over a 1000-character free-text
  field, so it was always `chart_led` — the one value that makes the check unable to fail.
* it had no route counts, so it could not tell a card that does not move (fine) from a generated
  clip that does not move (a wasted generation).
* `compile_destination_packages` wrote `{"status": "packaged"}` and not one word about **what**
  was packaged, so a package for a film that failed to render looked exactly like one for a film
  that had not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from content_factory.qc.delivery import (
    GENERATED_FRACTION_FOR_ANIMATED,
    promised_delivery,
)
from content_factory.schemas.delivery import DELIVERABLE_ROLES, DeliveryFile, DeliveryPackage


def _file(role: str = "video", path: str = "exports/final.mp4", sha: str = "a") -> DeliveryFile:
    return DeliveryFile(
        role=role,  # type: ignore[arg-type]
        path=path,
        sha256=sha * 64,
        bytes=1024,
        content_type="video/mp4",
    )


def _package(**kwargs) -> DeliveryPackage:
    return DeliveryPackage(
        deliverable_id="dlv_short0000001",
        destination="youtube",
        visibility="private",
        files=kwargs.pop("files", (_file(),)),
        built_at="2026-09-09T00:00:00+00:00",
        **kwargs,
    )


def test_a_package_records_the_files_and_their_digests() -> None:
    package = _package(files=(_file(), _file("caption", "captions/captions.srt", "b")))
    assert package.total_bytes == 2048
    assert [f.sha256[:1] for f in package.files] == ["a", "b"]


def test_a_package_with_nothing_to_publish_is_refused() -> None:
    """Captions and a QC report are not a deliverable. This is the case `{"status": "packaged"}`
    could not distinguish from a finished film."""
    with pytest.raises(ValidationError, match="nothing to publish"):
        _package(
            files=(
                _file("caption", "captions/captions.srt", "b"),
                _file("metadata", "qc/report.json", "c"),
            )
        )
    assert "caption" not in DELIVERABLE_ROLES and "video" in DELIVERABLE_ROLES


def test_a_package_with_no_files_is_refused() -> None:
    with pytest.raises(ValidationError):
        _package(files=())


def test_duplicate_paths_are_refused() -> None:
    """Two entries for one path make `total_bytes` a lie and the manifest ambiguous."""
    with pytest.raises(ValidationError, match="duplicate file path"):
        _package(files=(_file(), _file(sha="b")))


def test_an_absolute_or_escaping_path_is_refused() -> None:
    """A package is meant to survive the project being moved, and an absolute path in a manifest
    is a path on one machine."""
    with pytest.raises(ValidationError, match="relative"):
        DeliveryFile(
            role="video", path="/tmp/final.mp4", sha256="a" * 64, bytes=1, content_type="video/mp4"
        )
    with pytest.raises(ValidationError, match="relative"):
        DeliveryFile(
            role="video",
            path="../other/final.mp4",
            sha256="a" * 64,
            bytes=1,
            content_type="video/mp4",
        )


def test_a_zero_byte_file_is_refused() -> None:
    """A zero-byte export is a failed render that left a file behind, and in a manifest it looks
    like a deliverable."""
    with pytest.raises(ValidationError):
        DeliveryFile(
            role="video",
            path="exports/final.mp4",
            sha256="a" * 64,
            bytes=0,
            content_type="video/mp4",
        )


def test_qc_is_recorded_not_enforced() -> None:
    """A package is allowed to exist for a film that failed, and a publisher is allowed to refuse
    it. Refusing to *record* it would move the ignorance one step later."""
    assert _package().qc_passed is None
    assert _package(qc_passed=False).qc_passed is False


def test_the_promise_comes_from_what_the_film_is_made_of() -> None:
    """The old rule was `intent.startswith("animated")` over a prose field: false for every real
    brief, so the promise was always chart_led and the check could never fail."""
    assert promised_delivery("", {"render": 10}) == "chart_led"
    assert promised_delivery("a data explainer about wind power", {"render": 10}) == "chart_led"
    assert promised_delivery("", {"render": 8, "generate": 2}) == "mixed"
    assert promised_delivery("", {"render": 2, "generate": 8}) == "animated_explainer"
    assert promised_delivery("", None) == "chart_led"


def test_prose_that_names_motion_still_counts() -> None:
    """One signal among several rather than the only one: an operator who asked for an animated
    explainer and got a slideshow should hear about it even if every segment was a card."""
    for words in ("an animated explainer", "motion graphic piece", "with animation throughout"):
        assert promised_delivery(words, {"render": 10}) == "animated_explainer", words


def test_the_animated_threshold_is_the_documented_one() -> None:
    total = 10
    generated = round(GENERATED_FRACTION_FOR_ANIMATED * total)
    assert promised_delivery("", {"generate": generated, "render": total - generated}) == (
        "animated_explainer"
    )
    assert promised_delivery("", {"generate": generated - 1, "render": total - generated + 1}) == (
        "mixed"
    )


def test_the_stage_collects_what_is_on_disk_with_real_digests(tmp_path: Path) -> None:
    """Scanned rather than declared: what a lane produces depends on the lane, and a hand-written
    list of expected outputs would go stale the first time a lane changed."""
    from content_factory.schemas.base import file_sha256
    from content_factory.workflows.stages import _delivery_files

    class Ctx:
        def ddir(self) -> Path:
            return tmp_path

    (tmp_path / "exports").mkdir(parents=True)
    (tmp_path / "captions").mkdir()
    (tmp_path / "exports" / "final.mp4").write_bytes(b"a film")
    (tmp_path / "captions" / "captions.srt").write_text("1\n")
    # A zero-byte export is a failed render, not a deliverable.
    (tmp_path / "exports" / "generated.mp4").write_bytes(b"")
    # And carousel cards are collected by glob, because the count is a property of the copy.
    (tmp_path / "exports" / "card_1.png").write_bytes(b"card one")
    (tmp_path / "exports" / "card_2.png").write_bytes(b"card two")

    files = _delivery_files(Ctx())  # type: ignore[arg-type]
    by_path = {f.path: f for f in files}
    assert "exports/final.mp4" in by_path
    assert "exports/generated.mp4" not in by_path  # zero bytes
    assert {"exports/card_1.png", "exports/card_2.png"} <= set(by_path)
    # The digest is of the bytes on disk right now: a manifest is a claim about bytes.
    assert by_path["exports/final.mp4"].sha256 == file_sha256(tmp_path / "exports" / "final.mp4")
    assert by_path["captions/captions.srt"].role == "caption"
    assert by_path["exports/card_1.png"].content_type == "image/png"


def test_a_campaign_run_ships_its_render_bundles_not_only_its_metadata(tmp_path: Path) -> None:
    """The other naming scheme, which the scan used to miss entirely.

    A lane run through the local runner writes `exports/final.mp4` and `exports/card_*.png`. A
    campaign run through ProductionWorkflow writes one file per *render bundle* instead, because
    `render_artboard` names its output after the bundle it rendered — so a three-card carousel
    leaves `bnd_card000000001.png` and friends, and the fixed-name scan found nothing but
    `qc/report.json`. The package then carried only `["metadata"]` and `DeliveryPackage` refused
    it, which was correct: a package of captions and metadata with no film in it is exactly what
    that validator exists to catch. Four integration tests failed on it.
    """
    from content_factory.schemas.base import file_sha256
    from content_factory.workflows.stages import _delivery_files

    class Ctx:
        def ddir(self) -> Path:
            return tmp_path

    (tmp_path / "exports").mkdir(parents=True)
    (tmp_path / "qc").mkdir()
    (tmp_path / "qc" / "report.json").write_text('{"passed": true}')
    for i in (1, 2, 3):
        (tmp_path / "exports" / f"bnd_card00000000{i}.png").write_bytes(f"card {i}".encode())
        # The renderer drops a JSON sidecar beside each export; it is not a deliverable.
        (tmp_path / "exports" / f"bnd_card00000000{i}.json").write_text("{}")

    files = _delivery_files(Ctx())  # type: ignore[arg-type]
    by_path = {f.path: f for f in files}
    assert {f"exports/bnd_card00000000{i}.png" for i in (1, 2, 3)} <= set(by_path)
    assert not [f for f in files if f.path.endswith(".json") and f.role != "metadata"]
    # The whole point: there is something publishable, so the package validates.
    assert any(f.role in DELIVERABLE_ROLES for f in files)
    assert by_path["exports/bnd_card000000001.png"].sha256 == file_sha256(
        tmp_path / "exports" / "bnd_card000000001.png"
    )
    # The film first, metadata last, even though the files now arrive from two scans.
    assert [f.role for f in files] == ["image", "image", "image", "metadata"]


def test_a_muxed_film_supersedes_the_silent_bundle_it_was_made_from(tmp_path: Path) -> None:
    """Shipping both would put two films in one package."""
    from content_factory.workflows.stages import _delivery_files

    class Ctx:
        def ddir(self) -> Path:
            return tmp_path

    (tmp_path / "exports").mkdir(parents=True)
    (tmp_path / "exports" / "bnd_tl0000000001.mp4").write_bytes(b"silent")
    (tmp_path / "exports" / "bnd_tl0000000001.narrated.mp4").write_bytes(b"narrated")

    paths = {f.path for f in _delivery_files(Ctx())}  # type: ignore[arg-type]
    assert paths == {"exports/bnd_tl0000000001.narrated.mp4"}


def test_nothing_on_disk_is_a_named_failure(tmp_path: Path) -> None:
    """A package with no files is what the stage used to emit silently."""
    from content_factory.workflows.stages import _delivery_files

    class Ctx:
        def ddir(self) -> Path:
            return tmp_path

    assert _delivery_files(Ctx()) == []  # type: ignore[arg-type]


def test_the_package_json_from_a_real_run_carries_a_digest_per_file() -> None:
    """Pinned against the run recorded in STATUS.md, when it is still on disk."""
    manifest = Path(
        "output/local-runs/narrated-video/deliverables/dlv_short0000001"
        "/destination-packages/packages.json"
    )
    if not manifest.is_file():
        pytest.skip("no local narrated-video run on this machine")
    packages = [DeliveryPackage.model_validate(p) for p in json.loads(manifest.read_text())]
    assert packages
    for package in packages:
        assert package.files
        assert all(len(f.sha256) == 64 and f.bytes > 0 for f in package.files)
        assert any(f.role in DELIVERABLE_ROLES for f in package.files)
