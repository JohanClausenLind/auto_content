"""The static review page (F5): video, transcript, scenes, findings, sources, patches, exports."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from content_factory.explainer.errors import EpisodeInvalidError
from content_factory.explainer.ocr import crop_rect
from content_factory.explainer.passages import Tile
from content_factory.explainer.patches import PatchRecord, load_patches
from content_factory.explainer.pipeline import (
    CONFIG_NAME,
    EpisodeConfig,
    Ledger,
    beat_at,
    read_model,
    write_atomic,
)
from content_factory.explainer.qc import Decoder, QcUnmeasurableError, decode_frames
from content_factory.explainer.timing import TokenClock, paced_manifest, timeline_pauses
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import (
    AnnotateAction,
    CaptureAsset,
    CaptureQuote,
    ChartTemplate,
    DiagramTemplate,
    ExplainerRenderBundle,
    NarrationManifest,
    PageRect,
    ReviewFinding,
    ReviewReport,
    Scene,
    ScriptPlan,
    TextTemplate,
    VisualSpec,
)

PAGE_NAME = "review.html"
ASSETS_DIR = "review-page"
THUMB_WIDTH_PX = 320
REPORT_STEPS = ("qc_animatic", "review_animatic", "repair", "qc_final", "review_final")
ANIMATIC_STEPS = frozenset({"qc_animatic", "review_animatic"})
LINK_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class FindingRow:
    """One ReviewFinding with where it came from, its beat, and the video it was seen in."""

    step: str
    report_id: str
    finding: ReviewFinding
    beat_id: str | None
    frame: int
    video: str | None
    video_sha256: str | None


@dataclass(frozen=True)
class QcTally:
    step: str
    passed: int
    failed: int
    unknown: int


# --- what the page and the CLI read ---


def claim_citations(spec: VisualSpec) -> dict[str, list[str]]:
    """claim_id -> scenes showing it: scene claims, text items, annotations and drawn entities."""
    entity_claims = {e.entity_id: e.claim_ids for e in spec.entities}
    cited: dict[str, list[str]] = {}
    for scene in spec.scenes:
        claims = list(scene.claim_ids)
        if isinstance(scene.template, TextTemplate):
            claims += [i.claim_id for i in scene.template.items if i.claim_id]
        for beat in scene.beats:
            claims += [
                a.claim_id for a in beat.actions if isinstance(a, AnnotateAction) and a.claim_id
            ]
        for entity_id in _drawn_entities(scene):
            claims += entity_claims.get(entity_id, ())
        for claim_id in dict.fromkeys(claims):
            cited.setdefault(claim_id, []).append(scene.scene_id)
    return cited


def segment_citations(script: ScriptPlan) -> dict[str, list[str]]:
    cited: dict[str, list[str]] = {}
    for segment in script.segments:
        for claim_id in segment.claim_ids:
            cited.setdefault(claim_id, []).append(segment.segment_id)
    return cited


def finding_rows(episode_dir: Path) -> list[FindingRow]:
    """Every finding of every ReviewReport the ledger records, in pipeline order."""
    ledger = Ledger.load(episode_dir)
    videos = _videos(ledger)
    bundles = {name: _bundle(ledger, name) for name in ("compile", "repair")}
    rows: list[FindingRow] = []
    for step in REPORT_STEPS:
        entry = ledger.entry(step) or {}
        names = [n for n in entry.get("artifacts", {}) if n == "report" or n.startswith("round-")]
        for name in sorted(names, key=_round_order):
            report = read_model(ReviewReport, ledger.artifact(step, name))
            early = step in ANIMATIC_STEPS or name == "round-0"
            bundle = bundles["compile" if early else "repair"] or bundles["compile"]
            fps = bundle.timeline.fps if bundle else 30
            video = videos.get(report.artifact.sha256)
            label = step if name == "report" else f"{step} {name}"
            for finding in report.findings:
                at_ms = finding.interval.start_ms
                beat = finding.beat_id
                if beat is None and bundle is not None:
                    beat = beat_at(bundle, finding.scene_id, at_ms)
                rows.append(
                    FindingRow(
                        step=label,
                        report_id=report.report_id,
                        finding=finding,
                        beat_id=beat,
                        frame=at_ms * fps // 1000,
                        video=video,
                        video_sha256=report.artifact.sha256 if video else None,
                    )
                )
    return rows


def qc_tallies(episode_dir: Path) -> list[QcTally]:
    """Passed, failed and unmeasured checks per QC step, from its raw findings.json."""
    ledger = Ledger.load(episode_dir)
    tallies: list[QcTally] = []
    for step in ("qc_animatic", "qc_final"):
        try:
            path = ledger.artifact(step, "findings")
        except KeyError:
            continue
        found = json.loads(path.read_text(encoding="utf-8"))
        passed = [f["passed"] for f in found]
        tallies.append(QcTally(step, passed.count(True), passed.count(False), passed.count(None)))
    return tallies


def transcript_rows(
    script: ScriptPlan, narration: NarrationManifest
) -> list[tuple[str, int, int, str]]:
    """(segment_id, start_ms, end_ms, spoken text) on the final video's clock."""
    clock = TokenClock(script, narration)
    return [
        (
            s.segment_id,
            clock.start_ms(s.segment_id, 0),
            clock.end_ms(s.segment_id, len(s.tokens) - 1),
            s.spoken_text,
        )
        for s in script.segments
    ]


