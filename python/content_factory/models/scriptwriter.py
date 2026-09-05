"""One model call: beats and their typed scenes, from claim cards, datasets and a word budget.

This is the largest genuine gap in the pipeline. `plan_story` loads a hand-written `StoryPlan`
fixture or falls back to the demo's, so a topic an operator actually has becomes a film only if
somebody writes the plan by hand — the twenty-one-kind scene grammar, the claim links and the beat
timings included.

**One call, not two.** The obvious shape is to ask for beats and then, separately, ask for the
visuals. That resends every beat as context and pays for it twice, and worse, it lets the second
call disagree with the first: a beat whose sentence says "three drivers" and whose scene turns out
to be a big number. Here a beat and its scene are one object in one response.

**The model chooses a kind and fills a flat payload; Python builds the SceneSpec.** A discriminated
union of twenty-one branches is a schema a local 8-to-27B model does not reliably satisfy even
under constrained decoding, and the typing decision is exactly the part that must not be
approximated. So `DraftBeat` is flat, and `_build_scene` is deterministic code that either produces
a valid `SceneSpec` or records a gap.

**Nothing unsupported reaches generation.** Four validators run before a plan exists at all:

* every ``claim_id`` is one of the cards that were shown (a model cannot cite a claim into being);
* every number in a beat's text is in a dataset row or in a cited claim's verified value;
* every ``scene_kind`` is one the renderer actually draws (`scenes.kinds.IMPLEMENTED_KINDS`);
* every asset id, source id and dataset id was offered.

A beat that fails any of them is dropped into ``story/gaps.json`` with the reason. The film is
made of what survived, and the gaps are the operator's next task — not a silent omission, and not
a scene naming a source that does not exist.

Off by default (`execution.local_scriptwriter`), and it stays off until the evaluation pack in
`models/evaluation.py` scores a build. A writer whose output nobody has scored is not a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from content_factory.budgets.ledger import Scope
from content_factory.models.gateway import GatewayOptions, ModelGateway
from content_factory.research.claims import parse_numbers
from content_factory.scenes.kinds import (
    ASSET_BACKED_KINDS,
    DATA_BACKED_KINDS,
    IMPLEMENTED_KINDS,
    SOURCE_BACKED_KINDS,
)
from content_factory.schemas import scenes as sc
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.content import ContentCampaign
from content_factory.schemas.documentary import EpisodeOutline, EpisodeSectionKind
from content_factory.schemas.render import DatasetTable
from content_factory.schemas.research import ClaimRecord, VerificationStatus
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutorType,
    Lifecycle,
    SkillManifest,
    SkillPermissions,
)

SCRIPTWRITER_VERSION = "0.1.0"

WRITER_KINDS: tuple[str, ...] = (
    "title",
    "section_intro",
    "big_number",
    "chart",
    "bullet_sequence",
    "quote",
    "definition",
    "callout",
    "comparison",
    "chapter_transition",
    "timeline",
    "source_card",
    "outro",
)
"""The kinds the writer is allowed to ask for: implemented, and fillable from cards and datasets.

