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
    provider: Literal["mock", "qwen3tts", "kokoro", "elevenlabs", "azure", "human"]
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
    estimated = "estimated"  # real audio, timings apportioned rather than measured


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


class SpeechTranscript(VersionedModel):
    """What a recording says, with per-word timings: the input side of the narration contracts.

    :class:`NarrationSegment` describes speech this factory *produced* from a script it already
    had. This describes speech that arrived without one — an interview, a lecture, a voice memo —
    so the words are the transcriber's reading of the audio rather than a script the audio was
    checked against. That difference is the whole reason it is a separate contract and not a
    segment with an empty script: nothing downstream may treat these words as authored.

    ``words`` is the load-bearing field. A story planned from a transcript cuts it into beats at
    word boundaries, so every beat knows exactly which milliseconds of the recording it owns, and
    the picture for that beat is on screen for exactly as long as those words are spoken. Without
    timings the transcript is only text, which is why ``timing_source`` says how they were got.
    """

    transcript_id: OpaqueId
    audio_sha256: Sha256Hex
    """The normalised WAV the words were read off, not the file the operator dropped."""
    sample_rate_hz: int = Field(ge=8000, le=192000)
    duration_ms: int = Field(ge=1)
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")
    engine: str = Field(min_length=1, max_length=120)
    """What read it: ``faster-whisper:base.en``, ``fixture:<file>``. Part of every input hash that
    covers this transcript, so swapping the transcriber re-runs what depends on it."""
    text: str = Field(min_length=1, max_length=200_000)
    words: tuple[WordTiming, ...] = ()
    timing_source: TimingSource = TimingSource.asr

    @model_validator(mode="after")
    def _words_inside_and_ordered(self) -> SpeechTranscript:
        prev_end = 0
        for w in self.words:
            if w.start_ms < prev_end:
                msg = f"word {w.word!r} starts at {w.start_ms} ms, before the previous word ended"
                raise ValueError(msg)
            if w.end_ms > self.duration_ms:
                msg = (
                    f"word {w.word!r} ends at {w.end_ms} ms, after the recording's"
                    f" {self.duration_ms} ms"
                )
                raise ValueError(msg)
            prev_end = w.start_ms
        return self

    def span_ms(self) -> tuple[int, int]:
        """First word to last word, or the whole recording when there are no timings."""
        if not self.words:
            return 0, self.duration_ms
        return self.words[0].start_ms, self.words[-1].end_ms


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


class MusicTrack(SchemaModel):
    """One track in the local music library (fixtures/music by default). Attribution is part of
    the record, not an afterthought: it travels into destination packages with the mix."""

    track_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    filename: str = Field(min_length=1, max_length=255)
    sha256: Sha256Hex
    duration_s: float = Field(gt=0)
    moods: tuple[str, ...] = Field(min_length=1, max_length=8)
    attribution: str = Field(min_length=1, max_length=500)


class SoundCueRole(StrEnum):
    """What a cue is doing, which decides how it is placed and how loud it sits."""

    bed = "bed"
    """A looping ambience under a span: a place, weather, a drone, room tone. Needs a duration."""
    transition = "transition"
    """A one-shot on a cut, so a hard cut between two cards is not silent."""
    accent = "accent"
    """A one-shot on a reveal: a figure counting up, a bullet appearing, a chart drawing."""
    room_tone = "room_tone"
    """A bed whose only job is to stop the silence between lines sounding like a dropout."""


