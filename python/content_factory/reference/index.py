"""The sqlite side of the reference library: one file, built once, queried in words."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from content_factory.schemas.reference import ReferenceClip, ReferenceLibrary, ReferenceSource

SCHEMA = """
CREATE TABLE clip (
    id                INTEGER PRIMARY KEY,
    clip_id           TEXT    NOT NULL UNIQUE,
    source            TEXT    NOT NULL,
    source_ref        TEXT    NOT NULL,
    modality          TEXT    NOT NULL,
    usage             TEXT    NOT NULL,
    affection         TEXT    NOT NULL,
    people_count      INTEGER NOT NULL,
    interaction_tags  TEXT    NOT NULL,
    contact_tags      TEXT    NOT NULL,
    postures          TEXT    NOT NULL,
    setting           TEXT    NOT NULL,
    camera_angles     TEXT    NOT NULL,
    view_count        INTEGER NOT NULL,
    frame_count       INTEGER NOT NULL,
    native_fps        REAL    NOT NULL,
    duration_s        REAL    NOT NULL,
    width             INTEGER,
    height            INTEGER,
    pose_format       TEXT    NOT NULL,
    pose_root         TEXT,
    -- 1 when a rig can actually be aimed by this clip. A bounding box says where a person was,
    -- not how they were standing, so bbox_only counts as no pose here even though it is geometry.
    has_pose          INTEGER NOT NULL,
    retargeted_clip   TEXT,
    key_pose_count    INTEGER NOT NULL,
    closest_contact_m REAL,
    caption           TEXT    NOT NULL,
    caption_source    TEXT    NOT NULL,
    ingested_at       TEXT    NOT NULL,
    ingester_version  TEXT    NOT NULL,
    doc               TEXT    NOT NULL,
    doc_sha256        TEXT    NOT NULL
);
CREATE INDEX clip_source ON clip (source);
CREATE INDEX clip_affection ON clip (affection);
CREATE INDEX clip_usage ON clip (usage);
CREATE INDEX clip_people ON clip (people_count);
CREATE INDEX clip_duration ON clip (duration_s);
CREATE INDEX clip_has_pose ON clip (has_pose);
CREATE VIRTUAL TABLE clip_fts USING fts5(
    clip_id UNINDEXED,
    interaction,
    contact,
    posture,
    setting,
    caption,
    -- remove_diacritics 2 must match the query side: lexicon.normalize() folds NFKD and drops
    -- combining marks, so without it an accented caption word ("café") can never match the
    -- folded query word ("cafe"). prefix = '2 3' gives the index its own prefix terms.
    tokenize = "unicode61 remove_diacritics 2 tokenchars '_'",
    prefix = '2 3'
);
CREATE TABLE library (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    doc TEXT NOT NULL
);
"""
"""The whole DDL, as one constant, so a test can assert the tokenizer options it claims."""


def schema_matches(path: str | os.PathLike[str]) -> bool:
    """True when the index at ``path`` was built with the DDL this module currently declares."""
    target = Path(path)
    if not target.exists():
        return False
    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND sql IS NOT NULL"
            ).fetchall()
    except sqlite3.Error:
        return False
    live = " ".join(" ".join((row[0] or "").split()) for row in rows)
    wanted = (
        "tokenize = \"unicode61 remove_diacritics 2 tokenchars '_'\"",
        "prefix = '2 3'",
    )
    return all(" ".join(fragment.split()) in live for fragment in wanted)


FTS_COLUMNS: tuple[str, ...] = ("interaction", "contact", "posture", "setting", "caption")
"""The searchable columns, in the order ``clip_fts`` declares them, for bm25 weighting."""


class IndexUnavailableError(RuntimeError):
    """Raised when the host's sqlite cannot serve this index, i.e. it was built without FTS5."""


def fts5_available() -> bool:
    """True when this interpreter's sqlite3 can create an FTS5 table."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return True


def create(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """A fresh index at ``path`` with the schema applied, returned open for writing."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    conn = sqlite3.connect(target)
    # page_size only takes effect on an empty database, so it goes before the DDL.
    conn.execute("PRAGMA page_size=4096")
    conn.execute("PRAGMA journal_mode=delete")
    conn.execute("PRAGMA auto_vacuum=none")
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError as exc:
        conn.close()
        target.unlink(missing_ok=True)
        msg = (
            "cannot create the reference index schema, sqlite is probably built without"
            f" FTS5: {exc}"
        )
        raise IndexUnavailableError(msg) from exc
    conn.commit()
    return conn


def closest_contact_m(measured: dict[str, float | list[float]]) -> float | None:
    """The one number worth a column: how close the two people actually got, in metres."""
    wrists = measured.get("closest_wrists_m")
    if isinstance(wrists, int | float) and not isinstance(wrists, bool):
        return float(wrists)
    gap = measured.get("root_gap_m")
    if isinstance(gap, list) and gap:
        first = gap[0]
        if isinstance(first, int | float) and not isinstance(first, bool):
            return float(first)
    return None


