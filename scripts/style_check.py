"""Enforce the repo's comment and docstring style (.claude/skills/style/SKILL.md)."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

COMMENT_BLOCK_MAX = 2
MD_LIMITS = {"STATUS.md": 200, "CLAUDE.md": 80}
JOURNAL_SECTION_MAX = 60
EXCLUDE = re.compile(
    r"^(alembic/|\.agents/|\.claude/skills/remotion-|.*/generated/|packages/content-schema-ts/schema/"
    r"|.*node_modules/|.*/dist/|.*/out/)"
)
PY_PRAGMA = re.compile(r"^#!|^# (ruff|pyright|type|noqa|fmt|isort):")
TS_PRAGMA = re.compile(r"^// ?(eslint|oxlint|@ts-|biome|prettier|c8|v8|istanbul|vitest)")
BANNER = re.compile(r"^(#|//)\s*(-{3,}|={3,}|-+ .* -+)\s*$")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True, check=True
    )
    return [f for f in out.stdout.split("\n") if f and not EXCLUDE.match(f)]


def check_python(path: str, src: str, out: list[str]) -> None:
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        out.append(f"{path}:{exc.lineno}: syntax error")
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            const = body[0].value
            end = const.end_lineno or const.lineno
            if end != const.lineno:
                span = end - const.lineno + 1
                out.append(f"{path}:{const.lineno}: docstring spans {span} lines; one line only")
    _check_runs(path, src.splitlines(), out, lambda s: s.startswith("#"), PY_PRAGMA)


def check_ts(path: str, src: str, out: list[str]) -> None:
    lines = src.splitlines()
    in_block = start = 0
    is_doc = False
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if in_block:
            if "*/" in s:
                length = i - start + 1
                if is_doc and length > 1:
                    out.append(f"{path}:{start}: JSDoc spans {length} lines; one line only")
                elif not is_doc and length > COMMENT_BLOCK_MAX:
                    out.append(
                        f"{path}:{start}: comment block of {length} lines; max {COMMENT_BLOCK_MAX}"
                    )
                in_block = 0
            continue
        if s.startswith("/*") and "*/" not in s:
            in_block, start, is_doc = 1, i, s.startswith("/**")
    _check_runs(path, lines, out, lambda s: s.startswith("//"), TS_PRAGMA)


def _check_runs(
    path: str, lines: list[str], out: list[str], is_comment, pragma: re.Pattern[str]
) -> None:
    run = start = 0
    for i, raw in enumerate([*lines, ""], 1):
        s = raw.strip()
        if is_comment(s) and not pragma.match(s) and not BANNER.match(s):
            if not run:
                start = i
            run += 1
            continue
        if run > COMMENT_BLOCK_MAX:
            out.append(f"{path}:{start}: comment block of {run} lines; max {COMMENT_BLOCK_MAX}")
        run = 0


def check_journal(path: str, lines: list[str], out: list[str]) -> None:
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        while end > i and not lines[end - 1].strip():
            end -= 1
        if end - i > JOURNAL_SECTION_MAX:
            out.append(
                f"{path}:{i + 1}: journal entry of {end - i} lines; max {JOURNAL_SECTION_MAX}"
            )


def main() -> int:
    out: list[str] = []
    for f in tracked_files():
        p = Path(f)
        if p.suffix == ".py":
            check_python(f, p.read_text(encoding="utf-8", errors="replace"), out)
        elif p.suffix in {".ts", ".tsx", ".mjs"}:
            check_ts(f, p.read_text(encoding="utf-8", errors="replace"), out)
    for name, limit in MD_LIMITS.items():
        n = len(Path(name).read_text().splitlines())
        if n > limit:
            out.append(f"{name}:1: {n} lines; max {limit}")
    for journal in sorted(Path("docs/journal").glob("*.md")):
        check_journal(journal.as_posix(), journal.read_text().splitlines(), out)
    if "--stats" in sys.argv:
        kinds = {"docstring": 0, "JSDoc": 0, "comment": 0, "lines": 0}
        for line in out:
            for k in kinds:
                if k in line:
                    kinds[k] += 1
        print(f"violations={len(out)} {kinds}")
        return 1 if out else 0
    print("\n".join(out))
    if out:
        print(f"style_check: {len(out)} violation(s)", file=sys.stderr)
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
