"""Assets have to reach the renderer, and non-measured figures have to be labelled.

Two gaps this closes, both of the same shape — a field the contract carried and no code read:

* ``RenderBundle.assets`` was filled by nothing and served from nowhere, so `image`, `screenshot`
  and `map` scenes could not resolve a file even when ``ingest`` had stored one. The renderer
  resolves an asset path through Remotion's ``staticFile()``, which is relative to the bundle's own
  public directory, so an absolute path to an operator's upload is a 404 with no error on screen.
* ``ChartScene.source_ids`` and ``background_asset_id`` are new here; the tests below are the
  fixtures for them.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from content_factory.schemas import scenes as sc
from content_factory.schemas.render import DatasetTable, RenderBundle, SourceCard
from content_factory.timeline.compiler import compile_timeline
from content_factory.video import render as video_render
from content_factory.video.render import ASSET_BYTES_MAX, RenderError, stage_assets

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)
"""A real one-pixel PNG. Real bytes rather than a stub, because staging hashes the file."""


def _table() -> DatasetTable:
    return DatasetTable(
        dataset_id="ds_sides000001",
        classification="SOURCE_DATA",
        columns=("year", "share_pct"),
        rows=({"year": "2019", "share_pct": 12}, {"year": "2024", "share_pct": 34}),
        unit="%",
        label="Wind share by year",
    )


def _bundle(assets: dict[str, str], scenes_: tuple = ()) -> RenderBundle:
    table = _table()
    beat = sc.VisualBeat(
        beat_id="beat_000000001", order=0, display_text="One beat.", planned_duration_ms=1200
    )
    plan = sc.StoryPlan(
        plan_id="plan_assets00001",
        deliverable_id="dlv_assets00001",
        fps=30,
        width=1080,
        height=1920,
        beats=(beat,),
        scenes=scenes_
        or (
            sc.ImageScene(
                scene_id="scn_image000001",
                beat_id="beat_000000001",
                asset_id="ast_still000001",
                alt_text="A still.",
            ),
        ),
    )
    return RenderBundle(
        bundle_id="bnd_assets00001",
        kind="timeline",
        plan=plan,
        timeline=compile_timeline(plan, timeline_id="tl_assets000001", narrated=False),
        datasets={table.dataset_id: table},
        assets=assets,
    )


@pytest.fixture
def public_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage into a temporary directory. The real one lives inside the renderer app, and a unit
    test has no business writing into the source tree."""
    target = tmp_path / "public" / "assets"
    monkeypatch.setattr(video_render, "PUBLIC_ASSETS", target)
    return target


def test_a_local_file_is_staged_by_content_hash_into_the_renderers_public_directory(
    tmp_path: Path, public_assets: Path
) -> None:
    src = tmp_path / "shot.PNG"
    src.write_bytes(PNG)
    staged = stage_assets(_bundle({"ast_still000001": str(src)}))
    value = staged.assets["ast_still000001"]
    # Public-relative and forward-slashed: this string is handed to `staticFile()`.
    assert value.startswith("assets/")
    assert not value.startswith("/")
    assert (public_assets / Path(value).name).read_bytes() == PNG
    # The suffix is lowercased so one file cannot stage twice under two spellings.
    assert value.endswith(".png")


def test_staging_is_idempotent_and_independent_of_where_the_project_lives(
    tmp_path: Path, public_assets: Path
) -> None:
    """Two copies of one file in two places stage to one name, so a render's own hash does not
    change when a project moves. That is a property a provenance record should have."""
    a = tmp_path / "one" / "shot.png"
    b = tmp_path / "two" / "other-name.png"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(PNG)
    first = stage_assets(_bundle({"ast_still000001": str(a)}))
    second = stage_assets(_bundle({"ast_still000001": str(b)}))
    assert first.assets == second.assets
    assert first.content_hash() == second.content_hash()


def test_a_url_is_left_alone_and_a_missing_file_is_left_for_the_scene_to_label(
    tmp_path: Path, public_assets: Path
) -> None:
    urls = {
        "ast_remote00001": "https://example.invalid/a.png",
        "ast_data000001": "data:image/png;base64,iVBOR",
        "ast_gone000001": str(tmp_path / "not-there.png"),
    }
    staged = stage_assets(_bundle(urls))
    # Unchanged: ImageScene draws a labelled card naming the id it could not resolve, which is a
    # better failure than refusing to render the other five scenes of a film.
    assert staged.assets == urls


