"""One command that turns the reference tree into a queryable index.

    uv run python -m content_factory.reference.build [--root DIR] [--check] [--only cmu,sbu]

The driver does four things in a fixed order: find the ingesters, run each one, write every clip it
returns as its own canonical JSON document under ``_index/clips/``, then build the sqlite index
from the documents that are now on disk.

Four decisions here are worth reading.

**The index is built from the documents, not from the ingesters' return values.** The clips are
written out, read back, and validated again before they reach ``index.build``. That costs one pass
over a few thousand small files and buys the property the library is meant to have: the index is a
function of the tree, so anyone with the tree can rebuild the same index, and a hand-edited
document is either valid and indexed or invalid and loud.

**Nothing reads the clock.** ``ingested_at`` and ``built_at`` are arguments with a fixed default, so
two runs a week apart produce the same documents and the same ``manifest_sha256``. The only elapsed
times anywhere are in the printed report, which is not an artifact.

**The ingesters are discovered, not listed.** ``content_factory.reference.ingest`` is scanned for
modules defining both ``ingest`` and ``SOURCE`` (and the optional ``SOURCES`` for a module covering
several subsets of one dataset), so the driver works with however many of the sources have been
written, and says which it found. A source's documents belong to its ingester: a
run that includes a source replaces that source's documents wholesale and deletes the ones it no
longer produces, and leaves every other source alone. That is what makes ``--only`` safe.

**A rerun is a no-op.** The manifest of the documents on disk is compared with the manifest stored
inside the existing index, and a matching index is left untouched, mtime and all. ``--check`` builds
into a temporary directory instead and exits 1 when the digest moved, which is the reproducibility
gate.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from content_factory.reference import index as index_module
from content_factory.schemas.base import canonical_dumps, sha256_hex
from content_factory.schemas.reference import ReferenceClip, ReferenceLibrary, ReferenceSource

BUILDER_VERSION = "1.0.0"
"""Bump the minor when the document layout changes, the major when the index schema does."""

LIBRARY_ID = "reference"

DEFAULT_ROOT = Path("/mnt/fast/reference")

DEFAULT_AT = "2026-09-07T00:00:00Z"
"""The stamp every clip and every manifest carries unless the caller passes another one. A fixed
default rather than the clock, because ``--check`` compares digests of documents that contain it."""

INGEST_PACKAGE = "content_factory.reference.ingest"

SOURCE_ORDER: tuple[str, ...] = (
    "cmu",
    "harmony4d",
    "sbu",
    "ut",
    "tvhi",
    "motionhub",
    "stock",
)
"""Fixed run order, cheapest and most trusted source first. A module not named here still runs,
after these, in alphabetical order, so a new ingester needs no edit to be picked up."""

LEXICON_RELPATH = "fixtures/reference/lexicon.v1.json"

REPO_ROOT = Path(__file__).resolve().parents[3]

IngestFn = Callable[..., tuple[list[ReferenceClip], list[str]]]


class BuildError(RuntimeError):
    """A build that cannot be trusted: a bad ingester, or two ingesters claiming one clip_id."""


@dataclass(frozen=True)
class Ingester:
    """One discovered source module, already checked for the shape the driver calls."""

    name: str
    source: ReferenceSource
    """The module's primary source, used for ordering and for the printed line."""
    ingest: IngestFn
    sources: tuple[ReferenceSource, ...] = ()
    """Every source this module is allowed to emit and owns the documents of. One module usually
    means one source, but a module whose subsets share a layout (MotionHub covers egobody, GRAB
    and HumanML3D) declares the full list in ``SOURCES``; ``(source,)`` when it does not."""

    def owns(self, source: ReferenceSource) -> bool:
        return source in (self.sources or (self.source,))