def clock_text(ms: int) -> str:
    minutes, rest = divmod(ms, 60_000)
    return f"{minutes}:{rest // 1000:02d}.{rest % 1000:03d}"


# --- the page ---


def write_review_page(episode_dir: Path, *, decode: Decoder = decode_frames) -> Path:
    """review.html beside the ledger, with its stills in review-page/; every text is escaped."""
    ledger = Ledger.load(episode_dir)
    assets = episode_dir / ASSETS_DIR
    assets.mkdir(parents=True, exist_ok=True)
    video = _main_video(ledger)
    bundle = _bundle(ledger, "repair") or _bundle(ledger, "compile")
    body = [
        _header(ledger),
        '<main><div class="player">',
        _video_html(episode_dir, video),
        _transcript_html(ledger),
        '</div><div class="panels">',
        _scenes_html(bundle),
        _findings_html(episode_dir, assets, decode),
        _sources_html(ledger, assets),
        _patches_html(episode_dir),
        _exports_html(episode_dir),
        "</div></main>",
    ]
    title = f"Review {ledger.data.get('episode_id', episode_dir.name)}"
    page = (
        "<!doctype html>\n"
        f'<html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<style>{CSS}</style></head><body>{''.join(body)}<script>{JS}</script></body></html>\n"
    )
    return write_atomic(episode_dir / PAGE_NAME, page)


def _header(ledger: Ledger) -> str:
    data = ledger.data
    ready = "ready" if data.get("ready") else "not ready"
    status = f"{data.get('status', 'new')}, {ready}, updated {data.get('updated_at') or '-'}"
    return (
        f"<header><h1>Review: <code>{_e(str(data.get('episode_id', '')))}</code></h1>"
        f"<p>{_e(status)}</p></header>"
    )


def _video_html(episode_dir: Path, video: Path | None) -> str:
    if video is None:
        return '<p class="empty">No video rendered yet.</p>'
    rel = video.relative_to(episode_dir).as_posix()
    return (
        f'<video id="video" controls preload="metadata" src="{_e(quote(rel))}"></video>'
        f'<p class="note">Playing <code>{_e(rel)}</code>. Click any time to seek.</p>'
    )


def video_narration(ledger: Ledger) -> NarrationManifest | None:
    """The manifest on final.mp4's clock (mix's paced one), else narrate's paced by compile."""
    try:
        return read_model(NarrationManifest, ledger.artifact("mix", "manifest"))
    except KeyError:
        pass
    try:
        narration = read_model(NarrationManifest, ledger.artifact("narrate", "manifest"))
    except KeyError:
        return None
    bundle = _bundle(ledger, "compile")
    if bundle is None:
        return narration
    script = read_model(ScriptPlan, ledger.artifact("lock_script", "script"))
    return paced_manifest(narration, timeline_pauses(bundle.timeline), script)


def _transcript_html(ledger: Ledger) -> str:
    narration = video_narration(ledger)
    if narration is None:
        return _section("Transcript", '<p class="empty">No narration yet.</p>')
    script = read_model(ScriptPlan, ledger.artifact("lock_script", "script"))
    items = [
        f'<li data-start="{start / 1000:.3f}" data-end="{end / 1000:.3f}">'
        f"{_seek(start)} <code>{_e(sid)}</code> <span>{_e(text)}</span></li>"
        for sid, start, end, text in transcript_rows(script, narration)
    ]
    return _section("Transcript", f'<ol class="transcript">{"".join(items)}</ol>')


