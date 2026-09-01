"""Citation exports and the script-claim gate (section 10): research/{sources,evidence,claims}.json,
final/sources.md, final/citations.json; a critical statement without supporting claims blocks."""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.qc.media import Finding, QCResult, Severity
from content_factory.schemas.research import (
    ClaimRecord,
    Criticality,
    EvidenceRecord,
    SourceRecord,
    VerificationStatus,
)
from content_factory.schemas.scenes import StoryPlan

_OK = {
    VerificationStatus.supported,
    VerificationStatus.supported_with_caveat,
    VerificationStatus.not_applicable,
}


def export_research(
    out_dir: Path,
    sources: list[SourceRecord],
    evidence: list[EvidenceRecord],
    claims: list[ClaimRecord],
) -> None:
    research = out_dir / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "sources.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in sources], indent=1, sort_keys=True)
    )
    (research / "evidence.json").write_text(
        json.dumps([e.model_dump(mode="json") for e in evidence], indent=1, sort_keys=True)
    )
    (research / "claims.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in claims], indent=1, sort_keys=True)
    )
    final = out_dir / "final"
    final.mkdir(parents=True, exist_ok=True)
    (final / "sources.md").write_text(sources_markdown(sources))
    (final / "citations.json").write_text(
        json.dumps(citations_json(claims, evidence, sources), indent=1, sort_keys=True)
    )


def sources_markdown(sources: list[SourceRecord]) -> str:
    lines = ["# Sources", ""]
    for s in sorted(sources, key=lambda x: (x.publisher, x.title)):
        date = f" ({s.published_at})" if s.published_at else ""
        lines.append(
            f"- {s.publisher or s.canonical_url}: [{s.title or s.canonical_url}]({s.canonical_url}){date}. Accessed {s.accessed_at}."  # noqa: E501
        )
    return "\n".join(lines) + "\n"


def citations_json(
    claims: list[ClaimRecord], evidence: list[EvidenceRecord], sources: list[SourceRecord]
) -> dict:
    ev_by_id = {e.evidence_id: e for e in evidence}
    src_by_id = {s.source_id: s for s in sources}
    out = []
    for c in claims:
        cites = []
        for eid in c.evidence_ids:
            ev = ev_by_id.get(eid)
            if not ev:
                continue
            src = src_by_id.get(ev.source_id)
            cites.append(
                {
                    "evidence_id": eid,
                    "source_id": ev.source_id,
                    "url": src.canonical_url if src else None,
                    "excerpt": ev.excerpt,
                }
            )
        out.append(
            {
                "claim_id": c.claim_id,
                "statement": c.statement,
                "kind": c.kind.value,
                "status": c.status.value,
                "caveats": list(c.caveats),
                "citations": cites,
            }
        )
    return {"claims": out}


def script_claim_gate(plan: StoryPlan, claims: list[ClaimRecord]) -> QCResult:
    """Contract QC (blocking): every claim-linked statement resolves to a healthy claim; every
    critical verifiable claim used by the plan is supported; unsupported claims may not appear."""
    findings: list[Finding] = []
    by_id = {c.claim_id: c for c in claims}
    referenced: set[str] = set()
    for beat in plan.beats:
        referenced.update(beat.claim_ids)
    for scene in plan.scenes:
        for attr in (
            "title",
            "subtitle",
            "label",
            "context",
            "caption",
            "text",
            "quote",
            "definition",
            "heading",
        ):
            ref = getattr(scene, attr, None)
            if ref is not None and hasattr(ref, "claim_ids"):
                referenced.update(ref.claim_ids)
        data = getattr(scene, "value", None) or getattr(scene, "data", None)
        if data is not None and getattr(data, "claim_id", None):
            referenced.add(data.claim_id)
    for cid in sorted(referenced):
        claim = by_id.get(cid)
        if claim is None:
            findings.append(
                Finding(
                    "missing_claim", Severity.blocker, f"statement references unknown claim {cid}"
                )
            )
            continue
        if claim.status not in _OK:
            sev = Severity.blocker if claim.criticality == Criticality.high else Severity.critical
            findings.append(
                Finding(
                    "claim_status",
                    sev,
                    f"claim {cid} is {claim.status.value}: {claim.statement[:80]!r}",
                )
            )
        if claim.status == VerificationStatus.supported_with_caveat and not claim.caveats:
            findings.append(
                Finding(
                    "caveat_missing",
                    Severity.major,
                    f"claim {cid} is caveated but carries no caveat text",
                )
            )
    return QCResult(
        tuple(findings), {"referenced_claims": len(referenced), "claims_total": len(claims)}
    )