``image``, ``screenshot``, ``map`` and ``manim_asset`` are implemented and deliberately absent —
each needs a file or a topojson region that only ``ingest`` can supply, and a model inventing an
asset id produces a scene that renders nothing. They are offered back to the writer, per run, when
the assets actually exist."""

NUMBER_TOLERANCE = 0.02
"""Two per cent. A beat may round a dataset's 34.9 to "about 35"; it may not turn it into 40.
Rounding is editorial, invention is not, and this is where the line sits."""

_SKILL = SkillManifest(
    skill_id="content.scriptwriter",
    version="0.1.0",
    status=Lifecycle.draft,
    purpose="Draft a StoryPlan's beats and typed scenes from verified claims and datasets.",
    input_schema="ContentCampaign",
    output_schema="StoryPlan",
    executor=ExecutorType.model_role,
    implementation_ref="content_factory.models.scriptwriter:draft_story_plan",
    permitted_locations=(ExecutionLocation.local_gpu, ExecutionLocation.local_cpu),
    required_models=("local_structured", "local_structured_small"),
    # No egress: the writer sees claim cards that research already captured, never the web.
    permissions=SkillPermissions(network_egress=False),
    license_evidence="Apache-2.0 (Qwen3 derivative, local)",
    cost=CostEstimator(kind="per_token", usd=0),
    timeout_seconds=900,
    max_retries=1,
)


# --- what the model is shown ------------------------------------------------------------------


class ClaimCard(BaseModel):
    """One claim, as the writer sees it. Verdict included, because an unsupported claim is
    something the writer must be able to decline rather than something hidden from it."""

    claim_id: str
    statement: str
    status: str
    numbers: tuple[str, ...] = ()
    publishers: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


def claim_cards(claims: list[ClaimRecord], *, include_unsupported: bool = False) -> list[ClaimCard]:
    """Cards for the claims a script may use.

    Unsupported claims are excluded by default rather than shown-and-forbidden: a model given a
    fact and told not to use it uses it. What it cannot see, it cannot cite.
    """
    usable = {VerificationStatus.supported, VerificationStatus.supported_with_caveat}
    out: list[ClaimCard] = []
    for claim in claims:
        if not include_unsupported and claim.status not in usable:
            continue
        numbers = ()
        if claim.numeric is not None:
            unit = claim.numeric.unit
            numbers = (f"{claim.numeric.value:g}{unit}",)
        out.append(
            ClaimCard(
                claim_id=claim.claim_id,
                statement=claim.statement,
                status=claim.status.value,
                numbers=numbers,
                caveats=claim.caveats,
            )
        )
    return out


# --- what the model returns -------------------------------------------------------------------


class DraftBeat(BaseModel):
    """One beat and its scene, flat, so a local model can satisfy the schema.

    Flat rather than a discriminated union on purpose: twenty-one branches is not a schema a local
    model fills reliably, and the branch choice is the part that must not be approximated. The
    fields below are a superset; ``_build_scene`` takes the ones the chosen kind needs and records
    a gap when a required one is missing.
    """

    section: EpisodeSectionKind
    display_text: str = Field(min_length=1, max_length=1000)
    spoken_text: str | None = None
    """How the line is *said*, when it differs from how it is written: "21%" reads aloud as
    "twenty-one per cent". Honoured by ``synthesize_narration`` and locked by ``lock_script``."""
    claim_ids: tuple[str, ...] = ()
    scene_kind: str
    headline: str = ""
    """The scene's main line: a title, a heading, a term, a callout, an outro line."""
    secondary: str = ""
    """A subtitle, a section label, a definition body, a caption, a call to action."""
    bullets: tuple[str, ...] = ()
    quote: str = ""
    attribution: str = ""
    source_ids: tuple[str, ...] = ()
    dataset_id: str = ""
    column: str = ""
    row_key: str = ""
    unit: str = ""
    chart: str = ""
    x: str = ""
    y: tuple[str, ...] = ()
    left: str = ""
    right: str = ""
    tone: Literal["neutral", "warning", "positive"] = "neutral"
    asset_id: str = ""
    alt_text: str = ""


class DraftPlan(BaseModel):
    beats: tuple[DraftBeat, ...] = Field(min_length=1, max_length=40)


@dataclass(frozen=True)
class Gap:
    """A beat the writer asked for and the validators refused, with the reason.

    Kept and written to ``story/gaps.json`` rather than swallowed: "this film has no evidence for
    the third driver" is the operator's next task, and a silently shorter film hides it.
    """

    reason: str
    section: str = ""
    display_text: str = ""
    scene_kind: str = ""


@dataclass
class DraftResult:
    plan: sc.StoryPlan | None = None
    gaps: list[Gap] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    raw: DraftPlan | None = None
    """What the model returned, before validation dropped anything.

    Kept because the evaluation pack has to score the MODEL, not the validators. Scoring the
    surviving plan would report 1.0 for "no invented numbers" by construction — the beats that
    invented one are exactly the beats that are no longer there."""


