"""Phase 6 topics: ranking by the channel's criteria and promises the evidence can carry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from content_factory.explainer.topics import (
    DEFAULT_WEIGHTS,
    TopicCandidate,
    check_promises,
    rank_topics,
    ranking_markdown,
)
from content_factory.schemas.explainer import EvidencePack, ScriptPlan, TitlePromise

AMDAHL = Path(__file__).resolve().parents[2] / "fixtures" / "explainer" / "amdahl"


def candidate(topic_id: str, score: int = 3, sponsor_fit: int = 1, **scores: int) -> TopicCandidate:
    return TopicCandidate.model_validate(
        {
            "topic_id": topic_id,
            "question": f"Question for {topic_id}?",
            "angle": "One calculation the sources do not make.",
            "sponsor_fit": sponsor_fit,
            "sources": ["https://example.org/data"],
            **dict.fromkeys(DEFAULT_WEIGHTS, score),
            **scores,
        }
    )


def amdahl() -> tuple[EvidencePack, ScriptPlan]:
    return (
        EvidencePack.model_validate_json((AMDAHL / "pack.json").read_text()),
        ScriptPlan.model_validate_json((AMDAHL / "script.json").read_text()),
    )


def observed_pack() -> EvidencePack:
    """Amdahl's pack with its two latency parts observed and the totals read as interpretations."""
    payload: dict[str, Any] = json.loads((AMDAHL / "pack.json").read_text())
    for claim in payload["claims"]:
        if claim["claim_id"] in {"clm_compute_80ms", "clm_other_20ms"}:
            claim.update(epistemic_class="observation", evidence_ids=["evi_amdahl_title"])
        if claim["claim_id"] in {"clm_total_100ms", "clm_total_60ms"}:
            claim["epistemic_class"] = "interpretation"
    return EvidencePack.model_validate(payload)


def with_promises(script: ScriptPlan, *promises: tuple[str, tuple[str, ...]]) -> ScriptPlan:
    return script.model_copy(
        update={
            "promises": tuple(
                TitlePromise(title=title, thumbnail_promise="A promise", claim_ids=ids)
                for title, ids in promises
            )
        }
    )


def test_rank_topics_orders_by_the_weighted_sum_of_primary_criteria() -> None:
    ranked = rank_topics(
        [candidate("top_middle01", 3), candidate("top_best0001", 5), candidate("top_worst001", 1)]
    )
    assert [r.candidate.topic_id for r in ranked] == [
        "top_best0001",
        "top_middle01",
        "top_worst001",
    ]
    assert [r.rank for r in ranked] == [1, 2, 3]
    assert ranked[0].score == 30 and not any(r.tied for r in ranked)


def test_sponsor_fit_breaks_a_tie() -> None:
    ranked = rank_topics(
        [candidate("top_lowfit01", 4, sponsor_fit=1), candidate("top_highfit1", 4, sponsor_fit=5)]
    )
    assert [r.candidate.topic_id for r in ranked] == ["top_highfit1", "top_lowfit01"]
    assert all(r.tied for r in ranked)


def test_sponsor_fit_never_outweighs_a_higher_score() -> None:
    ranked = rank_topics(
        [
            candidate("top_sponsor1", 3, sponsor_fit=5),
            candidate("top_better01", 3, sponsor_fit=1, shelf_life=4),
        ]
    )
    assert [r.candidate.topic_id for r in ranked] == ["top_better01", "top_sponsor1"]


def test_weights_change_the_order() -> None:
    effortless = candidate("top_easy0001", 3, production_effort=5)
    asked = candidate(
        "top_asked001", 3, audience_question=5, evidence_availability=5, production_effort=2
    )
    assert rank_topics([effortless, asked])[0].candidate.topic_id == "top_asked001"
    weights = {**DEFAULT_WEIGHTS, "production_effort": 3.0}
    assert rank_topics([effortless, asked], weights)[0].candidate.topic_id == "top_easy0001"


