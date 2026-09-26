"""Reviewer corrections: an append-only patches.jsonl of typed repairs, replayed in order (F5)."""

from __future__ import annotations

import hashlib
import os
import shutil
import wave
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from content_factory.explainer.errors import (
    ContractIssue,
    EpisodeInvalidError,
    explain_validation_error,
)
from content_factory.explainer.evidence import check_spec_text_numbers
from content_factory.explainer.narration import take_id_for
from content_factory.explainer.repair import apply_repair
from content_factory.explainer.validate import check_bindings, check_state
from content_factory.schemas.base import SchemaModel, canonical_dumps, file_sha256
from content_factory.schemas.explainer import (
    EvidencePack,
    ScriptPlan,
    TakeSelectionRepair,
    TypedRepair,
    VisualSpec,
)

PATCHES_NAME = "patches.jsonl"
SPEC_KINDS = frozenset(
    {
        "cue_offset",
        "label_wording",
        "text_correction",
        "layout_choice",
        "hold",
        "source_passage",
        "split_scene",
    }
)
# apply_repair returns the script unchanged for every kind today; one that edits it joins here.
SCRIPT_KINDS: frozenset[str] = frozenset()
TAKE_KINDS = frozenset({"take_selection"})


class PatchRecord(SchemaModel):
    """One reviewer correction: the typed repair, why, who, when, and a take's WAV."""

    patch_id: str = Field(pattern=r"^pat_[a-f0-9]{16}$")
    repair: TypedRepair
    reason: str = Field(min_length=1, max_length=600)
    author: str = Field(min_length=1, max_length=120)
    created_at: str = Field(min_length=4)
    take_path: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _addressed(self) -> PatchRecord:
        if (self.take_path is not None) != isinstance(self.repair, TakeSelectionRepair):
            msg = "take_path is set on a take_selection patch and on no other kind"
            raise ValueError(msg)
        expected = patch_id_for(
            self.repair, self.reason, self.author, self.created_at, self.take_path
        )
        if self.patch_id != expected:
            msg = f"patch_id {self.patch_id} does not address its content ({expected})"
            raise ValueError(msg)
        return self

    @classmethod
    def create(
        cls,
        repair: TypedRepair,
        *,
        reason: str,
        author: str,
        take_path: str | None = None,
        created_at: str | None = None,
    ) -> PatchRecord:
        at = created_at or datetime.now(UTC).isoformat(timespec="seconds")
        return cls(
            patch_id=patch_id_for(repair, reason, author, at, take_path),
            repair=repair,
            reason=reason,
            author=author,
            created_at=at,
            take_path=take_path,
        )


def patch_id_for(
    repair: TypedRepair, reason: str, author: str, created_at: str, take_path: str | None
) -> str:
    """sha256 over every field but the id, so a hand-edited line no longer loads."""
    body = canonical_dumps(
        {
            "repair": repair.model_dump(mode="json"),
            "reason": reason,
            "author": author,
            "created_at": created_at,
            "take_path": take_path,
        }
    )
    return "pat_" + hashlib.sha256(body.encode()).hexdigest()[:16]


class StalePatchError(EpisodeInvalidError):
    """A recorded patch that no longer applies to the spec or script it is replayed on."""

    def __init__(self, record: PatchRecord, why: str) -> None:
        self.patch_id = record.patch_id
        issue = ContractIssue(
            kind="stale_binding",
            where=f"patch {record.patch_id}",
            message=f"patch {record.patch_id} ({record.repair.repair}) no longer applies: {why}.",
            fix=(
                f"delete patch {record.patch_id} from {PATCHES_NAME}, or append one that targets "
                "the current spec."
            ),
            ids=(record.patch_id,),
        )
        super().__init__([issue])


