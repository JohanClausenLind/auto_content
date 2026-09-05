"""Which HiDream-O1 weights are on disk, decided by hash rather than by directory name.

The two upstream repos — ``HiDream-ai/HiDream-O1-Image`` and ``…-Image-Dev`` — ship eight shards
each with **identical file sizes**, and shard 8 is byte-identical between them. Only shards 1-7
differ. So neither the directory name, nor the total size, nor a hash of any single arbitrary
shard identifies which model is present; this host's directory is named ``…-Image-Dev`` and
carried a comment asserting it held the full model, and the server was configured for the full
recipe on Dev weights for a week — 50 steps at guidance 5 where 28 at guidance 0 was correct.

The recipe is a separate setting from the weights, and nothing made them agree. This makes the
disagreement detectable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

ModelKind = Literal["full", "dev", "unknown"]

FIRST_SHARD = "model-00001-of-00008.safetensors"
"""Shard 1 differs between the repos, unlike shard 8. Any of 1-7 would do; 1 is the obvious one."""

SHARD1_SHA256: dict[str, ModelKind] = {
    "db5d56d92c14": "full",
    "575a1b54a028": "dev",
}
"""Leading 12 hex characters of each repo's shard 1, read from the Hub's own LFS oids."""


def identify(model_dir: Path, *, shard: str = FIRST_SHARD) -> tuple[ModelKind, str]:
    """(kind, digest prefix) for the weights in ``model_dir``.

    Hashes ~5 GB, so it is a doctor-time check rather than something on the generation path.
    """
    path = Path(model_dir) / shard
    if not path.exists():
        return "unknown", ""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(block)
    prefix = digest.hexdigest()[:12]
    return SHARD1_SHA256.get(prefix, "unknown"), prefix


def recipe_matches_weights(model_dir: Path, configured: str) -> tuple[bool, str]:
    """Does the configured recipe match the weights actually present?

    Returns (ok, explanation). An unknown checkpoint is not treated as a failure — a fine-tune or
    a repack is legitimate — but it is reported, because then nothing is checking the recipe.
    """
    kind, prefix = identify(model_dir)
    if kind == "unknown":
        return True, (
            f"cannot identify the checkpoint in {model_dir} "
            f"(shard 1 sha256 {prefix or 'missing'}); recipe '{configured}' is unverified"
        )
    if kind == configured:
        return True, f"recipe '{configured}' matches the {kind} weights on disk"
    return False, (
        f"recipe is '{configured}' but the weights are {kind} (shard 1 sha256 {prefix}). "
        f"full means 50 steps at guidance 5; dev means 28 steps at guidance 0 with the distilled "
        f"timestep schedule. Running the full recipe on dev weights costs roughly twice the steps "
        f"for no benefit, at a guidance the model was distilled not to need."
    )
