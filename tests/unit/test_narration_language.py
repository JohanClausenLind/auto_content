"""A locale the chosen voice cannot speak is refused by name, before any weights load.

The bug: ``skills/audio/kokoro/run.py`` mapped its language argument with

    lang_code = "a" if args.lang.startswith("en") else "b"

and ``"b"`` is Kokoro's *British English*. Every non-English locale therefore produced a British
voice reading foreign words as if they were English — a whole film narrated in the wrong language,
with nothing anywhere in the run reporting it. These tests pin both halves of the fix: the control
plane refuses before loading, and the skill's own runner refuses if called by hand.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from content_factory.audio.languages import (
    KOKORO_LOCALE_TO_CODE,
    NO_LOCAL_SWEDISH,
    QWEN_LANGUAGES,
    QWEN_LOCALE_TO_LANGUAGE,
    LanguageUnsupportedError,
    check_narration_language,
    kokoro_lang_code,
    locale_prefix,
)

REPO = Path(__file__).resolve().parents[2]


def _kokoro_runner():
    """The skill's own `run.py`, imported without its skill environment.

    It only imports numpy/soundfile/kokoro *inside* `main`, so the module and its language table
    load in the control plane's interpreter — which is what makes the mapping testable at all.
    """
    path = REPO / "skills" / "audio" / "kokoro" / "run.py"
    spec = importlib.util.spec_from_file_location("kokoro_run_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_locale_no_local_voice_speaks_is_refused_and_names_the_alternative() -> None:
    for provider in ("kokoro", "qwen3tts"):
        with pytest.raises(LanguageUnsupportedError) as exc:
            check_narration_language(provider, "sv-SE")
        message = str(exc.value)
        assert "sv-SE" in message and provider in message
        # The refusal carries the finding rather than making the operator rediscover it.
        assert "Chatterbox" in message
    assert "Chatterbox" in NO_LOCAL_SWEDISH


def test_the_refusal_lists_what_the_voice_can_speak() -> None:
    with pytest.raises(LanguageUnsupportedError, match="It supports:"):
        check_narration_language("kokoro", "de")
    # German is Qwen's, not Kokoro's — so the same locale is fine on the other backend, which is
    # the actionable half of the message ("or set narration.tts to a voice that speaks 'de'").
    assert check_narration_language("qwen3tts", "de").qwen_language == "german"


def test_english_regions_pick_the_right_kokoro_front_end() -> None:
    """The only case where the region subtag, not just the language, decides anything."""
    assert kokoro_lang_code("en") == "a"  # American
    assert kokoro_lang_code("en-US") == "a"
    assert kokoro_lang_code("en-GB") == "b"  # British
    assert check_narration_language("kokoro", "en-GB").kokoro_lang_code == "b"
    assert check_narration_language("kokoro", "en").kokoro_lang_code == "a"


def test_a_locale_kokoro_cannot_speak_has_no_code_rather_than_a_default() -> None:
    """The whole bug in one assertion: an unsupported locale must not resolve to a code."""
    assert kokoro_lang_code("sv-SE") == ""
    assert kokoro_lang_code("de") == ""
    assert "sv" not in KOKORO_LOCALE_TO_CODE


def test_the_two_spellings_of_a_language_have_to_agree() -> None:
    """A run configured for Swedish that tells Qwen 'english' would speak English and label it
    Swedish, and every consumer of `VoiceIdentity.locale` would believe the label."""
    with pytest.raises(LanguageUnsupportedError, match="disagrees with"):
        check_narration_language("qwen3tts", "de", language="english")
    # `auto` is not a disagreement: it means "take it from the locale", and it resolves to that.
    assert check_narration_language("qwen3tts", "de", language="auto").qwen_language == "german"
    assert check_narration_language("qwen3tts", "de", language="german").qwen_language == "german"


def test_a_language_name_the_model_does_not_declare_is_refused() -> None:
    with pytest.raises(LanguageUnsupportedError, match="not one Qwen3-TTS declares"):
        check_narration_language("qwen3tts", "en", language="swedish")
    assert "swedish" not in QWEN_LANGUAGES


def test_the_mock_speaks_no_language_so_it_accepts_any_locale() -> None:
    """Tone bursts at the right length produce no phonemes, so nothing can be in the wrong
    language — and the offline demo must not need a locale table to run."""
    assert check_narration_language("mock", "sv-SE").prefix == "sv"
    assert check_narration_language("mock", "xx-YY").qwen_language == "auto"


def test_locale_prefixes_are_case_insensitive() -> None:
    assert locale_prefix("EN-gb") == "en"
    assert locale_prefix("  sv-SE ") == "sv"


def test_every_qwen_locale_maps_to_a_language_the_model_declares() -> None:
    """The two tables are measured from the same `--list` call and must not drift apart."""
    assert set(QWEN_LOCALE_TO_LANGUAGE.values()) <= QWEN_LANGUAGES


def test_the_skill_runner_refuses_a_locale_it_cannot_speak() -> None:
    """Defence in depth: the same refusal for anyone calling the script by hand."""
    module = _kokoro_runner()
    with pytest.raises(SystemExit, match="cannot speak 'sv-SE'"):
        module.lang_code("sv-SE")
    with pytest.raises(SystemExit, match="it supports:"):
        module.lang_code("de")


def test_the_skill_runner_and_the_control_plane_agree_on_every_locale() -> None:
    """Two tables, one answer. They are in different languages' worth of process boundary, so a
    test is the only thing that keeps them in step."""
    module = _kokoro_runner()
    for locale in ("en", "en-US", "en-GB", "es", "fr", "hi", "it", "pt", "ja", "zh"):
        assert module.lang_code(locale) == kokoro_lang_code(locale), locale


def test_the_skill_runner_passes_a_raw_kokoro_code_through() -> None:
    """So an operator debugging by hand can say `--lang b` and get British English."""
    module = _kokoro_runner()
    assert module.lang_code("b") == "b"
    assert module.lang_code("j") == "j"