def insert_clip(conn: sqlite3.Connection, clip: ReferenceClip, order: int) -> None:
    """Write one clip as row ``order`` of ``clip`` and the matching row of ``clip_fts``."""
    if order < 0:
        msg = f"order must be non-negative, got {order}"
        raise ValueError(msg)
    doc = clip.canonical_json()
    text = clip.search_text()
    conn.execute(
        """
        INSERT INTO clip (
            id, clip_id, source, source_ref, modality, usage, affection, people_count,
            interaction_tags, contact_tags, postures, setting, camera_angles, view_count,
            frame_count, native_fps, duration_s, width, height, pose_format, pose_root,
            has_pose, retargeted_clip, key_pose_count, closest_contact_m, caption,
            caption_source, ingested_at, ingester_version, doc, doc_sha256
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?
        )
        """,
        (
            order,
            clip.clip_id,
            clip.source.value,
            clip.source_ref,
            clip.modality.value,
            clip.usage.value,
            clip.affection.value,
            clip.people_count,
            text["interaction"],
            text["contact"],
            text["posture"],
            clip.setting,
            " ".join(clip.camera_angles),
            clip.view_count,
            clip.frame_count,
            clip.native_fps,
            clip.duration_s,
            clip.width,
            clip.height,
            clip.pose_format,
            clip.pose_root,
            int(clip.pose_format not in ("none", "bbox_only")),
            clip.retargeted_clip,
            len(clip.key_poses),
            closest_contact_m(clip.measured),
            clip.caption,
            clip.caption_source,
            clip.ingested_at,
            clip.ingester_version,
            doc,
            hashlib.sha256(doc.encode("utf-8")).hexdigest(),
        ),
    )
    conn.execute(
        """
        INSERT INTO clip_fts (rowid, clip_id, interaction, contact, posture, setting, caption)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order,
            clip.clip_id,
            text["interaction"],
            text["contact"],
            text["posture"],
            text["setting"],
            text["caption"],
        ),
    )


def manifest_sha256(clips: Sequence[ReferenceClip]) -> str:
    """sha256 over the clips' canonical JSON: the documents are reproducible."""
    digest = hashlib.sha256()
    for clip in clips:
        digest.update(clip.canonical_json().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _library_id_from(target: Path) -> str:
    """A contract-legal library id from a file name: 3 to 64 characters, deterministically."""
    stem = target.stem
    if len(stem) < 3:
        stem = f"ref_{stem}"
    return stem[:64]


def build(
    clips: Iterable[ReferenceClip],
    path: str | os.PathLike[str],
    *,
    lexicon_sha256: str,
    built_at: str,
    builder_version: str,
    sources: Sequence[ReferenceSource] = (),
    skipped: Sequence[str] = (),
    library_id: str | None = None,
    root: str | None = None,
) -> ReferenceLibrary:
    """Build the whole index at ``path`` and return its manifest."""
    ordered = sorted(clips, key=lambda clip: clip.clip_id)
    seen: set[str] = set()
    for clip in ordered:
        if clip.clip_id in seen:
            msg = f"clip_id {clip.clip_id} appears twice in the build input"
            raise ValueError(msg)
        seen.add(clip.clip_id)

    target = Path(path)
    staging = target.with_name(target.name + ".building")
    library = ReferenceLibrary(
        library_id=library_id or _library_id_from(target),
        root=root or str(target.parent),
        index_path=str(target),
        builder_version=builder_version,
        built_at=built_at,
        lexicon_sha256=lexicon_sha256,
        sources=tuple(sources),
        clip_count=len(ordered),
        manifest_sha256=manifest_sha256(ordered),
        skipped=tuple(skipped),
    )

    conn = create(staging)
    try:
        for order, clip in enumerate(ordered):
            insert_clip(conn, clip, order)
        conn.execute("INSERT INTO library (id, doc) VALUES (1, ?)", (library.canonical_json(),))
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    os.replace(staging, target)
    return library


def open_index(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open an existing index read-only, so a query cannot alter what it is reading."""
    target = Path(path)
    if not target.is_file():
        msg = f"no reference index at {target}"
        raise FileNotFoundError(msg)
    conn = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_library(conn: sqlite3.Connection) -> ReferenceLibrary:
    """The manifest stored in the index, validated back into the contract."""
    row = conn.execute("SELECT doc FROM library WHERE id = 1").fetchone()
    if row is None:
        msg = "this index has no library row, it was not written by build()"
        raise IndexUnavailableError(msg)
    return ReferenceLibrary.model_validate_json(row[0])
