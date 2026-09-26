"""Typed helpers an editor uses to build an episode's pack, script and spec, valid by design."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from content_factory.explainer import units
from content_factory.explainer.capture import load_capture
from content_factory.explainer.passages import QuoteRequest, text_sha256
from content_factory.explainer.sources import SourceInfo, manifest_from_capture
from content_factory.explainer.tokens_gen import DESIGN_SYSTEM_VERSION
from content_factory.schemas.explainer import (
    Action,
    AssetRef,
    Beat,
    Calculation,
    CaptureQuote,
    Claim,
    ClaimOperand,
    ConstantOperand,
    Corroboration,
    Cue,
    CueRelation,
    DatasetColumn,
    DatasetRow,
    DurationClass,
    Entity,
    EpistemicClass,
    EvidenceDataset,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    HoldAction,
    Quantity,
    Scene,
    ScriptPlan,
    ScriptSegment,
    Section,
    SourceCaptureManifest,
    SourceDocumentTemplate,
    TargetAction,
    TextItem,
    TextTemplate,
    TitlePromise,
    VisualSpec,
    tokenize,
)
from content_factory.schemas.research import EvidenceLocator, SourceClass

REPO = Path(__file__).resolve().parents[3]
SOURCES_DIR = REPO / "output" / "explainer" / "sources"
HOLD = HoldAction(action="hold")
_PUNCT = re.compile(r"^[^\w%\u00d7]+|[^\w%\u00d7]+$")


@dataclass(frozen=True)
class CapturedPage:
    """A source page as replayed from its WACZ: the text quotes must come from."""

    url: str
    wacz: Path
    text: str
    publisher: str
    title: str
    accessed_at: str
    author: str = ""
    published_at: str | None = None

    @property
    def normalized(self) -> str:
        return " ".join(self.text.split())

    @property
    def content_sha256(self) -> str:
        return text_sha256(self.text)


def captured_page(
    slug: str,
    url: str,
    title: str,
    *,
    publisher: str = "Wikipedia",
    author: str = "Wikipedia contributors",
) -> CapturedPage:
    """A stored capture's replayed text, with its capture time as the access time."""
    folder = SOURCES_DIR / slug
    wacz = sorted(folder.glob("capture-*.wacz"))[0]
    return CapturedPage(
        url=url,
        wacz=wacz,
        text=(folder / "page.txt").read_text(),
        publisher=publisher,
        title=title,
        author=author,
        accessed_at=load_capture(wacz, url).captured_at,
    )


@dataclass(frozen=True)
class EpisodeCapture:
    """A source capture resolved for an episode's quotes, with its tile positions."""

    manifest: SourceCaptureManifest
    tiles: list[dict[str, object]]

    def quote_id(self, text: str) -> str:
        return self._quote(text).quote_id

    def section_of(self, text: str) -> str:
        return self._quote(text).section_id

    def _quote(self, text: str) -> CaptureQuote:
        want = " ".join(text.split())
        return next(q for q in self.manifest.quotes if " ".join(q.text.split()) == want)


def capture_quotes(
    page: CapturedPage, source_id: str, quotes: Sequence[tuple[str, Sequence[str]]]
) -> EpisodeCapture:
    """Resolve quotes against the stored capture and verify them against its pixels."""
    stored = load_capture(page.wacz, page.url)
    requests = [QuoteRequest(text=t, occurrence_index=None, claim_ids=tuple(c)) for t, c in quotes]
    info = SourceInfo(
        source_id=source_id, publisher=page.publisher, title=page.title, author=page.author
    )
    manifest, tiles = manifest_from_capture(stored, requests, page.wacz.parent, source=info)
    records: list[dict[str, object]] = [
        {"path": str(Path(t.path).resolve().relative_to(REPO)), "y_px": t.y_px} for t in tiles
    ]
    return EpisodeCapture(manifest, records)


def link_captures(pack: EvidencePack, captures: Mapping[str, EpisodeCapture]) -> EvidencePack:
    """Point each source at its capture; the pack hash ignores the link's provenance."""
    sources = tuple(
        s.model_copy(update={"capture_id": captures[s.source_id].manifest.capture_id})
        if s.source_id in captures
        else s
        for s in pack.sources
    )
    return EvidencePack.model_validate(pack.model_copy(update={"sources": sources}).model_dump())