@dataclass(frozen=True)
class SourceReport:
    """What one ingester did, in the three numbers worth printing."""

    name: str
    source: str
    """The module's primary source. ``sources`` is the full list it actually emitted."""
    clips: int
    skips: int
    seconds: float
    sources: tuple[str, ...] = ()
    """Every source value that appeared in this module's clips, in ``SOURCES`` order. One entry
    for a normal ingester; three for MotionHub, whose subsets share one layout."""
    unstamped: int = 0
    """Clips whose ``ingested_at`` is not the stamp the driver passed in. Zero, or a bug in that
    ingester: a clip carrying its own timestamp cannot be reproduced."""


@dataclass(frozen=True)
class BuildReport:
    """The result of one build, enough to print, to compare, and to assert on in a test."""

    root: Path
    index_dir: Path
    index_path: Path
    manifest_sha256: str
    clip_count: int
    sources: tuple[SourceReport, ...] = ()
    documents_written: int = 0
    documents_unchanged: int = 0
    documents_pruned: int = 0
    rebuilt: bool = True
    library: ReferenceLibrary | None = None
    skipped: tuple[str, ...] = ()
    nonconforming: tuple[str, ...] = field(default=())
    """Modules in the ingest package that define neither ``ingest`` nor ``SOURCE``, named so a
    half-written ingester is visible instead of silently absent."""
    seconds: float = 0.0

    def totals(self) -> dict[str, object]:
        """The one JSON object the command ends with. Keys are stable; callers parse this."""
        return {
            "root": str(self.root),
            "index_path": str(self.index_path),
            "ingesters": len(self.sources),
            "sources": list(
                dict.fromkeys(v for s in self.sources for v in (s.sources or (s.source,)))
            ),
            "clips": self.clip_count,
            "skipped": len(self.skipped),
            "documents_written": self.documents_written,
            "documents_unchanged": self.documents_unchanged,
            "documents_pruned": self.documents_pruned,
            "unstamped": sum(s.unstamped for s in self.sources),
            "nonconforming": list(self.nonconforming),
            "rebuilt": self.rebuilt,
            "manifest_sha256": self.manifest_sha256,
            "builder_version": BUILDER_VERSION,
            "seconds": round(self.seconds, 3),
        }


def lexicon_digest(path: Path | None = None) -> str:
    """The digest of the synonym lexicon, or the digest of nothing when it is not on disk yet.

    Guarantees a valid Sha256Hex either way. The index does not depend on the lexicon; only the
    query side does, and it carries this digest so a match set built against one lexicon can be
    told apart from a match set built against another. A missing file is therefore not fatal, and
    hashes as empty rather than as an error.
    """
    target = path if path is not None else REPO_ROOT / LEXICON_RELPATH
    if not target.is_file():
        return sha256_hex(b"")
    return sha256_hex(target.read_bytes())


def discover_ingesters(only: Sequence[str] = ()) -> tuple[tuple[Ingester, ...], tuple[str, ...]]:
    """Every module in the ingest package that defines both ``ingest`` and ``SOURCE``.

    Returns the conforming ingesters in ``SOURCE_ORDER``, then any others alphabetically, plus the
    names of modules that were found and did not conform. Guarantees: no import side effect beyond
    importing the modules themselves; a missing or empty ingest package returns two empty tuples
    rather than raising, because a tree with no ingesters yet is a legal, empty library; and
    ``only`` matches either a module name (``cmu``) or a source value (``cmu_mocap``), so both the
    file and the contract name work.

    Raises BuildError when a name in ``only`` matches nothing, since silently building nothing is
    worse than failing.
    """
    try:
        package = importlib.import_module(INGEST_PACKAGE)
    except ModuleNotFoundError:
        if only:
            msg = f"--only {','.join(only)} but {INGEST_PACKAGE} does not exist"
            raise BuildError(msg) from None
        return (), ()

    found: list[Ingester] = []
    nonconforming: list[str] = []
    for info in sorted(
        pkgutil.iter_modules(list(getattr(package, "__path__", ()))), key=_module_key
    ):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{INGEST_PACKAGE}.{info.name}")
        ingester = _as_ingester(info.name, module)
        if ingester is None:
            nonconforming.append(info.name)
        else:
            found.append(ingester)

    found.sort(key=lambda ing: (_order_key(ing.name), ing.name))
    if not only:
        return tuple(found), tuple(nonconforming)

    wanted = list(dict.fromkeys(only))
    selected = [ing for ing in found if _names_of(ing) & set(wanted)]
    matched = set().union(*(_names_of(ing) for ing in selected)) if selected else set()
    missing = [name for name in wanted if name not in matched]
    if missing:
        available = (
            ", ".join(f"{ing.name} ({'/'.join(s.value for s in ing.sources)})" for ing in found)
            or "none"
        )
        msg = f"--only names {', '.join(missing)}, which is not an ingester. Found: {available}"
        raise BuildError(msg)
    return tuple(selected), tuple(nonconforming)


