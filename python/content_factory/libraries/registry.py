"""Every downloaded data library on this host, declared: what it is, what reads it, where it
is indexed.

The shape deliberately mirrors :mod:`content_factory.models.weights`. A ``DataLibrary`` names a
directory under a host store, the ``datasets/<category>/<Name>`` symlink that makes it
discoverable from the repo, a ``probe`` that decides presence (so a half-finished transfer reads
as absent rather than installed), and — the field weights do not need — ``read_by``.

``read_by`` is the point of this file. It names the code that consumes the data, as import paths
or settings keys, and an empty tuple is a claim: *nothing in this repo reads this*. That is not a
lint failure, it is a fact worth being able to state, and :func:`unreached` states it. Three of
the four libraries on this host were in that condition and it took reading 236 runs' facts to
discover it.

Sizes are what ``du -sh`` reported on 2026-09-12 and are documentation, not a contract — nothing
compares them.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from content_factory.schemas.base import SchemaModel

DEFAULT_STORE = "/mnt/fast"
"""The fast disk on this host. Every library's ``store_dir`` is relative to it, the same way a
weight family's is relative to the weight store, so a machine that keeps its data elsewhere
overrides one path instead of forty."""


class DataLibrary(SchemaModel):
    """One data library directory, declared."""

    key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    """Stable id. Used by the CLI and by ``read_by`` cross-references; never displayed."""
    name: str = Field(min_length=1, max_length=80)
    """The link name under ``datasets/<category>/``. Upstream's own spelling, like a weight's."""
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """The ``datasets/<category>/`` folder. Chosen by what the data IS, not by who reads it, so a
    second consumer does not move a directory."""
    store_dir: str = Field(min_length=1, max_length=300)
    """Path under :data:`DEFAULT_STORE`, exactly as the download laid it down."""
    summary: str = Field(min_length=1, max_length=300)
    """One line: what a reader gets from it."""
    probe: str = Field(min_length=1, max_length=300)
    """A path under ``store_dir`` that exists if and only if the library is really there. A
    directory is not enough — an interrupted 19 GB fetch leaves one."""
    approx_size: str = Field(min_length=1, max_length=16)
    read_by: tuple[str, ...] = ()
    """Import paths or settings keys that consume this. **Empty means nothing reads it**, and that
    is a supported, deliberate value — see :func:`unreached`."""
    note: str = Field(default="", max_length=600)
    """What a reader needs to know before building on it: what it cannot give, or why it is
    unread. Kept short; the long version belongs in ``docs/datasets.md``."""

    def path(self, store: str | Path = DEFAULT_STORE) -> Path:
        return Path(store) / self.store_dir

    def present(self, store: str | Path = DEFAULT_STORE) -> bool:
        return (self.path(store) / self.probe).exists()

    def link(self, repo_root: Path) -> Path:
        return repo_root / "datasets" / self.category / self.name


LIBRARIES: tuple[DataLibrary, ...] = (
    # ---- human interaction -------------------------------------------------------------------
    # Seven sources behind one SQLite index. The index is what the pipeline talks to, so the
    # sources are declared individually (a missing one is a real gap) and the index separately.
    DataLibrary(
        key="cmu-mocap",
        name="CMU-Mocap",
        category="human_interaction",
        store_dir="reference/CMU-Mocap",
        summary="2514 mocap trials, 2311 with a text description, 110 two-person with A/B pairs.",
        probe=".",
        approx_size="6.7 GB",
        read_by=("content_factory.reference.ingest.cmu",),
        note="No hugging, cuddling, head-on-shoulder or parent-child. Its AVIs are stick figures"
        " on black, not footage.",
    ),
    DataLibrary(
        key="sbu-kinect",
        name="SBU-Kinect",
        category="human_interaction",
        store_dir="reference/SBU-Kinect",
        summary="282 sequences, 8 labelled actions, 15-joint skeletons for both people, RGB+depth.",
        probe=".",
        approx_size="6.1 GB",
        read_by=("content_factory.reference.ingest.sbu",),
        note="Sequences average 24 frames, so these are gestures rather than takes.",
    ),
    DataLibrary(
        key="harmony4d",
        name="Harmony4D",
        category="human_interaction",
        store_dir="reference/Harmony4D",
        summary="Two hug takes from 22 calibrated cameras, per-frame SMPL for both people.",
        probe=".",
        approx_size="4.2 GB",
        read_by=("content_factory.reference.ingest.harmony4d",),
        note="A geometry source, not a look source: tripods and lab clutter stand between the lens"
        " and the subjects in most views. Use the SMPL fits, re-render the contact yourself.",
    ),
    DataLibrary(
        key="motionhub",
        name="MotionHub",
        category="human_interaction",
        store_dir="reference/MotionHub",
        summary="SMPL-H motions with hierarchical text captions (HumanML3D, GRAB, EgoBody).",
        probe=".",
        approx_size="1.9 GB",
        read_by=("content_factory.reference.build",),
        note="Contains no two-person interaction: the interx/chi3d/hi4d paths it was fetched for"
        " do not exist upstream. 10463 of the index's clips come from here and none are contact.",
    ),
    DataLibrary(
        key="ut-interaction",
        name="UT-Interaction",
        category="human_interaction",
        store_dir="reference/UT-Interaction",
        summary="20 continuous sequences and 120 segmented clips, 6 classes with frame ranges.",
        probe=".",
        approx_size="584 MB",
        read_by=("content_factory.reference.build",),
        note="Only 2 of 6 classes are not aggression or pointing.",
    ),
    DataLibrary(
        key="tv-human-interactions",
        name="TV-Human-Interactions",
        category="human_interaction",
        store_dir="reference/TV-Human-Interactions",
        summary="300 broadcast clips: 6978 hug frames, 5401 kiss frames, per-person head pose.",
        probe=".",
        approx_size="166 MB",
        read_by=("content_factory.reference.ingest.tvhi",),
        note="624x352. Usable as reference for staging, not as footage to cut.",
    ),
    DataLibrary(
        key="reference-stock",
        name="Stock",
        category="human_interaction",
        store_dir="reference/Stock",
        summary="Hand-picked Pexels footage, fetched per shot.",
        probe=".",
        approx_size="17 MB",
        read_by=("content_factory.reference.build",),
        note="Small on purpose: Pexels forbids bulk copying.",
    ),
    DataLibrary(
        key="reference-index",
        name="_index",
        category="human_interaction",
        store_dir="reference/_index",
        summary="The queryable index over all seven sources: 15789 clips, FTS5 + BM25, plus the"
        " per-source class maps and the contact sheets that verify them.",
        probe="index.sqlite",
        approx_size="121 MB",
        read_by=(
            "content_factory.reference.query.search",
            "content_factory.workflows.stages.stage_find_reference",
            "settings: reference.index_path",
        ),
        note="This is the only part of the library the pipeline actually talks to.",
    ),
    # ---- staging -----------------------------------------------------------------------------
    DataLibrary(
        key="blender-assets",
        name="Blender-Assets",
        category="staging",
        store_dir="models/blender-assets",
        summary="4 MakeHuman characters, 58 retargeted two-person CMU takes, poses, and the"
        " MakeHuman CC0 asset pack (hair, skins, clothes).",
        probe="clips",
        approx_size="614 MB",
        read_by=(
            "content_factory.controls.blender",
            "content_factory.shots.planner",
            "content_factory.models.video_stack",
        ),
        note="Also indexed as a weight family at models/characters/Blender-Assets, because the"
        " installer put it there; this is the same directory seen as the data it is.",
    ),
    # ---- audio -------------------------------------------------------------------------------
    DataLibrary(
        key="sound-99sounds",
        name="99Sounds",
        category="audio",
        store_dir="sound-libraries/99Sounds",
        summary="11 packs, 669 files: electromagnetic fields, city, underground, water, rain,"
        " underwater, garage foley, cinematic textures.",
        probe=".",
        approx_size="9.4 GB",
        read_by=("skills/audio/music/build_library.py -> assets/sfx",),
        note="Read once, at library build time, not per run. 346 of the 670 curated sounds in"
        " assets/sfx came from here.",
    ),
    DataLibrary(
        key="sound-mixkit",
        name="mixkit",
        category="audio",
        store_dir="sound-libraries/mixkit",
        summary="311 files in 15 categories: place, weather, animal, vehicle, foley, ui, crowd.",
        probe=".",
        approx_size="700 MB",
        read_by=("skills/audio/music/build_library.py -> assets/sfx",),
        note="294 of the 670 curated sounds in assets/sfx came from here.",
    ),
    DataLibrary(
        key="sound-local-renders",
        name="local-renders",
        category="audio",
        store_dir="sound-libraries/local-renders",
        summary="20 locally generated sounds kept beside the licensed ones.",
        probe=".",
        approx_size="15 MB",
        read_by=("skills/audio/music/build_library.py -> assets/sfx",),
    ),
    # ---- image models (declared here as data because they are prompted, not just loaded) ------
    DataLibrary(
        key="ideogram-4",
        name="Ideogram-4",
        category="image_models",
        store_dir="models/ideogram-4-comfy",
        summary="Ideogram 4 (9.3 B, open weights, June 2026): two DiTs for the dual-model guider,"
        " the Qwen3-VL-8B encoder, and FLUX.2's VAE. Structured JSON prompting with native"
        " bounding-box layout and colour-palette control.",
        probe="diffusion_models/ideogram4_fp8_scaled.safetensors",
        approx_size="28 GB",
        read_by=("ComfyUI blueprint: Text to Image (Ideogram v4)",),
        note="MUST be prompted with a structured JSON caption, not prose. Prose goes through a"
        " chat template to an encoder that also *generates*, and its refusals get lettered onto"
        " the canvas: 16 of 17 prose generations refused, against 3 of 3 clean for the same"
        " subject as JSON. See docs/research/2026-09-12-ideogram-4.md.",
    ),
    # ---- training ----------------------------------------------------------------------------
    DataLibrary(
        key="romsketch",
        name="romsketch",
        category="training",
        store_dir="datasets/romsketch",
        summary="82 image+caption pairs of two-person affection (hugging, kissing, holding hands)"
        " drawn as pen-and-ink and watercolour sketches, and the HiDream-O1 style LoRA trained"
        " from them.",
        probe="dataset.toml",
        approx_size="6.0 GB",
        read_by=(
            "settings: local_services.hidream_lora",
            "skills/image/hidream/lora.py",
        ),
        note="A STYLE adapter, not a realism one — rendered 2026-09-12, it draws sketch on paper"
        " with a visible drawn border, which is what 'romsketch' names. Trigger token:"
        " 'romsketch style.' at the head of the prompt. The adapter is"
        " output/romsketch_ho1_v1b.safetensors; it had nowhere to plug in until the HiDream skill"
        " learned to load one.",
    ),
)


def by_key(key: str) -> DataLibrary:
    for library in LIBRARIES:
        if library.key == key:
            return library
    known = ", ".join(sorted(lib.key for lib in LIBRARIES))
    msg = f"no data library {key!r}; known: {known}"
    raise KeyError(msg)


def by_category() -> dict[str, tuple[DataLibrary, ...]]:
    """Every library grouped by its ``datasets/<category>/`` folder, categories in name order."""
    groups: dict[str, list[DataLibrary]] = {}
    for library in LIBRARIES:
        groups.setdefault(library.category, []).append(library)
    return {k: tuple(groups[k]) for k in sorted(groups)}


def unreached() -> tuple[DataLibrary, ...]:
    """Libraries nothing in this repo reads.

    Not an error and not a warning — a question the registry can now answer without grepping a
    run's facts. A library that is deliberately raw input to a build step names that step in
    ``read_by`` (the sound libraries do), so what is left here is genuinely unconsumed.
    """
    return tuple(lib for lib in LIBRARIES if not lib.read_by)