# --- validation -------------------------------------------------------------------------------


def _dataset_values(datasets: dict[str, DatasetTable]) -> set[float]:
    out: set[float] = set()
    for table in datasets.values():
        for row in table.rows:
            for value in row.values():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    out.add(float(value))
    return out


def _number_is_supported(
    value: float, dataset_values: set[float], claim_values: set[float]
) -> bool:
    """Is this figure in a dataset row, or in a claim the beat cites?

    Within :data:`NUMBER_TOLERANCE`, because a script rounds. A year is not checked — ``2025`` is a
    period, and ``parse_numbers`` already declines to treat a bare year as a quantity.
    """
    for known in dataset_values | claim_values:
        if known == value:
            return True
        if known and abs(value - known) / abs(known) <= NUMBER_TOLERANCE:
            return True
    return False


def validate_beat(
    beat: DraftBeat,
    *,
    cards: dict[str, ClaimCard],
    datasets: dict[str, DatasetTable],
    dataset_values: set[float],
    source_ids: set[str],
    asset_ids: set[str],
    allowed_kinds: set[str],
) -> str:
    """The reason this beat cannot be used, or an empty string.

    Order matters: the cheap structural checks first, so a beat naming an unimplemented kind is
    reported as that rather than as a missing field of a scene nobody can draw.
    """
    if beat.scene_kind not in IMPLEMENTED_KINDS:
        return (
            f"scene kind {beat.scene_kind!r} has no renderer"
            f" (implemented: {', '.join(sorted(IMPLEMENTED_KINDS))})"
        )
    if beat.scene_kind not in allowed_kinds:
        return f"scene kind {beat.scene_kind!r} is not available in this run"
    unknown = [c for c in beat.claim_ids if c not in cards]
    if unknown:
        return f"cites claims that were not offered: {', '.join(unknown)}"
    if beat.scene_kind in DATA_BACKED_KINDS:
        if not beat.dataset_id:
            return f"{beat.scene_kind} needs a dataset_id"
        if beat.dataset_id not in datasets:
            return f"names dataset {beat.dataset_id!r}, which this run does not have"
    if beat.scene_kind in ASSET_BACKED_KINDS and beat.asset_id not in asset_ids:
        return f"names asset {beat.asset_id!r}, which this run does not have"
    if beat.scene_kind in SOURCE_BACKED_KINDS:
        missing = [s for s in beat.source_ids if s not in source_ids]
        if beat.scene_kind != "source_card" and not beat.source_ids:
            return f"{beat.scene_kind} must name the source it is attributed to"
        if missing:
            return f"names sources this run does not have: {', '.join(missing)}"
    # Every figure on screen or in the voice has to exist somewhere it can be traced to.
    cited_values = {
        float(n.value) for cid in beat.claim_ids for n in parse_numbers(cards[cid].statement)
    }
    for text in (beat.display_text, beat.headline, beat.secondary, *beat.bullets):
        for number in parse_numbers(text):
            if not _number_is_supported(number.value, dataset_values, cited_values):
                return (
                    f"the figure {number.value:g}{number.unit} is in no dataset row and in no"
                    " claim this beat cites"
                )
    return ""


# --- the deterministic scene builder ----------------------------------------------------------


def _text(value: str, claim_ids: tuple[str, ...] = ()) -> sc.TextRef:
    return sc.TextRef(text=value.strip()[:2000], claim_ids=claim_ids)


