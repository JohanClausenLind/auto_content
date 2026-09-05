#!/usr/bin/env bash
# The contract pipeline, defined ONCE: Pydantic -> JSON Schema -> TS -> Ajv, plus the node
# catalogue and workflow templates. `just schemas` regenerates; `just schemas-check` gates.
#
# Usage: scripts/schemas.sh [--check]
set -euo pipefail
cd "$(dirname "$0")/.."

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

# Files the generators write that a git-based gate has to inspect. `git diff` is blind to
# untracked files, so a gate that diffs them while they are untracked passes silently — we
# fail loudly instead of pretending to check.
GATED=(fixtures/schema/node_catalog.json fixtures/schema/workflow_templates.json)

if [ "$CHECK" = 1 ]; then
  uv run python scripts/export_schemas.py --check
else
  uv run python scripts/export_schemas.py
fi

pnpm --filter @content-factory/content-schema-ts run generate
node scripts/dump_node_catalog.mjs   # no --check: always writes, so the gate diffs it below

if [ "$CHECK" = 1 ]; then
  uv run python scripts/export_workflows.py --check
else
  uv run python scripts/export_workflows.py
fi

[ "$CHECK" = 1 ] || exit 0

untracked=()
for f in "${GATED[@]}"; do
  git ls-files --error-unmatch "$f" >/dev/null 2>&1 || untracked+=("$f")
done
if [ "${#untracked[@]}" -gt 0 ]; then
  printf 'schemas-check: cannot gate untracked generated file(s):\n' >&2
  printf '  %s\n' "${untracked[@]}" >&2
  printf 'git diff is blind to untracked files, so this gate would pass silently.\n' >&2
  printf 'Commit them (or add them to .gitignore and drop them from GATED) and re-run.\n' >&2
  exit 1
fi
git diff --exit-code -- "${GATED[@]}"
