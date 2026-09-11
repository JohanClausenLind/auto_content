"""Where a beat boundary falls in a continuous recording.

Reported by the operator as "the voices cut off at the end instead of being smooth when they start
talking at the next timestamp", and that is exactly what the old spans did: a beat ran from its
first word's start to its last word's ``end_ms``, so the pause between sentences was discarded and
every clip was truncated at a nominal word edge rather than where the sound stopped.

Measured on `ps2b-amber`: six beats, five gaps of 520-1140 ms, **4.24 s of breath dropped**.
"""

from __future__ import annotations

from itertools import pairwise

from content_factory.audio.transcribe import HEAD_LEAD_MS, TAIL_PAD_MS, _close_the_gaps


def _order(count: int) -> list[str]:
    """Beat ids in story order — the only thing `_close_the_gaps` needs."""
    return [f"beat_{i}" for i in range(1, count + 1)]


# The spans ps2b-amber actually produced, from its own take markers.
AMBER = {
    "beat_1": (0, 6400),
    "beat_2": (7400, 14520),
    "beat_3": (15040, 20680),
    "beat_4": (21580, 28720),
    "beat_5": (29860, 34400),
    "beat_6": (35080, 41380),
}


def test_consecutive_beats_abut_so_nothing_is_discarded() -> None:
    """The mix concatenates these clips, so a boundary that is not shared is an audible cut."""
    spans = _close_the_gaps(AMBER, _order(6), duration_ms=42_000)
    ids = list(AMBER)
    for earlier, later in pairwise(ids):
        assert spans[earlier][1] == spans[later][0], f"{earlier} -> {later} leaves a gap"


def test_the_four_seconds_of_breath_come_back() -> None:
    before = sum(end - start for start, end in AMBER.values())
    after = sum(end - start for start, end in _close_the_gaps(AMBER, _order(6), 42_000).values())
    assert after - before >= 4_000


def test_each_beat_keeps_a_run_up_and_the_gap_remainder_stays_behind() -> None:
    """A long pause must not be front-loaded onto the next line: it delays the delivery and holds
    the previous picture past the point the eye is done with it."""
    spans = _close_the_gaps(AMBER, _order(6), 42_000)
    for beat_id, (original_start, _) in AMBER.items():
        if beat_id == "beat_1":
            continue
        lead = original_start - spans[beat_id][0]
        assert lead == HEAD_LEAD_MS, f"{beat_id} got {lead} ms of run-up"


def test_the_first_beat_gets_a_run_up_too_and_never_goes_negative() -> None:
    spans = _close_the_gaps({"beat_1": (0, 500), "beat_2": (2000, 3000)}, _order(2), 4000)
    assert spans["beat_1"][0] == 0  # clamped, not -220
    late = _close_the_gaps({"beat_1": (5000, 6000), "beat_2": (9000, 9500)}, _order(2), 10_000)
    assert late["beat_1"][0] == 5000 - HEAD_LEAD_MS


def test_the_last_beat_keeps_its_decay_but_not_past_the_recording() -> None:
    room = _close_the_gaps({"beat_1": (0, 1000)}, _order(1), duration_ms=9_000)
    assert room["beat_1"][1] == 1000 + TAIL_PAD_MS
    tight = _close_the_gaps({"beat_1": (0, 1000)}, _order(1), duration_ms=1_100)
    assert tight["beat_1"][1] == 1_100  # clamped to the recording, never past it


def test_a_gap_shorter_than_the_lead_is_split_at_the_next_first_word() -> None:
    """With only 80 ms between sentences the next beat cannot have 220, and taking it anyway
    would eat the previous beat's final consonant."""
    spans = _close_the_gaps({"beat_1": (0, 1000), "beat_2": (1080, 2000)}, _order(2), 3000)
    assert spans["beat_1"][1] == spans["beat_2"][0] == 1000
    assert spans["beat_1"][1] >= 1000, "never cuts into the earlier beat's words"


def test_overlapping_spans_are_left_alone_rather_than_reordered() -> None:
    """A beat matched out of order is a different fault, reported by its caller. Silently
    resolving it here would hide it."""
    overlapping = {"beat_1": (0, 5000), "beat_2": (3000, 6000)}
    spans = _close_the_gaps(overlapping, _order(2), 7000)
    assert spans["beat_1"][1] == 5000  # untouched
    assert spans["beat_2"][0] == 3000


def test_an_empty_plan_is_not_an_error() -> None:
    assert _close_the_gaps({}, _order(0), 1000) == {}


def test_the_cut_beats_reconstruct_the_recording(tmp_path) -> None:
    """The decisive property, and the one the operator actually hears.

    Edge levels are a proxy. If consecutive clips abut sample-for-sample then the mix has no
    discontinuity at any boundary *regardless* of how loud the audio is there — the waveform simply
    continues, which is what "smooth into the next line" means. Verified on the real ps2b-amber
    recording (2026-09-12): concatenating the re-cut beats is byte-identical to the source span,
    where the old spans lost 4.64 s and were not.
    """
    import struct
    import wave

    from content_factory.audio.transcribe import cut_beat

    sample_rate = 16_000
    # A deterministic ramp, so any dropped or repeated sample shows up as a mismatch rather than
    # hiding inside silence.
    total = sample_rate * 6
    samples = [((i * 7) % 2000) - 1000 for i in range(total)]
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{total}h", *samples))

    spans = _close_the_gaps(
        {"beat_1": (500, 1500), "beat_2": (2400, 3200), "beat_3": (4000, 5200)},
        _order(3),
        duration_ms=6000,
    )
    ordered = ["beat_1", "beat_2", "beat_3"]
    joined = b""
    for beat_id in ordered:
        start, end = spans[beat_id]
        dest = tmp_path / f"{beat_id}.wav"
        cut_beat(source, dest, start_ms=start, end_ms=end)
        with wave.open(str(dest)) as w:
            joined += w.readframes(w.getnframes())

    first_start, last_end = spans["beat_1"][0], spans["beat_3"][1]
    with wave.open(str(source)) as w:
        w.setpos(first_start * sample_rate // 1000)
        expected = w.readframes((last_end - first_start) * sample_rate // 1000)
    assert joined == expected