def _scenes_html(bundle: ExplainerRenderBundle | None) -> str:
    if bundle is None:
        return _section("Scenes", '<p class="empty">Not compiled yet.</p>')
    fps = bundle.timeline.fps
    spec = {s.scene_id: s for s in bundle.spec.scenes}
    rows = []
    for compiled in bundle.timeline.scenes:
        scene = spec[compiled.scene_id]
        start = compiled.start_frame * 1000 // fps
        end = (compiled.start_frame + compiled.duration_frames) * 1000 // fps
        rows.append(
            f'<tr id="scene-{_e(scene.scene_id)}" data-start="{start / 1000:.3f}" '
            f'data-end="{end / 1000:.3f}"><td>{_seek(start)}&ndash;{_e(clock_text(end))}</td>'
            f"<td><code>{_e(scene.scene_id)}</code></td><td>{_e(scene.section)}</td>"
            f"<td>{_e(scene.template.template)}</td><td>{_e(scene.purpose)}</td></tr>"
        )
    head = "<tr><th>Time</th><th>Scene</th><th>Section</th><th>Template</th><th>Purpose</th></tr>"
    return _section("Scenes", f"<table>{head}{''.join(rows)}</table>")


def _findings_html(episode_dir: Path, assets: Path, decode: Decoder) -> str:
    rows = finding_rows(episode_dir)
    tallies = "".join(
        f"<li>{_e(t.step)}: {t.passed} passed, {t.failed} failed, {t.unknown} not measured</li>"
        for t in qc_tallies(episode_dir)
    )
    items = []
    for row in rows:
        f = row.finding
        still = _still(episode_dir, assets, row, decode)
        image = (
            f'<img src="{_e(quote(still))}" alt="frame {row.frame}" loading="lazy">'
            if still
            else '<span class="empty">no frame</span>'
        )
        repair = (
            f"<pre>{_e(json.dumps(f.proposed_repair.model_dump(mode='json')))}</pre>"
            if f.proposed_repair
            else ""
        )
        seen_in = f" in <code>{_e(row.video)}</code>" if row.video else ""
        items.append(
            f'<li class="finding {_e(f.disposition)}">{image}<div>'
            f"<p>{_seek(f.interval.start_ms)} frame {row.frame}{seen_in} &middot; "
            f"<code>{_e(f.scene_id)}</code> beat <code>{_e(row.beat_id or '-')}</code></p>"
            f"<p><b>{_e(f.category)}</b> / {_e(f.severity)} / {_e(f.disposition)} &middot; "
            f"{_e(row.step)} <code>{_e(f.finding_id)}</code></p>"
            f'<p>{_e(f.observed)}</p><p class="note">{_e(f.evidence)}</p>{repair}</div></li>'
        )
    body = f'<ul class="tally">{tallies}</ul>' if tallies else ""
    body += f'<ul class="findings">{"".join(items)}</ul>' if items else '<p class="empty">None.</p>'
    return _section(f"Findings ({len(rows)})", body)


def _still(episode_dir: Path, assets: Path, row: FindingRow, decode: Decoder) -> str | None:
    """A PNG of the reviewed video at the finding's time, decoded once per video and time."""
    if row.video is None or row.video_sha256 is None:
        return None
    at_ms = row.finding.interval.start_ms
    out = assets / f"frame-{row.video_sha256[:12]}-{at_ms}.png"
    if not out.is_file():
        try:
            [frame] = decode(episode_dir / row.video, [at_ms], width=THUMB_WIDTH_PX)
        except (QcUnmeasurableError, OSError, ValueError):
            return None
        frame.save(out)
    return out.relative_to(episode_dir).as_posix()


def _sources_html(ledger: Ledger, assets: Path) -> str:
    try:
        index = json.loads(ledger.artifact("capture_sources", "index").read_text(encoding="utf-8"))
    except KeyError:
        return _section("Sources", '<p class="empty">Sources not captured yet.</p>')
    captures = [read_model(CaptureAsset, ledger.resolve(p)) for p in index["captures"]]
    if not captures:
        return _section("Sources", '<p class="empty">No source capture is shown.</p>')
    blocks = []
    for asset in captures:
        m = asset.manifest
        quotes = []
        for q in m.quotes:
            crop = _quote_crop(ledger.episode_dir, assets, asset, q)
            image = (
                f'<img src="{_e(quote(crop))}" alt="quote {_e(q.quote_id)}">'
                if crop
                else '<span class="empty">no crop</span>'
            )
            verified = (
                f"OCR verified {q.ocr_similarity:.2f}" if q.ocr_verified else "not OCR verified"
            )
            claims = ", ".join(q.claim_ids) or "no claim"
            quotes.append(
                f"<li><blockquote>{_e(q.text)}</blockquote>{image}"
                f'<p class="note"><code>{_e(q.quote_id)}</code> &middot; {_e(verified)} &middot; '
                f"{_e(claims)}</p></li>"
            )
        signed = f", signed for {m.signature_domain}" if m.signed else ", unsigned"
        blocks.append(
            f"<article><h3>{_e(m.title or m.url)}</h3>"
            f"<p>{_e(m.publisher or 'publisher not stated')} &middot; {_link(m.url)}</p>"
            f'<p class="note">capture <code>{_e(m.capture_id)}</code> of source '
            f"<code>{_e(m.source_id)}</code>, captured {_e(m.captured_at)}{_e(signed)}</p>"
            f'<ul class="quotes">{"".join(quotes)}</ul></article>'
        )
    return _section("Sources", "".join(blocks))


