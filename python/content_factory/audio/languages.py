"""Which languages each local TTS can actually speak, and a check that runs before model load."""

from __future__ import annotations

from dataclasses import dataclass

# Language *names* Qwen3-TTS accepts for `--language`, straight from the model. "auto" is the
# model's own detection and is not a locale, so it is kept out of the locale map below.
QWEN_LANGUAGES: frozenset[str] = frozenset(
    {
        "auto",
        "chinese",
        "english",
        "french",
        "german",
        "italian",
        "japanese",
        "korean",
        "portuguese",
        "russian",
        "spanish",
    }
)

# Locale prefix -> the language name Qwen3-TTS wants. Two spellings of one thing exist because the
# contract carries a locale (`VoiceIdentity.locale`, an ISO tag) and this model wants a word.
QWEN_LOCALE_TO_LANGUAGE: dict[str, str] = {
    "zh": "chinese",
    "en": "english",
    "fr": "french",
    "de": "german",
    "it": "italian",
    "ja": "japanese",
    "ko": "korean",
    "pt": "portuguese",
    "ru": "russian",
    "es": "spanish",
}

# Locale prefix -> Kokoro lang_code. The single-letter codes are Kokoro's own; the mapping is not
# a guess about which dialect it picks, which is exactly the mistake the old `else "b"` made.
KOKORO_LOCALE_TO_CODE: dict[str, str] = {
    "en": "a",  # American English. en-GB takes "b" — see `kokoro_lang_code`.
    "es": "e",
    "fr": "f",
    "hi": "h",
    "it": "i",
    "pt": "p",
    "ja": "j",
    "zh": "z",
}

# The mock speaks nothing: it is tone bursts at the right length. Any locale is fine, because no
# phonemes are produced and nothing can be in the wrong language.
SUPPORTED_LOCALES: dict[str, frozenset[str] | None] = {
    "mock": None,
    "qwen3tts": frozenset(QWEN_LOCALE_TO_LANGUAGE),
    "kokoro": frozenset(KOKORO_LOCALE_TO_CODE),
}

NO_LOCAL_SWEDISH = (
    "No locally installed TTS speaks Swedish: Qwen3-TTS does not list it"
    " (measured 2026-09-08), Kokoro has no lang code for it, and Breeze-TTS-2 is Mandarin and"
    " English. Chatterbox Multilingual is the candidate to evaluate, and downloading it is the"
    " operator's decision."
)


ENGLISH_ONLY_SUFFIX = ".en"
"""What Whisper calls its English-only checkpoints: `base.en`, `small.en`, `medium.en`."""


def aligner_model_for(locale: str, model: str, *, configured: bool) -> str:
    """The forced-alignment model for a narration language, given what the settings asked for."""
    if configured or locale_prefix(locale) == "en":
        return model
    if model.endswith(ENGLISH_ONLY_SUFFIX):
        return model[: -len(ENGLISH_ONLY_SUFFIX)]
    return model


class LanguageUnsupportedError(RuntimeError):
    """The narration language asked for is not one the chosen voice can speak."""


@dataclass(frozen=True)
class LanguageChoice:
    """A validated narration language, in both spellings the backends want."""

    locale: str
    """The locale as configured, e.g. `en`, `en-GB`, `sv-SE`."""
    prefix: str
    """Its language subtag, lowercased: the key every table here is indexed by."""
    qwen_language: str
    """The word Qwen3-TTS wants, or `auto` when the provider is not Qwen."""
    kokoro_lang_code: str
    """Kokoro's single-letter code, or `""` when the provider is not Kokoro."""


def locale_prefix(locale: str) -> str:
    """`en-GB` -> `en`. Lowercased, because a locale's language subtag is case-insensitive."""
    return locale.split("-", 1)[0].strip().lower()


def kokoro_lang_code(locale: str) -> str:
    """Kokoro's lang code for a locale, or `""` when it has none."""
    prefix = locale_prefix(locale)
    if prefix == "en":
        return "b" if locale.strip().lower().endswith("-gb") else "a"
    return KOKORO_LOCALE_TO_CODE.get(prefix, "")


def check_narration_language(
    provider: str, locale: str, *, language: str | None = None
) -> LanguageChoice:
    """Refuse a language the chosen voice cannot speak, by name, before anything is loaded."""
    prefix = locale_prefix(locale)
    supported = SUPPORTED_LOCALES.get(provider)
    if supported is not None and prefix not in supported:
        detail = f" {NO_LOCAL_SWEDISH}" if prefix == "sv" else ""
        msg = (
            f"narration.locale {locale!r} is not a language {provider} can speak."
            f" It supports: {', '.join(sorted(supported))}."
            f" Set narration.locale to one of those, or narration.tts to a voice that speaks"
            f" {prefix!r}.{detail}"
        )
        raise LanguageUnsupportedError(msg)

    qwen = "auto"
    if provider == "qwen3tts":
        wanted = QWEN_LOCALE_TO_LANGUAGE[prefix]
        if language:
            asked = language.strip().lower()
            if asked not in QWEN_LANGUAGES:
                msg = (
                    f"narration.qwen_language {language!r} is not one Qwen3-TTS declares."
                    f" It accepts: {', '.join(sorted(QWEN_LANGUAGES))}."
                )
                raise LanguageUnsupportedError(msg)
            if asked not in ("auto", wanted):
                msg = (
                    f"narration.qwen_language {language!r} disagrees with narration.locale"
                    f" {locale!r}, which is {wanted}. The audio would be spoken in one language"
                    f" and labelled as another. Set them to agree, or leave the language 'auto'."
                )
                raise LanguageUnsupportedError(msg)
            qwen = asked if asked != "auto" else wanted
        else:
            qwen = wanted
    return LanguageChoice(
        locale=locale, prefix=prefix, qwen_language=qwen, kokoro_lang_code=kokoro_lang_code(locale)
    )