def append_patch(path: Path, record: PatchRecord) -> bool:
    """Append one JSON line; False when that exact record is already there (safe to run twice)."""
    if any(p.patch_id == record.patch_id for p in load_patches(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.canonical_json() + "\n")
    return True


def load_patches(path: Path | None) -> list[PatchRecord]:
    """Every record in file order; a missing file is no patches, a bad line names its number."""
    if path is None or not path.is_file():
        return []
    records: list[PatchRecord] = []
    issues: list[ContractIssue] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(PatchRecord.model_validate_json(line))
        except ValidationError as error:
            for issue in explain_validation_error(error, PatchRecord):
                issues.append(
                    ContractIssue(
                        kind=issue.kind,
                        where=f"{path.name}:{number} {issue.where}",
                        message=issue.message,
                        fix=issue.fix,
                        ids=issue.ids,
                    )
                )
    seen: set[str] = set()
    for record in records:
        if record.patch_id in seen:
            issues.append(
                ContractIssue(
                    kind="conflicting_state",
                    where=path.name,
                    message=f"patch {record.patch_id} is recorded twice.",
                    fix="keep one of the two identical lines.",
                    ids=(record.patch_id,),
                )
            )
        seen.add(record.patch_id)
    if issues:
        raise EpisodeInvalidError(issues)
    return records


def of_kinds(patches: Sequence[PatchRecord], kinds: frozenset[str]) -> list[PatchRecord]:
    return [p for p in patches if p.repair.repair in kinds]


def apply_patches(
    spec: VisualSpec,
    script: ScriptPlan,
    narration_takes: Mapping[str, Path],
    patches: Sequence[PatchRecord],
    *,
    root: Path = Path(),
) -> tuple[VisualSpec, ScriptPlan, dict[str, Path]]:
    """Replay each patch through apply_repair in order; take paths resolve against root."""
    takes = dict(narration_takes)
    for record in patches:
        try:
            spec, script = apply_repair(spec, script, record.repair)
            script = ScriptPlan.model_validate(script.model_dump())
        except ValidationError as error:
            raise StalePatchError(record, "; ".join(e["msg"] for e in error.errors())) from None
        except ValueError as error:
            raise StalePatchError(record, str(error)) from None
        if isinstance(record.repair, TakeSelectionRepair):
            takes[record.repair.segment_id] = _selected_take(record, record.repair, script, root)
    return spec, script, takes


def _selected_take(
    record: PatchRecord, repair: TakeSelectionRepair, script: ScriptPlan, root: Path
) -> Path:
    if all(s.segment_id != repair.segment_id for s in script.segments):
        raise StalePatchError(record, f"the script has no segment {repair.segment_id}")
    path = root / (record.take_path or "")
    if not path.is_file():
        raise StalePatchError(record, f"its recording {path} is missing")
    actual = take_id_for(repair.segment_id, file_sha256(path))
    if actual != repair.take_id:
        raise StalePatchError(record, f"{path.name} is now take {actual}, not {repair.take_id}")
    return path


def store_take(wav: Path, segment_id: str, takes_dir: Path) -> tuple[str, Path]:
    """Copy a PCM WAV to <takes_dir>/<segment_id>/<take_id>.wav; an older take is never lost."""
    try:
        with wave.open(str(wav), "rb") as wf:
            frames = wf.getnframes()
    except (wave.Error, EOFError) as error:
        msg = f"{wav} is not a PCM WAV: {error}"
        raise ValueError(msg) from None
    if frames == 0:
        msg = f"{wav} holds no audio"
        raise ValueError(msg)
    take_id = take_id_for(segment_id, file_sha256(wav))
    out = take_path_for(take_id, segment_id, takes_dir)
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
        shutil.copyfile(wav, tmp)
        tmp.replace(out)
    return take_id, out


def take_path_for(take_id: str, segment_id: str, takes_dir: Path) -> Path:
    return takes_dir / segment_id / f"{take_id}.wav"


def record_patch(
    path: Path,
    repair: TypedRepair,
    *,
    reason: str,
    author: str,
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    take: Path | None = None,
    created_at: str | None = None,
) -> tuple[PatchRecord, bool]:
    """Check a repair on top of every earlier patch, then append it; nothing is written on error."""
    take_path = (
        None if take is None else take.resolve().relative_to(path.parent.resolve(), walk_up=True)
    )
    record = PatchRecord.create(
        repair,
        reason=reason,
        author=author,
        take_path=None if take_path is None else take_path.as_posix(),
        created_at=created_at,
    )
    patched, patched_script, _ = apply_patches(
        spec, script, {}, [*load_patches(path), record], root=path.parent
    )
    issues = check_bindings(pack, patched_script, patched) + check_state(patched)
    issues += check_spec_text_numbers(pack, patched)
    if issues:
        raise EpisodeInvalidError(issues)
    return record, append_patch(path, record)
