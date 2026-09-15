---
name: style
description: The coding, comment and docstring ruleset for this repo. Use before writing, editing or reviewing any Python, TypeScript or Markdown here, when asked about conventions or style, and when trimming comments, docstrings or docs to save tokens. `just lint` enforces the mechanical half through scripts/style_check.py.
---

# Style

Every line here is read by an agent many more times than it is written. Code says what; a
comment says only what the code cannot. Delete a comment when losing it would cost nobody a
wrong decision.

## Docstrings and JSDoc

- One line, always: `"""Noun or verb phrase, ending in a period."""` and `/** The same. */`.
  No second line, no Args/Returns/Raises/Example sections; the signature and types carry those.
- Omit it when the name already says it. Keep it when it adds the why, the unit, the invariant
  or the one non-obvious constraint.
- Typer commands and FastAPI routes: the docstring is the user-facing help, so it is one
  complete sentence that stands alone.
- Pydantic models under `schemas/`: the class docstring becomes the JSON Schema description.
  Change one, then run `just schemas`.
- Module docstring: one line naming what the module is for, or none.

## Comments

- At most two lines per block. A block that needs more is a journal entry
  (`docs/journal/<year>-<month>.md`, cited by date) or a sign the code should change.
- Say the why, the invariant, the measured constraint or the trap. Never the what (the code
  says it), never the history (git does), never the story or a table of measurements (journal).
- A number may stay when the code depends on it, with its source:
  `# 8 s: MMAudio drops out past its window (journal 2026-09-12)`.
- No commented-out code. No TODO without a pointer to STATUS.md's next task or a journal date.
- Section banners (`# --- name ---`) only in files over 400 lines, one per section.
- A pragma (`# noqa: S603`, `// eslint-disable-next-line x`) names the rule and, when the reason
  is not obvious, adds a few words of why on the same line.

## Code

- Python: ruff format and lint (pyproject.toml), pyright standard, line length 100. Type every
  public signature. Subprocess takes argument arrays, never shell strings. `pathlib` over
  `os.path`. No bare `except`.
- TypeScript: strict, oxlint over `.oxlintrc.json`. Exported functions declare return types.
  No `any`.
- Contracts start as Pydantic models; unknown fields are errors; IDs are opaque strings. Workflow
  code is deterministic and every activity is idempotent.
- Names carry the documentation: a function is a verb phrase, a boolean reads as a predicate, a
  quantity names its unit (`timeout_s`, `width_px`, `gain_db`).
- Tests: one behaviour per test, named for the behaviour. A docstring only when the name cannot
  hold the why.
- Dependencies are pinned exactly and resolved from the registry, never from memory.

## Docs, status and the journal

- `STATUS.md` is at most 200 lines: verdict, next task, one line per phase. Proof goes in the
  journal.
- A journal entry is `## <date> — <title>` and at most 60 lines, in this order and only what
  applies: **Changed** (one line per thing, with paths), **Measured** (only numbers that decided
  something, with the decision), **Dead ends** (one line each), **Gates** (commands and results),
  **Open** (what was left). No narrative, no restated context, no quoting the request, no tables
  of every run, no code beyond the exact command that matters.
- `CLAUDE.md` is at most 80 lines: what applies every session, plus pointers. Nothing a skill or
  a doc already holds.
- A doc states the current truth. A dated finding is a journal entry. A decision that changes
  the architecture is an ADR. Each fact lives in exactly one of the three.

## Check

```bash
uv run python scripts/style_check.py    # part of `just lint`
```

It reports multi-line docstrings and JSDoc, comment blocks over two lines, and the doc size caps.

## Trimming existing code

1. Collapse a docstring to its first sentence when that sentence is true and fits; otherwise
   write the one line that is.
2. For each comment block over two lines, keep the decision or invariant in two lines. Move a
   measurement or dead end to the journal only if it is not already there (grep first; most are).
3. Delete a comment that restates the line below it.
4. Prove behaviour is unchanged: `uv run ruff format <files> && uv run ruff check <files>`,
   `uv run pyright <files>`, and the tests that cover them. Comment and docstring edits never
   change the AST.
