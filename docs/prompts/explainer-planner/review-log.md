# Review log

One entry per round of the loop described in README.md. Round 0 is the prompt as it was received.

## Round 1: score 6.5 (min of 6.5 on R1, 6.7 on R2)
Top issues: the title question clashed with the shared "no rhetorical questions" instruction;
output far larger than local models emit; `update` had no new value; on-screen/in-view/dim state
undefined; claims_to_verify unanchored and blind to on-screen facts; no rule for stating facts
without source notes; both test plans broke the 100-word structural-move gap.
Changes:
- <pacing>: the title question is the one question the narration asks, a question the video answers, not a rhetorical aside; the resolution asks it again; every other sentence states. Checker errors on any other question.
- <explanation_rules>: without source notes, well-established facts are allowed because every one is listed and verified; on-screen facts (labels, code, register names) must be listed too.
- claims_to_verify is now {claim, beat, sentence, object}; sentence -1 plus object for on-screen facts. Schema, example (two claims) and checker (anchor checks) follow.
- visual_updates gains value (new on-screen text for update; the pointed-at cell for highlight/fault on data objects). Checker requires it for update and forbids it elsewhere.
- Data objects (memory_row, register, state_table, graph_line, graph_bar, timeline) hold their content in label, cells separated by " | ".
- <operations>: three state rules: on screen from entrance to dismiss; a zoom changes only what's in view (frame and its descendants), so updates target only those; dimming lasts to the next highlight or the beat's end. Checker errors on targets outside the frame.
- <output_format>: the on-screen paragraph now lists exactly which targets an op may bring in, matching the checker (compare, dismiss, branch fork point included).
- New <output_size>: size budget (at most 30 objects, 16 beats, short goal/question/mechanism/notes), plus skeleton and beats-batch (at most 4 beats) modes; schema.json gains $defs/skeleton and $defs/beat_batch, and the root schema now uses $defs.
- Removed beat.visual (duplicated the notes; saves tokens); notes are now the shot-spec description.
- 145 wpm (2.4 w/s): landmarks now word 29 and word 85; checker --wps default 145/60 and landmarks 29/85.
- Structural moves: exactly "at most 100 words apart", counted from the first word to the last, with how transition and in-beat moves are positioned; the model keeps a running count and brings a move in past about 70 words; planning step 5 marks move beats in the outline. Checker now errors above 100 (was 110) since the rule is now exact.
- Narration doubles as captions: short numbers as digits, symbols as words, long numbers on screen.
- Color roles named by meaning (neutral, software, privileged, hardware, data, fault, accent_1, accent_2), enum in schema; color_semantics is {role, meaning}; example updated.
- Schema: id patterns (snake_case objects, ^b[0-9]{2}$ beats), at_sentence >= 0, est_seconds and target_duration_s >= 1; merge and compare take two or more targets (checker).
- Default duration stays 420 s with a one-clause reason (one question rarely fills the documentary lane's 600).
- Cuts for length: two of four semantic_zoom patterns, the "Teach through the boundary" paragraph (movement 5 says it), the decorative-transitions paragraph (merged into <visual_language>), redundant vocabulary and final_check wording.
Declined:
- transition_out removal (R1 nit): small saving, and it would change the checker's cut rules for little gain.
- Same label for two objects of one concept (R2 nit): conflicts with the checker's shared-label warning; not free.
- Editing templates.py or the README pipeline facts: the loop driver owns those this round.
Words: 3670 -> 4067