class SoundCue(SchemaModel):
    """One sound from the curated library, at one place, for one stated reason.

    ``assets/sfx`` has held 49 curated, loudness-measured, provenance-tracked sounds since
    2026-09-07 and **nothing placed any of them**: the only non-speech audio a film could get was
    a generated MMAudio bed, so a chart drawing itself on screen was silent and a hard cut between
    two cards had nothing on it. This is the contract that says where a sound goes.

    ``reason`` is required and is not decoration. A cue sheet is the one part of the mix a person
    reads rather than hears, and "chart_reveal at 12.4 s because scn_chart000001 is a chart" is
    reviewable where a bare id and a timestamp are not.
    """

    cue_id: OpaqueId
    sound_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    """The library id, e.g. `chart_reveal`. Resolved against `assets/sfx/manifest.json`."""
    role: SoundCueRole
    at_ms: int = Field(ge=0)
    gain_db: float = Field(le=0)
    """Trim relative to the library's own level. The library is already normalised to a bed
    target, so this is a placement decision and not a repair of an unknown loudness."""
    duration_ms: int | None = Field(default=None, ge=1)
    """How long a bed runs. `None` for a one-shot, which is as long as the sound is."""
    fade_in_ms: int = Field(default=0, ge=0, le=10000)
    fade_out_ms: int = Field(default=0, ge=0, le=10000)
    scene_id: OpaqueId | None = None
    """The scene this cue is for, when it is for one. What makes a sheet auditable per scene."""
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _role_shape(self) -> SoundCue:
        beds = {SoundCueRole.bed, SoundCueRole.room_tone}
        if self.role in beds and self.duration_ms is None:
            msg = f"cue {self.cue_id}: a {self.role.value} runs for a span, so it needs a duration"
            raise ValueError(msg)
        if self.role not in beds and self.duration_ms is not None:
            msg = (
                f"cue {self.cue_id}: a {self.role.value} is a one-shot and is as long as the sound"
                " is; a duration here would either truncate it or pad it with silence"
            )
            raise ValueError(msg)
        return self


class CueSheet(VersionedModel):
    """Every library sound in one deliverable's mix, in time order.

    Tied to the library it was cut against by ``library_sha256``: a cue names a sound by id, and an
    id that resolves to different bytes than the sheet was reviewed with is a different mix. The
    renderer refuses a sheet whose library has moved rather than quietly using the new sound.
    """

    deliverable_id: OpaqueId
    library_sha256: Sha256Hex
    total_ms: int = Field(ge=1)
    cues: tuple[SoundCue, ...] = ()

    @model_validator(mode="after")
    def _ordered_and_inside(self) -> CueSheet:
        last = -1
        for cue in self.cues:
            if cue.at_ms < last:
                msg = f"cue {cue.cue_id} at {cue.at_ms} ms is out of time order"
                raise ValueError(msg)
            last = cue.at_ms
            if cue.at_ms >= self.total_ms:
                msg = f"cue {cue.cue_id} starts at {cue.at_ms} ms, past the {self.total_ms} ms mix"
                raise ValueError(msg)
        return self


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


class AudioProfile(StrEnum):
    """What kind of material this is, which decides what counts as a defect.

    The same numbers mean different things: a flat spectrum is hiss in a narration take and it is
    the entire point of a rain bed. Nothing here is cosmetic — the profile selects which checks run
    and at what limits, and (in :mod:`content_factory.audio.condition`) which processing is even
    allowed to touch the audio.
    """

    speech = "speech"
    sound_effect = "sound_effect"
    music = "music"


class AudioArtifactFinding(SchemaModel):
    check: Literal[
        "level",
        "clipping",
        "dc_offset",
        "noise_floor",
        "sibilance",
        "harsh_band",
        "band_limited",
        "spectral_flatness",
        "silence",
    ]
    severity: Literal["blocker", "critical", "major", "minor", "advisory"]
    message: str
    value: float


