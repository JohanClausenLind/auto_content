"""The film's own pronunciation list reaches the synthesiser, and the sidechain key is the voice.

Two dead contract fields, both filled here:

* ``NarrationRequest.lexicon`` and ``normalize_for_speech``'s `lexicon` argument have existed
  since phase 15 and **nothing ever passed one**, so every respelling this repo could express was
  dead weight. Measured on the narrated-video lane (2026-09-08): Qwen3-TTS read
  "Energimyndigheten" as *"energym and de hetten"* and "kraftnät" as *"craft name"*, and the
  alignment gate failed the run at similarity 0.33 with no way for the operator to fix it short of
  rewriting the display text.
* ``add_music_bed`` sidechained its bed against whatever it was mixing into. Beds are added one
  after another, so by the time the effects bed arrived the first input already held the music —
  and a -18 dB music bed is loud enough to hold the effects ducked for the whole film.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.audio.normalize import normalize_for_speech
from content_factory.schemas.audio import PronunciationEntry
from content_factory.schemas.fixtures import sample_story_plan
from content_factory.workflows.stages import LEXICON_FILENAME, spoken_line, story_lexicon


class _Ctx:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir


def _write_lexicon(project: Path, entries: list[dict]) -> None:
    (project / "story").mkdir(parents=True, exist_ok=True)
    (project / "story" / LEXICON_FILENAME).write_text(json.dumps(entries))


def test_no_lexicon_file_is_no_lexicon_not_a_crash(tmp_path: Path) -> None:
    assert story_lexicon(_Ctx(tmp_path)) == ()  # type: ignore[arg-type]


def test_the_lexicon_is_loaded_and_validated(tmp_path: Path) -> None:
    _write_lexicon(
        tmp_path,
        [
            {"term": "Energimyndigheten", "respelling": "Ener-gee-mund-ig-heten"},
            {"term": "kraftnät", "respelling": "kraft-nate", "notes": "Swedish for power grid"},
        ],
    )
    entries = story_lexicon(_Ctx(tmp_path))  # type: ignore[arg-type]
    assert [e.term for e in entries] == ["Energimyndigheten", "kraftnät"]
    assert all(isinstance(e, PronunciationEntry) for e in entries)


def test_an_entry_for_another_language_is_not_applied_to_this_read(tmp_path: Path) -> None:
    """A Swedish respelling of a Swedish name is wrong guidance for an English narrator."""
    _write_lexicon(
        tmp_path,
        [
            {"term": "kraftnät", "respelling": "kraft-nate", "locale": "en"},
            {"term": "kraftnät", "respelling": "krahft-nayt", "locale": "sv"},
        ],
    )
    english = story_lexicon(_Ctx(tmp_path), "en-GB")  # type: ignore[arg-type]
    assert [e.respelling for e in english] == ["kraft-nate"]
    swedish = story_lexicon(_Ctx(tmp_path), "sv-SE")  # type: ignore[arg-type]
    assert [e.respelling for e in swedish] == ["krahft-nayt"]
    # No locale asked for means no filtering, which is what a caller with no voice yet wants.
    assert len(story_lexicon(_Ctx(tmp_path))) == 2  # type: ignore[arg-type]


def test_a_malformed_entry_fails_by_name_rather_than_being_skipped(tmp_path: Path) -> None:
    _write_lexicon(tmp_path, [{"term": "x"}])
    with pytest.raises(Exception, match="respelling"):
        story_lexicon(_Ctx(tmp_path))  # type: ignore[arg-type]


def test_a_respelling_reaches_the_spoken_line() -> None:
    """`spoken_line` is the only place a respelling can take effect: it is what the synthesiser
    is handed and what the script is locked with."""
    beat = sample_story_plan().beats[3]
    assert "Energimyndigheten" in beat.display_text
    plain = spoken_line(beat)
    assert "Energimyndigheten" in plain
    lexicon = (PronunciationEntry(term="Energimyndigheten", respelling="Ener-gee-mund-ig-heten"),)
    respelled = spoken_line(beat, lexicon)
    assert "Ener-gee-mund-ig-heten" in respelled
    assert "Energimyndigheten" not in respelled


def test_a_written_spoken_line_still_gets_the_lexicon() -> None:
    """`spoken_text` says how a line is *said*; the lexicon says how a word is said. A hand-written
    spoken line can still contain a proper noun nobody can pronounce."""
    beat = (
        sample_story_plan()
        .beats[0]
        .model_copy(update={"spoken_text": "Svenska kraftnät reported it."})
    )
    lexicon = (PronunciationEntry(term="kraftnät", respelling="kraft-nate"),)
    assert spoken_line(beat, lexicon) == "Svenska kraft-nate reported it."


def test_longer_terms_are_respelled_first() -> None:
    """Otherwise a rule for "kraft" would eat the inside of "kraftnät" and leave a fragment."""
    lexicon = (
        PronunciationEntry(term="kraft", respelling="KRAFT"),
        PronunciationEntry(term="kraftnät", respelling="kraft-nate"),
    )
    assert normalize_for_speech("Svenska kraftnät", lexicon) == "Svenska kraft-nate"


def test_the_sidechain_key_is_a_separate_input_when_it_is_a_separate_file(tmp_path: Path) -> None:
    """The argument array is the contract with ffmpeg, so it is what a test can hold."""
    from content_factory.audio import mix

    calls: list[list[str]] = []
    original = mix.ffmpeg
    try:
        mix.ffmpeg = lambda args, **kw: calls.append(args)  # type: ignore[assignment]
        stem = tmp_path / "narration-stem.wav"
        bed = tmp_path / "sfx.wav"
        with_music = tmp_path / "narration-with-music.wav"
        # No key: two inputs, and the mix itself is the key — right for the first bed.
        mix.add_music_bed(stem, bed, tmp_path / "a.wav", duration_ms=1000)
        assert calls[-1].count("-i") == 2
        assert "[0:a]aresample" in " ".join(calls[-1])
        # A key that is a different file: three inputs, and the key is the third.
        mix.add_music_bed(with_music, bed, tmp_path / "b.wav", duration_ms=1000, duck_against=stem)
        graph = " ".join(calls[-1])
        assert calls[-1].count("-i") == 3
        assert str(stem) in calls[-1]
        assert "[2:a]aresample" in graph
        assert "[bed][key]sidechaincompress" in graph
        # A key that IS the first input stays a two-input graph rather than opening it twice.
        mix.add_music_bed(stem, bed, tmp_path / "c.wav", duration_ms=1000, duck_against=stem)
        assert calls[-1].count("-i") == 2
    finally:
        mix.ffmpeg = original  # type: ignore[assignment]
