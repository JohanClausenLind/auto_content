# Test requests

The reviewer uses request number `round mod 6` (round 1 uses R1, and round 7 wraps back to R1),
exactly as written. Two reviewers in the same round use two consecutive requests.

## R1

<request>
<topic>Why does 0.1 + 0.2 not equal 0.3 in JavaScript?</topic>
<target_duration_s>240</target_duration_s>
</request>

## R2

<request>
<topic>page faults</topic>
<target_duration_s>420</target_duration_s>
<audience>Developers who know C but have never looked at the OS memory manager</audience>
</request>

## R3

<request>
<topic>How does HTTPS know the site is really your bank?</topic>
<target_duration_s>300</target_duration_s>
<renderer_constraints>No graph_line, graph_bar or morph yet.</renderer_constraints>
</request>

## R4

<request>
<topic>Why does an SSD slow down when it's nearly full?</topic>
<target_duration_s>180</target_duration_s>
<source_notes>NAND flash is written in pages but erased in blocks of many pages. A page can't be overwritten in place; the controller writes elsewhere and marks the old page stale. Garbage collection copies live pages out of a block before erasing it. Less free space means more copying per write (write amplification).</source_notes>
</request>

## R5

<request>
<topic>What actually happens when two threads increment the same counter?</topic>
<target_duration_s>300</target_duration_s>
<audience>Self-taught programmers</audience>
</request>

## R6

<request>
<topic>How does a hash table find a key without looking at every entry?</topic>
</request>