def _names_of(ingester: Ingester) -> set[str]:
    """Every name ``--only`` accepts for one ingester: its module name and its source values."""
    return {ingester.name, *(source.value for source in ingester.sources)}


def _module_key(info: pkgutil.ModuleInfo) -> str:
    return info.name


def _order_key(name: str) -> int:
    return SOURCE_ORDER.index(name) if name in SOURCE_ORDER else len(SOURCE_ORDER)


def _as_ingester(name: str, module: ModuleType) -> Ingester | None:
    """The module as an Ingester, or None when it is not one.

    ``SOURCES`` is honoured when the module defines it, so a module covering several subsets of
    one dataset validates and prunes against all of them; ``SOURCE`` alone means exactly one.
    A ``SOURCES`` that omits its own ``SOURCE`` is a BuildError rather than a silent widening.
    """
    fn: IngestFn | None = getattr(module, "ingest", None)
    source = getattr(module, "SOURCE", None)
    if fn is None or not callable(fn) or source is None:
        return None
    primary = ReferenceSource(source)
    declared = getattr(module, "SOURCES", None)
    if declared is None:
        sources = (primary,)
    else:
        if isinstance(declared, str) or not isinstance(declared, Iterable):
            msg = f"ingester {name}.SOURCES is {type(declared).__name__}, want a sequence"
            raise BuildError(msg)
        sources = tuple(dict.fromkeys(ReferenceSource(value) for value in declared))
        if primary not in sources:
            msg = f"ingester {name} declares SOURCE={primary.value}, which is not in its SOURCES"
            raise BuildError(msg)
    return Ingester(name=name, source=primary, ingest=fn, sources=sources)


def run_ingester(
    ingester: Ingester, root: Path, *, ingested_at: str
) -> tuple[list[ReferenceClip], list[str], float]:
    """Call one ingester and check what it handed back before anything is written.

    Guarantees the driver never writes a document it cannot name a source for: the return value
    must be a pair of a list of ReferenceClip and a list of str, every clip must declare this
    module's SOURCE, and anything else is a BuildError naming the module.
    """
    started = time.perf_counter()
    result = ingester.ingest(root, ingested_at=ingested_at)
    seconds = time.perf_counter() - started
    if not isinstance(result, tuple) or len(result) != 2:
        msg = (
            f"ingester {ingester.name}.ingest returned {type(result).__name__}, want (clips, skips)"
        )
        raise BuildError(msg)
    clips, skips = result
    if not isinstance(clips, list) or not all(isinstance(c, ReferenceClip) for c in clips):
        msg = f"ingester {ingester.name}.ingest returned a non-ReferenceClip in its first element"
        raise BuildError(msg)
    if not isinstance(skips, list) or not all(isinstance(s, str) for s in skips):
        msg = f"ingester {ingester.name}.ingest returned a non-string skip reason"
        raise BuildError(msg)
    wrong = [(c.clip_id, c.source.value) for c in clips if not ingester.owns(c.source)]
    if wrong:
        declared = "/".join(source.value for source in ingester.sources)
        msg = (
            f"ingester {ingester.name} declares {declared} but returned {len(wrong)} clip(s) of"
            f" another source, first {wrong[0][0]} ({wrong[0][1]})"
        )
        raise BuildError(msg)
    return clips, skips, seconds


def document_path(clips_dir: Path, clip_id: str) -> Path:
    """Where one clip's document lives. One file per clip, named by the id that is in the id."""
    return clips_dir / f"{clip_id}.json"


