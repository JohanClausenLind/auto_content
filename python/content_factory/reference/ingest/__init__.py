"""Per-source ingesters, all the same shape so the build driver can call them in a loop.

Every module here exposes ``INGESTER_VERSION``, ``SOURCE`` and

    ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]

and nothing else is required of it. ``root`` is the reference root, every ``ReferenceFile.path``
is relative to it, and ``ingested_at`` is passed in rather than read from the clock so a rebuild
of unchanged bytes produces unchanged bytes. The second half of the return value is one
human-readable line per thing on disk that did not become a clip, because a silent absence in a
reference library is indistinguishable from a bug.
"""

from __future__ import annotations