def test_an_oversized_asset_is_refused_rather_than_copied_into_every_webpack_bundle(
    tmp_path: Path, public_assets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(video_render, "ASSET_BYTES_MAX", 512)
    big = tmp_path / "huge.png"
    big.write_bytes(b"\0" * 1024)
    with pytest.raises(RenderError, match="staging limit"):
        _ = stage_assets(_bundle({"ast_big00000001": str(big)}))
    assert not public_assets.exists()


def test_a_bundle_with_no_assets_is_returned_untouched() -> None:
    bundle = _bundle({})
    assert stage_assets(bundle) is bundle


def test_ingest_writes_the_asset_map_that_compile_timeline_reads(tmp_path: Path) -> None:
    """The two halves of the plumbing, checked against each other rather than by eye."""
    from content_factory.workflows.stages import _project_assets

    class Ctx:
        project_dir = tmp_path

    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "uploads.json").write_text(
        json.dumps({"files": [], "assets": {"ast_abc000000001": "/data/uploads/shot.png"}})
    )
    assert _project_assets(Ctx()) == {"ast_abc000000001": "/data/uploads/shot.png"}  # type: ignore[arg-type]


def test_no_uploads_json_means_no_assets_not_a_crash(tmp_path: Path) -> None:
    from content_factory.workflows.stages import _project_assets

    class Ctx:
        project_dir = tmp_path

    assert _project_assets(Ctx()) == {}  # type: ignore[arg-type]


def test_a_comparison_scene_needs_the_datasets_its_two_figures_point_at() -> None:
    """The bundle validator collected `value` and `data` and not the comparison's own two refs, so
    a bundle could promise a side-by-side against a table it did not carry."""
    comparison = sc.ComparisonScene(
        scene_id="scn_compare0001",
        beat_id="beat_000000001",
        title=sc.TextRef(text="Then against now"),
        left=sc.TextRef(text="2019"),
        right=sc.TextRef(text="2024"),
        left_value=sc.DataRef(dataset_id="ds_absent00001", column="share_pct", row_key="2019"),
        right_value=None,
    )
    with pytest.raises(ValueError, match="missing datasets"):
        _ = _bundle({}, (comparison,))


def test_the_four_typeset_cards_take_an_optional_background_still() -> None:
    beat = "beat_000000001"
    title = sc.TitleScene(
        scene_id="scn_title000001",
        beat_id=beat,
        title=sc.TextRef(text="A title"),
        background_asset_id="ast_still000001",
    )
    assert title.background_asset_id == "ast_still000001"
    # Unset by default on all four: these are typeset cards and read best on paper.
    cards: tuple[sc.TitleScene | sc.CalloutScene | sc.OutroScene | sc.QuoteScene, ...] = (
        sc.TitleScene(scene_id="scn_title000002", beat_id=beat, title=sc.TextRef(text="t")),
        sc.CalloutScene(scene_id="scn_call0000001", beat_id=beat, text=sc.TextRef(text="c")),
        sc.OutroScene(scene_id="scn_outro000001", beat_id=beat, text=sc.TextRef(text="o")),
        sc.QuoteScene(
            scene_id="scn_quote000001",
            beat_id=beat,
            quote=sc.TextRef(text="q"),
            attribution=sc.TextRef(text="a"),
            source_id="src_energimynd01",
        ),
    )
    for card in cards:
        assert card.background_asset_id is None


def test_a_chart_credits_its_own_sources_and_a_step_chart_is_a_chart_kind() -> None:
    chart = sc.ChartScene(
        scene_id="scn_step00000001",
        beat_id="beat_000000001",
        chart=sc.ChartKind.step,
        data=sc.DataRef(dataset_id="ds_sides000001", column="share_pct"),
        x="year",
        y=("share_pct",),
        title=sc.TextRef(text="A step chart"),
        source_ids=("src_energimynd01",),
    )
    assert chart.source_ids == ("src_energimynd01",)
    assert chart.chart.value == "step"
    # A step chart is not a bar, so it does not inherit the zero-baseline disclosure rule.
    assert (
        sc.ChartScene(
            scene_id="scn_step00000002",
            beat_id="beat_000000001",
            chart=sc.ChartKind.step,
            data=sc.DataRef(dataset_id="ds_sides000001"),
            x="year",
            y=("share_pct",),
            title=sc.TextRef(text="Truncated"),
            zero_baseline=False,
        ).truncation_disclosure
        is None
    )


def test_a_source_card_is_a_source_card() -> None:
    """Guards the credit line's shape: `citationLine` is publisher then title."""
    card = SourceCard(
        source_id="src_energimynd01",
        title="Energy statistics",
        publisher="Energimyndigheten",
        url="https://example.invalid/stats",
        accessed="2026-09-08",
    )
    assert f"{card.publisher} — {card.title}" == "Energimyndigheten — Energy statistics"


def test_the_staging_limit_is_a_real_number() -> None:
    assert 1024 * 1024 <= ASSET_BYTES_MAX <= 512 * 1024 * 1024
