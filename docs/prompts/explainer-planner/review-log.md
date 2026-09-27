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

## Round 2: score 7.8 (min of 7.9 on R3, 7.8 on R4)
Top issues: batch calls had no state (on-screen objects, running word/sentence counts); planning step 5's "four typical beats" contradicted the 100-word move rule; cells referenced by text were ambiguous; toy-example values read as invented figures; nested-frame drawing and un-dimming undefined; update could rename a named concept.
Changes:
- <output_size>: a beat batch now receives <state> from code (ids on screen, each data object's current content, words so far, words since the last move, sentences since the last change) and continues from it; skeleton decides each beat's move up front. <inputs> and the schema $comment mention <state>.
- Planning step 5: moves are at most 100 words apart and a typical beat runs 25–70 words, so nearly every beat carries one; <pacing> states the same word range.
- value for highlight/fault (one data-object target) and trace (last target a data object) is now 0-based cell indices ("3", "2,5") counted in current content; "" is the whole object. Checker tracks content through updates and checks the indices.
- update is limited to data objects, code_blocks, callouts and labels; status on a named object is a callout. Checker errors on other primitives.
- <operations>: a nested beat frame is drawn collapsed unless this beat targets something inside it; other ops' targets and a highlighted object's descendants are un-dimmed.
- <output_format>: an object enters only once its parent is on screen (checker error; catches R3's leaf_cert-before-chain); state_table cells are "key: value" rows, graph points/bars "name=value"; data_packet parent and resting place; traced objects move as compact tokens; branch takes one object per alternative; the transition wording ("that update is the transition"); beat layer "usually the frame's layer".
- Toy examples: mark as an example in narration and list only the arithmetic as an "In this example," claim.
- Source notes: keep narration within them; conflicts and gaps go in the notes field.
- Semantic color: cells aren't tinted; cell state lives in text, a broken cell is faulted.
- Duration: both est_seconds and spoken length within 10% ("about" dropped); checker now errors on spoken length outside it (under 90% allowed with notes).
- Cuts to pay for it: core_idea's closing line, explanation_rules intro, opener examples, the look paragraph's list, the second zoom pattern's tail, object-permanence example, <inputs> renderer sentence, several trims.
Declined:
- Batches capped at two beats and a separate "objects" mode (R3, R4): the corrected Pipeline facts say ~1k tokens is one measurement, not a ceiling, and pieces should stay large enough to plan well; the fix is the <state> input.
- Dropping goal from the skeleton (R3, optional): saves little and would change beat_outline for every consumer.
- A strict collapse (nested frames never open without a zoom, R3's wording): it would forbid the natural "block with its pages visible inside the NAND view" layout in the R4 plan; collapse-unless-targeted solves the resolution clutter without that cost.
- Role prefixes on cells (R4 option A): chose the simpler "cell text carries state, fault marks breakage".
- transition_out redundancy (R4 nit): no change needed.
Words: 4067 -> 4302

## Round 3: score 8.1 (min of 8.1 on R5, 8.2 on R6)
Top issues: the 100-word move rule counted only zoom, compare and fault, forcing repeated faults and back-and-forth zooms on shallow topics; morph and merge never retired their sources (and arrows kept pointing at them), and split's container was unstated; data_packet could not be updated, so load-modify-store flows needed duplicate packets; same-sentence update order undefined.
Changes:
- <pacing>: a structural move is any op that changes the diagram's structure: zoom_in, zoom_out, split, morph, merge, branch, compare, fault; reveal, highlight, update, trace, dismiss don't count. Every move must be motivated by the narration, never inserted for the count; if none fits, cut or restructure the stretch. Checker STRUCTURAL_OPS matches exactly; message says "structural move".
- Planning step 5 marks "the structural move each beat carries" instead of "zoom, compare, or fault".
- <operations> state rules: morph and merge replace their targets with into (targets and their contents leave, arrows ending at a target now end at into); split keeps its source on screen as its children's container, and the children's parent is the source. Checker removes morph/merge targets and their descendants from the on-screen set and errors on a split child whose parent isn't the source.
- One name per concept: a morph's into is the same concept in its next form, keeping term and color under a new id.
- update now allows data_packet (prompt and checker UPDATABLE).
- at_sentence: updates on one sentence play in array order, spread across the sentence.
- Data objects: a shown name is the first cell's key ("count: 5") and updates keep it; checker warns when an update drops the key.
- data_packet at rest is drawn at its endpoint while that endpoint is in view, but only a beat whose frame contains its parent can target it; a callout or fault_marker points at its parent.
- Collapsed nested frames open for the whole beat when the beat targets inside them.
- Missing duration: plan 420 s and say so in notes. target_duration_s is 60 to 3600 (prompt and schema, root and skeleton).
- beats.question: for the two opening beats, the question the beat raises.
- Cuts to pay: object-permanence example tail, operations intro, the openers line, "a real plan contains every field", the beat-type sentence, step 5's beat-length clause, and pacing wording.
Declined:
- Relaxing the gap to 120 words (R6 option): the broader move set removes the pressure while keeping the gap tight; 100 stays.
- R5's "fault on the step being explained" guidance for any stretch: superseded by the wider move set plus the motivation rule; repeating a fault is exactly what the driver asked to stop.
- Collapsed frame opening "from that update on" (R6 nit) vs "for the whole beat" (R5 nit): chose the whole beat, so the layout doesn't reflow mid-beat.
- Arrow endpoint tracking in the checker: the repoint rule needs no validation (arrows never become invalid), so the checker only notes it.
Words: 4302 -> 4430

## Round 4: score 8.2 (min of 8.6 on R1, 8.2 on R2)
Top issues: fault marks had no lifetime or clearing rule (R2 major); a data_packet resting at a morphed or merged target, and merge's into placement and role, were undefined; memory_row, register and timeline had no cell grammar; the 20-word field limit sat away from the fields and the example's b03 mechanism broke it; the density number was not countable; toy-example and on-screen-fact rules overlapped; spoken facts also shown on screen had no on-screen claim; renderer_constraints had no rule; "another rate" pointed at no input.
Changes:
- <operations> state rules: a fault mark lasts until its object is dismissed, replaced, or updated, and into never inherits it; morph/merge into is placed by its own parent, and a data_packet resting at a target now rests at into.
- Op lines: morph's into keeps its color_role; merge's into takes a color_role naming the combined unit.
- Cell grammar: timeline cell "time=event"; memory_row or register cell is its shown text (same sentence as state_table and graph).
- 20-word limit moved to the goal field definition (covering question, mechanism, note), removed from <output_size>; example b03 mechanism shortened to 13 words.
- Density: the five-to-seven figure is now explicitly "a guide, not a count"; the highlight-or-reveal rule stays.
- Toy examples: on-screen example values (sample tables, code) follow the same "In this example," arithmetic-only rule, anchored to their object.
- claims_to_verify: an on-screen fact gets its own sentence -1 entry even when also spoken; explanation_rules says "facts shown on screen"; example adds the ud_fault on-screen claim.
- <inputs>: never use an excluded primitive or op; use the nearest allowed one and say so in notes.
- <pacing>: dropped "unless the request gives another rate"; the 70-word cue no longer asks for a hand-kept running count.
- Planning step 5: write each beat's structural move and word budget beside it, so no two moves are budgeted more than 100 words apart.
- Checker: warns when goal, question, mechanism or note reach 20 words; warns on a fault of an object that still carries a fault mark (cleared by update, dismiss, or morph/merge replacement); warns when a morph's into changes color_role (would have caught R1's double_box slip).
- Cuts to pay: the claim→mechanism→example line, the third contrast example, font details in "The look", the color_semantics duplicate sentence, final_check 3 and 8 wording, several pacing/operations trims.
Declined:
- maxLength on goal/question/mechanism/note in schema.json (R2 nit): the limit is in words; a character cap would disagree with it. The checker warns instead.
- "Transitions need targets revealed in an earlier beat" (R1 nit): already implied by the on-screen rule; not free.
- Revealed data_packet position and split children riding along (R2 nit): renderer layout detail, not free.
- <words_per_minute> input (R2 option): dropped the "another rate" clause instead; the checker keeps --wps for code.
Words: 4430 -> 4448

## Round 5: score 8.1 (min of 8.3 on R3, 8.1 on R4)
Top issues: the on-screen state rules were a pile of special cases, and each round's patch exposed the next edge case. A status callout on a collapsed box opened it and flooded the resolution frame (R4 major). A packet could not be targeted after a zoom into its destination, which forced a duplicate object for one concept (R3 major). A faulted arrow or box could only be cleared by dismissing it. The toy-example rule and the on-screen-fact rule overlapped. "Exactly one data-object target" had two readings. target_duration_s allowed 3600 s, which 16 beats cannot hold.
Changes (a simplification pass, per the loop driver):
- <operations>: one statement of the division of labor (the model decides objects, containment, frames, and each update's sentence, op, targets and value; the renderer derives layout, camera, open or collapsed, dimming, and packet rests), followed by five short general rules in place of the 200-word block: On screen, In view, Drawn, Dimming, Fault marks.
- In view is now recursive: the frame, everything on screen inside it, and any data_packet resting on something in view, with its contents. A packet rests where its latest trace ended. A packet can therefore be opened where it lands (closes R3's major without a new special case).
- Drawn: a target's ancestors up to the frame open; everything else keeps its default (another beat's frame is collapsed). A callout or fault_marker sits on its parent's edge, so it shows without opening a collapsed parent (closes R4's major).
- Fault marks are cleared by an update, a dismiss, a trace through the object, or a morph or merge (R4 minor).
- An object that an operation would bring in but is already on screen stays where it is (R3 minor: branch or trace onto existing objects).
- The claims rule is one statement: every real-world statement, spoken or shown, gets a claim; generic part names need none. Invented values are marked as an example and only their arithmetic is claimed; the real behavior an example stands for gets an ordinary claim (R3 and R4 minors).
- value: "exactly one data object among its targets", matching the checker (R3 minor).
- target_duration_s is 60 to 600 in the prompt and in both schema locations; <pacing> says beats run up to about 90 words past about 450 s (R3 minor).
- memory_row and register draw a name key as a header (R4 minor). <state> for beat batches now includes where each data_packet rests.
- Checker: in-view is computed from on-screen containment plus packet rests (an arrow's `to` when a trace ends on an arrow; rests follow morph and merge to into); a trace clears fault marks; the shared-label warning skips data objects (R4 nit).
- Cuts: the zoom pattern list, the separate "Contrast alternatives" paragraph (folded into movement 7), planning step 7, the duplicated entrance paragraph in <output_format>, the trace-token sentence, the seconds in the pacing landmarks, several final_check items, and other trims.
Declined:
- A dismiss-then-reveal workaround for excluded morph (R3 alternative fix): the recursive in-view rule lets one packet object be opened at its destination, so the concept keeps one id.
- Bulleting the state block without other changes (R3 minor): the block is now bulleted as part of the rewrite, which covers it.
- Comparing data objects by name key in the shared-label warning: tried, but state tables whose first row is a field such as "name:" gave new false positives, so data objects are skipped instead.
- Default duration 420 s (R3 nit): kept, with the reason already stated.
Words: 4448 -> 4440

## Round 6: score 8.2 (min of 8.3 on R5, 8.2 on R6)
Top issues: a child of a data object could not name the cell it hangs from, so a bucket's chain or a page-table entry's frame had no layout anchor (R6 major). Minors: code_block line separator undefined; a packet resting on a data object versus that object's cell text; claim anchors for update-set text and for restatements; fault marks missing from the staged <state>; "data object" used before it was defined; one-new-concept versus a system_overview naming parts; "in view" read as description rather than requirement; trace start ambiguity; the 10% duration rule versus "don't pad"; state_table name key; parent and child in one reveal; the 20-word field limit hidden in goal. Executability held at 8 with the prompt at ~4,440 words.
Changes:
- New object field `cell` (schema $defs/object, required, pattern `^([0-9]+)?$`; prompt objects bullet; every example object): the 0-based cell of a data-object parent an object hangs from. The renderer lays such children out at their cell.
- <operations> opens by defining data objects; objects.label points back to it.
- In view: targets and entrants "must already be in view", so a packet resting outside the frame can't move; a packet resting on a data object sits beside the cell its trace value names and never changes the object's text; an arrow-ended trace rests at the arrow's to.
- On screen: a parent listed earlier in the same update counts.
- targets: a trace doesn't list where the packet already rests.
- label: a code_block's lines are separated by "\n"; the name-key rule is limited to memory_row and register (state_table rows are all rows).
- claims_to_verify: one entry per place a claim airs (restatements get their own); on-screen text set by an update uses that update's beat.
- <state> for beat batches includes which objects carry a fault mark.
- <pacing>: a system_overview may name several parts because its one new concept is the contract joining them (example b03 new_concept is now "decode before execute"); the 10% rule reads "unless the explanation is complete sooner: then don't pad it, and say so in notes".
- 20-word limit on goal, question, mechanism and note stated once in the <output_format> intro.
- Length: final_check collapsed from 11 items to one sentence of points that refer to earlier sections; output_format field definitions, narrative movements, explanation and visual_language paragraphs, semantic_zoom, planning steps 1, 3, 6, and the example intro compressed. No rule removed.
- Checker: `cell` must have a data-object parent and be in range of its declared content (error); an update that leaves an on-screen child's cell out of range errors; a trace listing the packet's current rest as its first place errors; a same-update parent must come earlier in the entering list; the name-key warning applies only to memory_row and register.
- Re-run: both round 6 plans now fail the schema only for the missing required `cell` (21 and 25 objects). With `cell: ""` added: R6 plan_fixed passes with 0 errors and 0 warnings (also with chain and flood_chain at cell "5"); R5 plan gets 4 new errors, all traces that list the packet's current rest first (b04 and b05, val_a/val_b), which the prompt now forbids, plus the existing shared-label warning.
Declined:
- A separate line for the 20-word limit after beats (R5 option): folded into the output_format intro instead, which states it once for all four fields at no extra length.
- Nits needing no change (R5 default duration and transition_out redundancy, R6 full-mode size): kept as written, as the reviewers advised.
Words: 4440 -> 3999
