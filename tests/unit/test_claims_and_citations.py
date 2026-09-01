"""Claim classification, numeric verification, staleness, requirements, and the script gate."""

from __future__ import annotations

from datetime import date

from content_factory.research.citations import citations_json, script_claim_gate, sources_markdown
from content_factory.research.claims import (
    build_claim,
    classify_statement,
    compile_requirements,
    parse_numbers,
    verify_numeric_claim,
)
from content_factory.schemas.fixtures import WS, sample_dataset, sample_story_plan
from content_factory.schemas.research import (
    ClaimKind,
    Criticality,
    EvidenceLocator,
    EvidenceRecord,
    SourceRecord,
    VerificationStatus,
)

TODAY = date(2026, 9, 1)


def ev(eid: str, text: str, source_id: str = "src_energimynd01") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=eid,
        source_id=source_id,
        excerpt=text,
        locator=EvidenceLocator(kind="char_range", start=0, end=len(text)),
        captured_at="2026-09-01",
    )


def src(sid: str = "src_energimynd01", published: str | None = "2026-03-01") -> SourceRecord:
    return SourceRecord(
        source_id=sid,
        workspace_id=WS,
        canonical_url="https://example.se/report",
        requested_url="https://example.se/report",
        final_url="https://example.se/report",
        title="Report",
        publisher="Energimyndigheten",
        accessed_at="2026-09-01",
        capture_sha256="0" * 64,
        content_type="text/html",
        size_bytes=1000,
        published_at=published,
    )


def test_classification_covers_the_kinds() -> None:
    ops = ("We are not selling anything.",)
    assert (
        classify_statement("Wind supplied 21% of electricity in 2025.")
        == ClaimKind.externally_verifiable_fact
    )
    assert (
        classify_statement("Roughly 21 percent of electricity came from wind.")
        == ClaimKind.estimate
    )
    assert classify_statement("We believe wind will keep growing.") == ClaimKind.opinion
    assert (
        classify_statement('"Wind is the backbone of the grid," said the minister.')
        == ClaimKind.attributed_quotation
    )
    assert classify_statement("The best turbines. Buy now!") == ClaimKind.promotional
    assert (
        classify_statement("We are not selling anything.", operator_assertions=ops)
        == ClaimKind.operator_assertion
    )


def test_parse_numbers_units_scales_periods() -> None:
    nums = parse_numbers(
        "Output rose to 45.6 TWh in 2025, about 21 % of the total, worth 3.2 billion SEK."
    )
    assert [(n.value, n.unit) for n in nums] == [(45.6, "TWh"), (21.0, "%"), (3.2e9, "SEK")]
    assert all(n.period == "2025" for n in nums)
    assert (
        parse_numbers("In 2025 nothing was measured.") == []
    )  # bare year is a period, not a value


def test_numeric_verification_supported_caveat_unsupported_and_dataset() -> None:
    statement = "Wind supplied 21% of Sweden's electricity in 2025."
    supported, num, _ = verify_numeric_claim(
        statement, [ev("evd_a0000000001", "wind reached 21 per cent of generation in 2025")]
    )
    assert supported == VerificationStatus.supported and num and num.unit == "%"
    caveat, _, notes = verify_numeric_claim(
        statement, [ev("evd_a0000000002", "wind accounted for 20.5% of generation in 2025")]
    )
    assert caveat == VerificationStatus.supported_with_caveat and "differs" in notes[0]
    period, _, notes2 = verify_numeric_claim(
        statement, [ev("evd_a0000000003", "wind reached 21% of generation in 2024")]
    )
    assert period == VerificationStatus.supported_with_caveat and "period" in notes2[0]
    bad, _, _ = verify_numeric_claim(
        statement, [ev("evd_a0000000004", "wind reached 34% of generation in 2025")]
    )
    assert bad == VerificationStatus.unsupported
    ds_ok, _, _ = verify_numeric_claim(statement, [], dataset=sample_dataset())
    assert ds_ok == VerificationStatus.supported


def test_staleness_follows_policy() -> None:
    statement = "Wind supplied 21% of electricity in 2025."
    evidence = [ev("evd_a0000000005", "21% in 2025")]
    sources = {"src_energimynd01": src(published="2024-01-15")}
    status, _, caveats = verify_numeric_claim(
        statement, evidence, sources_by_id=sources, freshness_days=365, today=TODAY
    )
    assert status == VerificationStatus.stale and "freshness" in caveats[-1]
    fresh, _, _ = verify_numeric_claim(
        statement,
        evidence,
        sources_by_id={"src_energimynd01": src(published="2026-06-01")},
        freshness_days=365,
        today=TODAY,
    )
    assert fresh == VerificationStatus.supported


def test_operator_assertions_are_never_silently_verified() -> None:
    claim = build_claim(
        "clm_op00000001",
        WS,
        "We are not selling anything.",
        [],
        operator_assertions=("We are not selling anything.",),
        checked_at="2026-09-01",
    )
    assert claim.kind == ClaimKind.operator_assertion
    assert claim.status == VerificationStatus.needs_review
    assert "not independently verified" in claim.caveats[0]


def test_requirements_plan_blocks_full_auto_for_high_stakes() -> None:
    reqs = compile_requirements(
        [
            "Wind supplied 21% of electricity in 2025.",
            "We think this is exciting.",
            "This treatment cures fatigue in 90% of patients.",
        ],
        freshness_days=30,
    )
    fact, opinion, medical = reqs
    assert (
        fact.requires_evidence
        and fact.criticality == Criticality.high
        and not fact.blocks_full_auto
    )
    assert not opinion.requires_evidence and opinion.reason.startswith("opinion")
    assert medical.blocks_full_auto and medical.requires_independent_sources == 2


def test_script_claim_gate_blocks_uncited_critical_claims() -> None:
    plan = sample_story_plan()
    evidence = [ev("evd_a0000000006", "wind reached 21% of generation in 2025")]
    good = [
        build_claim(
            "clm_wind0000001",
            WS,
            "Wind supplied 21% of Sweden's electricity in 2025.",
            evidence,
            dataset=sample_dataset(),
            dataset_id="ds_wind00000001",
            checked_at="2026-09-01",
        ),
        build_claim(
            "clm_wind0000002",
            WS,
            "The share roughly doubled from 11% in 2018 to 21% in 2025.",
            [ev("evd_a0000000007", "from 11% in 2018 to 21% in 2025")],
            checked_at="2026-09-01",
        ),
    ]
    ok = script_claim_gate(plan, good)
    assert ok.passed, ok.findings
    missing = script_claim_gate(plan, good[:1])
    assert not missing.passed and any(f.check == "missing_claim" for f in missing.findings)
    bad = [good[0].model_copy(update={"status": VerificationStatus.unsupported}), good[1]]
    blocked = script_claim_gate(plan, bad)
    assert not blocked.passed and any(
        f.check == "claim_status" and f.severity == "blocker" for f in blocked.findings
    )


def test_citation_exports_render() -> None:
    evidence = [ev("evd_a0000000008", "wind reached 21% in 2025")]
    claims = [
        build_claim(
            "clm_wind0000001",
            WS,
            "Wind supplied 21% of electricity in 2025.",
            evidence,
            checked_at="2026-09-01",
        )
    ]
    md = sources_markdown([src()])
    assert (
        md.startswith("# Sources") and "energimyndigheten" in md.lower()
    ) or "Energimyndigheten" in md
    cj = citations_json(claims, evidence, [src()])
    assert cj["claims"][0]["citations"][0]["url"] == "https://example.se/report"
    assert cj["claims"][0]["status"] == "supported"
