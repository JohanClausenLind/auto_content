"""Sort a downloaded sound pack into its staging root, one directory per library category.

    uv run --project skills/audio/sfx python skills/audio/sfx/stage_pack.py \
        --recipe skills/audio/sfx/mixkit.json --from ~/Downloads [--copy] [--dry-run]

The pack roots hold licensed source audio and live *outside* the repo, next to the other
recorded libraries under `/mnt/fast/sound-libraries` -- the same arrangement as the
#GameAudioGDC bundle, and for the same reason: the Mixkit licence permits an End Product that
incorporates a sound, not redistribution of the sound itself, so the sources must not be
committable. `.gitignore` already bars media under `assets/`; nothing bars a stray copy inside
the repo, which is why the default root is not in it.

The recipe decides where each file goes. `src.pack` is the category directory and `src.file` the
delivered filename, so this script has no taxonomy of its own and cannot disagree with the
builder about one: recategorise a sound in the recipe, run this again, and the file moves.

Idempotent. A file already staged with the same bytes is left alone; one already staged with
*different* bytes is refused by name rather than overwritten, because that is either a re-download
of a changed source or two sounds fighting over a filename, and both want a human.

`--dry-run` prints every move it would make and touches nothing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import recorded
import sfx

DUPLICATES = "_duplicates"
CHECKSUMS = "SOURCES.sha256"


def plan(recipe: dict, src_dir: Path, root: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    """(moves, problems). A move is (source, destination); both absolute."""
    moves: list[tuple[Path, Path]] = []
    problems: list[str] = []
    for spec in recipe["sounds"]:
        name = spec["src"]["file"]
        dest = root / spec["src"]["pack"] / name
        src = src_dir / name
        if dest.is_file():
            if not src.is_file():
                continue  # already staged, nothing left in the download directory
            if sfx.sha256(dest) == sfx.sha256(src):
                moves.append((src, dest))  # same bytes: consume the duplicate download
                continue
            problems.append(f"{spec['id']}: {dest} exists with different bytes than {src}")
            continue
        if not src.is_file():
            problems.append(f"{spec['id']}: {name} is not in {src_dir} and not staged at {dest}")
            continue
        moves.append((src, dest))
    return moves, problems


def find_duplicates(recipe: dict, src_dir: Path) -> dict[str, str]:
    """Leftover downloads whose bytes are already accounted for: {filename -> id it duplicates}.

    Browsers name a second download of the same file `x(1).wav`; the pattern is not trusted here,
    the bytes are. A file is a duplicate only if its sha256 matches a file the recipe names.
    """
    wanted = {spec["src"]["file"]: spec["id"] for spec in recipe["sounds"]}
    by_hash: dict[str, str] = {}
    for name, sound_id in wanted.items():
        p = src_dir / name
        if p.is_file():
            by_hash[sfx.sha256(p)] = sound_id
    out: dict[str, str] = {}
    for p in sorted(src_dir.glob("*.wav")):
        if p.name in wanted:
            continue
        h = sfx.sha256(p)
        if h in by_hash:
            out[p.name] = by_hash[h]
    return out


def write_checksums(root: Path) -> int:
    lines = []
    for p in sorted(root.rglob("*.wav")):
        if DUPLICATES in p.parts:
            continue
        lines.append(f"{sfx.sha256(p)}  {p.relative_to(root)}")
    (root / CHECKSUMS).write_text("\n".join(lines) + "\n")
    return len(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", type=Path, required=True)
    ap.add_argument("--from", dest="src_dir", type=Path, default=Path.home() / "Downloads")
    ap.add_argument("--copy", action="store_true", help="copy instead of move")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    recipe = json.loads(args.recipe.read_text())
    if "pack" not in recipe:
        raise SystemExit(f"{args.recipe} has no `pack` block: not a pack recipe")
    pack = recipe["pack"]
    root = recorded.pack_root(pack, must_exist=False)
    src_dir = args.src_dir.expanduser()
    if not src_dir.is_dir():
        raise SystemExit(f"download directory not found: {src_dir}")

    moves, problems = plan(recipe, src_dir, root)
    dupes = find_duplicates(recipe, src_dir)
    verb = "copy" if args.copy else "move"

    print(f"pack:  {pack['name']}  ({pack['id']})", file=sys.stderr)
    print(f"root:  {root}  [{pack['root_env']}]", file=sys.stderr)
    print(f"from:  {src_dir}", file=sys.stderr)
    per_cat = Counter(dest.parent.name for _s, dest in moves)
    for cat, n in sorted(per_cat.items()):
        print(f"  {cat:11s} {n:4d}", file=sys.stderr)
    print(
        f"{len(moves)} to {verb}, {len(dupes)} duplicate download(s), {len(problems)} problem(s)",
        file=sys.stderr,
    )
    for p in problems:
        print(f"  PROBLEM {p}", file=sys.stderr)
    if problems:
        raise SystemExit("refusing to stage a partial pack; fix the problems above")

    if args.dry_run:
        for src, dest in moves:
            print(f"{verb} {src}  ->  {dest}")
        for name, sound_id in dupes.items():
            print(
                f"{verb} {src_dir / name}  ->  {root / DUPLICATES / name}  (duplicate of {sound_id})"
            )
        print("\n--dry-run: nothing moved", file=sys.stderr)
        return 0

    for src, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file():  # same bytes, verified in plan()
            src.unlink() if not args.copy else None
            continue
        shutil.copy2(src, dest) if args.copy else shutil.move(str(src), str(dest))
    if dupes:
        (root / DUPLICATES).mkdir(parents=True, exist_ok=True)
        for name in dupes:
            target = root / DUPLICATES / name
            if target.exists():
                (src_dir / name).unlink() if not args.copy else None
                continue
            shutil.copy2(src_dir / name, target) if args.copy else shutil.move(
                str(src_dir / name), str(target)
            )

    n = write_checksums(root)
    print(
        f"\nstaged {len(moves)}, {n} files under {root}, checksums in {CHECKSUMS}", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
