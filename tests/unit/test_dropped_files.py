"""Dropping a file on the canvas: what it is, which node holds it, what to offer next.

The suggestions are the part worth guarding. They are offered to an operator as one click that
spawns a wired node, so every one of them has to name a node type that exists and a slot whose
type actually accepts what the source node emits — a suggestion that produces a refused link is
worse than no suggestion at all. The node catalogue fixture (`fixtures/schema/node_catalog.json`,
generated from the canvas's own definitions) is what makes that checkable from Python.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.ingest.dropped import (
    SOURCE_NODE,
    SOURCE_SLOT,
    describe,
    safe_name,
    suggestions_for,
)
from content_factory.schemas.content import StagedUpload

CATALOG = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "schema" / "node_catalog.json").read_text()
)["node_types"]


def _types(spec: str) -> set[str]:
    return {t.strip() for t in spec.split(",") if t.strip()}


def test_every_kind_has_a_node_that_exists() -> None:
    for kind, node_type in SOURCE_NODE.items():
        assert node_type in CATALOG, f"{kind} -> {node_type}"
        slot = SOURCE_SLOT[node_type]
        assert slot in CATALOG[node_type]["outputs"], f"{node_type}.{slot}"


def test_every_suggestion_is_type_correct_against_the_real_catalogue() -> None:
    facts = {
        "sample_rate_hz": 16000,
        "channels": 1,
        "duration_ms": 4200,
        "width": 1920,
        "height": 1080,
        "fps": 24.0,
    }
    for kind, node_type in SOURCE_NODE.items():
        source_slot = SOURCE_SLOT[node_type]
        emitted = _types(CATALOG[node_type]["output_types"][source_slot])
        for suggestion in suggestions_for(kind, facts):
            target = CATALOG.get(suggestion.node_type)
            assert target is not None, f"{kind}: {suggestion.node_type} is not a node type"
            if not suggestion.to_slot:
                continue  # spawned unwired: it reads the uploads folder itself
            assert suggestion.to_slot in target["inputs"], (
                f"{kind}: {suggestion.node_type} has no input {suggestion.to_slot}"
            )
            accepted = _types(target["input_types"][suggestion.to_slot])
            assert emitted & accepted, (
                f"{kind}: {node_type}.{source_slot} ({emitted}) cannot feed"
                f" {suggestion.node_type}.{suggestion.to_slot} ({accepted})"
            )


def test_every_suggested_widget_value_is_a_widget_that_node_declares() -> None:
    """A value set on a spawned node has to be a knob it has, or the canvas drops it silently."""
    for kind in SOURCE_NODE:
        for suggestion in suggestions_for(kind, {}):
            declared = set(CATALOG[suggestion.node_type]["widgets"])
            options = CATALOG[suggestion.node_type]["widget_options"]
            for name, value in suggestion.values.items():
                assert name in declared, f"{suggestion.node_type} has no widget {name}"
                if name in options:
                    assert str(value) in options[name], f"{suggestion.node_type}.{name}={value}"


def test_a_dropped_recording_is_offered_the_step_that_actually_works_on_it() -> None:
    """`restore_speech` and `mix_audio` were offered wired straight to the dropped file, and both
    read `<beat_id>.wav` files that only a voice stage writes — so one click produced a graph that
    failed on its first stage. Reading the recording is the door: it is what makes the beats, and
    therefore the repair and the captions, exist."""
    narrow = suggestions_for("audio", {"sample_rate_hz": 16000, "channels": 1})
    assert [s.node_type for s in narrow] == ["transcribe_audio", "qc_deliverable"]
    read = narrow[0]
    assert read.to_slot == "audio"
    assert "16 kHz" in read.why and "mono" in read.why

    wide = suggestions_for("audio", {"sample_rate_hz": 48000, "channels": 2})
    # The measurement still shapes what is said about it, which is what makes it specific.
    assert "no band extension" in wide[0].why


def test_data_and_documents_go_through_ingest_rather_than_a_node_of_their_own() -> None:
    assert SOURCE_NODE["data"] == "ingest"
    assert SOURCE_NODE["document"] == "ingest"
    assert [s.node_type for s in suggestions_for("data", {})] == ["compile_datasets", "research"]
    assert [s.node_type for s in suggestions_for("document", {})] == ["research"]
    # compile_datasets reads the uploads folder, so it is offered without a wire rather than
    # with one the graph would refuse (SOURCES cannot feed an EVIDENCE input).
    assert suggestions_for("data", {})[0].to_slot == ""


def test_describe_says_what_was_measured_and_nothing_else() -> None:
    assert describe("audio", {"duration_ms": 4200, "sample_rate_hz": 16000, "channels": 1}) == (
        "audio · 4.2 s · 16 kHz · mono"
    )
    assert (
        describe("video", {"width": 1920, "height": 1080, "fps": 24.0})
        == "video · 1920x1080 · 24.0 fps"
    )
    # Nothing measurable: the kind alone, not an invented number.
    assert describe("audio", {}) == "audio"
    assert describe("data", {}) == "data"


def test_safe_name_keeps_only_what_the_contract_accepts() -> None:
    for raw, expected in (
        ("take one.wav", "take one.wav"),
        ("../../etc/passwd", "passwd"),
        ("ünïcode.wav", "n-code.wav"),  # non-ASCII becomes a dash; leading ones are trimmed
        (".hidden.wav", "file-.hidden.wav"),  # a name the contract needs a leading alnum for
        ("", "dropped"),
        (None, "dropped"),
    ):
        cleaned = safe_name(raw)
        assert cleaned == expected, raw
        # Whatever comes out has to be a legal staged filename, or the run cannot carry it.
        StagedUpload(
            asset_id="ws_workspace01/originals-audio/ab/" + "a" * 64 + ".wav",
            filename=cleaned,
            kind="audio",
            size_bytes=1,
            sha256="a" * 64,
        )


def test_materialize_is_idempotent_and_reads_only_the_store(tmp_path: Path) -> None:
    from content_factory.artifacts import FilesystemArtifactStore
    from content_factory.ingest.dropped import materialize

    store = FilesystemArtifactStore(tmp_path / "artifacts")
    source = tmp_path / "take.wav"
    source.write_bytes(b"RIFFxxxx" * 8)
    ref = store.put_file("ws_workspace01", "originals-audio", source, content_type="audio/x-wav")
    staged = StagedUpload(
        asset_id=ref.key,
        filename="take one.wav",
        kind="audio",
        size_bytes=ref.size_bytes,
        sha256=ref.sha256,
    )
    uploads = tmp_path / "project" / "uploads"

    first = materialize(uploads, store, "ws_workspace01", staged)
    assert first.read_bytes() == source.read_bytes()
    mtime = first.stat().st_mtime_ns
    second = materialize(uploads, store, "ws_workspace01", staged)
    assert second == first and second.stat().st_mtime_ns == mtime  # not re-copied


def test_footage_that_is_already_smooth_does_not_lead_with_interpolation() -> None:
    """A screen recording arrives at 60 fps. Offering "smooth the motion" first would spend a GPU
    pass on the one thing it does not need, so it moves last and says what it is actually for."""
    smooth = suggestions_for("video", {"fps": 60.0, "width": 1920, "height": 1080})
    assert smooth[0].node_type == "upscale_video"
    interpolate = next(s for s in smooth if s.node_type == "interpolate")
    assert interpolate is smooth[-1]
    assert "already 60 fps" in interpolate.why and "slow motion" in interpolate.why

    choppy = suggestions_for("video", {"fps": 24.0})
    assert choppy[0].node_type == "interpolate"
    assert "24 fps in" in choppy[0].why