def _build_scene(beat: DraftBeat, *, scene_id: str, beat_id: str) -> sc.SceneSpec:
    """One `DraftBeat` into the typed scene its kind requires. Raises on a missing field.

    Deterministic on purpose: the model picks the kind and supplies the words, and the shape of the
    contract is decided here, where a validator can see it.
    """
    # Annotated: every branch below spreads this into a different scene model, and without an
    # annotation the checker matches the spread against each one's `kind` Literal in turn.
    common: dict[str, Any] = {"scene_id": scene_id, "beat_id": beat_id}
    headline = beat.headline or beat.display_text
    kind = beat.scene_kind
    if kind == "title":
        return sc.TitleScene(
            **common,
            title=_text(headline, beat.claim_ids),
            subtitle=_text(beat.secondary) if beat.secondary else None,
        )
    if kind == "section_intro":
        return sc.SectionIntroScene(
            **common,
            label=_text(beat.secondary or beat.section.value.replace("_", " ")),
            heading=_text(headline, beat.claim_ids),
        )
    if kind == "big_number":
        return sc.BigNumberScene(
            **common,
            value=sc.DataRef(
                dataset_id=beat.dataset_id,
                claim_id=beat.claim_ids[0] if beat.claim_ids else None,
                column=beat.column or None,
                row_key=beat.row_key or None,
            ),
            unit=beat.unit[:32],
            label=_text(headline, beat.claim_ids),
            context=_text(beat.secondary) if beat.secondary else None,
        )
    if kind == "chart":
        chart = beat.chart if beat.chart in {c.value for c in sc.ChartKind} else "bar"
        return sc.ChartScene(
            **common,
            chart=sc.ChartKind(chart),
            data=sc.DataRef(
                dataset_id=beat.dataset_id,
                claim_id=beat.claim_ids[0] if beat.claim_ids else None,
            ),
            x=beat.x or "x",
            y=beat.y or (beat.column or "y",),
            title=_text(headline, beat.claim_ids),
            caption=_text(beat.secondary) if beat.secondary else None,
        )
    if kind == "bullet_sequence":
        bullets = tuple(_text(b) for b in beat.bullets if b.strip())[:6]
        if not bullets:
            msg = "bullet_sequence with no bullets"
            raise ValueError(msg)
        return sc.BulletSequenceScene(**common, title=_text(headline), bullets=bullets)
    if kind == "quote":
        return sc.QuoteScene(
            **common,
            quote=_text(beat.quote or headline, beat.claim_ids),
            attribution=_text(beat.attribution or "source"),
            source_id=beat.source_ids[0],
        )
    if kind == "definition":
        if not beat.secondary:
            msg = "definition with no definition body"
            raise ValueError(msg)
        return sc.DefinitionScene(
            **common, term=_text(headline), definition=_text(beat.secondary, beat.claim_ids)
        )
    if kind == "callout":
        return sc.CalloutScene(**common, text=_text(headline, beat.claim_ids), tone=beat.tone)
    if kind == "comparison":
        if not (beat.left and beat.right):
            msg = "comparison needs both sides"
            raise ValueError(msg)
        return sc.ComparisonScene(
            **common,
            left=_text(beat.left),
            right=_text(beat.right),
            # The two sides are two *rows* of one column — "2019 vs 2024", "gas vs wind" — so each
            # side's own label is its row key. Reading the second side out of a second column
            # instead would compare two different measures and call it a comparison.
            left_value=(
                sc.DataRef(
                    dataset_id=beat.dataset_id,
                    column=beat.column or None,
                    row_key=beat.row_key or beat.left,
                )
                if beat.dataset_id
                else None
            ),
            right_value=(
                sc.DataRef(
                    dataset_id=beat.dataset_id, column=beat.column or None, row_key=beat.right
                )
                if beat.dataset_id
                else None
            ),
            title=_text(headline, beat.claim_ids),
        )
    if kind == "chapter_transition":
        return sc.ChapterTransitionScene(**common, label=_text(headline))
    if kind == "timeline":
        events = tuple(
            sc.TimelineEvent(date_label=(b.split(":", 1)[0])[:40], text=_text(b.split(":", 1)[-1]))
            for b in beat.bullets
            if ":" in b
        )[:8]
        if len(events) < 2:
            msg = "timeline needs at least two 'date: what happened' bullets"
            raise ValueError(msg)
        return sc.TimelineScene(**common, events=events, title=_text(headline))
    if kind == "source_card":
        if not beat.source_ids:
            msg = "source_card with no sources"
            raise ValueError(msg)
        return sc.SourceCardScene(**common, source_ids=beat.source_ids[:12])
    if kind == "outro":
        return sc.OutroScene(
            **common,
            text=_text(headline),
            cta=_text(beat.secondary) if beat.secondary else None,
        )
    if kind == "image":
        return sc.ImageScene(
            **common,
            asset_id=beat.asset_id,
            alt_text=(beat.alt_text or headline)[:500],
            caption=_text(beat.secondary) if beat.secondary else None,
            motion="slow_push",
        )
    msg = f"no builder for scene kind {kind!r}"
    raise ValueError(msg)