def write_episode(
    out_dir: Path,
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    captures: Sequence[EpisodeCapture],
) -> dict[str, object]:
    """Write pack, script, spec and capture manifests with their tile positions."""

    def dump(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    for name, model in (("pack", pack), ("script", script), ("spec", spec)):
        dump(out_dir / f"{name}.json", model.model_dump(mode="json"))
    folder = out_dir / "captures"
    folder.mkdir(exist_ok=True)
    for cap in captures:
        dump(folder / f"{cap.manifest.capture_id}.json", cap.manifest.model_dump(mode="json"))
        dump(folder / f"{cap.manifest.capture_id}.tiles.json", cap.tiles)
    return {
        "pack": pack.pack_hash()[:12],
        "claims": len(pack.claims),
        "segments": len(script.segments),
        "words": sum(len(g.tokens) for g in script.segments),
        "scenes": len(spec.scenes),
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class PackDraft:
    """Accumulates sources, passages, claims and datasets; `build` freezes the pack."""

    pack_id: str
    topic: str
    checked_at: str
    sources: list[EvidenceSource] = field(default_factory=list)
    items: list[EvidenceItem] = field(default_factory=list)
    claims: dict[str, Claim] = field(default_factory=dict)
    datasets: list[EvidenceDataset] = field(default_factory=list)
    calculations: list[Calculation] = field(default_factory=list)
    _pages: dict[str, CapturedPage] = field(default_factory=dict)

    def source(self, source_id: str, page: CapturedPage, *, source_class: SourceClass) -> str:
        self._pages[source_id] = page
        self.sources.append(
            EvidenceSource(
                source_id=source_id,
                url=page.url,
                canonical_url=page.url,
                publisher=page.publisher,
                title=page.title,
                author=page.author,
                published_at=page.published_at,
                accessed_at=page.accessed_at,
                content_sha256=page.content_sha256,
                source_class=source_class,
            )
        )
        return source_id

    def passage(self, item_id: str, source_id: str, text: str, *, context: int = 160) -> str:
        """Quote the page exactly; a passage that is not in the captured text is refused."""
        page = self._pages[source_id].normalized
        needle = " ".join(text.split())
        start = page.find(needle)
        if start < 0:
            msg = f"passage {item_id} is not in the captured text of {source_id}: {needle[:80]!r}"
            raise ValueError(msg)
        end = start + len(needle)
        self.items.append(
            EvidenceItem(
                item_id=item_id,
                source_id=source_id,
                passage=needle,
                context_before=page[max(0, start - context) : start].lstrip()[-400:],
                context_after=page[end : end + context].rstrip()[:400],
                locator=EvidenceLocator(kind="char_range", start=start, end=end),
                quoted_at=self.checked_at,
            )
        )
        return item_id

    def observation(
        self,
        claim_id: str,
        statement: str,
        evidence: Sequence[str],
        *,
        rationale: str,
        value: Quantity | None = None,
        short_label: str = "",
        epistemic_class: EpistemicClass = "observation",
        consequential: bool = False,
        corroborated_by: Sequence[str] = (),
        time_basis: str = "",
    ) -> str:
        corroboration = (
            Corroboration(status="corroborated", independent_source_ids=tuple(corroborated_by))
            if corroborated_by
            else Corroboration(status="not_required")
        )
        self._add(
            Claim(
                claim_id=claim_id,
                statement=statement,
                short_label=short_label,
                epistemic_class=epistemic_class,
                evidence_ids=tuple(evidence),
                value=value,
                consequential=consequential,
                corroboration=corroboration,
                stable=True,
                time_basis=time_basis,
                rationale=rationale,
                checked_at=self.checked_at,
            )
        )
        return claim_id

    def assumption(
        self,
        claim_id: str,
        statement: str,
        value: Quantity,
        *,
        rationale: str,
        short_label: str = "",
    ) -> str:
        self._add(
            Claim(
                claim_id=claim_id,
                statement=statement,
                short_label=short_label,
                epistemic_class="illustrative_assumption",
                value=value,
                stable=True,
                rationale=rationale,
                checked_at=self.checked_at,
            )
        )
        return claim_id

    def derive(
        self,
        claim_id: str,
        statement: str,
        op: str,
        operands: Sequence[str | Quantity],
        *,
        rationale: str,
        short_label: str = "",
        unit: str | None = None,
    ) -> Quantity:
        """A value the compiler can reproduce: computed here from its operands, never typed."""
        quantities = [self._value(o) for o in operands]
        fn = {"add": units.add, "sub": units.sub, "mul": units.mul, "div": units.div}[op]
        result = quantities[0]
        for q in quantities[1:]:
            result = fn(result, q)
        if unit is not None:
            result = units.convert(result, unit)
        illustrative = any(isinstance(o, str) and self._traces_to_assumption(o) for o in operands)
        calc_id = f"calc_{claim_id.removeprefix('clm_')}"[:40]
        self.calculations.append(
            Calculation(
                calc_id=calc_id,
                op=op,  # type: ignore[arg-type]
                operands=tuple(
                    ClaimOperand(kind="claim", claim_id=o)
                    if isinstance(o, str)
                    else ConstantOperand(kind="constant", value=o)
                    for o in operands
                ),
                result_claim_id=claim_id,
            )
        )
        self._add(
            Claim(
                claim_id=claim_id,
                statement=statement,
                short_label=short_label,
                epistemic_class="illustrative_assumption" if illustrative else "interpretation",
                value=Quantity(magnitude=result.magnitude, unit=result.unit),
                calculation_id=calc_id,
                stable=True,
                rationale=rationale,
                checked_at=self.checked_at,
            )
        )
        return result

    def dataset(
        self,
        dataset_id: str,
        title: str,
        columns: Sequence[DatasetColumn],
        rows: Sequence[tuple[str, Sequence[float | str | None], Sequence[str]]],
    ) -> EvidenceDataset:
        built = EvidenceDataset(
            dataset_id=dataset_id,
            title=title,
            columns=tuple(columns),
            rows=tuple(
                DatasetRow(key=key, values=tuple(values), claim_ids=tuple(claims))
                for key, values, claims in rows
            ),
        )
        self.datasets.append(built)
        return built

    def build(self, frozen_at: str) -> EvidencePack:
        return EvidencePack(
            pack_id=self.pack_id,
            topic=self.topic,
            frozen_at=frozen_at,
            sources=tuple(self.sources),
            items=tuple(self.items),
            claims=tuple(self.claims.values()),
            datasets=tuple(self.datasets),
            calculations=tuple(self.calculations),
        )

    def value(self, claim_id: str) -> Quantity:
        return self._value(claim_id)

    def _add(self, claim: Claim) -> None:
        if claim.claim_id in self.claims:
            msg = f"duplicate claim id {claim.claim_id}"
            raise ValueError(msg)
        self.claims[claim.claim_id] = claim

    def _value(self, operand: str | Quantity) -> Quantity:
        if isinstance(operand, Quantity):
            return operand
        value = self.claims[operand].value
        if value is None:
            msg = f"claim {operand} has no value to compute with"
            raise ValueError(msg)
        return value

    def _traces_to_assumption(self, claim_id: str) -> bool:
        return self.claims[claim_id].epistemic_class == "illustrative_assumption"


@dataclass
class ScriptDraft:
    """Segments in arc order; tokens always come from `tokenize` so cues stay stable."""

    segments: list[ScriptSegment] = field(default_factory=list)

    def say(
        self,
        segment_id: str,
        section: Section,
        text: str,
        *,
        claims: Sequence[str] = (),
        display: str | None = None,
    ) -> str:
        self.segments.append(
            ScriptSegment(
                segment_id=segment_id,
                section=section,
                spoken_text=text,
                display_text=display,
                tokens=tokenize(text),
                claim_ids=tuple(claims),
            )
        )
        return segment_id

    def build(
        self,
        pack: EvidencePack,
        *,
        script_id: str,
        channel_id: str,
        question: str,
        contribution: str,
        promises: Sequence[TitlePromise],
        locked_at: str,
    ) -> ScriptPlan:
        return ScriptPlan(
            script_id=script_id,
            channel_id=channel_id,
            pack_id=pack.pack_id,
            pack_hash=pack.pack_hash(),
            question=question,
            contribution=contribution,
            promises=tuple(promises),
            segments=tuple(self.segments),
            locked_at=locked_at,
        )

    def token(self, segment_id: str, word: str, occurrence: int = 0) -> int:
        """Index of a word in a segment, ignoring punctuation and case; -1 means the last."""
        segment = next(s for s in self.segments if s.segment_id == segment_id)
        want = _PUNCT.sub("", word).lower()
        hits = [i for i, t in enumerate(segment.tokens) if _PUNCT.sub("", t).lower() == want]
        if occurrence < 0 and hits:
            return hits[occurrence]
        if len(hits) <= occurrence:
            msg = f"{segment_id} has no token {word!r} (occurrence {occurrence})"
            raise ValueError(msg)
        return hits[occurrence]

    def cue(
        self,
        segment_id: str,
        word: str,
        *,
        to: str | None = None,
        relation: CueRelation = "on",
        duration: DurationClass = "short",
        occurrence: int = 0,
    ) -> Cue:
        start = self.token(segment_id, word, occurrence)
        end = start if to is None else self.token(segment_id, to)
        return Cue(
            segment_id=segment_id,
            token_start=start,
            token_end=max(start, end),
            relation=relation,
            duration_class=duration,
        )


@dataclass
class SpecDraft:
    """Entities, assets and scenes; `build` binds the spec to the frozen pack and locked script."""

    spec_id: str
    entities: dict[str, Entity] = field(default_factory=dict)
    assets: list[AssetRef] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)

    def entity(
        self,
        entity_id: str,
        label: str,
        kind: str,
        *,
        short_label: str = "",
        claims: Sequence[str] = (),
    ) -> str:
        if entity_id not in self.entities:
            self.entities[entity_id] = Entity(
                entity_id=entity_id,
                label=label,
                kind=kind,  # type: ignore[arg-type]
                short_label=short_label,
                claim_ids=tuple(claims),
            )
        return entity_id

    def dataset_asset(self, asset_id: str, dataset: EvidenceDataset) -> str:
        self.assets.append(
            AssetRef(
                asset_id=asset_id,
                kind="dataset",
                sha256=sha256_text(dataset.canonical_json()),
                dataset_id=dataset.dataset_id,
            )
        )
        return asset_id

    def capture_asset(self, asset_id: str, capture_id: str, artifact_sha256: str) -> str:
        self.assets.append(
            AssetRef(
                asset_id=asset_id, kind="capture", sha256=artifact_sha256, capture_id=capture_id
            )
        )
        return asset_id

    def text_scene(
        self,
        scene_id: str,
        section: Section,
        purpose: str,
        variant: str,
        items: Sequence[tuple[str, str, str | None]],
        beats: Sequence[Beat],
        *,
        claims: Sequence[str] = (),
    ) -> Scene:
        """A text scene whose items become text entities bound to their claims."""
        for entity_id, label, claim in items:
            self.entity(entity_id, label[:80], "text", claims=[claim] if claim else [])
        template = TextTemplate(
            template="text",
            variant=variant,  # type: ignore[arg-type]
            items=tuple(TextItem(entity_id=e, text=t, claim_id=c) for e, t, c in items),
        )
        return self.add(
            Scene(
                scene_id=scene_id,
                section=section,
                purpose=purpose,
                template=template,
                beats=tuple(beats),
                claim_ids=tuple(claims),
            )
        )

    def source_scene(
        self,
        scene_id: str,
        section: Section,
        purpose: str,
        asset_id: str,
        capture: EpisodeCapture,
        first_quote: str,
        beats: Sequence[Beat],
        *,
        claims: Sequence[str],
    ) -> Scene:
        """A source-document scene that opens on the section holding its first quote."""
        template = SourceDocumentTemplate(
            template="source_document",
            capture_asset_id=asset_id,
            initial_section_id=capture.section_of(first_quote),
        )
        return self.add(
            Scene(
                scene_id=scene_id,
                section=section,
                purpose=purpose,
                template=template,
                beats=tuple(beats),
                claim_ids=tuple(claims),
                source_ids=(capture.manifest.source_id,),
            )
        )

    def add(self, scene: Scene) -> Scene:
        self.scenes.append(scene)
        return scene

    def build(self, pack: EvidencePack, script: ScriptPlan) -> VisualSpec:
        return VisualSpec(
            spec_id=self.spec_id,
            script_id=script.script_id,
            script_hash=script.script_hash(),
            pack_hash=pack.pack_hash(),
            design_system_version=DESIGN_SYSTEM_VERSION,
            entities=tuple(self.entities.values()),
            assets=tuple(self.assets),
            scenes=tuple(self.scenes),
        )


def cue_order_issues(spec: VisualSpec, script: ScriptPlan) -> list[str]:
    """Beats inside a scene must follow the narration: a later beat never cues an earlier token."""
    order = {s.segment_id: i for i, s in enumerate(script.segments)}
    issues: list[str] = []
    for scene in spec.scenes:
        last = (-1, -1)
        for b in scene.beats:
            here = (order[b.cue.segment_id], b.cue.token_start)
            if here < last:
                issues.append(f"{scene.scene_id}: beat {b.beat_id} cues before the previous beat")
            last = max(last, here)
    return issues


def beat(beat_id: str, cue: Cue, *actions: Action) -> Beat:
    return Beat(beat_id=beat_id, cue=cue, actions=actions)


def act(action: str, *targets: str) -> TargetAction:
    return TargetAction(action=action, targets=targets)  # type: ignore[arg-type]


def qty(magnitude: float, unit: str) -> Quantity:
    return Quantity(magnitude=magnitude, unit=unit)
