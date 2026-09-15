---
name: weights
description: Install, locate and mirror model weights for this repo. Use when asked to install, download, add, find, copy, sync or duplicate a model or its weights, when a model is missing on one machine, or when adding a new model from Hugging Face. Covers the /mnt/fast/models store and keeping vegaserv and nova identical.
---

# Model weights

**Every weight lives at `/mnt/fast/models/<family>` — the identical absolute path on every host.**
Five modules hardcode `/mnt/fast` with no environment escape (`shots/planner.py`,
`cli/workflows_cmd.py`, `controls/blender.py`, `reference/build.py`,
`skills/video/postchain/common.py`), so a weight anywhere else is a weight the pipeline cannot
open. Never invent a path, never pass a destination — the registry decides, and a destination
argument is exactly how the store drifted before (Ideogram 4's fp8 weights landed nested on
vegaserv and flat on nova; the same declared filename resolved on one box and not the other).

`python/content_factory/models/weights.py` is the single source of truth for what a family is,
where it comes from and where it lands. Everything below reads it.

## The four commands

```bash
uv run python scripts/weights.py status              # every family: here, and on nova
uv run python scripts/weights.py install <key>       # pinned download + models/ index link
uv run python scripts/weights.py mirror <key>        # rsync to nova at the identical path
uv run python scripts/weights.py pin <hf-repo-id>    # resolve sha+size, print the stanza to paste
```

`status` is one line per family and is the only thing to read first — it already answers "is it
installed", "is it linked" and "does nova have it". Do not go looking in the filesystem.

## Installing a model that is already in the registry

```bash
uv run python scripts/weights.py install <key> && uv run python scripts/weights.py mirror <key>
```

That is the whole job. `install` creates the `models/<category>/<Name>` symlink the skills resolve
through; `mirror` copies to nova and verifies the declared files on the far side.

## Adding a model that is NOT in the registry yet

1. `uv run python scripts/weights.py pin <org/repo>` — prints a `WeightPackage(...)` stanza with
   the 40-hex commit sha and real byte sizes, read from the Hub API.
2. Paste it into `WEIGHT_PACKAGES` in `python/content_factory/models/weights.py` and fill the
   `TODO`s (`purpose`, `index_category`, `index_name`).
3. `install` then `mirror`, as above.

Never pin a branch. `main` moves, and a moved weight is a different model.

## Two machines, two transports

| What | How | Why |
| --- | --- | --- |
| **Weights** | `scripts/weights.py mirror` (rsync) | Too big for git, and content-identical |
| **Code** | `git push` here, `git pull` on nova | Reviewable, reversible, and the only way stages and weights stay in step |

Do not rsync code and do not commit weights. A host running last week's stage against this week's
weights fails in a way that looks like a model regression.

nova is `nova@100.82.150.94`. Add another host with `--host user@addr` (repeatable), or extend
`DEFAULT_HOSTS` in `scripts/weights.py`.

## Which host should run it

**System RAM decides, not the card** — both boxes are RTX 3090s with 24 GB. A graph too big for
the card does not fail, it stages the overflow through system RAM: vegaserv has 31 GB, nova 76 GB.
The routing table is `docs/gpu-hosts.md`, "Where to run which model". Rule of thumb: weights
totalling more than ~20 GB belong on nova, *unless* the family is a quantised repack built to fit
(Ideogram 4 SDNQ is the worked example — 4-bit plus leaf-level group offloading is what lets it run
on vegaserv where the fp8 pair OOMs at load).

## Before you download anything

`status` first. 18 GB re-fetched because nobody looked is the failure this skill exists to prevent,
and the store already holds ~350 GB. Check free space with `df -h /mnt/fast` if the family is large.