def test_rank_topics_refuses_weights_that_miss_or_invent_a_criterion() -> None:
    weights = {k: v for k, v in DEFAULT_WEIGHTS.items() if k != "shelf_life"}
    with pytest.raises(ValueError, match="shelf_life"):
        rank_topics([candidate("top_one00001")], weights)
    with pytest.raises(ValueError, match="sponsor_fit"):
        rank_topics([candidate("top_one00001")], {**DEFAULT_WEIGHTS, "sponsor_fit": 1.0})


def test_rank_topics_refuses_duplicate_topic_ids() -> None:
    with pytest.raises(ValueError, match="duplicate topic ids: top_twice001"):
        rank_topics([candidate("top_twice001"), candidate("top_twice001")])


def test_scores_stay_between_one_and_five() -> None:
    with pytest.raises(ValidationError):
        candidate("top_bad00001", shelf_life=6)


def test_amdahl_promises_on_illustrative_claims_are_rejected() -> None:
    pack, script = amdahl()
    issues = check_promises(script, pack)
    assert [(i.kind, i.where) for i in issues] == [
        ("evidence", "ScriptPlan.promises[0].claim_ids"),
        ("evidence", "ScriptPlan.promises[2].claim_ids"),
    ]
    assert "source clm_compute_80ms, clm_other_20ms, clm_speedup_2x " in issues[0].fix
    assert issues[0].ids == ("clm_total_100ms", "clm_total_60ms")
    assert "source clm_other_20ms " in issues[1].fix


def test_promise_on_an_observation_passes() -> None:
    pack, script = amdahl()
    assert check_promises(with_promises(script, ("Amdahl's law", ("clm_amdahl_1967",))), pack) == []


def test_promise_on_a_claim_derived_from_observations_passes() -> None:
    _, script = amdahl()
    promise = ("Where the 100 ms goes", ("clm_total_100ms",))
    assert check_promises(with_promises(script, promise), observed_pack()) == []


def test_derived_promise_with_an_illustrative_input_names_that_input() -> None:
    _, script = amdahl()
    issues = check_promises(with_promises(script, ("Why 60", ("clm_total_60ms",))), observed_pack())
    assert [i.kind for i in issues] == ["evidence"]
    assert "source clm_speedup_2x " in issues[0].fix


def test_near_identical_titles_are_rejected() -> None:
    pack, script = amdahl()
    observed = ("clm_amdahl_1967",)
    similar = with_promises(
        script,
        ("Why doubling the processor does not halve the wait", observed),
        ("Why doubling the processor doesn't halve the wait!", observed),
        ("Amdahl's law in one bar chart", observed),
    )
    issues = check_promises(similar, pack)
    assert [(i.kind, i.where) for i in issues] == [
        ("invalid_value", "ScriptPlan.promises[1].title")
    ]
    assert "promise 0" in issues[0].message


def test_promise_citing_an_unknown_claim_is_an_invalid_reference() -> None:
    pack, script = amdahl()
    unknown = with_promises(script, ("Amdahl's law", ("clm_amdahl_1967", "clm_missing001")))
    issues = check_promises(unknown, pack)
    assert [(i.kind, i.ids) for i in issues] == [("invalid_reference", ("clm_missing001",))]


def test_ranking_markdown_lists_topics_in_rank_order_and_marks_ties() -> None:
    ranked = rank_topics(
        [
            candidate("top_second01", 3, sponsor_fit=1),
            candidate("top_first001", 5),
            candidate("top_second02", 3, sponsor_fit=4),
        ]
    )
    text = ranking_markdown(ranked)
    rows = [line for line in text.splitlines() if line.startswith("| ") and "`top_" in line]
    assert [row.split(" | ")[1] for row in rows] == [
        "`top_first001`",
        "`top_second02`",
        "`top_second01`",
    ]
    assert rows[1].startswith("| 2= |") and rows[0].startswith("| 1 |")
    assert "- <https://example.org/data>" in text
