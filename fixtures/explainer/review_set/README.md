# Review set

Seven accepted/rejected pairs of compiled `ExplainerRenderBundle` JSON for the deterministic QC (`content_factory.explainer.qc_checks`), built by `scripts/make_review_fixtures.py` from `pack.json` and each pair's `script.json`. A rejected bundle is the accepted one mutated past the compiler's guards, or a second compile of a spec the compiler cannot refuse.

| pair | expected failed check on `rejected.json` |
| --- | --- |
| `tiny_text` | `text_floor` |
| `clipping` | `clipping` |
| `wrong_highlight` | `ocr_text` |
| `negation_omitted` | `ocr_text` |
| `axis_change` | `axis_change` |
| `narration_visual_mismatch` | `narration_visual` |
| `broken_transition` | `transition` |
