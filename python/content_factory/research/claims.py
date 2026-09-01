"""Deterministic claim machinery (2.14, section 10): classification, numeric verification against
evidence and datasets, evidence-requirement compilation, staleness. No model decides a pass."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from content_factory.schemas.render import DatasetTable
from content_factory.schemas.research import (
    ClaimKind,
    ClaimRecord,
    Criticality,
    EvidenceRecord,
    EvidenceRequirement,
    NumericValue,
    SourceRecord,
    VerificationStatus,
)

_OPINION = re.compile(
    r"\b(we|i)\s+(think|believe|feel|reckon)|\bin (my|our) (view|opinion)\b", re.I
)
_ESTIMATE = re.compile(
    r"\b(about|around|roughly|approximately|an estimated|estimated at|nearly|close to)\b", re.I
)
_PROMO = re.compile(
    r"\b(best|greatest|unbeatable|revolutionary|game.chang|must.have|buy now|limited offer)\b", re.I
)
_QUOTE = re.compile(r"[\"“][^\"”]{10,}[\"”]\s*(said|says|according to|—|-)\s+", re.I)
_ATTRIBUTED = re.compile(r"\baccording to\b|\bsaid\b|\bsays\b", re.I)
_NUMBERISH = re.compile(r"\d")
_HIGH_STAKES = re.compile(
    r"\b(dosage|diagnos|cure|treatment|invest|returns?|guarantee|election|vote for|allegation)\b",
    re.I,
)

_NUMBER = re.compile(
    r"(?<![\w.])"
    r"(?P<value>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?!\d)\s*"
    r"(?P<scale>trillion|billion|million|thousand|bn|k)?\s*"
    r"(?P<unit>%|percent|per cent|percentage points?|TWh|GWh|MWh|GW|MW|SEK|EUR|USD|kr|€|\$)?",
    re.I,
)
_SCALES = {"thousand": 1e3, "k": 1e3, "million": 1e6, "billion": 1e9, "bn": 1e9, "trillion": 1e12}
_UNIT_NORMALIZE = {
    "percent": "%",
    "per cent": "%",
    "kr": "SEK",
    "€": "EUR",
    "$": "USD",
    "percentage point": "pp",
    "percentage points": "pp",
}
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


@dataclass(frozen=True)
class ParsedNumber:
    value: float
    unit: str
    period: str


def parse_numbers(text: str) -> list[ParsedNumber]:
    year_match = _YEAR.search(text)
    period = year_match.group(0) if year_match else ""
    out: list[ParsedNumber] = []
    for m in _NUMBER.finditer(text):
        raw = m.group("value").replace(",", "")
        value = float(raw)
        # Skip bare years (they matched the number pattern but are periods, not quantities).
        if not m.group("unit") and not m.group("scale") and _YEAR.fullmatch(m.group("value")):
            continue
        if m.group("scale"):
            value *= _SCALES[m.group("scale").lower()]
        unit = m.group("unit") or ""
        unit = _UNIT_NORMALIZE.get(unit.lower(), unit)
        out.append(ParsedNumber(value=value, unit=unit, period=period))
    return out


def classify_statement(statement: str, *, operator_assertions: tuple[str, ...] = ()) -> ClaimKind:
    s = statement.strip()
    if s in operator_assertions:
        return ClaimKind.operator_assertion
    if _QUOTE.search(s) or (_ATTRIBUTED.search(s) and '"' in s):
        return ClaimKind.attributed_quotation
    if _OPINION.search(s):
        return ClaimKind.opinion
    if _PROMO.search(s) and not _NUMBERISH.search(s):
        return ClaimKind.promotional
    if _ESTIMATE.search(s) and _NUMBERISH.search(s):
        return ClaimKind.estimate
    if _NUMBERISH.search(s):
        return ClaimKind.externally_verifiable_fact
    return ClaimKind.opinion if _PROMO.search(s) else ClaimKind.externally_verifiable_fact


def criticality_of(statement: str, kind: ClaimKind) -> Criticality:
    if kind in {ClaimKind.opinion, ClaimKind.promotional, ClaimKind.illustration}:
        return Criticality.low
    if _HIGH_STAKES.search(statement):
        return Criticality.high
    if kind in {ClaimKind.externally_verifiable_fact, ClaimKind.estimate} and _NUMBERISH.search(
        statement
    ):
        return Criticality.high
    return Criticality.medium


def compile_requirements(
    statements: list[str], *, operator_assertions: tuple[str, ...] = (), freshness_days: int = 30
) -> list[EvidenceRequirement]:
    out: list[EvidenceRequirement] = []
    for s in statements:
        kind = classify_statement(s, operator_assertions=operator_assertions)
        crit = criticality_of(s, kind)
        requires = kind in {
            ClaimKind.externally_verifiable_fact,
            ClaimKind.attributed_quotation,
            ClaimKind.estimate,
        }
        independent = (
            2 if (crit == Criticality.high and _HIGH_STAKES.search(s)) else (1 if requires else 0)
        )
        out.append(
            EvidenceRequirement(
                statement=s,
                kind=kind,
                criticality=crit,
                requires_evidence=requires,
                requires_independent_sources=independent,
                blocks_full_auto=bool(_HIGH_STAKES.search(s)),
                reason={
                    ClaimKind.externally_verifiable_fact: "verifiable fact: needs captured evidence",  # noqa: E501
                    ClaimKind.attributed_quotation: "quotation: needs the original source",
                    ClaimKind.estimate: "estimate: needs the estimate's source and an on-screen label",  # noqa: E501
                    ClaimKind.operator_assertion: "operator-supplied: recorded as the operator's statement, never independently verified silently",  # noqa: E501
                    ClaimKind.opinion: "opinion: no citation required, no fake citations",
                    ClaimKind.promotional: "promotional language: no citation; tone gates apply",
                    ClaimKind.illustration: "illustration: labeled on screen, no citation",
                }[kind],
            )
        )
    return out


_REL_TOL = 0.005  # exact within rounding
_CAVEAT_TOL = 0.05  # supported_with_caveat within 5 %


def _match(
    claim_num: ParsedNumber, candidates: list[ParsedNumber]
) -> tuple[VerificationStatus, str]:
    unit_matches = [
        c for c in candidates if c.unit == claim_num.unit or not claim_num.unit or not c.unit
    ]
    same_period = [
        c
        for c in unit_matches
        if not claim_num.period or not c.period or c.period == claim_num.period
    ]
    pool = same_period or unit_matches
    for c in pool:
        if claim_num.value == 0:
            if c.value == 0:
                return VerificationStatus.supported, ""
            continue
        rel = abs(c.value - claim_num.value) / abs(claim_num.value)
        if rel <= _REL_TOL:
            if claim_num.period and c.period and claim_num.period != c.period:
                return (
                    VerificationStatus.supported_with_caveat,
                    f"evidence period {c.period} differs from claim period {claim_num.period}",
                )
            if not same_period and claim_num.period:
                return (
                    VerificationStatus.supported_with_caveat,
                    "evidence does not state the claim's period",
                )
            return VerificationStatus.supported, ""
        if rel <= _CAVEAT_TOL:
            return (
                VerificationStatus.supported_with_caveat,
                f"evidence value {c.value:g} differs from claim {claim_num.value:g} by {rel:.1%} (rounding?)",  # noqa: E501
            )
    if unit_matches != candidates and not unit_matches:
        return VerificationStatus.unsupported, f"no evidence number carries unit {claim_num.unit!r}"
    return VerificationStatus.unsupported, "no evidence number matches"


def verify_numeric_claim(
    statement: str,
    evidence: list[EvidenceRecord],
    *,
    dataset: DatasetTable | None = None,
    sources_by_id: dict[str, SourceRecord] | None = None,
    freshness_days: int = 365,
    today: date | None = None,
) -> tuple[VerificationStatus, NumericValue | None, tuple[str, ...]]:
    nums = parse_numbers(statement)
    if not nums:
        return VerificationStatus.needs_review, None, ("statement carries no parseable number",)
    claim_num = nums[0]
    candidates: list[ParsedNumber] = []
    for ev in evidence:
        candidates.extend(parse_numbers(ev.excerpt))
    if dataset is not None:
        for row in dataset.rows:
            for col, val in row.items():
                if isinstance(val, int | float):
                    candidates.append(
                        ParsedNumber(
                            value=float(val),
                            unit=dataset.unit,
                            period=str(row.get("year", "")) or "",
                        )
                    )
                del col
    if not candidates:
        return VerificationStatus.unsupported, None, ("no numeric evidence captured",)
    status, note = _match(claim_num, candidates)
    caveats = (note,) if note else ()
    # Staleness: every supporting source must be within the freshness policy when dated.
    if (
        status in {VerificationStatus.supported, VerificationStatus.supported_with_caveat}
        and sources_by_id
    ):
        today = today or datetime.now(UTC).date()
        for ev in evidence:
            src = sources_by_id.get(ev.source_id)
            if src and src.published_at:
                try:
                    published = date.fromisoformat(src.published_at[:10])
                except ValueError:
                    continue
                if (today - published).days > freshness_days:
                    return (
                        VerificationStatus.stale,
                        NumericValue(
                            value=claim_num.value, unit=claim_num.unit, period=claim_num.period
                        ),
                        (
                            *caveats,
                            f"source {src.source_id} published {src.published_at} exceeds freshness policy {freshness_days} d",  # noqa: E501
                        ),
                    )
    numeric = NumericValue(value=claim_num.value, unit=claim_num.unit, period=claim_num.period)
    return status, numeric, caveats


def build_claim(
    claim_id: str,
    workspace_id: str,
    statement: str,
    evidence: list[EvidenceRecord],
    *,
    operator_assertions: tuple[str, ...] = (),
    dataset: DatasetTable | None = None,
    sources_by_id: dict[str, SourceRecord] | None = None,
    dataset_id: str | None = None,
    freshness_days: int = 365,
    today: date | None = None,
    checked_at: str = "",
) -> ClaimRecord:
    kind = classify_statement(statement, operator_assertions=operator_assertions)
    crit = criticality_of(statement, kind)
    checked = checked_at or datetime.now(UTC).date().isoformat()
    if kind in {ClaimKind.opinion, ClaimKind.promotional, ClaimKind.illustration}:
        status: VerificationStatus = VerificationStatus.not_applicable
        numeric, caveats = None, ()
    elif kind == ClaimKind.operator_assertion:
        status = VerificationStatus.needs_review
        numeric, caveats = (
            None,
            ("operator-supplied assertion; presented as such, not independently verified",),
        )
    elif _NUMBERISH.search(statement):
        status, numeric, caveats = verify_numeric_claim(
            statement,
            evidence,
            dataset=dataset,
            sources_by_id=sources_by_id,
            freshness_days=freshness_days,
            today=today,
        )
    else:
        status = VerificationStatus.supported if evidence else VerificationStatus.unsupported
        numeric, caveats = None, () if evidence else ("no evidence captured",)
    return ClaimRecord(
        claim_id=claim_id,
        workspace_id=workspace_id,
        statement=statement,
        kind=kind,
        criticality=crit,
        status=status,
        evidence_ids=tuple(e.evidence_id for e in evidence),
        dataset_id=dataset_id,
        numeric=numeric,
        caveats=tuple(caveats),
        checked_at=checked,
    )