def write_documents(
    clips_dir: Path, clips: Iterable[ReferenceClip], *, owned_sources: Iterable[ReferenceSource]
) -> tuple[int, int, int]:
    """Put the clips on disk as canonical JSON and remove the stale ones, returning three counts.

    Guarantees: each document is exactly ``clip.canonical_json()`` encoded UTF-8, so its file
    digest equals ``clip.content_hash()``; a document whose bytes are already correct is not
    rewritten, which is what makes a rerun leave the tree alone; every write goes through a
    temporary file and ``Path.replace``, so a reader sees either the old document or the new one;
    and pruning is limited to ``owned_sources``, so a ``--only`` run cannot delete another source's
    work.
    """
    clips_dir.mkdir(parents=True, exist_ok=True)
    owned = {source.value for source in owned_sources}
    keep: dict[str, bytes] = {}
    for clip in clips:
        if clip.clip_id in keep:
            msg = f"two ingesters produced clip_id {clip.clip_id}"
            raise BuildError(msg)
        keep[clip.clip_id] = clip.canonical_json().encode("utf-8")

    written = unchanged = 0
    for clip_id in sorted(keep):
        target = document_path(clips_dir, clip_id)
        payload = keep[clip_id]
        if target.is_file() and target.read_bytes() == payload:
            unchanged += 1
            continue
        staging = target.with_name(f"{target.name}.writing")
        staging.write_bytes(payload)
        staging.replace(target)
        written += 1

    pruned = 0
    for path in sorted(clips_dir.glob("*.json")):
        if path.stem in keep:
            continue
        if _document_source(path) in owned:
            path.unlink()
            pruned += 1
    return written, unchanged, pruned


