# Quality control

Four layers (section 17); phase 2 implements the deterministic post-render layer and the static
legibility report, with the pass policy fixed from the start:

1. **Contract and factual QC (blocking)** — schema validity (Ajv/Pydantic, unknown fields are
   errors), dataset lineage for every on-screen number (`RenderBundle` refuses unresolved
   `DataRef`s), chart semantics (`ChartScene`: zero baseline or explicit truncation disclosure,
   single-series donuts, dual axes off by default). Claims/citations arrive in phase 4.
2. **Pre-export static/visual QC** — `packages/content-ui` `legibilityReport`: minimum font px at
   1080, line counts vs `max_lines`, role-colour contrast ≥ 4.5; safe areas from the artboard.
3. **Post-render deterministic QC** — `python/content_factory/qc/media.py`: ffprobe assertions,
   black/frozen frame sampling, PNG integrity/blank detection. Only relevant checks run per
   deliverable type.
4. **Multimodal critic (optional)** — phase 7; advisory findings with allowed fix operations only.

Pass policy: PASS requires all deterministic gates green, zero blocker/critical findings, no
unresolved major finding in factual accuracy, legibility, synchronization, or rights. Severities:
blocker, critical, major, minor, advisory. A model score is never the pass decision.
