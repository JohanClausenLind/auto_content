"""The review page and the review CLI on a fake episode: escaping, seeking, stills, crops."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from content_factory.cli.explainer_cmd import app
from content_factory.explainer.patches import PatchRecord, append_patch
from content_factory.explainer.pipeline import EpisodeConfig, run
from content_factory.explainer.review_page import ASSETS_DIR, PAGE_NAME, write_review_page
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import (
    CaptureAsset,
    CaptureQuote,
    CaptureSection,
    CaptureTile,
    DomRangeLocator,
    LayoutChoiceRepair,
    PageRect,
    ScriptPlan,
    SourceCaptureManifest,
    Viewport,
    VisualSpec,
)
from tests.unit.explainer_fakes import SIXTY, VOICE, fake_seams

HOSTILE_TITLE = "<script>alert('t')</script>"
HOSTILE_QUOTE = "<img src=x onerror=alert(1)>"
HOSTILE_REASON = "</pre><script>alert('reason')</script>"


def _quiet(_line: str) -> None:
    return None


@dataclass(frozen=True)
class Episode:
    config_path: Path
    config: EpisodeConfig


@pytest.fixture(scope="module")
def episode(tmp_path_factory: pytest.TempPathFactory) -> Episode:
    """A finished fake run from a config file, one hostile patch and one hostile capture."""
    root = tmp_path_factory.mktemp("page")
    config_path = root / "config.json"
    fields = {
        "episode_id": "epi_reviewpage1",
        "pack": str(SIXTY / "pack.json"),
        "script": str(SIXTY / "script.json"),
        "spec": str(SIXTY / "spec.json"),
        "voice": VOICE.model_dump(mode="json"),
        "output_root": "episodes",
    }
    config_path.write_text(json.dumps(fields))
    config = EpisodeConfig.load(config_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CF_SERVICES_DIR", str(root / "services"))
        assert run(config, seams=fake_seams(), log=_quiet).status == "done"
    patches = root / "patches.jsonl"
    assert config.patches == patches
    repair = LayoutChoiceRepair(repair="layout_choice", scene_id="scn_teeth001", layout="primary")
    append_patch(patches, PatchRecord.create(repair, reason=HOSTILE_REASON, author="tester"))
    _hostile_capture(config.episode_dir)
    return Episode(config_path, config)


def _hostile_capture(episode_dir: Path) -> None:
    """Two 800 px tiles with the quote's line straddling their seam, named by the captures index."""
    tiles = []
    for i, rows in enumerate(((790, 800), (0, 10))):
        image = Image.new("RGB", (1280, 800), "white")
        image.paste((0, 0, 0), (100, rows[0], 500, rows[1]))
        path = episode_dir / "captures" / f"tile-{i}.png"
        image.save(path)
        tiles.append(
            CaptureTile(
                path=str(path), y_px=i * 800, width=1280, height=800, sha256=file_sha256(path)
            )
        )
    quote = CaptureQuote(
        quote_id="qte_00000001",
        section_id="sec_00000001",
        text=HOSTILE_QUOTE,
        locator=DomRangeLocator(
            kind="dom_range", start_path="/p", start_offset=0, end_path="/p", end_offset=5
        ),
        occurrence_index=0,
        line_rects=(PageRect(x=100, y=790, width=400, height=20),),
    )
    manifest = SourceCaptureManifest(
        capture_id="cap_hostile0001",
        source_id="src_hostile01",
        url="javascript:alert(1)",
        publisher="<b>Evil</b>",
        title=HOSTILE_TITLE,
        captured_at="2026-09-26T00:00:00Z",
        capture_kind="wacz",
        artifact_sha256="0" * 64,
        viewport=Viewport(width=1280, height=800),
        page_height_px=1600,
        text_sha256="1" * 64,
        extractor="test",
        extractor_version="1",
        sections=(CaptureSection(section_id="sec_00000001", order=0, scroll_y_px=0),),
        quotes=(quote,),
    )
    asset = CaptureAsset(capture_id=manifest.capture_id, manifest=manifest, tiles=tuple(tiles))
    (episode_dir / "captures" / "cap_hostile0001.json").write_text(asset.canonical_json())
    index = {"captures": ["captures/cap_hostile0001.json"]}
    (episode_dir / "captures" / "index.json").write_text(json.dumps(index))


@pytest.fixture(scope="module")
def page(episode: Episode) -> str:
    return write_review_page(episode.config.episode_dir).read_text()