# --- the call ---------------------------------------------------------------------------------


def _prompt(
    campaign: ContentCampaign,
    outline: EpisodeOutline,
    cards: list[ClaimCard],
    datasets: dict[str, DatasetTable],
    allowed_kinds: list[str],
    source_ids: list[str],
    asset_ids: list[str],
) -> str:
    brief = campaign.brief
    sections = "\n".join(
        f"- {s.kind.value}: {s.purpose} ({s.word_budget} words, {s.end_s - s.start_s}s)"
        for s in outline.sections
    )
    card_lines = (
        "\n".join(
            f"- {c.claim_id}: {c.statement}"
            + (f" [numbers: {', '.join(c.numbers)}]" if c.numbers else "")
            + (f" [caveat: {c.caveats[0]}]" if c.caveats else "")
            for c in cards
        )
        or "- (none: this film has no verified claims, so it must not state figures)"
    )
    data_lines = (
        "\n".join(
            f"- {d.dataset_id}: columns {', '.join(d.columns)};"
            f" {len(d.rows)} rows; {d.classification}"
            for d in datasets.values()
        )
        or "- (none)"
    )
    return (
        f"Topic: {brief.topic}\n"
        f"Objective: {brief.objective}\n"
        f"Audience: {brief.audience or 'general'}\n"
        f"Language: {brief.language.code}\n\n"
        "Write one beat per editorial section, in this order, respecting each word budget:\n"
        f"{sections}\n\n"
        "Verified claims you may cite, by id. You may state a figure ONLY if it appears in one of\n"
        "these claims or in a dataset row below. Do not cite a claim id that is not listed:\n"
        f"{card_lines}\n\n"
        f"Datasets available to chart:\n{data_lines}\n\n"
        f"Scene kinds you may use: {', '.join(allowed_kinds)}.\n"
        f"Source ids you may attribute to: {', '.join(source_ids) or '(none)'}.\n"
        f"Asset ids you may show: {', '.join(asset_ids) or '(none)'}.\n\n"
        "For each beat set `display_text` (1-3 sentences of narration), `spoken_text` when the\n"
        "line reads aloud differently from how it is written (expand % and numerals), the\n"
        "`claim_ids` it rests on, and the scene: `scene_kind` plus the fields that kind needs\n"
        "(`headline`, `secondary`, `bullets`, `quote`+`attribution`+`source_ids`,\n"
        "`dataset_id`+`column`+`row_key`+`unit`, `chart`+`x`+`y`,\n"
        "`left`+`right` naming two rows of one column).\n"
        "A `timeline` takes bullets shaped 'date: what happened'.\n"
        "Say nothing you cannot cite. A beat with no evidence is better left out."
    )


