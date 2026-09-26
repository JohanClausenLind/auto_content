"""Reviewer patches: the append-only log, content-addressed ids, stale bindings, rerun preview."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import get_args

import pytest

from content_factory.explainer.errors import EpisodeInvalidError
from content_factory.explainer.narration import take_id_for
from content_factory.explainer.patches import (
    SCRIPT_KINDS,
    SPEC_KINDS,
    TAKE_KINDS,
    PatchRecord,
    StalePatchError,
    append_patch,
    apply_patches,
    load_patches,
    record_patch,
    store_take,
)
from content_factory.explainer.pipeline import (
    LEDGER_NAME,
    EpisodeConfig,
    Seams,
    invalidated_steps,
    run,
)
from content_factory.explainer.timing import ESTIMATED_TOKEN_MS
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import (
    EvidencePack,
    LabelWordingRepair,
    LayoutChoiceRepair,
    NarrationManifest,
    ScriptPlan,
    TakeSelectionRepair,
    TypedRepair,
    VisualSpec,
)
from tests.unit.explainer_fakes import SIXTY, fake_seams, heard_script, sixty_config, write_tone

AT = "2026-09-26T10:00:00+00:00"
SCRIPT = ScriptPlan.model_validate_json((SIXTY / "script.json").read_text())
SPEC = VisualSpec.model_validate_json((SIXTY / "spec.json").read_text())
PACK = EvidencePack.model_validate_json((SIXTY / "pack.json").read_text())
SEGMENT = "seg_build0001"


def _quiet(_line: str) -> None:
    return None


def _label(entity_id: str = "ent_front_ser", short: str = "Big ring") -> LabelWordingRepair:
    return LabelWordingRepair(repair="label_wording", entity_id=entity_id, short_label=short)


def _record(repair: TypedRepair, reason: str = "reads better", **fields: str) -> PatchRecord:
    return PatchRecord.create(repair, reason=reason, author="tester", created_at=AT, **fields)


def _seams() -> Seams:
    return fake_seams(asr=heard_script(SCRIPT))


def test_appended_records_load_back_equal_and_in_order(tmp_path: Path) -> None:
    path = tmp_path / "patches.jsonl"
    first = _record(_label())
    second = _record(
        LayoutChoiceRepair(repair="layout_choice", scene_id="scn_teeth001", layout="split")
    )
    assert append_patch(path, first) and append_patch(path, second)
    assert load_patches(path) == [first, second]
    assert len(path.read_text().splitlines()) == 2


def test_appending_the_same_record_twice_writes_one_line(tmp_path: Path) -> None:
    path = tmp_path / "patches.jsonl"
    record = _record(_label())
    assert append_patch(path, record) is True
    assert append_patch(path, record) is False
    assert load_patches(path) == [record]


def test_a_missing_patches_file_is_no_patches(tmp_path: Path) -> None:
    assert load_patches(tmp_path / "absent.jsonl") == [] and load_patches(None) == []


def test_patch_ids_address_every_field_but_the_id() -> None:
    record = _record(_label())
    assert record.patch_id == _record(_label()).patch_id
    assert record.patch_id.startswith("pat_")
    assert record.patch_id != _record(_label(), reason="another reason").patch_id
    assert record.patch_id != _record(_label(short="Chainring")).patch_id
    other_time = PatchRecord.create(
        _label(), reason="reads better", author="tester", created_at="x" * 4
    )
    assert record.patch_id != other_time.patch_id


def test_a_hand_edited_line_no_longer_loads(tmp_path: Path) -> None:
    path = tmp_path / "patches.jsonl"
    append_patch(path, _record(_label()))
    edited = json.loads(path.read_text())
    edited["reason"] = "someone changed the reason by hand"
    path.write_text(json.dumps(edited) + "\n")
    with pytest.raises(EpisodeInvalidError, match=r"patches\.jsonl:1 .*does not address"):
        load_patches(path)


def test_only_a_take_selection_carries_a_take_path() -> None:
    with pytest.raises(ValueError, match="take_path is set on a take_selection"):
        _record(_label(), take_path="takes/x.wav")
    with pytest.raises(ValueError, match="take_path is set on a take_selection"):
        _record(
            TakeSelectionRepair(
                repair="take_selection", segment_id=SEGMENT, take_id="take_0123456789abcdef"
            )
        )


def test_every_repair_kind_is_applied_by_exactly_one_step() -> None:
    [union, *_] = get_args(TypedRepair)
    kinds = [get_args(model.model_fields["repair"].annotation)[0] for model in get_args(union)]
    groups = (SPEC_KINDS, SCRIPT_KINDS, TAKE_KINDS)
    assert sorted(kinds) == sorted(k for group in groups for k in group)


def test_patches_apply_in_order_so_the_later_one_wins() -> None:
    patches = [_record(_label(short="Big ring")), _record(_label(short="Front"))]
    spec, script, takes = apply_patches(SPEC, SCRIPT, {}, patches)
    entity = next(e for e in spec.entities if e.entity_id == "ent_front_ser")
    assert entity.short_label == "Front" and script == SCRIPT and takes == {}


def test_a_patch_on_a_removed_entity_is_a_stale_binding_naming_the_patch() -> None:
    record = _record(_label(entity_id="ent_gone00001"))
    with pytest.raises(StalePatchError) as raised:
        apply_patches(SPEC, SCRIPT, {}, [_record(_label()), record])
    [issue] = raised.value.issues
    assert raised.value.patch_id == record.patch_id
    assert issue.kind == "stale_binding" and issue.ids == (record.patch_id,)
    assert record.patch_id in issue.message and "unknown entity ent_gone00001" in issue.message


def test_a_take_selection_resolves_its_wav_and_goes_stale_when_the_wav_changes(
    tmp_path: Path,
) -> None:
    wav = write_tone(tmp_path / "new.wav", 4000)
    take_id, stored = store_take(wav, SEGMENT, tmp_path / "takes")
    assert take_id == take_id_for(SEGMENT, file_sha256(wav))
    assert stored == tmp_path / "takes" / SEGMENT / f"{take_id}.wav"
    repair = TakeSelectionRepair(repair="take_selection", segment_id=SEGMENT, take_id=take_id)
    record = _record(repair, take_path=stored.relative_to(tmp_path).as_posix())
    _, _, takes = apply_patches(SPEC, SCRIPT, {}, [record], root=tmp_path)
    assert takes == {SEGMENT: stored}
    write_tone(stored, 5000)
    with pytest.raises(StalePatchError, match=f"now take take_[0-9a-f]{{16}}, not {take_id}"):
        apply_patches(SPEC, SCRIPT, {}, [record], root=tmp_path)


def test_record_patch_refuses_a_repair_that_does_not_apply_and_writes_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "patches.jsonl"
    with pytest.raises(StalePatchError):
        record_patch(
            path,
            _label(entity_id="ent_gone00001"),
            reason="typo",
            author="tester",
            pack=PACK,
            script=SCRIPT,
            spec=SPEC,
        )
    assert not path.exists()


@pytest.fixture(scope="module")
def finished(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One full fake run whose narration verifies every take with the script-hearing ASR."""
    root = tmp_path_factory.mktemp("finished")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CF_SERVICES_DIR", str(root / "services"))
        result = run(_config(root), seams=_seams(), log=_quiet)
    assert result.status == "done"
    return root