class AudioArtifactThresholds(SchemaModel):
    """Where "this asset has a problem" begins. One place, so the detector, the QC gate and the
    processing chains all agree on the same numbers.

    Use :meth:`for_profile` rather than the bare defaults: the defaults are the speech ones, and
    applying them to a sound effect would flag rain as hiss and a whoosh as harsh.
    """

    # Above this the noise between words is audible under a music bed.
    noise_floor_dbfs_max: float = Field(default=-55.0, le=0)
    clipped_sample_ratio_max: float = Field(default=0.0005, ge=0, le=1)
    dc_offset_max: float = Field(default=0.002, ge=0, le=1)
    # Sibilance band (5-9 kHz) energy over the speech band (0.3-5 kHz). Above this, esses spit.
    sibilance_ratio_max: float = Field(default=0.12, gt=0)
    # A take whose energy stops below this is band-limited: super-resolution or the generative
    # restorer can rebuild the missing top, plain EQ cannot.
    band_limit_hz_min: float = Field(default=14000.0, gt=0)
    # Voiced speech is tonal (flatness well under 0.3); a flat spectrum means hiss or buzz.
    spectral_flatness_max: float = Field(default=0.35, ge=0, le=1)
    # Quieter than this and mastering has to add so much gain that the noise floor comes with it.
    peak_dbfs_min: float = Field(default=-30.0, le=0)

    @classmethod
    def for_profile(cls, profile: AudioProfile) -> AudioArtifactThresholds:
        if profile is AudioProfile.speech:
            return cls()
        # Non-speech: the level and integrity checks still apply, the voice-shaped ones do not.
        # A designed sound may legitimately be broadband (rain, hiss, a paper rustle) and may
        # legitimately stop at 8 kHz (a sub drop), so those two stop being defects and the 5-9 kHz
        # measure becomes a harshness limit instead of a sibilance one, at a looser threshold
        # because a cymbal or a riser lives up there on purpose.
        return cls(
            noise_floor_dbfs_max=0.0,  # disabled: a bed IS its noise floor
            spectral_flatness_max=1.0,  # disabled: broadband is a design choice here
            band_limit_hz_min=1.0,  # disabled: a sub drop has no top end by design
            sibilance_ratio_max=0.55,
            # A one-shot is meant to peak; what matters is that it is neither clipped nor dead.
            peak_dbfs_min=-45.0,
            clipped_sample_ratio_max=0.0005,
        )


class AudioArtifactReport(VersionedModel):
    """What one audio asset measurably *is*, before anything is done to it.

    The "artifact / noise detection" step shared by every generated-audio chain in this repo. For a
    narration beat it decides whether the optional cleanup and band-extension passes are worth
    running, rather than denoising takes that were already clean. For a generated sound effect it
    decides whether anything needs repairing at all — and the measurements are what keep the
    speech models away from material they would destroy.
    """

    profile: AudioProfile = AudioProfile.speech
    # What this describes: a narration beat id, "sfx", a library entry id. Deliberately looser
    # than OpaqueId — the beat-id pattern would reject every non-beat asset.
    asset_id: str = Field(min_length=1, max_length=120)
    sample_rate_hz: int = Field(ge=8000, le=192000)
    duration_ms: int = Field(ge=1)
    peak_dbfs: float
    rms_dbfs: float
    noise_floor_dbfs: float
    clipped_sample_ratio: float = Field(ge=0, le=1)
    dc_offset: float
    sibilance_ratio: float = Field(ge=0)
    band_limit_hz: float = Field(ge=0)
    spectral_flatness: float = Field(ge=0, le=1)
    thresholds: AudioArtifactThresholds = AudioArtifactThresholds()
    findings: tuple[AudioArtifactFinding, ...] = ()

    def has(self, check: str) -> bool:
        return any(f.check == check for f in self.findings)

    @property
    def passed(self) -> bool:
        return not any(f.severity in {"blocker", "critical"} for f in self.findings)

    @property
    def needs_cleanup(self) -> bool:
        """Noise, hiss/buzz or clipping — the things a speech enhancer removes."""
        return any(self.has(c) for c in ("noise_floor", "spectral_flatness", "clipping"))

    @property
    def needs_repair(self) -> bool:
        """Damage worth fixing on any material: clipping, a DC offset, or a dead asset."""
        return any(self.has(c) for c in ("clipping", "dc_offset", "silence"))

    @property
    def needs_band_extension(self) -> bool:
        return self.has("band_limited")