def _quote_crop(
    episode_dir: Path, assets: Path, asset: CaptureAsset, q: CaptureQuote
) -> str | None:
    """The page pixels under the quote's lines, cut from the capture tiles."""
    tiles = [Tile(Path(t.path), int(t.y_px)) for t in asset.tiles]
    if not all(t.path.is_file() for t in tiles):
        return None
    x0 = min(r.x for r in q.line_rects)
    y0 = min(r.y for r in q.line_rects)
    x1 = max(r.x + r.width for r in q.line_rects)
    y1 = max(r.y + r.height for r in q.line_rects)
    out = assets / f"quote-{asset.capture_id}-{q.quote_id}.png"
    try:
        crop_rect(tiles, PageRect(x=x0, y=y0, width=x1 - x0, height=y1 - y0)).save(out)
    except (OSError, ValueError):
        return None
    return out.relative_to(episode_dir).as_posix()


def _patches_html(episode_dir: Path) -> str:
    config_path = episode_dir / CONFIG_NAME
    if not config_path.is_file():
        return _section("Patches", '<p class="empty">No config recorded.</p>')
    config = EpisodeConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    try:
        records: list[PatchRecord] = load_patches(config.patches)
    except EpisodeInvalidError as error:
        return _section("Patches", f"<pre>{_e(str(error))}</pre>")
    if not records:
        return _section("Patches (0)", '<p class="empty">No reviewer patches.</p>')
    items = [
        f"<li><p><code>{_e(r.patch_id)}</code> <b>{_e(r.repair.repair)}</b> by {_e(r.author)}, "
        f"{_e(r.created_at)}</p><p>{_e(r.reason)}</p>"
        f"<pre>{_e(json.dumps(r.repair.model_dump(mode='json')))}</pre>"
        + (f'<p class="note">take <code>{_e(r.take_path)}</code></p>' if r.take_path else "")
        + "</li>"
        for r in records
    ]
    return _section(f"Patches ({len(records)})", f'<ol class="patches">{"".join(items)}</ol>')


def _exports_html(episode_dir: Path) -> str:
    from content_factory.explainer.export import EXPORT_MANIFEST, EXPORTS_DIR, ExportManifest

    path = episode_dir / EXPORTS_DIR / EXPORT_MANIFEST
    if not path.is_file():
        return _section("Export", '<p class="empty">Not exported yet.</p>')
    manifest = ExportManifest.model_validate_json(path.read_text(encoding="utf-8"))
    rows = [
        f'<tr><td><a href="{_e(quote(f"{EXPORTS_DIR}/{f.path}"))}">{_e(f.path)}</a></td>'
        f"<td>{f.size_bytes:,}</td><td><code>{_e(f.sha256[:12])}</code></td></tr>"
        for f in manifest.files
    ]
    head = "<tr><th>File</th><th>Bytes</th><th>sha256</th></tr>"
    note = f"<p>{len(manifest.files)} files, created {_e(manifest.created_at)}</p>"
    return _section("Export", f"{note}<table>{head}{''.join(rows)}</table>")


# --- helpers ---


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _section(title: str, body: str) -> str:
    return f"<section><h2>{_e(title)}</h2>{body}</section>"


def _seek(ms: int) -> str:
    return f'<a class="t" href="#t={ms / 1000:.3f}" data-t="{ms / 1000:.3f}">{clock_text(ms)}</a>'


def _link(url: str) -> str:
    """An http(s) URL as a link; anything else (javascript:, data:) stays inert text."""
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme not in LINK_SCHEMES:
        return f"<code>{_e(url)}</code>"
    return f'<a href="{_e(url)}" rel="noreferrer noopener" target="_blank">{_e(url)}</a>'