def _document_source(path: Path) -> str | None:
    """The ``source`` field of a document, or None when it cannot be read as one.

    Read as raw JSON rather than validated, because the point is to decide whether this file
    belongs to a source that just ran, and an unparseable file belongs to nobody and stays.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    source = doc.get("source") if isinstance(doc, dict) else None
    return source if isinstance(source, str) else None


def read_documents(clips_dir: Path) -> tuple[ReferenceClip, ...]:
    """Every document in ``clips_dir``, validated, sorted by clip_id.

    This is the index's only input. Guarantees a stable order independent of directory iteration
    order, and a BuildError naming the file when a document is not a valid ReferenceClip, because
    an index quietly missing a clip is worse than a failed build.
    """
    clips: list[ReferenceClip] = []
    for path in sorted(clips_dir.glob("*.json")) if clips_dir.is_dir() else []:
        try:
            clip = ReferenceClip.model_validate_json(path.read_bytes())
        except ValueError as exc:
            msg = f"{path} is not a valid ReferenceClip: {exc}"
            raise BuildError(msg) from exc
        if clip.clip_id != path.stem:
            msg = f"{path} holds clip_id {clip.clip_id}, so the tree cannot address it by name"
            raise BuildError(msg)
        clips.append(clip)
    clips.sort(key=lambda clip: clip.clip_id)
    return tuple(clips)


def existing_manifest(index_path: Path) -> str | None:
    """The ``manifest_sha256`` of the index already at ``index_path``, or None.

    None means there is nothing to compare against: no file, a file that is not this index, or one
    without a library row. Any of those is a reason to build, never a reason to fail.
    """
    if not index_path.is_file():
        return None
    try:
        conn = index_module.open_index(index_path)
    except (FileNotFoundError, sqlite3.Error):
        return None
    try:
        return index_module.read_library(conn).manifest_sha256
    except (index_module.IndexUnavailableError, sqlite3.Error, ValueError):
        return None
    finally:
        conn.close()


def build_library(
    root: Path,
    *,
    index_dir: Path | None = None,
    only: Sequence[str] = (),
    ingested_at: str = DEFAULT_AT,
    built_at: str = DEFAULT_AT,
    lexicon_path: Path | None = None,
    library_id: str = LIBRARY_ID,
) -> BuildReport:
    """Ingest, write the documents, and build the index. The whole driver, minus the printing.

    Guarantees: the ingesters run in ``SOURCE_ORDER``; the index is built from the documents on
    disk and from nothing else; ``manifest_sha256`` is ``index.manifest_sha256`` over those
    documents in clip_id order, so it does not depend on how many ingesters ran or in what order;
    an index whose stored manifest already matches is left untouched and ``rebuilt`` is False; and
    no value written anywhere comes from the clock.

    ``index_dir`` defaults to ``root/_index`` and exists so ``--check`` can ingest the real tree
    into somewhere disposable. With ``only``, ``skipped`` covers just the sources that ran, since
    the sources that did not run reported nothing.
    """
    started = time.perf_counter()
    ingesters, nonconforming = discover_ingesters(only)
    target_dir = index_dir if index_dir is not None else root / "_index"
    clips_dir = target_dir / "clips"
    index_path = target_dir / "index.sqlite"

    reports: list[SourceReport] = []
    fresh: list[ReferenceClip] = []
    skipped: list[str] = []
    for ingester in ingesters:
        clips, skips, seconds = run_ingester(ingester, root, ingested_at=ingested_at)
        fresh.extend(clips)
        skipped.extend(skips)
        reports.append(
            SourceReport(
                name=ingester.name,
                source=ingester.source.value,
                clips=len(clips),
                skips=len(skips),
                seconds=seconds,
                sources=tuple(
                    source.value
                    for source in ingester.sources
                    if any(c.source is source for c in clips)
                ),
                unstamped=sum(1 for clip in clips if clip.ingested_at != ingested_at),
            )
        )

    written, unchanged, pruned = write_documents(
        clips_dir, fresh, owned_sources=[s for ing in ingesters for s in ing.sources]
    )
    on_disk = read_documents(clips_dir)
    manifest = index_module.manifest_sha256(on_disk)

    # A matching manifest is not enough: it digests the clip documents, so a schema change (the
    # tokenizer, say) leaves every document identical while the index on disk still carries the old
    # DDL. Ask the index module whether the file was built with the current schema.
    if existing_manifest(index_path) == manifest and index_module.schema_matches(index_path):
        return BuildReport(
            root=root,
            index_dir=target_dir,
            index_path=index_path,
            manifest_sha256=manifest,
            clip_count=len(on_disk),
            sources=tuple(reports),
            documents_written=written,
            documents_unchanged=unchanged,
            documents_pruned=pruned,
            rebuilt=False,
            library=None,
            skipped=tuple(skipped),
            nonconforming=nonconforming,
            seconds=time.perf_counter() - started,
        )

    library = index_module.build(
        on_disk,
        index_path,
        lexicon_sha256=lexicon_digest(lexicon_path),
        built_at=built_at,
        builder_version=BUILDER_VERSION,
        sources=sorted({clip.source for clip in on_disk}, key=lambda s: s.value),
        skipped=tuple(skipped),
        library_id=library_id,
        root=str(root),
    )
    manifest_file = target_dir / "library.json"
    manifest_file.write_text(library.canonical_json(), encoding="utf-8")
    return BuildReport(
        root=root,
        index_dir=target_dir,
        index_path=index_path,
        manifest_sha256=library.manifest_sha256,
        clip_count=library.clip_count,
        sources=tuple(reports),
        documents_written=written,
        documents_unchanged=unchanged,
        documents_pruned=pruned,
        rebuilt=True,
        library=library,
        skipped=tuple(skipped),
        nonconforming=nonconforming,
        seconds=time.perf_counter() - started,
    )


def _emit(line: str) -> None:
    """One line to stdout. Not ``print``: T20 bans it in the control plane, and this is the one
    module whose output is its interface."""
    sys.stdout.write(f"{line}\n")


def _source_column(report: SourceReport) -> str:
    """One column wide enough for the longest source value, however many a module emitted.

    A module covering several subsets shows its primary plus a count rather than the whole list,
    so the table stays aligned; the full list is in the totals JSON.
    """
    extra = len(report.sources) - 1
    return f"{report.source}+{extra}" if extra > 0 else report.source


def report_lines(report: BuildReport) -> tuple[str, ...]:
    """The human half of the output: one line per source, then the totals as JSON.

    Kept as a function so a test can read what the command says without capturing stdout.
    """
    lines = [
        f"{s.name:10s} {_source_column(s):22s}"
        f" clips={s.clips:5d} skips={s.skips:4d} {s.seconds:6.2f}s"
        + (f" unstamped={s.unstamped}" if s.unstamped else "")
        for s in report.sources
    ]
    if not report.sources:
        lines.append(f"no ingesters found in {INGEST_PACKAGE}")
    for name in report.nonconforming:
        lines.append(f"{name:10s} skipped: defines no ingest()/SOURCE pair")
    if not report.rebuilt:
        lines.append(f"index unchanged, manifest already {report.manifest_sha256[:12]}")
    lines.append(canonical_dumps(report.totals()))
    return tuple(lines)


def check(
    root: Path,
    *,
    only: Sequence[str] = (),
    ingested_at: str = DEFAULT_AT,
    built_at: str = DEFAULT_AT,
    lexicon_path: Path | None = None,
) -> tuple[bool, str, str, BuildReport]:
    """Rebuild into a temporary directory and compare manifests.

    Returns ``(same, live, fresh, report)``.

    The comparison is against the index in the tree when there is one, and against a second
    temporary build when there is not, so ``--check`` is meaningful on a tree that has never been
    built. Guarantees the tree is not touched: everything is written under a temporary directory
    that is removed before returning.
    """
    live = existing_manifest(root / "_index" / "index.sqlite")
    with tempfile.TemporaryDirectory(prefix="reference-check-") as tmp:
        report = build_library(
            root,
            index_dir=Path(tmp) / "_index",
            only=only,
            ingested_at=ingested_at,
            built_at=built_at,
            lexicon_path=lexicon_path,
        )
        fresh = report.manifest_sha256
        if live is None:
            with tempfile.TemporaryDirectory(prefix="reference-check-") as second:
                twin = build_library(
                    root,
                    index_dir=Path(second) / "_index",
                    only=only,
                    ingested_at=ingested_at,
                    built_at=built_at,
                    lexicon_path=lexicon_path,
                )
            live = twin.manifest_sha256
    return live == fresh, live, fresh, report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m content_factory.reference.build",
        description="Build the reference library index from the reference tree.",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the reference tree")
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild into a temporary directory and exit 1 if manifest_sha256 moved",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma separated ingester or source names, for iterating on one ingester",
    )
    parser.add_argument(
        "--at",
        default=DEFAULT_AT,
        help=f"the stamp for ingested_at and built_at (default {DEFAULT_AT})",
    )
    parser.add_argument(
        "--lexicon", type=Path, default=None, help=f"synonym lexicon (default {LEXICON_RELPATH})"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the driver. Returns 0 on success, 1 when ``--check`` sees a moved manifest, 2 on error.

    Every exit path prints the totals JSON last, so a caller can parse the outcome without
    guessing from the exit code alone.
    """
    args = _parser().parse_args(argv)
    only = tuple(part for part in str(args.only).replace(" ", "").split(",") if part)
    root = Path(args.root)
    if not root.is_dir():
        _emit(f"no reference tree at {root}")
        return 2
    try:
        if args.check:
            same, live, fresh, report = check(
                root, only=only, ingested_at=args.at, built_at=args.at, lexicon_path=args.lexicon
            )
            for line in report_lines(report)[:-1]:
                _emit(line)
            _emit(f"live   {live}")
            _emit(f"rebuilt {fresh}")
            _emit(canonical_dumps({**report.totals(), "check": "same" if same else "differs"}))
            return 0 if same else 1
        report = build_library(
            root, only=only, ingested_at=args.at, built_at=args.at, lexicon_path=args.lexicon
        )
    except BuildError as exc:
        _emit(f"build failed: {exc}")
        return 2
    for line in report_lines(report):
        _emit(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