class VoiceChainSpec(SchemaModel):
    """The deterministic tail of the voice chain: de-esser → EQ → light compression.

    One FFmpeg filter graph, no model, no randomness: the same input always produces the same
    bytes, which is what lets the restoration stage cache per beat.
    """

    # FFmpeg's `deesser` takes normalized controls, not Hz/dB, so this exposes its actual
    # parameters instead of inventing units that would have to be guessed back.
    de_ess: bool = True
    de_ess_intensity: float = Field(default=0.25, ge=0, le=1)
    de_ess_max_reduction: float = Field(default=0.5, ge=0, le=1)
    de_ess_frequency: float = Field(default=0.5, ge=0, le=1)
    # EQ: rumble and plosive energy out, presence in, air only where there is a top to lift.
    high_pass_hz: int = Field(default=85, ge=0, le=300)
    low_shelf_hz: int = Field(default=200, ge=50, le=500)
    low_shelf_db: float = Field(default=-1.5, ge=-12, le=12)
    presence_hz: int = Field(default=3000, ge=1000, le=6000)
    presence_db: float = Field(default=1.5, ge=-12, le=12)
    presence_q: float = Field(default=0.9, gt=0, le=10)
    air_hz: int = Field(default=10000, ge=4000, le=16000)
    air_db: float = Field(default=0.0, ge=-12, le=12)
    compressor: bool = True
    comp_threshold_db: float = Field(default=-18.0, ge=-60, le=0)
    comp_ratio: float = Field(default=2.5, ge=1, le=20)
    comp_attack_ms: float = Field(default=15.0, ge=0.01, le=2000)
    comp_release_ms: float = Field(default=220.0, ge=0.01, le=9000)
    comp_makeup_db: float = Field(default=0.0, ge=0, le=24)


class SpeechRestorationSpec(SchemaModel):
    """One beat's trip through the voice chain, in the order the audio travels it:

    detection → cleanup (ClearerVoice SE) → band extension (ClearerVoice SR) →
    restoration (Resemble Enhance) → de-esser → EQ → light compression.

    The neural steps default to ``off``: like every other model backend in this repo the mocks
    are the default and ``.env`` turns the real thing on. The FFmpeg tail needs nothing but
    FFmpeg, so it runs everywhere.
    """

    cleanup: Literal["off", "clearervoice"] = "off"
    band_extension: Literal["off", "clearervoice_sr"] = "off"
    enhancer: Literal["off", "resemble_enhance"] = "off"
    enhancer_mode: Literal["enhance", "denoise"] = "enhance"
    enhancer_nfe: int = Field(default=32, ge=1, le=128)
    enhancer_solver: Literal["midpoint", "rk4", "euler"] = "midpoint"
    enhancer_lambd: float = Field(default=0.5, ge=0, le=1)
    enhancer_tau: float = Field(default=0.5, ge=0, le=1)
    # Cleanup and band extension are gated on the detection report by default: a clean take is
    # left alone. ``always`` forces them, which is what an evaluation run wants.
    gate: Literal["detected", "always"] = "detected"
    device: Literal["cpu", "cuda"] = "cpu"
    sample_rate_hz: Literal[44100, 48000] = 48000
    # A restorer that changed the length would silently break every word timing downstream.
    max_duration_drift_ms: int = Field(default=10, ge=0, le=200)
    thresholds: AudioArtifactThresholds = AudioArtifactThresholds()
    chain: VoiceChainSpec = VoiceChainSpec()


class SpeechRestorationReport(VersionedModel):
    """What actually ran on one beat, and what it did to the measurements."""

    beat_id: OpaqueId
    steps: tuple[str, ...] = ()  # in execution order
    skipped: tuple[str, ...] = ()  # "step: why"
    input_sha256: Sha256Hex
    output_sha256: Sha256Hex
    input_sample_rate_hz: int = Field(ge=8000, le=192000)
    output_sample_rate_hz: int = Field(ge=8000, le=192000)
    input_duration_ms: int = Field(ge=1)
    output_duration_ms: int = Field(ge=1)
    before: AudioArtifactReport
    after: AudioArtifactReport