def _config(root: Path) -> EpisodeConfig:
    return sixty_config(root, patches=root / "patches.jsonl", takes_dir=root / "takes")


def _copy(finished: Path, folder: Path, monkeypatch: pytest.MonkeyPatch) -> EpisodeConfig:
    shutil.copytree(finished, folder, dirs_exist_ok=True)
    monkeypatch.setenv("CF_SERVICES_DIR", str(folder / "services"))
    return _config(folder)


def _patch(config: EpisodeConfig, repair: TypedRepair, take: Path | None = None) -> None:
    assert config.patches is not None
    record_patch(
        config.patches,
        repair,
        reason="review",
        author="tester",
        pack=PACK,
        script=SCRIPT,
        spec=SPEC,
        take=take,
    )


def test_without_patches_nothing_is_invalidated(
    finished: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _copy(finished, tmp_path, monkeypatch)
    assert invalidated_steps(config, seams=_seams()) == ()
    assert run(config, seams=_seams(), log=_quiet).ran == ()


def test_a_label_patch_previews_and_reruns_plan_visuals_onward_but_not_narrate(
    finished: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _copy(finished, tmp_path, monkeypatch)
    _patch(config, _label())
    preview = invalidated_steps(config, seams=_seams())
    result = run(config, seams=_seams(), log=_quiet)
    assert result.status == "done" and result.ran == preview
    assert preview[:2] == ("plan_visuals", "compile")
    assert {"narrate", "capture_sources", "lock_script"} <= set(result.skipped)
    spec = VisualSpec.model_validate_json((config.episode_dir / "repair" / "spec.json").read_text())
    assert (
        next(e for e in spec.entities if e.entity_id == "ent_front_ser").short_label == "Big ring"
    )


def test_a_take_patch_previews_and_reruns_narrate_onward_only(
    finished: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _copy(finished, tmp_path, monkeypatch)
    tokens = len(SCRIPT.segment(SEGMENT).tokens)
    wav = write_tone(tmp_path / "longer.wav", tokens * ESTIMATED_TOKEN_MS * 3 // 2)
    take_id, stored = store_take(wav, SEGMENT, tmp_path / "takes")
    _patch(
        config,
        TakeSelectionRepair(repair="take_selection", segment_id=SEGMENT, take_id=take_id),
        stored,
    )
    preview = invalidated_steps(config, seams=_seams())
    result = run(config, seams=_seams(), log=_quiet)
    assert result.status == "done" and result.ran == preview and preview[0] == "narrate"
    assert result.skipped[:4] == (
        "freeze_evidence",
        "lock_script",
        "plan_visuals",
        "capture_sources",
    )
    manifest = NarrationManifest.model_validate_json(
        (config.episode_dir / "narration" / "narration-manifest.json").read_text()
    )
    [take] = [t for t in manifest.takes if t.segment_ids == (SEGMENT,)]
    assert take.take_id == take_id and take.kind == "recorded"
    assert take.audio_sha256 == file_sha256(wav)


def test_a_stale_spec_patch_fails_plan_visuals_with_the_patch_id(
    finished: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _copy(finished, tmp_path, monkeypatch)
    assert config.patches is not None
    record = _record(_label(entity_id="ent_gone00001"))
    append_patch(config.patches, record)
    result = run(config, seams=_seams(), log=_quiet)
    assert result.status == "failed" and result.stopped_at == "plan_visuals"
    assert result.error is not None and f"[stale_binding] patch {record.patch_id}" in result.error
    ledger = json.loads((config.episode_dir / LEDGER_NAME).read_text())
    assert ledger["ready"] is False
