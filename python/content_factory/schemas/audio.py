"""Narration, alignment, captions, and audio QC contracts (section 15). Integer milliseconds."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, Sha256Hex, VersionedModel
from content_factory.schemas.scenes import WordTiming


class PronunciationEntry(SchemaModel):
    term: str = Field(min_length=1, max_length=80)
    respelling: str = Field(min_length=1, max_length=120)  # e.g. "en-er-yee-MIN-dih-heh-ten"
    ipa: str | None = Field(default=None, max_length=120)
    locale: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")
    notes: str = Field(default="", max_length=200)


class VoiceIdentity(SchemaModel):
    provider: Literal["mock", "kokoro", "elevenlabs", "azure"]
    voice_id: str = Field(min_length=1, max_length=80)
    model_revision: str = Field(min_length=1, max_length=120)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    seed: int | None = Field(default=None, ge=0)
    locale: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")


class NarrationRequest(SchemaModel):
    beat_id: OpaqueId
    display_text: str = Field(min_length=1, max_length=2000)
    spoken_text: str = Field(min_length=1, max_length=2000)  # after normalization/pronunciation
    voice: VoiceIdentity
    lexicon: tuple[PronunciationEntry, ...] = ()
    normalization_version: str = Field(default="1", min_length=1)


class TimingSource(StrEnum):
    provider = "provider"
    forced_alignment = "forced_alignment"
    asr = "asr"
    synthetic = "synthetic"  # mock executor only


class NarrationSegment(VersionedModel):
    """One synthesized beat: immutable audio + measured word timings (ms, segment-relative)."""

    beat_id: OpaqueId
    audio_sha256: Sha256Hex
    audio_format: Literal["wav", "mp3", "flac"] = "wav"
    sample_rate_hz: int = Field(ge=8000, le=192000)
    channels: Literal[1, 2] = 1
    duration_ms: int = Field(ge=1)
    words: tuple[WordTiming, ...] = Field(min_length=1)
    timing_source: TimingSource
    voice: VoiceIdentity
    spoken_text: str
    display_text: str
    normalization_version: str

    @model_validator(mode="after")
    def _words_inside(self) -> NarrationSegment:
        for w in self.words:
            if w.end_ms > self.duration_ms:
                msg = (
                    f"word {w.word!r} ends at {w.end_ms} ms after segment end {self.duration_ms} ms"
                )
                raise ValueError(msg)
        return self


class AlignmentFinding(SchemaModel):
    check: Literal[
        "monotonic", "overlap", "missing_words", "extra_words", "gap", "duration_mismatch", "empty"
    ]
    severity: Literal["blocker", "critical", "major", "minor", "advisory"]
    message: str
    word_index: int | None = None


class AlignmentReport(SchemaModel):
    beat_id: OpaqueId
    findings: tuple[AlignmentFinding, ...] = ()
    coverage: float = Field(ge=0, le=1, description="fraction of script words with timings")

    @property
    def passed(self) -> bool:
        return not any(f.severity in {"blocker", "critical"} for f in self.findings)


class CaptionCue(SchemaModel):
    index: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    lines: tuple[str, ...] = Field(min_length=1, max_length=2)


class CaptionTrack(VersionedModel):
    deliverable_id: OpaqueId
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")
    cues: tuple[CaptionCue, ...] = Field(min_length=1)
    max_chars_per_line: int = Field(default=32, ge=10, le=60)
    style: Literal["documentary", "burned_in_restrained"] = "documentary"

    @model_validator(mode="after")
    def _ordered(self) -> CaptionTrack:
        prev_end = -1
        for c in self.cues:
            if c.start_ms < prev_end or c.end_ms <= c.start_ms:
                msg = f"cue {c.index} is out of order or empty"
                raise ValueError(msg)
            prev_end = c.end_ms
        return self


class LoudnessReport(SchemaModel):
    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float | None = None
    target_lufs: float = -14.0
    target_true_peak_dbtp: float = -1.0
    tolerance_lu: float = 1.0

    @property
    def passed(self) -> bool:
        return (
            abs(self.integrated_lufs - self.target_lufs) <= self.tolerance_lu
            and self.true_peak_dbtp <= self.target_true_peak_dbtp + 0.1
        )


class AudioMixSpec(SchemaModel):
    """Narration stem laid out on the timeline; music optional with provenance (phase 3: none)."""

    deliverable_id: OpaqueId
    sample_rate_hz: Literal[44100, 48000] = 48000
    target_lufs: float = -14.0
    target_true_peak_dbtp: float = -1.0
    inter_beat_pause_ms: int = Field(default=350, ge=0, le=5000)
    lead_in_ms: int = Field(default=400, ge=0, le=5000)
    tail_ms: int = Field(default=600, ge=0, le=10000)
    music_asset_sha256: Sha256Hex | None = None
    music_gain_db: float = Field(default=-18.0, le=0)
