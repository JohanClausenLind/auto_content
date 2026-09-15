"""The alignment gate compares words as words and numbers as numbers."""

from __future__ import annotations

from content_factory.audio.normalize import (
    NUMBER_TOKEN,
    is_number_word,
    spoken_word_shape,
    tokenize_words,
)
from content_factory.human_tasks.validation import _script_similarity


def test_the_two_measured_failures_now_agree_exactly() -> None:
    """The regressions this exists for, in the exact words the aligner produced."""
    score, diff = _script_similarity("40.8 terawatt-hours", "40 8 terawatt hours")
    assert score == 1.0, (score, diff)
    score, diff = _script_similarity(
        "Wind added twelve hundred megawatts and four fifty more since.",
        "wind added 1200 megawatts and 450 more since",
    )
    assert score > 0.95, (score, diff)


def test_a_dropped_figure_still_fails() -> None:
    """The failure that matters: the sentinel is per *run*."""
    score, diff = _script_similarity(
        "Wind supplied twenty-one per cent in 2025.", "wind supplied per cent"
    )
    assert score < 0.9
    assert any("figure(s) in the script" in line for line in diff)


def test_a_garbled_read_still_fails() -> None:
    """What the gate is actually for: a truncated or hallucinated read."""
    score, _diff = _script_similarity(
        "Three things drove it: new turbines, better siting, and cheaper finance.",
        "the weather in stockholm is mild for the season",
    )
    assert score < 0.4


def test_a_truncated_read_still_fails() -> None:
    score, _diff = _script_similarity(
        "In 2025, wind supplied about a fifth of Sweden's electricity.", "in 2025 wind supplied"
    )
    assert score < 0.8


def test_an_identical_read_scores_one() -> None:
    line = "Three things drove it: new turbines, better siting, and cheaper finance."
    score, diff = _script_similarity(line, line.lower())
    assert score == 1.0
    assert diff == ()


def test_the_diff_is_readable_by_a_person() -> None:
    """The sentinel is a private-use control character."""
    _score, diff = _script_similarity("about 40 per cent", "about 55 per cent")
    joined = " ".join(diff)
    assert NUMBER_TOKEN not in joined
    # Which is why the numbers become the word "<number>" there. (Both sides mask to the same
    # token, so a *wrong* figure does not show up in the diff at all — see spoken_word_shape.)
    assert joined == "" or "<number>" in joined


def test_number_words_and_digits_are_both_numbers() -> None:
    for word in ("40", "40.8", "1,200", "21%", "twelve", "hundred", "forty", "point", "million"):
        assert is_number_word(word), word
    for word in ("terawatt", "wind", "fifth", "and", "per"):
        assert not is_number_word(word), word


def test_a_run_of_number_words_is_one_figure() -> None:
    shape, runs = spoken_word_shape("two point five million tonnes")
    assert shape == [NUMBER_TOKEN, "tonnes"]
    assert runs == 1


def test_separate_figures_stay_separate() -> None:
    shape, runs = spoken_word_shape("1200 and 450")
    assert shape == [NUMBER_TOKEN, "and", NUMBER_TOKEN]
    assert runs == 2
    # And the spoken form of the same two figures gives the same shape.
    assert spoken_word_shape("twelve hundred and four fifty") == (shape, runs)


def test_a_line_with_no_numbers_is_unchanged() -> None:
    line = "new turbines better siting and cheaper finance"
    shape, runs = spoken_word_shape(line)
    assert shape == tokenize_words(line)
    assert runs == 0


def test_a_hyphenated_compound_agrees_with_the_words_it_is_made_of() -> None:
    """Hyphens are orthography and the aligner does not write them."""
    assert _script_similarity("twenty-one per cent", "21 per cent")[0] == 1.0
    assert _script_similarity("a well-sited turbine", "a well sited turbine")[0] == 1.0
    assert spoken_word_shape("terawatt-hours") == (["terawatt", "hours"], 0)


def test_a_year_is_a_figure() -> None:
    """It is one for this purpose: the aligner may write "twenty twenty five" for "2025"."""
    shape, runs = spoken_word_shape("in 2025 wind supplied a fifth")
    assert shape == ["in", NUMBER_TOKEN, "wind", "supplied", "a", "fifth"]
    assert runs == 1
    assert (
        _script_similarity("in 2025 wind supplied", "in twenty twenty five wind supplied")[0] == 1.0
    )