def draft_story_plan(
    campaign: ContentCampaign,
    outline: EpisodeOutline,
    *,
    deliverable_id: str,
    claims: list[ClaimRecord],
    datasets: dict[str, DatasetTable] | None = None,
    source_ids: tuple[str, ...] = (),
    asset_ids: tuple[str, ...] = (),
    width: int = 1080,
    height: int = 1920,
    fps: Literal[24, 25, 30, 60] = 30,
    visual_subject: str | None = None,
    gateway: ModelGateway | None = None,
) -> DraftResult:
    """Beats and typed scenes for one deliverable, from evidence that already exists.

    Returns a `DraftResult`: the plan built from the beats that survived validation, and a gap per
    beat that did not. A run with no surviving beats gets ``plan=None`` and every gap, because a
    one-beat film assembled from whatever passed is not a film — it is a failure that rendered.
    """
    from content_factory.models.catalog import build_gateway

    datasets = datasets or {}
    cards = claim_cards(claims)
    by_id = {c.claim_id: c for c in cards}
    available = {k for k in WRITER_KINDS}
    if asset_ids:
        available |= {"image"}
    allowed = sorted(available)
    prompt = _prompt(campaign, outline, cards, datasets, allowed, list(source_ids), list(asset_ids))
    gw = gateway or build_gateway()
    result = gw.complete_structured(
        _SKILL,
        PRESETS["private_local"],
        DraftPlan,
        [{"role": "user", "content": prompt}],
        budget_scopes=[(Scope.monthly, "scriptwriter")],
        estimated_tokens=6000,
        options=GatewayOptions(
            structured_output=True,
            think=False,
            # A whole episode's worth of claim cards, datasets and section budgets, plus the
            # response schema, does not fit in Ollama's 4096 default.
            num_ctx=32768,
            keep_alive=0,
        ),
    )
    draft = result.value
    assert isinstance(draft, DraftPlan)

    dataset_values = _dataset_values(datasets)
    known_sources, known_assets = set(source_ids), set(asset_ids)
    gaps: list[Gap] = []
    beats: list[sc.VisualBeat] = []
    built: list[sc.SceneSpec] = []
    for index, item in enumerate(draft.beats):
        reason = validate_beat(
            item,
            cards=by_id,
            datasets=datasets,
            dataset_values=dataset_values,
            source_ids=known_sources,
            asset_ids=known_assets,
            allowed_kinds=available,
        )
        if reason:
            gaps.append(Gap(reason, item.section.value, item.display_text[:200], item.scene_kind))
            continue
        beat_id = f"bet_{sha256_hex(f'{deliverable_id}{index}{item.display_text}'.encode())[:12]}"
        try:
            scene = _build_scene(
                item, scene_id=f"scn_{beat_id.removeprefix('bet_')}", beat_id=beat_id
            )
        except (ValueError, TypeError) as exc:
            gaps.append(
                Gap(
                    f"scene could not be built: {exc}",
                    item.section.value,
                    item.display_text[:200],
                    item.scene_kind,
                )
            )
            continue
        section = next((s for s in outline.sections if s.kind == item.section), outline.sections[0])
        beats.append(
            sc.VisualBeat(
                beat_id=beat_id,
                order=len(beats),
                display_text=item.display_text.strip()[:1000],
                spoken_text=(item.spoken_text or "").strip()[:2000] or None,
                claim_ids=item.claim_ids,
                section=item.section.value,
                # The section's own word budget at the outline's words-per-minute. A planned
                # duration is what the silent path cuts to and what plan_shots sizes a shot from.
                planned_duration_ms=max(
                    200, round(section.word_budget / outline.words_per_minute * 60_000)
                ),
            )
        )
        built.append(scene)

    facts = {
        "writer": result.model_alias,
        "version": SCRIPTWRITER_VERSION,
        "requested": len(draft.beats),
        "beats": len(beats),
        "gaps": len(gaps),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "elapsed_s": result.elapsed_s,
        "schema_enforced": result.schema_enforced,
    }
    if not beats:
        return DraftResult(None, gaps, facts, draft)
    plan = sc.StoryPlan(
        plan_id=f"stp_{sha256_hex(f'{deliverable_id}{SCRIPTWRITER_VERSION}'.encode())[:12]}",
        deliverable_id=deliverable_id,
        fps=fps,
        width=width,
        height=height,
        beats=tuple(beats),
        scenes=tuple(built),
        visual_subject=visual_subject,
    )
    return DraftResult(plan, gaps, facts, draft)
