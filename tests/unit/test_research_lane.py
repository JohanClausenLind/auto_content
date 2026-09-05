"""The research lane, end to end offline: uploads in, verified claims out.

Every library this exercises already existed and was tested. `ingest.uploads` has had size caps,
magic-number sniffing and an allowlist that never accepts SVG since phase 4; `research.claims` has
verified numbers against evidence and datasets since section 10; `research.fetch` re-validates
every redirect hop against SSRF. What was missing was anything that put them in a line, so:

* `stage_ingest` did not exist, which is why `data-story-video` was the one lane the catalogue
  marked unfinished;
* `stage_compile_datasets` returned `sample_dataset()`, so a data-led film could only be about
  Swedish wind power whatever the operator uploaded;
* `stage_verify_claims` re-derived the FIXTURE claims and gated `sample_story_plan()` — a film
  nobody was rendering — and it ran before `plan_story`, so there was no script to gate;
* `EvidenceRequirement.requires_independent_sources` was computed and read nowhere, so two outlets
  reprinting one press release counted twice.

Nothing here touches the network: `execution.live_research` is off, which is the default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.config import get_settings
from content_factory.datasets import DatasetError, Transform, compile_dataset, parse_transforms
from content_factory.runners.local import make_context
from content_factory.schemas.render import DatasetTable
from content_factory.workflows.stages import (
    StageContext,
    stage_compile_datasets,
    stage_ingest,
    stage_research,
    stage_verify_claims,
)

WIND_CSV = """year,source,twh
2023,wind,28.5
2024,wind,33.1
2025,wind,34.9
2025,nuclear,47.2
"""


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CF__EXECUTION__LIVE_RESEARCH", "false")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    return make_context(project_dir=tmp_path / "project", brief={"topic": "wind in Sweden"})


def _upload(ctx: StageContext, name: str, content: str | bytes) -> Path:
    uploads = ctx.project_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


# --- the dataset compiler ---------------------------------------------------------------------


def test_a_csv_becomes_a_typed_table_and_transforms_are_recorded(tmp_path: Path) -> None:
    path = tmp_path / "energy.csv"
    path.write_text(WIND_CSV)
    result = compile_dataset(path, dataset_id="ds_energy000001")
    assert isinstance(result.table, DatasetTable)
    assert result.table.columns == ("year", "source", "twh")
    assert len(result.table.rows) == 4
    assert result.table.rows[0]["twh"] == 28.5  # a number, not the string "28.5"
    # Read straight from the file, so it is source data.
    assert result.table.classification == "SOURCE_DATA"
    assert result.input_sha256 == __import__("hashlib").sha256(path.read_bytes()).hexdigest()

    shaped = compile_dataset(
        path,
        dataset_id="ds_energy000001",
        transforms=(
            Transform(kind="filter", column="source", op="eq", value="wind"),
            Transform(kind="sort", columns=("year",), descending=True),
            Transform(kind="head", limit=2),
            Transform(kind="select", columns=("year", "twh")),
        ),
    )
    assert [r["year"] for r in shaped.table.rows] == [2025, 2024]
    assert shaped.table.columns == ("year", "twh")
    # Still source data: filtering and sorting compute nothing.
    assert shaped.table.classification == "SOURCE_DATA"
    # And the derivation is on the label and in the record, readable without this module.
    assert "filter source eq 'wind'" in shaped.table.label
    assert shaped.record()["described"] == [
        "filter source eq 'wind'",
        "sort by year desc",
        "first 2 rows",
        "select year, twh",
    ]


def test_a_computed_column_makes_the_table_derived_data(tmp_path: Path) -> None:
    """The distinction is load-bearing: the renderer labels an estimate on screen, and calling a
    computed share "source data" puts a derived number on a chart claiming to be measured."""
    path = tmp_path / "energy.csv"
    path.write_text(WIND_CSV)
    result = compile_dataset(
        path,
        dataset_id="ds_share00000001",
        transforms=(
            Transform(kind="filter", column="year", op="eq", value=2025),
            Transform(kind="share_of_total", column="twh", into="share_pct"),
            Transform(kind="round", columns=("share_pct",), digits=1),
        ),
    )
    assert result.table.classification == "DERIVED_DATA"
    assert "share_pct" in result.table.columns
    # A dataset cell is `str | int | float | None`; a share is a number by construction here.
    shares = [float(r["share_pct"] or 0) for r in result.table.rows]
    assert sum(shares) == pytest.approx(100.0, abs=0.2)
    assert result.record()["described"][1] == "share_pct = twh / sum(twh)"


def test_a_transform_naming_a_column_the_file_lacks_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "energy.csv"
    path.write_text(WIND_CSV)
    with pytest.raises(DatasetError, match="gigawatts"):
        compile_dataset(
            path,
            dataset_id="ds_x00000000001",
            transforms=(Transform(kind="select", columns=("gigawatts",)),),
        )
    # Refused on the suffix, before the bytes are read: an unsupported 200 MB export should not
    # be loaded into memory to be told no. (The file need not even exist.)
    with pytest.raises(DatasetError, match=r"only \.csv and \.json"):
        compile_dataset(tmp_path / "notes.txt", dataset_id="ds_x00000000001")


def test_an_unnamed_derived_column_and_a_bad_transform_list_are_both_refused() -> None:
    """An unnamed derived column is a number on a chart nobody can trace back."""
    with pytest.raises(ValueError, match="untraceable"):
        Transform(kind="share_of_total", column="twh")
    with pytest.raises(DatasetError, match="JSON array"):
        parse_transforms("{not json")
    with pytest.raises(DatasetError, match="not one this module can run"):
        parse_transforms([{"kind": "exec_arbitrary_python"}])
    assert parse_transforms(None) == () and parse_transforms("[]") == ()


def test_a_json_upload_works_in_every_shape_an_export_uses(tmp_path: Path) -> None:
    rows = [{"year": 2024, "twh": 33.1}, {"year": 2025, "twh": 34.9}]
    for name, payload in (
        ("array.json", rows),
        ("wrapped.json", {"rows": rows}),
        ("single.json", rows[0]),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(payload))
        table = compile_dataset(path, dataset_id="ds_json00000001").table
        assert "twh" in table.columns, name


# --- the stages -------------------------------------------------------------------------------


def test_ingest_sniffs_every_upload_and_records_it_as_a_source(ctx: StageContext) -> None:
    _upload(ctx, "energy.csv", WIND_CSV)
    _upload(ctx, "notes.txt", "Wind supplied about a fifth of the country's electricity.")
    # A real PNG, so the magic sniff has something to agree with.
    _upload(
        ctx,
        "chart.png",
        bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000a49444154789c6360000002000100ffff0300000600"
            "0557bfabd40000000049454e44ae426082"
        ),
    )
    out = stage_ingest(ctx)
    assert out.facts["uploads"] == 3 and out.facts["sources"] == 3
    assert out.facts["kinds"] == ["data", "image", "text"]
    # The image is offered as a render asset; a CSV is not (it becomes a dataset instead).
    assert out.facts["assets"] == 1

    manifest = json.loads((ctx.project_dir / "research" / "uploads.json").read_text())
    by_file = {r["file"]: r for r in manifest["files"]}
    assert by_file["energy.csv"]["mime"] == "text/csv"
    assert by_file["chart.png"]["mime"] == "image/png"
    sources = json.loads((ctx.project_dir / "research" / "upload-sources.json").read_text())
    assert {s["classification"] for s in sources} == {"operator_upload"}
    # The URL is the file's own path. Inventing an http one would be inventing provenance.
    assert all(s["canonical_url"].startswith("file://") for s in sources)
    assert all(s["capture_sha256"] and s["size_bytes"] > 0 for s in sources)


def test_a_hand_copied_recording_is_converted_rather_than_refused(ctx: StageContext) -> None:
    """A .mkv copied into the run's uploads folder used to fail the whole stage. The browser
    converts on upload; this is the same step for a file put there by hand — the operator's own
    file is left alone and the converted copy is what becomes the source."""
    import subprocess

    uploads = ctx.project_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    mkv = uploads / "screen recording.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=10:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(mkv),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    before = mkv.read_bytes()

    out = stage_ingest(ctx)

    assert out.facts["uploads"] == 1 and out.facts["kinds"] == ["video"]
    record = json.loads((ctx.project_dir / "research" / "uploads.json").read_text())["files"][0]
    assert record["file"] == "screen recording.mkv"  # what the operator sees on disk
    assert record["mime"] == "video/mp4"  # what the run actually stored
    assert record["converted_from"] == "video/x-matroska"
    assert record["conversion"] == "remux"
    assert record["stored_as"].endswith(".mp4")
    # The upload itself is untouched: converting in place would edit the operator's file.
    assert mkv.read_bytes() == before
    # A scene naming this asset is handed the converted copy, not the .mkv.
    assets = json.loads((ctx.project_dir / "research" / "uploads.json").read_text())["assets"]
    assert next(iter(assets.values())).endswith(".mp4")


def test_a_rejected_upload_fails_the_stage_rather_than_being_skipped(ctx: StageContext) -> None:
    """A film built from three of an operator's four files, silently, is worse than one that
    stopped. And a declared .svg is refused whatever the bytes sniff as."""
    _upload(ctx, "energy.csv", WIND_CSV)
    _upload(ctx, "logo.svg", "<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    with pytest.raises(RuntimeError, match=r"logo\.svg"):
        stage_ingest(ctx)


def test_an_empty_uploads_directory_is_a_no_op_and_says_so(ctx: StageContext) -> None:
    out = stage_ingest(ctx)
    assert out.facts == {"uploads": 0, "sources": 0}
    note = json.loads((ctx.project_dir / "research" / "uploads.json").read_text())
    assert note["files"] == [] and "nothing to ingest" in note["note"]


def test_uploaded_tables_become_the_datasets_and_the_fixture_is_the_fallback(
    ctx: StageContext,
) -> None:
    # No uploads: the fixture, so every lane that charts something still runs.
    assert stage_compile_datasets(ctx).facts == {"datasets": 1, "source": "fixture"}

    _upload(ctx, "energy.csv", WIND_CSV)
    sidecar = ctx.project_dir / "uploads" / "energy.csv.transforms.json"
    sidecar.write_text(
        json.dumps(
            [
                {"kind": "filter", "column": "source", "op": "eq", "value": "wind"},
                {"kind": "share_of_total", "column": "twh", "into": "share_pct"},
            ]
        )
    )
    ingested = stage_ingest(ctx)
    # The sidecar is metadata about an upload, not an upload: it is JSON, so without the check it
    # looked like a data file and turned one CSV into two datasets.
    assert ingested.facts["uploads"] == 1 and ingested.facts["transform_sidecars"] == 1
    out = stage_compile_datasets(ctx)
    assert out.facts["source"] == "uploads" and out.facts["datasets"] == 1
    assert out.facts["classifications"] == ["DERIVED_DATA"] and out.facts["transforms"] == 2

    written = sorted((ctx.project_dir / "data").glob("ds_*.json"))
    tables = [p for p in written if not p.name.endswith(".transforms.json")]
    table = DatasetTable.model_validate_json(tables[0].read_text())
    assert "share_pct" in table.columns and table.classification == "DERIVED_DATA"
    # The derivation is beside the table, so it is reproducible from disk.
    record = json.loads(tables[0].with_suffix(".transforms.json").read_text())
    assert record["described"][0] == "filter source eq 'wind'"
    assert record["input_sha256"]


def test_research_carries_the_operators_uploads_as_sources(ctx: StageContext) -> None:
    _upload(ctx, "energy.csv", WIND_CSV)
    stage_ingest(ctx)
    out = stage_research(ctx)
    assert out.facts["live"] is False
    # The two fixture sources plus the operator's own file.
    assert out.facts["sources"] == 3
    sources = json.loads((ctx.project_dir / "research" / "sources.json").read_text())
    assert any(s["classification"] == "operator_upload" for s in sources)


def test_the_claim_digest_ignores_the_check_date(ctx: StageContext) -> None:
    """A cache key that changes with the clock is not a cache key: folding `checked_at` in made
    every stage downstream of research re-run at midnight."""
    from content_factory.schemas.research import ClaimRecord
    from content_factory.workflows.stages import _claim_digest

    stage_research(ctx)
    claims = [
        ClaimRecord.model_validate(r)
        for r in json.loads((ctx.project_dir / "research" / "claims.json").read_text())
    ]
    later = [c.model_copy(update={"checked_at": "2030-01-01"}) for c in claims]
    assert _claim_digest(claims) == _claim_digest(later)
    # But a changed verdict is a changed claim set.
    from content_factory.schemas.research import VerificationStatus

    reviewed = claims[0].model_copy(update={"status": VerificationStatus.needs_review})
    flipped = [reviewed, *claims[1:]]
    assert _claim_digest(flipped) != _claim_digest(claims)


def test_verify_claims_reverifies_what_is_on_disk_and_needs_research_first(
    ctx: StageContext,
) -> None:
    with pytest.raises(RuntimeError, match="run research first"):
        stage_verify_claims(ctx)
    stage_research(ctx)
    out = stage_verify_claims(ctx)
    assert out.facts["claims"] == 2
    assert out.facts["by_status"]["supported"] == 2
    assert out.facts["independence"] == []
    # Deterministic: the same evidence gives the same digest, whatever the wall clock says.
    assert stage_verify_claims(ctx).outputs_hash == out.outputs_hash


# --- the independence gate, computed since section 10 and read nowhere -------------------------


def _sourced(publisher: str, source_id: str, excerpt: str):
    from content_factory.schemas.research import (
        EvidenceLocator,
        EvidenceRecord,
        SourceClass,
        SourceRecord,
    )

    source = SourceRecord(
        source_id=source_id,
        workspace_id="ws_demo00000001",
        canonical_url=f"https://{publisher.lower().replace(' ', '')}.example/x",
        requested_url=f"https://{publisher.lower().replace(' ', '')}.example/x",
        final_url=f"https://{publisher.lower().replace(' ', '')}.example/x",
        title="report",
        publisher=publisher,
        accessed_at="2026-09-01",
        capture_sha256="0" * 64,
        content_type="text/html",
        size_bytes=100,
        classification=SourceClass.news,
    )
    record = EvidenceRecord(
        evidence_id=f"evd_{source_id[-9:]}",
        source_id=source_id,
        excerpt=excerpt,
        locator=EvidenceLocator(kind="char_range", start=0, end=len(excerpt)),
        captured_at="2026-09-01",
    )
    return source, record


def test_two_outlets_reprinting_one_wire_report_count_as_one_publisher() -> None:
    """`requires_independent_sources=2` has been set on high-stakes claims since the requirement
    compiler was written, and nothing read it — so this exact case passed."""
    from content_factory.research.claims import (
        build_claim,
        compile_requirements,
        independence_findings,
        independent_publishers,
    )

    statement = "The treatment cut hospital admissions by 30 percent, according to the trial."
    assert compile_requirements([statement])[0].requires_independent_sources == 2

    wire_excerpt = "The trial cut admissions by 30 percent, Reuters reported on Tuesday."
    a_source, a_evidence = _sourced("Daily Mirror", "src_aaaaaaaaaaa1", wire_excerpt)
    b_source, b_evidence = _sourced("Evening Post", "src_bbbbbbbbbbb2", wire_excerpt)
    sources = {s.source_id: s for s in (a_source, b_source)}
    evidence = [a_evidence, b_evidence]
    claim = build_claim(
        "clm_wire00000001",
        "ws_demo00000001",
        statement,
        evidence,
        sources_by_id=sources,
        checked_at="2026-09-01",
    )
    # Two source ids, two publishers on paper, ONE piece of reporting.
    assert len(claim.evidence_ids) == 2
    assert independent_publishers(claim, evidence, sources) == ("reuters",)
    findings = independence_findings([claim], evidence, sources)
    assert [f.check for f in findings] == ["independent_sources"]
    assert "needs 2 independent publishers and has 1" in findings[0].message

    # Two publishers doing their own reporting pass.
    own_a = a_evidence.model_copy(
        update={"excerpt": "Our analysis of the trial found admissions fell 30 percent."}
    )
    own_b = b_evidence.model_copy(
        update={"excerpt": "We reviewed the trial data: admissions were down 30 percent."}
    )
    claim2 = claim.model_copy(update={"evidence_ids": (own_a.evidence_id, own_b.evidence_id)})
    assert set(independent_publishers(claim2, [own_a, own_b], sources)) == {
        "daily mirror",
        "evening post",
    }
    assert independence_findings([claim2], [own_a, own_b], sources) == []


def test_the_gate_leaves_ordinary_claims_and_unsupported_ones_alone() -> None:
    """It fires only where the requirement compiler asks for two publishers, and never on a claim
    that already failed — a second finding on an unsupported claim buries the first."""
    from content_factory.research.claims import build_claim, independence_findings
    from content_factory.schemas.research import VerificationStatus

    source, evidence = _sourced("Energimyndigheten", "src_ccccccccccc3", "Wind supplied 21%.")
    sources = {source.source_id: source}
    ordinary = build_claim(
        "clm_ordinary0001",
        "ws_demo00000001",
        "In 2025, wind supplied about 21% of Sweden's electricity.",
        [evidence],
        sources_by_id=sources,
        checked_at="2026-09-01",
    )
    assert independence_findings([ordinary], [evidence], sources) == []

    unsupported = ordinary.model_copy(
        update={
            "statement": "The treatment cures the disease in 90 percent of patients.",
            "status": VerificationStatus.unsupported,
        }
    )
    assert independence_findings([unsupported], [evidence], sources) == []


# --- the script gate, now where a script exists -----------------------------------------------


def test_lock_script_gates_the_script_it_locked_and_not_a_fixture(ctx: StageContext) -> None:
    """`verify_claims` ran BEFORE `plan_story` and gated `sample_story_plan()` — a film nobody was
    rendering. The gate belongs at the last moment before the words are spoken."""
    import json as _json

    from content_factory.schemas.research import ClaimRecord, VerificationStatus
    from content_factory.workflows.stages import stage_lock_script, stage_plan_story

    stage_research(ctx)
    stage_plan_story(ctx)
    out = stage_lock_script(ctx)
    gate = _json.loads((ctx.ddir() / "script" / "claim-gate.json").read_text())
    assert gate["passed"] and gate["findings"] == []
    # The demo plan links two claims from its beats, and both were checked.
    assert out.facts["claims_checked"] == gate["referenced"] == 2

    # Break one of the claims the script actually references and the lock refuses.
    claims_path = ctx.project_dir / "research" / "claims.json"
    claims = [ClaimRecord.model_validate(r) for r in _json.loads(claims_path.read_text())]
    broken = [
        c.model_copy(update={"status": VerificationStatus.unsupported, "evidence_ids": ()})
        if c.claim_id == "clm_wind0000001"
        else c
        for c in claims
    ]
    claims_path.write_text(
        _json.dumps([c.model_dump(mode="json") for c in broken], indent=1, sort_keys=True)
    )
    with pytest.raises(RuntimeError, match="lock_script refused"):
        stage_lock_script(ctx)


def test_a_written_film_with_no_claims_is_not_gated_into_the_ground(ctx: StageContext) -> None:
    """Every picture-story lane is a hand-written StoryPlan with no claim links. Inventing a gate
    failure for one would block the lanes that never had claims to check."""
    import json as _json

    from content_factory.workflows.stages import stage_lock_script

    object.__setattr__(ctx, "params", {"story": "fixtures/story/love_story.json"})
    from content_factory.workflows.stages import stage_plan_story

    stage_plan_story(ctx)
    assert not (ctx.project_dir / "research" / "claims.json").exists()
    out = stage_lock_script(ctx)
    gate = _json.loads((ctx.ddir() / "script" / "claim-gate.json").read_text())
    assert out.facts["sentences"] == 6 and gate["passed"]
    assert "nothing to check the script against" in gate["note"]