def test_the_page_escapes_every_untrusted_text_and_runs_only_its_own_script(page: str) -> None:
    assert page.count("<script>") == 1
    assert "&lt;script&gt;alert(&#x27;t&#x27;)&lt;/script&gt;" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page and HOSTILE_QUOTE not in page
    assert "&lt;/pre&gt;&lt;script&gt;alert(&#x27;reason&#x27;)" in page
    assert "&lt;b&gt;Evil&lt;/b&gt;" in page


def test_a_non_http_source_url_is_shown_as_text_never_as_a_link(page: str) -> None:
    assert 'href="javascript:' not in page
    assert "<code>javascript:alert(1)</code>" in page


def test_the_page_plays_the_final_mp4_and_every_scene_and_segment_seeks_it(page: str) -> None:
    assert '<video id="video" controls preload="metadata" src="final.mp4">' in page
    script = ScriptPlan.model_validate_json((SIXTY / "script.json").read_text())
    spec = VisualSpec.model_validate_json((SIXTY / "spec.json").read_text())
    for segment in script.segments:
        assert f"<code>{segment.segment_id}</code>" in page
    for scene in spec.scenes:
        assert f'id="scene-{scene.scene_id}"' in page
    assert page.count('data-t="') >= len(script.segments) + len(spec.scenes)


def test_findings_get_stills_decoded_from_the_video_they_were_seen_in(
    episode: Episode, page: str
) -> None:
    stills = sorted((episode.config.episode_dir / ASSETS_DIR).glob("frame-*.png"))
    assert stills and f'<img src="{ASSETS_DIR}/{stills[0].name}"' in page
    assert "in <code>animatic/animatic.mp4</code>" in page and "in <code>final.mp4</code>" in page


def test_a_quote_crop_is_stitched_across_the_tile_seam(episode: Episode, page: str) -> None:
    crop = episode.config.episode_dir / ASSETS_DIR / "quote-cap_hostile0001-qte_00000001.png"
    assert f'src="{ASSETS_DIR}/{crop.name}"' in page
    with Image.open(crop) as image:
        assert image.size == (412, 32)
        assert image.getpixel((206, 10)) == (0, 0, 0) and image.getpixel((206, 20)) == (0, 0, 0)


def test_the_page_lists_the_patch_history_and_the_export_files(episode: Episode, page: str) -> None:
    assert "layout_choice" in page and "Patches (1)" in page
    assert 'href="exports/final.mp4"' in page
    assert (episode.config.episode_dir / PAGE_NAME).is_file()


def test_evidence_names_sources_claims_and_the_scenes_citing_them(episode: Episode) -> None:
    result = CliRunner().invoke(app, ["evidence", str(episode.config_path)])
    assert result.exit_code == 0, result.output
    assert "src_wiki_gear" in result.output
    assert "<https://en.wikipedia.org/wiki/Bicycle_gearing>" in result.output
    assert "- clm_ratio_top [illustrative_assumption] = 4 ratio (exact):" in result.output
    assert "scenes: scn_question, scn_ratio001, scn_formula1;" in result.output


def test_findings_print_scene_beat_time_frame_and_video_link(episode: Episode) -> None:
    root = str(episode.config.output_root)
    result = CliRunner().invoke(app, ["findings", episode.config.episode_id, "--root", root])
    assert result.exit_code == 0, result.output
    assert "scene=scn_open0001" in result.output and "frame=0" in result.output
    assert "final.mp4#t=0.000" in result.output
    assert json.loads(result.output.splitlines()[-1])["findings"] >= 2


def test_a_repair_that_is_not_json_is_refused_and_nothing_is_recorded(
    episode: Episode, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    shutil.copyfile(episode.config_path, config_path)
    data = json.loads(config_path.read_text())
    config_path.write_text(json.dumps({**data, "output_root": str(episode.config.output_root)}))
    result = CliRunner().invoke(
        app, ["patch", str(config_path), "--repair", '{"repair": "rename"}', "--reason", "x"]
    )
    assert result.exit_code == 1
    assert not (tmp_path / "patches.jsonl").exists()


def test_the_page_command_writes_review_html(episode: Episode) -> None:
    root = str(episode.config.output_root)
    result = CliRunner().invoke(app, ["page", episode.config.episode_id, "--root", root])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["page"] == str(episode.config.episode_dir / PAGE_NAME)