def _drawn_entities(scene: Scene) -> list[str]:
    template = scene.template
    ids = list(scene.initial_visible)
    if isinstance(template, ChartTemplate):
        ids += [s.entity_id for s in template.series]
    elif isinstance(template, DiagramTemplate):
        ids += [n.entity_id for n in template.nodes] + [e.entity_id for e in template.edges]
    elif isinstance(template, TextTemplate):
        ids += [i.entity_id for i in template.items]
    for beat in scene.beats:
        for action in beat.actions:
            ids += getattr(action, "targets", ())
    return ids


def _round_order(name: str) -> tuple[int, str]:
    return (int(name.removeprefix("round-")), name) if name.startswith("round-") else (-1, name)


def _bundle(ledger: Ledger, step: str) -> ExplainerRenderBundle | None:
    try:
        return read_model(ExplainerRenderBundle, ledger.artifact(step, "bundle"))
    except KeyError:
        return None


def _main_video(ledger: Ledger) -> Path | None:
    for step in ("mux", "render_final", "render_animatic"):
        try:
            path = ledger.artifact(step, "mp4")
        except KeyError:
            continue
        if path.is_file():
            return path
    return None


def _videos(ledger: Ledger) -> dict[str, str]:
    """sha256 -> episode-relative mp4, so a finding links the exact video it was seen in."""
    found: dict[str, str] = {}
    candidates = [ledger.episode_dir / "final.mp4", *sorted(ledger.episode_dir.glob("*/*.mp4"))]
    for path in candidates:
        if path.is_file():
            found.setdefault(file_sha256(path), path.relative_to(ledger.episode_dir).as_posix())
    return found


CSS = """
:root { color-scheme: light dark; --fg: #1d2330; --bg: #f7f6f2; --card: #ffffff;
  --line: #d9d6cc; --muted: #5d6472; --accent: #1f5fbf; --fail: #b3261e; --warn: #8a5a00; }
@media (prefers-color-scheme: dark) { :root { --fg: #e6e4de; --bg: #15171c; --card: #1e2128;
  --line: #333844; --muted: #a0a6b2; --accent: #7fb0ff; --fail: #ff8a80; --warn: #e0b050; } }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); }
header { padding: 12px 16px; border-bottom: 1px solid var(--line); }
h1 { font-size: 20px; margin: 0; } h2 { font-size: 17px; margin: 0 0 8px; } h3 { margin: 0; }
main { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; padding: 16px; }
@media (min-width: 1100px) { main { grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); }
  .player { position: sticky; top: 0; align-self: start; max-height: 100vh; overflow: auto; } }
section, article { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px; margin-bottom: 12px; overflow-wrap: anywhere; }
video { width: 100%; background: #000; border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
td, th { text-align: left; padding: 4px 6px; border-top: 1px solid var(--line);
  vertical-align: top; }
ol, ul { padding-left: 20px; margin: 0; } li { margin: 4px 0; }
.transcript li, .findings li, .quotes li { list-style: none; margin-left: -20px; }
.findings li { display: flex; gap: 12px; padding: 8px 0; border-top: 1px solid var(--line); }
.findings img { width: 200px; height: auto; flex: none; border-radius: 4px; }
.finding.fail b { color: var(--fail); } .finding.uncertain b { color: var(--warn); }
.findings p, .quotes p { margin: 2px 0; }
.quotes img { max-width: 100%; border: 1px solid var(--line); margin-top: 4px; }
blockquote { margin: 8px 0 0; padding-left: 10px; border-left: 3px solid var(--accent); }
pre { white-space: pre-wrap; font-size: 13px; margin: 4px 0; }
a { color: var(--accent); } a.t { font-variant-numeric: tabular-nums; }
.note, .empty { color: var(--muted); font-size: 13px; }
.now { background: color-mix(in srgb, var(--accent) 16%, transparent); }
"""

JS = """
(() => {
  const video = document.getElementById("video");
  const spans = Array.from(document.querySelectorAll("[data-start]"));
  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-t]");
    if (!link || !video) return;
    event.preventDefault();
    video.currentTime = Number(link.dataset.t);
    video.scrollIntoView({ block: "nearest" });
  });
  if (!video) return;
  video.addEventListener("timeupdate", () => {
    const now = video.currentTime;
    for (const el of spans) {
      const on = now >= Number(el.dataset.start) && now < Number(el.dataset.end);
      el.classList.toggle("now", on);
    }
  });
})();
"""
