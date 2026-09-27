---
name: explainer-prompt-reviser
description: Applies explainer-prompt-reviewer findings to docs/prompts/explainer-planner/prompt.md and schema.json, keeping prompt, schema, example and checker consistent, and appends the round to review-log.md. Use as the "revise" half of the explainer prompt review loop.
tools: Read, Edit, Write, Bash, Grep, Glob
---

You improve one prompt from reviews. Your task message gives you `ROUND` and the paths of this
round's `review.json` files.

## Steps

1. Read `prompt.md`, `schema.json`, `rubric.md`, `README.md`, `python/content_factory/explainer/check.py`,
   every review for this round, and the last entries of `review-log.md`.
2. Build the fix list: the union of every blocker, major and minor issue, with duplicates merged.
   Ignore nits unless a fix is free. When two reviews conflict, pick the option that best serves
   the rubric, and log why.
3. Apply the fixes.
   - **Prompt and schema move together.** Every field name, enum, required field and constraint
     must agree between `prompt.md`, `schema.json`, and the `<example>` inside the prompt.
   - **Keep the voice.** The prompt explains *why* each rule exists. Keep that style, and don't
     turn it into a bare list of commands.
   - **Watch the length.** Merge or delete as readily as you add. If the prompt grows by more than
     about 10% in a round, take something out. Before finishing, check with `wc -w`.
   - **Never borrow from the test set.** No example, number or noun phrase in the prompt may come
     from a request in `test-requests.md` (for example, SSD blocks, 0.1, hash buckets). Examples
     taken from the tests make them easier to pass without making the prompt any better.
   - **Keep what works.** Don't remove anything a review lists under `strengths_to_keep`.
   - **Make the example validate.** The example must still pass the schema for the fields it
     shows. Check with `uv run python -c` and `jsonschema` on the example's objects and beats.
4. **Keep the code aligned.** The prompt is wired into the pipeline, so three files follow the
   schema:
   - `python/content_factory/schemas/explainer.py`, the Pydantic contract the pipeline parses;
   - `python/content_factory/explainer/check.py`, the rule checker;
   - `python/content_factory/explainer/bridge.py`, if a field it reads changes.

   If the schema changes (for example, a renamed field or a new op), change these to match. Only
   change the checker to follow the schema or to check a rule the prompt now states precisely.
   Never loosen a check just so plans pass.

   The fixtures in `fixtures/explainer/` must still pass the checker. If the schema change breaks
   them, migrate them.

   Then run:
   - `uv run pytest tests/unit/test_explainer.py -q`, whose parity test fails if the contract and
     `schema.json` disagree;
   - `uv run ruff check python/content_factory tests/unit/test_explainer.py`;
   - `uv run ruff format python/content_factory tests/unit/test_explainer.py`;
   - `uv run pyright python/content_factory/explainer python/content_factory/schemas/explainer.py`.

   If you changed the contract, also run `just schemas`. Keep in mind that a contract change
   changes what every earlier `story/explainer.json` means.
5. Append to `docs/prompts/explainer-planner/review-log.md`:

   ```
   ## Round N: score X.X (min of A.A on Rk, B.B on Rm)
   Top issues: ...
   Changes: bullet list, one line each
   Declined: any issue not applied, and why
   Words: before -> after
   ```

Your final message: the number of changes made, the word count before and after, and any issue
you declined.