class MasterChainSpec(SchemaModel):
    """The programme master: true-peak limiter → two-pass EBU R128 normalization → 48 kHz WAV."""

    limiter: bool = True
    # The limiter sits BELOW the delivery ceiling on purpose: loudnorm's final gain move happens
    # after it, and a limiter parked exactly at the ceiling leaves that move nowhere to go.
    limiter_ceiling_dbtp: float = Field(default=-1.5, ge=-12, le=0)
    limiter_attack_ms: float = Field(default=5.0, ge=0.1, le=80)
    limiter_release_ms: float = Field(default=50.0, ge=1, le=8000)
    target_lufs: float = Field(default=-14.0, ge=-40, le=0)
    target_true_peak_dbtp: float = Field(default=-1.0, ge=-12, le=0)
    loudness_range_lu: float = Field(default=11.0, gt=0, le=30)
    sample_rate_hz: Literal[44100, 48000] = 48000


class SoundConditionSpec(SchemaModel):
    """How a *generated non-speech* asset is conditioned before it reaches the mix.

    Same shape as the speech chain — measure, repair only what is broken, normalise, cap the true
    peak, land at the delivery rate, same length out as in — and deliberately none of its models.
    Measured on this host 2026-09-07, on sounds from ``assets/sfx``:

    * ClearerVoice ``MossFormer2_SE_48K`` (speech enhancement) left **0.5-1.0 %** of the energy of
      a whoosh, a rain bed and an impact. To a speech enhancer a sound effect *is* the noise.
    * Resemble Enhance took a 2 s whoosh from -24.0 to -73.4 dBFS with a waveform correlation of
      **0.002** against its input — it did not restore the sound, it replaced it with unrelated
      sub-200 Hz rumble.

    So there is no field here for either of them, and there will not be one. What non-speech
    material actually needs is damage repair and a predictable level, which is what this does.
    """

    profile: AudioProfile = AudioProfile.sound_effect
    # Repairs, each applied only when the measurements ask for it.
    remove_dc: bool = True
    declip: bool = True  # ffmpeg adeclip, only when clipped samples are measured
    declick: bool = True  # ffmpeg adeclick, for the joins between generated windows
    # Broadband denoise is OFF by default and should stay that way for most material: on a bed the
    # "noise" is the content. It exists for a generated one-shot with an audible noise bed under it.
    denoise: bool = False
    denoise_nr_db: float = Field(default=6.0, ge=0.01, le=97.0)
    # Sub-sonic and ultrasonic trim: nothing below/above these carries a usable effect, and both
    # ends eat headroom the limiter would otherwise give to the sound itself. 0 disables.
    high_pass_hz: int = Field(default=25, ge=0, le=200)
    low_pass_hz: int = Field(default=0, ge=0, le=24000)
    # Level. `integrated` suits a bed that runs under a whole scene; `max_momentary` suits a
    # one-shot, whose integrated loudness is meaningless because it is mostly silence. The
    # defaults match assets/sfx/library.json so the runtime path and the curated library agree.
    loudness_metric: Literal["integrated", "max_momentary", "off"] = "integrated"
    target_lufs: float = Field(default=-23.0, ge=-40, le=0)
    target_true_peak_dbtp: float = Field(default=-1.0, ge=-12, le=0)
    # A bed must not be lifted indefinitely: past this the gain is bringing up a noise floor, not
    # a sound, and the asset should be regenerated instead.
    max_gain_db: float = Field(default=24.0, ge=0, le=60)
    sample_rate_hz: Literal[44100, 48000] = 48000
    # A generated bed is scored against the picture frame by frame, so its length is as load-
    # bearing here as word timings are in speech.
    max_duration_drift_ms: int = Field(default=10, ge=0, le=200)
    thresholds: AudioArtifactThresholds | None = None  # None -> for_profile(profile)


class SoundConditionReport(VersionedModel):
    """What actually ran on one generated asset, and what it did to the measurements."""

    asset_id: str = Field(min_length=1, max_length=120)
    profile: AudioProfile
    steps: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    input_sha256: Sha256Hex
    output_sha256: Sha256Hex
    input_duration_ms: int = Field(ge=1)
    output_duration_ms: int = Field(ge=1)
    output_sample_rate_hz: int = Field(ge=8000, le=192000)
    gain_applied_db: float = 0.0
    gain_limited: bool = False
    measured_lufs: float | None = None
    measured_true_peak_dbtp: float | None = None
    before: AudioArtifactReport
    after: AudioArtifactReport
