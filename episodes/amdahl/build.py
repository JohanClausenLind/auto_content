"""Build the Amdahl's-law episode: pack from stored captures, locked script, visual spec."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from content_factory.explainer.authoring import (
    HOLD,
    PackDraft,
    ScriptDraft,
    SpecDraft,
    act,
    beat,
    capture_quotes,
    captured_page,
    cue_order_issues,
    link_captures,
    qty,
    write_episode,
)
from content_factory.explainer.validate import check_episode
from content_factory.schemas.explainer import (
    ChartTemplate,
    DatasetColumn,
    DiagramEdgeSpec,
    DiagramNodeSpec,
    DiagramTemplate,
    FieldEncoding,
    FilterAction,
    QuoteAction,
    Scene,
    SeriesBinding,
    ShowSourceAction,
    TitlePromise,
)
from content_factory.schemas.research import SourceClass

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCES = REPO / "output" / "explainer" / "sources"
DATE = "2026-09-26"
ILLUSTRATIVE = "illustrative job chosen for round numbers; not a measured program"
CORES = (1, 2, 4, 8, 16)
CURVE_CORES = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
CURVES = (
    ("half", 50.0, "Half serial"),
    ("fifth", 20.0, "A fifth serial"),
    ("tw", 5.0, "A twentieth serial"),
)

AMDAHL_URL = "https://en.wikipedia.org/wiki/Amdahl%27s_law"
GUSTAFSON_URL = "https://en.wikipedia.org/wiki/Gustafson%27s_law"
Q_DEF = (
    "is a formula limiting the speedup of a task as resources are added to the system executing "
    "that task"
)
Q_1967 = (
    "presented at the American Federation of Information Processing Societies (AFIPS) Spring "
    "Joint Computer Conference in 1967"
)
Q_EXAMPLE = (
    "The part that scans the directory and creates the file list cannot be sped up on a parallel "
    "computer, but the part that processes the files can."
)
Q_LIMITS = (
    "If exactly 50% of the work can be parallelized, the best possible speedup is 2 times. If 95% "
    "of the work can be parallelized, the best possible speedup is 20 times."
)
Q_FRACTION = (
    "the overall performance improvement gained by optimizing a single part of a system is limited "
    "by the fraction of time that the improved part is actually used."
)
Q_GUS = (
    "In contrast to Amdahl's law, which assumes a fixed problem size and yields pessimistic "
    "scaling, Gustafson's law assumes that problem sizes grow with available computing resources, "
    "allowing far greater effective speedup from parallel execution."
)
Q_GUS_SERIAL = (
    "Gustafson and his colleagues further observed from their workloads that time for the serial "
    "part typically does not grow as the problem and the system scale"
)
Q_GUS_1988 = 'Gustafson, John L. (May 1988). "Reevaluating Amdahl\'s Law"'
Q_OPT_FULL = (
    "if a programmer enhances a part of the code that represents 10% of the total execution time "
    "(i.e. Time optimized of 0.10) and achieves a Speedup optimized of 10,000, then Speedup overall "
    "becomes 1.11 which means only 11% improvement in total speedup of the program."
)
Q_OPT_SMALL = (
    "So, despite a massive improvement in one section, the overall benefit is quite small."
)


def build() -> None:
    pack = PackDraft(
        pack_id="pk_amdahl_2026",
        topic="Why a twice-as-fast computer does not halve the wait",
        checked_at=DATE,
    )
    amdahl = captured_page("amdahl", AMDAHL_URL, "Amdahl's law")
    gustafson = captured_page("gustafson", GUSTAFSON_URL, "Gustafson's law")
    pack.source("src_wiki_amdahl", amdahl, source_class=SourceClass.reference)
    pack.source("src_wiki_gustafson", gustafson, source_class=SourceClass.reference)
    for item, source, text in (
        ("ev_amdahl_def", "src_wiki_amdahl", Q_DEF),
        ("ev_amdahl_1967", "src_wiki_amdahl", Q_1967),
        ("ev_amdahl_example", "src_wiki_amdahl", Q_EXAMPLE),
        ("ev_amdahl_limits", "src_wiki_amdahl", Q_LIMITS),
        ("ev_amdahl_fraction", "src_wiki_amdahl", Q_FRACTION),
        ("ev_gus_contrast", "src_wiki_gustafson", Q_GUS),
        ("ev_gus_serial", "src_wiki_gustafson", Q_GUS_SERIAL),
        ("ev_gus_1988", "src_wiki_gustafson", Q_GUS_1988),
        ("ev_amdahl_optimize", "src_wiki_amdahl", Q_OPT_FULL),
        ("ev_amdahl_small", "src_wiki_amdahl", Q_OPT_SMALL),
    ):
        pack.passage(item, source, text)

    why = "stated in the captured Wikipedia article"
    pack.observation(
        "clm_amdahl_def",
        "Amdahl's law is a formula limiting the speedup of a task as resources are added.",
        ["ev_amdahl_def"],
        rationale=why,
        short_label="Amdahl's law",
    )
    pack.observation(
        "clm_amdahl_1967",
        "Gene Amdahl presented the argument at the AFIPS Spring Joint Computer Conference in 1967.",
        ["ev_amdahl_1967"],
        rationale=why,
        time_basis="1967",
    )
    pack.observation(
        "clm_amdahl_example",
        "In a file-processing program, building the file list cannot be sped up in parallel; processing the files can.",
        ["ev_amdahl_example"],
        rationale=why,
    )
    pack.observation(
        "clm_frac_50pc",
        "Half of the work can be parallelized in the article's first example.",
        ["ev_amdahl_limits"],
        rationale=why,
        value=qty(50, "%"),
    )
    pack.observation(
        "clm_limit_50pc",
        "With 50% parallelizable work the best possible speedup is 2 times.",
        ["ev_amdahl_limits"],
        rationale=why,
        value=qty(2, "ratio"),
        short_label="50% parallel: at most 2×",
        consequential=True,
    )
    pack.observation(
        "clm_frac_95pc",
        "95% of the work can be parallelized in the article's second example.",
        ["ev_amdahl_limits"],
        rationale=why,
        value=qty(95, "%"),
    )
    pack.observation(
        "clm_limit_95pc",
        "With 95% parallelizable work the best possible speedup is 20 times.",
        ["ev_amdahl_limits"],
        rationale=why,
        value=qty(20, "ratio"),
        short_label="95% parallel: at most 20×",
        consequential=True,
    )
    pack.observation(
        "clm_amdahl_fraction",
        "Improving one part of a system is limited by the fraction of time that part is used.",
        ["ev_amdahl_fraction"],
        rationale=why,
    )
    pack.observation(
        "clm_gus_contrast",
        "Gustafson's law assumes problem sizes grow with computing resources, unlike Amdahl's fixed size.",
        ["ev_gus_contrast"],
        rationale=why,
    )
    pack.observation(
        "clm_gus_serial",
        "Gustafson observed that serial time typically does not grow as problem and system scale.",
        ["ev_gus_serial"],
        rationale=why,
    )
    pack.observation(
        "clm_gus_1988",
        "Gustafson published 'Reevaluating Amdahl's Law' in May 1988.",
        ["ev_gus_1988"],
        rationale=why,
        time_basis="1988",
    )

    pack.assumption(
        "clm_serial_20s",
        "The illustrative job spends 20 seconds building its file list.",
        qty(20, "s"),
        short_label="Serial part: 20 s",
        rationale=ILLUSTRATIVE,
    )
    pack.assumption(
        "clm_parallel_80s",
        "The illustrative job spends 80 seconds processing files on one core.",
        qty(80, "s"),
        short_label="Parallel part: 80 s",
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_total_1core",
        "On one core the illustrative job takes 100 seconds.",
        "add",
        ["clm_serial_20s", "clm_parallel_80s"],
        rationale=ILLUSTRATIVE,
        short_label="One core: 100 s",
    )
    for n in sorted({*CORES, *CURVE_CORES}):
        pack.assumption(
            f"clm_cores_{n:04d}" if n >= 1000 else f"clm_cores_{n:03d}",
            f"The illustrative machine has {n} core{'s' if n > 1 else ''}.",
            qty(n, "count"),
            short_label=f"{n} cores",
            rationale=ILLUSTRATIVE,
        )
    for n in (2, 4, 8, 16, 64):
        pack.derive(
            f"clm_par_{n:03d}c",
            f"With {n} cores the parallel part takes 80/{n} seconds.",
            "div",
            ["clm_parallel_80s", qty(n, "ratio")],
            rationale=ILLUSTRATIVE,
        )
        pack.derive(
            f"clm_tot_{n:03d}c",
            f"With {n} cores the job takes 20 seconds plus its parallel share.",
            "add",
            ["clm_serial_20s", f"clm_par_{n:03d}c"],
            rationale=ILLUSTRATIVE,
            short_label=f"{n} cores",
        )
        pack.derive(
            f"clm_spd_{n:03d}c",
            f"With {n} cores the job runs 100 seconds divided by its new time faster.",
            "div",
            ["clm_total_1core", f"clm_tot_{n:03d}c"],
            rationale=ILLUSTRATIVE,
            unit="ratio",
        )
    pack.derive(
        "clm_ceiling_5x",
        "The illustrative job can never run more than 100/20 = 5 times faster.",
        "div",
        ["clm_total_1core", "clm_serial_20s"],
        rationale=ILLUSTRATIVE,
        unit="ratio",
        short_label="Ceiling: 5×",
    )
    pack.derive(
        "clm_gain_16_64",
        "Going from 16 to 64 cores saves 25 minus 21.25 seconds.",
        "sub",
        ["clm_tot_016c", "clm_tot_064c"],
        rationale=ILLUSTRATIVE,
    )
    pack.assumption(
        "clm_serial_fast",
        "After rewriting the folder scan, the serial part takes 10 seconds.",
        qty(10, "s"),
        short_label="Faster scan: 10 s",
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_tot_fast8",
        "With the faster scan and 8 cores the job takes 10 plus 10 seconds.",
        "add",
        ["clm_serial_fast", "clm_par_008c"],
        rationale=ILLUSTRATIVE,
        short_label="Faster scan, 8 cores",
    )
    pack.derive(
        "clm_ceiling_10x",
        "With a 10-second serial part the job can never beat 100/10 = 10 times faster.",
        "div",
        ["clm_total_1core", "clm_serial_fast"],
        rationale=ILLUSTRATIVE,
        unit="ratio",
        short_label="New ceiling: 10×",
    )
    pack.observation(
        "clm_opt_share",
        "The article's example improves a part taking 10% of the running time.",
        ["ev_amdahl_optimize"],
        rationale=why,
        value=qty(10, "%"),
    )
    pack.observation(
        "clm_opt_factor",
        "The article's example makes that part 10,000 times faster.",
        ["ev_amdahl_optimize"],
        rationale=why,
        value=qty(10000, "ratio"),
    )
    pack.observation(
        "clm_opt_overall",
        "The article's example program becomes only 1.11 times faster overall.",
        ["ev_amdahl_optimize"],
        rationale=why,
        value=qty(1.11, "ratio"),
        short_label="Whole program: 1.11×",
    )
    pack.observation(
        "clm_opt_gain",
        "The article's example gains only 11% in total speedup.",
        ["ev_amdahl_optimize"],
        rationale=why,
        value=qty(11, "%"),
    )
    pack.observation(
        "clm_opt_small",
        "Despite a massive improvement in one section, the overall benefit is small.",
        ["ev_amdahl_small"],
        rationale=why,
    )
    pack.derive(
        "clm_opt_rest",
        "The untouched part of the example is 100% minus 10% of the running time.",
        "sub",
        [qty(100, "%"), "clm_opt_share"],
        rationale="our check of the article's arithmetic",
    )
    pack.derive(
        "clm_opt_part",
        "The improved part shrinks to 10% divided by 10,000.",
        "div",
        ["clm_opt_share", "clm_opt_factor"],
        rationale="our check of the article's arithmetic",
    )
    pack.derive(
        "clm_opt_denom",
        "The new running time is the untouched part plus the shrunken part.",
        "add",
        ["clm_opt_rest", "clm_opt_part"],
        rationale="our check of the article's arithmetic",
    )
    pack.derive(
        "clm_opt_model",
        "Our recomputation of the example's overall speedup.",
        "div",
        [qty(100, "%"), "clm_opt_denom"],
        rationale="our check of the article's arithmetic",
        unit="ratio",
    )
    pack.derive(
        "clm_save_1to2",
        "Going from 1 to 2 cores saves 100 minus 60 seconds.",
        "sub",
        ["clm_total_1core", "clm_tot_002c"],
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_save_8to16",
        "Going from 8 to 16 cores saves 30 minus 25 seconds.",
        "sub",
        ["clm_tot_008c", "clm_tot_016c"],
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_serial_2xcore",
        "On a core twice as fast the serial part takes 20/2 seconds.",
        "div",
        ["clm_serial_20s", qty(2, "ratio")],
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_par_2xcore",
        "On a core twice as fast the parallel part takes 80/2 seconds.",
        "div",
        ["clm_parallel_80s", qty(2, "ratio")],
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_tot_2xcore",
        "On one core twice as fast the job takes 10 plus 40 seconds.",
        "add",
        ["clm_serial_2xcore", "clm_par_2xcore"],
        rationale=ILLUSTRATIVE,
        short_label="Core twice as fast",
    )
    pack.derive(
        "clm_gus_par8",
        "Eight times as many files make 80 times 8 seconds of processing.",
        "mul",
        ["clm_parallel_80s", qty(8, "ratio")],
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_gus_onecore",
        "The bigger job takes 20 plus 640 seconds on one core.",
        "add",
        ["clm_serial_20s", "clm_gus_par8"],
        rationale=ILLUSTRATIVE,
        short_label="Bigger job, one core",
    )
    pack.derive(
        "clm_gus_share8",
        "Split over 8 cores, the bigger job's processing takes 640/8 seconds.",
        "div",
        ["clm_gus_par8", qty(8, "ratio")],
        rationale=ILLUSTRATIVE,
    )
    pack.derive(
        "clm_gus_eight",
        "The bigger job takes 20 plus 80 seconds on 8 cores.",
        "add",
        ["clm_serial_20s", "clm_gus_share8"],
        rationale=ILLUSTRATIVE,
        short_label="Bigger job, 8 cores",
    )
    pack.derive(
        "clm_gus_scaled",
        "Against one core, the bigger job runs 660/100 times faster on 8 cores.",
        "div",
        ["clm_gus_onecore", "clm_gus_eight"],
        rationale=ILLUSTRATIVE,
        unit="ratio",
    )

    curve_rows = []
    for key, serial, _label in CURVES:
        sid = f"clm_cv_{key}_serial"
        pack.assumption(
            sid,
            f"Comparison job '{key}': {serial:g} of its 100 seconds are serial.",
            qty(serial, "s"),
            rationale=ILLUSTRATIVE,
        )
        pid = f"clm_cv_{key}_par"
        pack.derive(
            pid,
            f"Comparison job '{key}': 100 seconds minus its serial part.",
            "sub",
            ["clm_total_1core", sid],
            rationale=ILLUSTRATIVE,
        )
        for n in CURVE_CORES:
            tid, spid = f"clm_cv_{key}_t{n:04d}", f"clm_cv_{key}_s{n:04d}"
            pack.derive(
                f"clm_cv_{key}_p{n:04d}",
                f"Comparison job '{key}': parallel part on {n} cores.",
                "div",
                [pid, qty(n, "ratio")],
                rationale=ILLUSTRATIVE,
            )
            pack.derive(
                tid,
                f"Comparison job '{key}': total time on {n} cores.",
                "add",
                [sid, f"clm_cv_{key}_p{n:04d}"],
                rationale=ILLUSTRATIVE,
            )
            speed = pack.derive(
                spid,
                f"Comparison job '{key}': speedup on {n} cores.",
                "div",
                ["clm_total_1core", tid],
                rationale=ILLUSTRATIVE,
                unit="ratio",
            )
            curve_rows.append(
                (
                    f"{key}-{n}",
                    [key, f"{n:,}", float(n), speed.magnitude],
                    [f"clm_cores_{n:04d}" if n >= 1000 else f"clm_cores_{n:03d}", spid],
                )
            )

    ds_cores = pack.dataset(
        "ds_amdahl_cores",
        "The illustrative job on 1 to 16 cores",
        [
            DatasetColumn(name="label", kind="nominal"),
            DatasetColumn(name="cores", kind="quantitative", unit="count"),
            DatasetColumn(name="part", kind="nominal"),
            DatasetColumn(name="seconds", kind="quantitative", unit="s"),
        ],
        [
            row
            for n in CORES
            for row in (
                (
                    f"{n}-serial",
                    [f"{n} core{'s' if n > 1 else ''}", float(n), "serial", 20.0],
                    ["clm_serial_20s", f"clm_cores_{n:03d}"],
                ),
                (
                    f"{n}-parallel",
                    [f"{n} core{'s' if n > 1 else ''}", float(n), "parallel", 80.0 / n],
                    ["clm_parallel_80s" if n == 1 else f"clm_par_{n:03d}c", f"clm_cores_{n:03d}"],
                ),
            )
        ],
    )
    ds_curves = pack.dataset(
        "ds_amdahl_curves",
        "Speedup of three comparison jobs (illustrative)",
        [
            DatasetColumn(name="job", kind="nominal"),
            DatasetColumn(name="cores_label", kind="ordinal"),
            DatasetColumn(name="cores", kind="quantitative", unit="count"),
            DatasetColumn(name="speedup", kind="quantitative", unit="ratio"),
        ],
        curve_rows,
    )
    ds_upgrades = pack.dataset(
        "ds_amdahl_upgrades",
        "Two upgrades from 8 cores (illustrative)",
        [
            DatasetColumn(name="option", kind="nominal"),
            DatasetColumn(name="part", kind="nominal"),
            DatasetColumn(name="seconds", kind="quantitative", unit="s"),
        ],
        [
            ("now-serial", ["8 cores today", "serial", 20.0], ["clm_serial_20s"]),
            ("now-parallel", ["8 cores today", "parallel", 10.0], ["clm_par_008c"]),
            ("double-serial", ["16 cores", "serial", 20.0], ["clm_serial_20s"]),
            ("double-parallel", ["16 cores", "parallel", 5.0], ["clm_par_016c"]),
            ("scan-serial", ["8 cores, faster scan", "serial", 10.0], ["clm_serial_fast"]),
            ("scan-parallel", ["8 cores, faster scan", "parallel", 10.0], ["clm_par_008c"]),
        ],
    )
    frozen = pack.build(frozen_at=f"{DATE}T09:00:00Z")

    cap_a = capture_quotes(
        amdahl,
        "src_wiki_amdahl",
        [
            (Q_DEF, ["clm_amdahl_def"]),
            (Q_1967, ["clm_amdahl_1967"]),
            (Q_EXAMPLE, ["clm_amdahl_example"]),
            (Q_LIMITS, ["clm_limit_50pc", "clm_limit_95pc"]),
            (Q_FRACTION, ["clm_amdahl_fraction"]),
            (Q_OPT_SMALL, ["clm_opt_small"]),
        ],
    )
    cap_g = capture_quotes(
        gustafson,
        "src_wiki_gustafson",
        [
            (Q_GUS, ["clm_gus_contrast"]),
            (Q_GUS_SERIAL, ["clm_gus_serial"]),
        ],
    )
    man_a, man_g = cap_a.manifest, cap_g.manifest
    frozen = link_captures(frozen, {"src_wiki_amdahl": cap_a, "src_wiki_gustafson": cap_g})

    s = ScriptDraft()
    s.say(
        "seg_amdahl_hook_one",
        "cold_open",
        "You replace a four-core computer with an eight-core one. The photo export that took 40 seconds now takes 30.",
        claims=["clm_tot_004c", "clm_tot_008c"],
    )
    s.say(
        "seg_amdahl_hook_two",
        "cold_open",
        "Twice the cores bought you a quarter off the wait, not half. That gap is not a flaw in your new machine. It is arithmetic, and it has a name.",
    )
    s.say(
        "seg_amdahl_question",
        "question_stakes",
        "So here is the question. If a computer is twice as fast, why does the work not finish in half the time?",
    )
    s.say(
        "seg_amdahl_stakes",
        "question_stakes",
        "The answer decides whether a faster laptop, a bigger cloud server or a stack of graphics cards is money well spent. It takes one idea and one formula to see it.",
    )
    s.say(
        "seg_amdahl_def",
        "build_model",
        "Engineers call it Amdahl's law. Wikipedia describes it as a formula limiting the speedup of a task as resources are added to the system executing that task.",
        claims=["clm_amdahl_def"],
    )
    s.say(
        "seg_amdahl_1967",
        "build_model",
        "It is named after the computer scientist Gene Amdahl, who presented it at a conference in 1967.",
        claims=["clm_amdahl_1967"],
    )
    s.say(
        "seg_amdahl_program",
        "build_model",
        "The idea is easiest to see in a real program. Take one that processes a folder of files. First it scans the folder and builds a list of every file. Then it hands each file to a separate worker, and the workers process their files at the same time.",
    )
    s.say(
        "seg_amdahl_example",
        "build_model",
        "The encyclopedia puts the key point plainly. The part that scans the directory and creates the file list cannot be sped up on a parallel computer, but the part that processes the files can.",
        claims=["clm_amdahl_example"],
    )
    s.say(
        "seg_amdahl_numbers",
        "build_model",
        "Let us give that program some illustrative numbers. On a single core, building the list takes 20 seconds, and processing the files takes 80 seconds. That is 100 seconds in total.",
        claims=["clm_serial_20s", "clm_parallel_80s", "clm_total_1core"],
    )
    s.say(
        "seg_amdahl_kinds",
        "build_model",
        "The 20 seconds are serial: one step after another, however many cores you own. The 80 seconds are parallel: they can be split into equal shares. Almost every real job has a serial part like this, in reading the input, handing out the work, or collecting the results at the end. You meet serial steps like these every day: an app that must read its settings before it can draw its first screen, or a spreadsheet that must finish one column before the next column can use it.",
        claims=["clm_serial_20s", "clm_parallel_80s"],
    )
    s.say(
        "seg_amdahl_two",
        "run_system",
        "With 2 cores, the parallel part splits in half, from 80 seconds to 40. Add the 20 serial seconds, and the job takes 60.",
        claims=[
            "clm_cores_002",
            "clm_parallel_80s",
            "clm_par_002c",
            "clm_serial_20s",
            "clm_tot_002c",
        ],
    )
    s.say(
        "seg_amdahl_four",
        "run_system",
        "With 4 cores, the parallel part takes 20 seconds, and the job takes 40.",
        claims=["clm_cores_004", "clm_par_004c", "clm_tot_004c"],
    )
    s.say(
        "seg_amdahl_eight",
        "run_system",
        "With 8 cores, 10 seconds of parallel work and 30 in total. With 16 cores, 5 seconds of parallel work and 25 in total.",
        claims=[
            "clm_cores_008",
            "clm_par_008c",
            "clm_tot_008c",
            "clm_cores_016",
            "clm_par_016c",
            "clm_tot_016c",
        ],
    )
    s.say(
        "seg_amdahl_watch",
        "run_system",
        "Now watch the serial bar. It never moves. Every doubling of cores halves a part that keeps getting smaller, while the 20 serial seconds stay exactly where they were.",
        claims=["clm_serial_20s"],
    )
    s.say(
        "seg_amdahl_speedup",
        "run_system",
        "Speedup is the old time divided by the new one. 2 cores give about 1.7 times. 4 cores give 2.5 times. 8 cores give about 3.3 times, and 16 cores give 4 times.",
        claims=[
            "clm_cores_002",
            "clm_spd_002c",
            "clm_cores_004",
            "clm_spd_004c",
            "clm_cores_008",
            "clm_spd_008c",
            "clm_cores_016",
            "clm_spd_016c",
        ],
    )
    s.say(
        "seg_amdahl_savings",
        "run_system",
        "Each doubling still helps, but less than the one before. Going from 1 core to 2 saved 40 seconds. Going from 8 cores to 16 saved just 5.",
        claims=[
            "clm_cores_001",
            "clm_cores_002",
            "clm_save_1to2",
            "clm_cores_008",
            "clm_cores_016",
            "clm_save_8to16",
        ],
    )
    s.say(
        "seg_amdahl_ceiling",
        "run_system",
        "And there is a ceiling. With endlessly many cores, the parallel part shrinks towards nothing, but the serial 20 seconds remain. 100 seconds divided by 20 is 5. This program can never run more than 5 times faster, whatever you buy.",
        claims=["clm_serial_20s", "clm_total_1core", "clm_ceiling_5x"],
    )
    s.say(
        "seg_amdahl_diminish",
        "run_system",
        "Past 16 cores the gains get tiny. At 64 cores the job still takes about 21 seconds, just 4 seconds better than at 16.",
        claims=["clm_cores_016", "clm_cores_064", "clm_tot_064c", "clm_gain_16_64"],
    )
    s.say(
        "seg_amdahl_formula",
        "run_system",
        "That reasoning fits in one line. Call the parallel share of the work p, and the number of cores N. The serial share is one minus p. The parallel share, spread over N cores, becomes p divided by N. The speedup is one divided by their sum.",
    )
    s.say(
        "seg_amdahl_curves",
        "run_system",
        "Now compare three programs that differ only in how much of their work is serial. In the first, half of it is. In ours, a fifth. In the third, only a twentieth. Plot their speedup as the cores double, from 1 all the way to 1,024.",
        claims=["clm_cores_001", "clm_cores_1024", "clm_cv_fifth_s1024"],
    )
    s.say(
        "seg_amdahl_plateau",
        "run_system",
        "Every curve bends and flattens. The program that is half serial never passes 2 times. Ours flattens towards 5. The third keeps climbing longest, and still levels off below 20.",
        claims=["clm_limit_50pc", "clm_ceiling_5x", "clm_limit_95pc"],
    )
    s.say(
        "seg_amdahl_limits",
        "run_system",
        "Those ceilings are not our invention. The same article states that if exactly 50% of the work can be parallelized, the best possible speedup is 2 times. If 95% of the work can be parallelized, the best possible speedup is 20 times.",
        claims=["clm_frac_50pc", "clm_limit_50pc", "clm_frac_95pc", "clm_limit_95pc"],
    )
    s.say(
        "seg_amdahl_rule",
        "run_system",
        "Our curves head for exactly those limits, because the ceiling is always one divided by the serial share.",
        claims=["clm_limit_50pc", "clm_limit_95pc"],
    )
    s.say(
        "seg_amdahl_upgrades",
        "change_variable",
        "So what should you change? Take our program on 8 cores, at 30 seconds, and try two upgrades.",
        claims=["clm_cores_008", "clm_tot_008c"],
    )
    s.say(
        "seg_amdahl_double",
        "change_variable",
        "Upgrade one: double the cores to 16. The job drops to 25 seconds.",
        claims=["clm_cores_016", "clm_tot_016c"],
    )
    s.say(
        "seg_amdahl_rewrite",
        "change_variable",
        "Upgrade two: keep 8 cores, but rewrite the folder scan so it takes 10 seconds instead of 20. Now the job takes 20 seconds.",
        claims=["clm_cores_008", "clm_serial_fast", "clm_serial_20s", "clm_tot_fast8"],
    )
    s.say(
        "seg_amdahl_moved",
        "change_variable",
        "Halving the serial part beat doubling the hardware, and it did something more important: it moved the ceiling. With 10 serial seconds, the job can never beat 10 seconds, which is 10 times faster than the original 100.",
        claims=["clm_serial_fast", "clm_ceiling_10x", "clm_total_1core"],
    )
    s.say(
        "seg_amdahl_fraction",
        "change_variable",
        "The article sums up the general rule: the overall performance improvement gained by optimizing a single part of a system is limited by the fraction of time that the improved part is actually used.",
        claims=["clm_amdahl_fraction"],
    )
    s.say(
        "seg_amdahl_wrongpart",
        "change_variable",
        "The article backs this with a sharp example. Take a part of the code that accounts for 10% of the running time, and make it 10,000 times faster. The whole program becomes only 1.11 times faster, an 11% improvement.",
        claims=[
            "clm_opt_share",
            "clm_opt_factor",
            "clm_opt_overall",
            "clm_opt_gain",
            "clm_opt_model",
        ],
    )
    s.say(
        "seg_amdahl_small",
        "change_variable",
        "Or, as the article concludes: so, despite a massive improvement in one section, the overall benefit is quite small.",
        claims=["clm_opt_small"],
    )
    s.say(
        "seg_amdahl_clock",
        "change_variable",
        "There is one upgrade that does halve the time, and it takes us back to the opening question. Instead of adding cores, make every core twice as fast. The serial 20 seconds become 10, the parallel 80 become 40, and the job takes 50 seconds, exactly half of the 100 we started with. A faster core speeds up the serial part too. More cores never touch it.",
        claims=[
            "clm_serial_20s",
            "clm_serial_2xcore",
            "clm_parallel_80s",
            "clm_par_2xcore",
            "clm_tot_2xcore",
            "clm_total_1core",
            "clm_tot_002c",
        ],
    )
    s.say(
        "seg_amdahl_fixed",
        "show_limits",
        "Amdahl's law is not the whole story. It assumes the job stays the same size while you add cores.",
        claims=["clm_gus_contrast"],
    )
    s.say(
        "seg_amdahl_gustafson",
        "show_limits",
        "In 1988, John Gustafson argued that people rarely keep it that way. In the words of the article on his law: in contrast to Amdahl's law, which assumes a fixed problem size and yields pessimistic scaling, Gustafson's law assumes that problem sizes grow with available computing resources, allowing far greater effective speedup from parallel execution.",
        claims=["clm_gus_1988", "clm_gus_contrast"],
    )
    s.say(
        "seg_amdahl_gus_serial",
        "show_limits",
        "He and his colleagues observed that the time for the serial part typically does not grow as the problem and the system scale. Give the same cores a bigger folder, and more cores buy you more work done in the same time, rather than the same work done faster.",
        claims=["clm_gus_serial"],
    )
    s.say(
        "seg_amdahl_gusworked",
        "show_limits",
        "Try it on our program. Give the 8 cores eight times as many files. The processing now adds up to 640 seconds of single-core work, so one core alone would need 660 seconds in all. Split across the 8 cores, the processing takes 80 seconds, and the whole job still finishes in 100. Against one core, that is a speedup of 6.6 times, already past the ceiling of 5 we found for the fixed-size job.",
        claims=[
            "clm_cores_008",
            "clm_gus_par8",
            "clm_gus_onecore",
            "clm_gus_share8",
            "clm_gus_eight",
            "clm_gus_scaled",
            "clm_ceiling_5x",
        ],
    )
    s.say(
        "seg_amdahl_leaves_out",
        "show_limits",
        "Our model also leaves things out: the cost of coordinating cores, of sharing memory and of moving data. Every one of those only adds time, so these curves are the best case, not a forecast.",
    )
    s.say(
        "seg_amdahl_answer",
        "synthesis",
        "So why does twice the computer not give you half the time? Because some part of the work can only happen one step at a time, and that part does not care how many cores you have. Twice the cores halves only the part that can be shared. A core twice as fast halves all of it.",
    )
    s.say(
        "seg_amdahl_advice",
        "synthesis",
        "Before you pay for more cores, find that part and measure it. Its share of the running time is your ceiling, and shrinking it is often the cheapest speedup you will ever buy.",
    )
    promises = [
        TitlePromise(
            title="Twice the cores, not half the wait",
            thumbnail_promise="40 s becomes 30 s, not 20 s: the arithmetic behind it",
            claim_ids=("clm_amdahl_def", "clm_tot_004c", "clm_tot_008c"),
        ),
        TitlePromise(
            title="The one number that caps every speedup",
            thumbnail_promise="95% parallel code tops out at 20×",
            claim_ids=("clm_frac_95pc", "clm_limit_95pc"),
        ),
        TitlePromise(
            title="Why more cores stop helping",
            thumbnail_promise="Half-serial work never passes 2×, whatever you buy",
            claim_ids=("clm_frac_50pc", "clm_limit_50pc"),
        ),
    ]
    script = s.build(
        frozen,
        script_id="scr_amdahl_ep01",
        channel_id="ch_explainer_demo",
        question="If a computer is twice as fast, why does the work not finish in half the time?",
        contribution="A worked model of a file-processing job that shows how its serial part caps every speedup, compares doubling the cores with shrinking the serial part, and sets Amdahl's fixed-size view against Gustafson's growing workloads.",
        promises=promises,
        locked_at=f"{DATE}T10:00:00Z",
    )

    v = SpecDraft(spec_id="vs_amdahl_ep01")
    ser = v.entity(
        "ent_amdahl_serial_bar",
        "Serial part",
        "series",
        short_label="Serial",
        claims=["clm_serial_20s"],
    )
    par = v.entity(
        "ent_amdahl_parallel_bar",
        "Parallel part",
        "series",
        short_label="Parallel",
        claims=["clm_parallel_80s"],
    )
    a_cores = v.dataset_asset("ast_amdahl_cores", ds_cores)
    a_curves = v.dataset_asset("ast_amdahl_curves", ds_curves)
    a_upg = v.dataset_asset("ast_amdahl_upgrades", ds_upgrades)
    a_wiki = v.capture_asset("ast_wiki_amdahl", man_a.capture_id, man_a.artifact_sha256)
    a_gus = v.capture_asset("ast_wiki_gustafson", man_g.capture_id, man_g.artifact_sha256)

    hold = HOLD
    v.text_scene(
        "scn_amdahl_hook_times",
        "cold_open",
        "Show the disappointing upgrade as two plain times.",
        "list",
        [
            ("ent_amdahl_hook_four", "Four cores: 40 seconds", "clm_tot_004c"),
            ("ent_amdahl_hook_eight", "Eight cores: 30 seconds", "clm_tot_008c"),
        ],
        [
            beat(
                "bt_amdahl_hook_four",
                s.cue("seg_amdahl_hook_one", "40"),
                act("reveal", "ent_amdahl_hook_four"),
            ),
            beat(
                "bt_amdahl_hook_eight",
                s.cue("seg_amdahl_hook_one", "30"),
                act("reveal", "ent_amdahl_hook_eight"),
            ),
            beat(
                "bt_amdahl_hook_times_hold",
                s.cue("seg_amdahl_hook_one", "30", relation="after", duration="short"),
                hold,
            ),
        ],
        claims=["clm_tot_004c", "clm_tot_008c"],
    )
    v.text_scene(
        "scn_amdahl_hook_line",
        "cold_open",
        "Name the gap between expectation and result.",
        "statement",
        [("ent_amdahl_hook_line", "Twice the cores. Not half the wait.", None)],
        [
            beat(
                "bt_amdahl_hook_line",
                s.cue("seg_amdahl_hook_two", "Twice"),
                act("reveal", "ent_amdahl_hook_line"),
            ),
            beat(
                "bt_amdahl_hook_hold",
                s.cue("seg_amdahl_hook_two", "name", relation="after", duration="medium"),
                hold,
            ),
        ],
    )
    v.text_scene(
        "scn_amdahl_question",
        "question_stakes",
        "Pose the episode's question.",
        "statement",
        [
            (
                "ent_amdahl_question",
                "If a computer is twice as fast, why doesn't the work finish in half the time?",
                None,
            )
        ],
        [
            beat(
                "bt_amdahl_question",
                s.cue("seg_amdahl_question", "If"),
                act("reveal", "ent_amdahl_question"),
            ),
            beat(
                "bt_amdahl_question_hold",
                s.cue("seg_amdahl_question", "time?", relation="after", duration="medium"),
                hold,
            ),
        ],
    )
    v.text_scene(
        "scn_amdahl_stakes",
        "question_stakes",
        "Show where the answer costs money.",
        "list",
        [
            ("ent_amdahl_stake_laptop", "A faster laptop", None),
            ("ent_amdahl_stake_cloud", "A bigger cloud server", None),
            ("ent_amdahl_stake_gpu", "A stack of graphics cards", None),
        ],
        [
            beat(
                "bt_amdahl_stake_laptop",
                s.cue("seg_amdahl_stakes", "laptop,"),
                act("reveal", "ent_amdahl_stake_laptop"),
            ),
            beat(
                "bt_amdahl_stake_cloud",
                s.cue("seg_amdahl_stakes", "cloud"),
                act("reveal", "ent_amdahl_stake_cloud"),
            ),
            beat(
                "bt_amdahl_stake_gpu",
                s.cue("seg_amdahl_stakes", "graphics"),
                act("reveal", "ent_amdahl_stake_gpu"),
            ),
            beat(
                "bt_amdahl_stake_hold",
                s.cue("seg_amdahl_stakes", "formula", relation="after", duration="short"),
                hold,
            ),
        ],
    )
    qd, q67 = cap_a.quote_id(Q_DEF), cap_a.quote_id(Q_1967)
    v.source_scene(
        "scn_amdahl_src_definition",
        "build_model",
        "Show the law's definition and origin on the real article.",
        a_wiki,
        cap_a,
        Q_DEF,
        [
            beat(
                "bt_amdahl_def_show",
                s.cue("seg_amdahl_def", "Engineers"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_amdahl_def_focus",
                s.cue("seg_amdahl_def", "describes"),
                QuoteAction(action="focus_passage", quote_id=qd),
            ),
            beat(
                "bt_amdahl_def_mark",
                s.cue("seg_amdahl_def", "formula"),
                QuoteAction(action="highlight_quote", quote_id=qd),
            ),
            beat(
                "bt_amdahl_def_hold",
                s.cue("seg_amdahl_def", "task.", occurrence=-1, relation="after", duration="short"),
                hold,
            ),
            beat("bt_amdahl_1967_clear", s.cue("seg_amdahl_1967", "named"), act("clear_highlight")),
            beat(
                "bt_amdahl_1967_focus",
                s.cue("seg_amdahl_1967", "Gene"),
                QuoteAction(action="focus_passage", quote_id=q67),
            ),
            beat(
                "bt_amdahl_1967_hold",
                s.cue("seg_amdahl_1967", "1967.", relation="after", duration="medium"),
                hold,
            ),
        ],
        claims=["clm_amdahl_def", "clm_amdahl_1967"],
    )
    nodes = [
        ("ent_amdahl_node_scan", "Scan the folder"),
        ("ent_amdahl_node_list", "Build the file list"),
        ("ent_amdahl_node_w1", "Worker 1"),
        ("ent_amdahl_node_w2", "Worker 2"),
        ("ent_amdahl_node_w3", "Worker 3"),
        ("ent_amdahl_node_done", "Done"),
    ]
    for eid, label in nodes:
        v.entity(eid, label, "node")
    edges = [
        ("ent_amdahl_edge_sl", "ent_amdahl_node_scan", "ent_amdahl_node_list"),
        ("ent_amdahl_edge_l1", "ent_amdahl_node_list", "ent_amdahl_node_w1"),
        ("ent_amdahl_edge_l2", "ent_amdahl_node_list", "ent_amdahl_node_w2"),
        ("ent_amdahl_edge_l3", "ent_amdahl_node_list", "ent_amdahl_node_w3"),
        ("ent_amdahl_edge_1d", "ent_amdahl_node_w1", "ent_amdahl_node_done"),
        ("ent_amdahl_edge_2d", "ent_amdahl_node_w2", "ent_amdahl_node_done"),
        ("ent_amdahl_edge_3d", "ent_amdahl_node_w3", "ent_amdahl_node_done"),
    ]
    for eid, a, b in edges:
        v.entity(eid, f"{a} to {b}"[:80], "edge")
    v.add(
        Scene(
            scene_id="scn_amdahl_program_flow",
            section="build_model",
            purpose="Draw the program as one serial path feeding parallel workers.",
            template=DiagramTemplate(
                template="diagram",
                direction="LR",
                nodes=tuple(DiagramNodeSpec(entity_id=e, label=text) for e, text in nodes),
                edges=tuple(
                    DiagramEdgeSpec(entity_id=e, source_entity_id=a, target_entity_id=b)
                    for e, a, b in edges
                ),
            ),
            beats=(
                beat(
                    "bt_amdahl_flow_scan",
                    s.cue("seg_amdahl_program", "scans"),
                    act("reveal", "ent_amdahl_node_scan"),
                ),
                beat(
                    "bt_amdahl_flow_list",
                    s.cue("seg_amdahl_program", "list"),
                    act("reveal", "ent_amdahl_node_list", "ent_amdahl_edge_sl"),
                ),
                beat(
                    "bt_amdahl_flow_workers",
                    s.cue("seg_amdahl_program", "worker,"),
                    act(
                        "reveal",
                        "ent_amdahl_node_w1",
                        "ent_amdahl_node_w2",
                        "ent_amdahl_node_w3",
                        "ent_amdahl_edge_l1",
                        "ent_amdahl_edge_l2",
                        "ent_amdahl_edge_l3",
                    ),
                ),
                beat(
                    "bt_amdahl_flow_done",
                    s.cue("seg_amdahl_program", "same"),
                    act(
                        "reveal",
                        "ent_amdahl_node_done",
                        "ent_amdahl_edge_1d",
                        "ent_amdahl_edge_2d",
                        "ent_amdahl_edge_3d",
                    ),
                    act("flow", "ent_amdahl_edge_l1", "ent_amdahl_edge_l2", "ent_amdahl_edge_l3"),
                ),
                beat(
                    "bt_amdahl_flow_trace",
                    s.cue("seg_amdahl_program", "time.", relation="after", duration="medium"),
                    act(
                        "trace",
                        "ent_amdahl_node_scan",
                        "ent_amdahl_edge_sl",
                        "ent_amdahl_node_list",
                    ),
                ),
                beat(
                    "bt_amdahl_flow_hold",
                    s.cue(
                        "seg_amdahl_program",
                        "time.",
                        occurrence=-1,
                        relation="after",
                        duration="medium",
                    ),
                    hold,
                ),
            ),
        )
    )
    qe = cap_a.quote_id(Q_EXAMPLE)
    v.source_scene(
        "scn_amdahl_src_example",
        "build_model",
        "Quote the article's own serial-versus-parallel example.",
        a_wiki,
        cap_a,
        Q_EXAMPLE,
        [
            beat(
                "bt_amdahl_ex_show",
                s.cue("seg_amdahl_example", "encyclopedia"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_amdahl_ex_focus",
                s.cue("seg_amdahl_example", "plainly."),
                QuoteAction(action="focus_passage", quote_id=qe),
            ),
            beat(
                "bt_amdahl_ex_mark",
                s.cue("seg_amdahl_example", "scans"),
                QuoteAction(action="highlight_quote", quote_id=qe),
            ),
            beat(
                "bt_amdahl_ex_hold",
                s.cue("seg_amdahl_example", "can.", relation="after", duration="medium"),
                hold,
            ),
        ],
        claims=["clm_amdahl_example"],
    )
    bars = ChartTemplate(
        template="chart",
        chart_kind="stacked_bar",
        title="The job's time on more cores (illustrative)",
        dataset_asset_id=a_cores,
        x=FieldEncoding(field="label", kind="nominal"),
        y=FieldEncoding(field="seconds", kind="quantitative", unit="s", title="seconds"),
        series_field="part",
        series=(
            SeriesBinding(value="serial", entity_id=ser),
            SeriesBinding(value="parallel", entity_id=par),
        ),
    )
    v.add(
        Scene(
            scene_id="scn_amdahl_bars_single",
            section="build_model",
            purpose="Build the job on one core as serial plus parallel time.",
            template=bars,
            beats=(
                beat(
                    "bt_amdahl_bars_serial",
                    s.cue("seg_amdahl_numbers", "20"),
                    act("reveal", ser),
                    FilterAction(action="filter", field="cores", op="lte", value=1),
                ),
                beat(
                    "bt_amdahl_bars_parallel", s.cue("seg_amdahl_numbers", "80"), act("reveal", par)
                ),
                beat(
                    "bt_amdahl_bars_serial_hi",
                    s.cue("seg_amdahl_kinds", "serial:"),
                    act("highlight", ser),
                ),
                beat(
                    "bt_amdahl_bars_parallel_hi",
                    s.cue("seg_amdahl_kinds", "parallel:"),
                    act("clear_highlight", ser),
                    act("highlight", par),
                ),
                beat(
                    "bt_amdahl_bars_clear",
                    s.cue("seg_amdahl_kinds", "Almost"),
                    act("clear_highlight"),
                    act("highlight", ser),
                ),
                beat(
                    "bt_amdahl_bars_single_hold",
                    s.cue(
                        "seg_amdahl_kinds", "it.", occurrence=-1, relation="after", duration="short"
                    ),
                    hold,
                ),
            ),
            claim_ids=("clm_serial_20s", "clm_parallel_80s", "clm_total_1core"),
        )
    )
    v.add(
        Scene(
            scene_id="scn_amdahl_bars_cores",
            section="run_system",
            purpose="Add cores bar by bar while the serial part stays put.",
            template=bars,
            initial_visible=(ser, par),
            beats=(
                beat(
                    "bt_amdahl_bars_two",
                    s.cue("seg_amdahl_two", "2"),
                    FilterAction(action="filter", field="cores", op="lte", value=2),
                ),
                beat(
                    "bt_amdahl_bars_four",
                    s.cue("seg_amdahl_four", "4"),
                    FilterAction(action="filter", field="cores", op="lte", value=4),
                ),
                beat(
                    "bt_amdahl_bars_eight",
                    s.cue("seg_amdahl_eight", "8"),
                    FilterAction(action="filter", field="cores", op="lte", value=8),
                ),
                beat(
                    "bt_amdahl_bars_sixteen",
                    s.cue("seg_amdahl_eight", "16"),
                    FilterAction(action="filter", field="cores", op="lte", value=16),
                ),
                beat(
                    "bt_amdahl_bars_watch",
                    s.cue("seg_amdahl_watch", "serial"),
                    act("highlight", ser),
                ),
                beat(
                    "bt_amdahl_bars_hold",
                    s.cue("seg_amdahl_watch", "were.", relation="after", duration="medium"),
                    hold,
                ),
            ),
            claim_ids=(
                "clm_serial_20s",
                "clm_parallel_80s",
                "clm_par_002c",
                "clm_tot_002c",
                "clm_cores_002",
                "clm_cores_004",
                "clm_par_004c",
                "clm_tot_004c",
                "clm_cores_008",
                "clm_par_008c",
                "clm_tot_008c",
                "clm_cores_016",
                "clm_par_016c",
                "clm_tot_016c",
            ),
        )
    )
    v.text_scene(
        "scn_amdahl_speedups",
        "run_system",
        "Turn the times into speedups, one line at a time.",
        "list",
        [
            ("ent_amdahl_spd_two", "Two cores: about 1.7 times faster", "clm_spd_002c"),
            ("ent_amdahl_spd_four", "Four cores: 2.5 times faster", "clm_spd_004c"),
            ("ent_amdahl_spd_eight", "Eight cores: about 3.3 times faster", "clm_spd_008c"),
            ("ent_amdahl_spd_sixteen", "Sixteen cores: 4 times faster", "clm_spd_016c"),
        ],
        [
            beat(
                "bt_amdahl_spd_two",
                s.cue("seg_amdahl_speedup", "1.7"),
                act("reveal", "ent_amdahl_spd_two"),
            ),
            beat(
                "bt_amdahl_spd_four",
                s.cue("seg_amdahl_speedup", "2.5"),
                act("reveal", "ent_amdahl_spd_four"),
            ),
            beat(
                "bt_amdahl_spd_eight",
                s.cue("seg_amdahl_speedup", "3.3"),
                act("reveal", "ent_amdahl_spd_eight"),
            ),
            beat(
                "bt_amdahl_spd_sixteen",
                s.cue("seg_amdahl_speedup", "times.", occurrence=3),
                act("reveal", "ent_amdahl_spd_sixteen"),
            ),
            beat(
                "bt_amdahl_spd_hold",
                s.cue(
                    "seg_amdahl_speedup",
                    "times.",
                    occurrence=3,
                    relation="after",
                    duration="medium",
                ),
                hold,
            ),
        ],
        claims=[
            "clm_spd_002c",
            "clm_spd_004c",
            "clm_spd_008c",
            "clm_spd_016c",
            "clm_cores_001",
            "clm_cores_002",
            "clm_cores_004",
            "clm_cores_008",
            "clm_cores_016",
            "clm_save_1to2",
            "clm_save_8to16",
        ],
    )
    v.text_scene(
        "scn_amdahl_ceiling",
        "run_system",
        "State the job's hard ceiling as one number.",
        "big_number",
        [
            ("ent_amdahl_ceiling_num", "5×", "clm_ceiling_5x"),
            ("ent_amdahl_ceiling_label", "the most this job can ever gain", None),
        ],
        [
            beat(
                "bt_amdahl_ceiling_num",
                s.cue("seg_amdahl_ceiling", "5.", occurrence=0),
                act("reveal", "ent_amdahl_ceiling_num"),
            ),
            beat(
                "bt_amdahl_ceiling_label",
                s.cue("seg_amdahl_ceiling", "never"),
                act("reveal", "ent_amdahl_ceiling_label"),
            ),
            beat(
                "bt_amdahl_ceiling_hold",
                s.cue("seg_amdahl_ceiling", "buy.", relation="after", duration="medium"),
                hold,
            ),
        ],
        claims=["clm_ceiling_5x", "clm_serial_20s", "clm_total_1core"],
    )
    v.text_scene(
        "scn_amdahl_diminish",
        "run_system",
        "Show how little 48 more cores buy.",
        "big_number",
        [
            ("ent_amdahl_dim_num", "21.25 s", "clm_tot_064c"),
            ("ent_amdahl_dim_label", "on 64 cores, against 25 s on 16", None),
        ],
        [
            beat(
                "bt_amdahl_dim_num",
                s.cue("seg_amdahl_diminish", "21"),
                act("reveal", "ent_amdahl_dim_num"),
            ),
            beat(
                "bt_amdahl_dim_label",
                s.cue("seg_amdahl_diminish", "better"),
                act("reveal", "ent_amdahl_dim_label"),
            ),
            beat(
                "bt_amdahl_dim_hold",
                s.cue(
                    "seg_amdahl_diminish", "16.", occurrence=-1, relation="after", duration="short"
                ),
                hold,
            ),
        ],
        claims=["clm_tot_064c", "clm_gain_16_64", "clm_cores_064", "clm_cores_016"],
    )
    formula = [
        ("ent_amdahl_f_lhs", r"S = 1 \div \big[", None),
        ("ent_amdahl_f_serial", r"(1-p)", None),
        ("ent_amdahl_f_parallel", r"+\ \tfrac{p}{N}", None),
        ("ent_amdahl_f_close", r"\big]", None),
    ]
    v.text_scene(
        "scn_amdahl_formula",
        "run_system",
        "Write the law as one line and light up each term as it is named.",
        "formula",
        formula,
        [
            beat(
                "bt_amdahl_f_show",
                s.cue("seg_amdahl_formula", "line."),
                act(
                    "reveal",
                    "ent_amdahl_f_lhs",
                    "ent_amdahl_f_serial",
                    "ent_amdahl_f_parallel",
                    "ent_amdahl_f_close",
                ),
            ),
            beat(
                "bt_amdahl_f_serial",
                s.cue("seg_amdahl_formula", "minus"),
                act("highlight", "ent_amdahl_f_serial"),
            ),
            beat(
                "bt_amdahl_f_parallel",
                s.cue("seg_amdahl_formula", "spread"),
                act("clear_highlight", "ent_amdahl_f_serial"),
                act("highlight", "ent_amdahl_f_parallel"),
            ),
            beat(
                "bt_amdahl_f_sum",
                s.cue("seg_amdahl_formula", "sum."),
                act("clear_highlight"),
                act("highlight", "ent_amdahl_f_serial", "ent_amdahl_f_parallel"),
            ),
            beat(
                "bt_amdahl_f_hold",
                s.cue("seg_amdahl_formula", "sum.", relation="after", duration="medium"),
                hold,
            ),
        ],
    )
    curve_ents = {
        key: v.entity(f"ent_amdahl_curve_{key}", label, "series", short_label=label)
        for key, _s, label in CURVES
    }
    curves = ChartTemplate(
        template="chart",
        chart_kind="line",
        title="Speedup as cores double (illustrative)",
        dataset_asset_id=a_curves,
        x=FieldEncoding(field="cores_label", kind="ordinal", title="cores"),
        y=FieldEncoding(field="speedup", kind="quantitative", unit="ratio", title="times faster"),
        series_field="job",
        series=tuple(SeriesBinding(value=key, entity_id=e) for key, e in curve_ents.items()),
    )
    v.add(
        Scene(
            scene_id="scn_amdahl_curves",
            section="run_system",
            purpose="Compare three jobs whose only difference is their serial share.",
            template=curves,
            beats=(
                beat(
                    "bt_amdahl_cv_half",
                    s.cue("seg_amdahl_curves", "first,"),
                    act("reveal", curve_ents["half"]),
                ),
                beat(
                    "bt_amdahl_cv_fifth",
                    s.cue("seg_amdahl_curves", "ours,"),
                    act("reveal", curve_ents["fifth"]),
                ),
                beat(
                    "bt_amdahl_cv_tw",
                    s.cue("seg_amdahl_curves", "third,"),
                    act("reveal", curve_ents["tw"]),
                ),
                beat(
                    "bt_amdahl_cv_bend",
                    s.cue("seg_amdahl_plateau", "flattens."),
                    act("highlight", curve_ents["half"]),
                ),
                beat(
                    "bt_amdahl_cv_ours",
                    s.cue("seg_amdahl_plateau", "Ours"),
                    act("clear_highlight"),
                    act("highlight", curve_ents["fifth"]),
                ),
                beat(
                    "bt_amdahl_cv_third",
                    s.cue("seg_amdahl_plateau", "third"),
                    act("clear_highlight"),
                    act("highlight", curve_ents["tw"]),
                ),
                beat(
                    "bt_amdahl_cv_hold",
                    s.cue("seg_amdahl_plateau", "20.", relation="after", duration="medium"),
                    hold,
                ),
            ),
            claim_ids=(
                "clm_cores_001",
                "clm_cores_1024",
                "clm_cv_fifth_s1024",
                "clm_limit_50pc",
                "clm_ceiling_5x",
                "clm_limit_95pc",
            ),
        )
    )
    ql = cap_a.quote_id(Q_LIMITS)
    v.source_scene(
        "scn_amdahl_src_limits",
        "run_system",
        "Show that the article states the same ceilings.",
        a_wiki,
        cap_a,
        Q_LIMITS,
        [
            beat(
                "bt_amdahl_lim_show",
                s.cue("seg_amdahl_limits", "invention."),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_amdahl_lim_focus",
                s.cue("seg_amdahl_limits", "states"),
                QuoteAction(action="focus_passage", quote_id=ql),
            ),
            beat(
                "bt_amdahl_lim_mark",
                s.cue("seg_amdahl_limits", "50%"),
                QuoteAction(action="highlight_quote", quote_id=ql),
            ),
            beat(
                "bt_amdahl_lim_hold",
                s.cue(
                    "seg_amdahl_limits", "times.", occurrence=1, relation="after", duration="medium"
                ),
                hold,
            ),
        ],
        claims=["clm_frac_50pc", "clm_limit_50pc", "clm_frac_95pc", "clm_limit_95pc"],
    )
    v.text_scene(
        "scn_amdahl_rule",
        "run_system",
        "State the general rule the curves obey.",
        "statement",
        [("ent_amdahl_rule", "The ceiling is always one divided by the serial share.", None)],
        [
            beat(
                "bt_amdahl_rule",
                s.cue("seg_amdahl_rule", "ceiling"),
                act("reveal", "ent_amdahl_rule"),
            ),
            beat(
                "bt_amdahl_rule_hold",
                s.cue("seg_amdahl_rule", "share.", relation="after", duration="medium"),
                hold,
            ),
        ],
        claims=["clm_limit_50pc", "clm_limit_95pc"],
    )
    upg = ChartTemplate(
        template="chart",
        chart_kind="stacked_bar",
        title="Two upgrades from 8 cores (illustrative)",
        dataset_asset_id=a_upg,
        x=FieldEncoding(field="option", kind="nominal"),
        y=FieldEncoding(field="seconds", kind="quantitative", unit="s", title="seconds"),
        series_field="part",
        series=(
            SeriesBinding(value="serial", entity_id=ser),
            SeriesBinding(value="parallel", entity_id=par),
        ),
    )
    v.add(
        Scene(
            scene_id="scn_amdahl_upgrades",
            section="change_variable",
            purpose="Compare doubling the cores with halving the serial part.",
            template=upg,
            beats=(
                beat(
                    "bt_amdahl_upg_now",
                    s.cue("seg_amdahl_upgrades", "30"),
                    act("reveal", ser, par),
                    FilterAction(action="filter", field="option", op="eq", value="8 cores today"),
                ),
                beat(
                    "bt_amdahl_upg_double",
                    s.cue("seg_amdahl_double", "16."),
                    FilterAction(
                        action="filter", field="option", op="neq", value="8 cores, faster scan"
                    ),
                ),
                beat(
                    "bt_amdahl_upg_scan",
                    s.cue("seg_amdahl_rewrite", "rewrite"),
                    FilterAction(action="filter", field="option", op="neq", value="none"),
                ),
                beat(
                    "bt_amdahl_upg_serial", s.cue("seg_amdahl_rewrite", "10"), act("highlight", ser)
                ),
                beat(
                    "bt_amdahl_upg_hold",
                    s.cue(
                        "seg_amdahl_rewrite",
                        "seconds.",
                        occurrence=1,
                        relation="after",
                        duration="medium",
                    ),
                    hold,
                ),
            ),
            claim_ids=(
                "clm_cores_008",
                "clm_tot_008c",
                "clm_cores_016",
                "clm_tot_016c",
                "clm_serial_fast",
                "clm_serial_20s",
                "clm_tot_fast8",
            ),
        )
    )
    v.text_scene(
        "scn_amdahl_new_ceiling",
        "change_variable",
        "Show that the smaller serial part raised the ceiling.",
        "big_number",
        [
            ("ent_amdahl_new_ceiling", "10×", "clm_ceiling_10x"),
            ("ent_amdahl_new_ceiling_label", "the new ceiling after the faster scan", None),
        ],
        [
            beat(
                "bt_amdahl_newc_num",
                s.cue("seg_amdahl_moved", "ceiling."),
                act("reveal", "ent_amdahl_new_ceiling"),
            ),
            beat(
                "bt_amdahl_newc_label",
                s.cue("seg_amdahl_moved", "original"),
                act("reveal", "ent_amdahl_new_ceiling_label"),
            ),
            beat(
                "bt_amdahl_newc_hold",
                s.cue("seg_amdahl_moved", "100.", relation="after", duration="short"),
                hold,
            ),
        ],
        claims=["clm_ceiling_10x", "clm_serial_fast", "clm_total_1core"],
    )
    qf = cap_a.quote_id(Q_FRACTION)
    v.source_scene(
        "scn_amdahl_src_fraction",
        "change_variable",
        "Quote the article's general rule.",
        a_wiki,
        cap_a,
        Q_FRACTION,
        [
            beat(
                "bt_amdahl_fr_show",
                s.cue("seg_amdahl_fraction", "article"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_amdahl_fr_focus",
                s.cue("seg_amdahl_fraction", "rule:"),
                QuoteAction(action="focus_passage", quote_id=qf),
            ),
            beat(
                "bt_amdahl_fr_mark",
                s.cue("seg_amdahl_fraction", "improvement"),
                QuoteAction(action="highlight_quote", quote_id=qf),
            ),
            beat(
                "bt_amdahl_fr_hold",
                s.cue("seg_amdahl_fraction", "used.", relation="after", duration="medium"),
                hold,
            ),
        ],
        claims=["clm_amdahl_fraction"],
    )
    v.text_scene(
        "scn_amdahl_wrongpart",
        "change_variable",
        "Lay out the article's optimize-the-wrong-part example.",
        "list",
        [
            ("ent_amdahl_opt_share", "Part improved: 10% of the running time", "clm_opt_share"),
            ("ent_amdahl_opt_factor", "Made 10,000 times faster", "clm_opt_factor"),
            ("ent_amdahl_opt_overall", "Whole program: 1.11 times faster", "clm_opt_overall"),
        ],
        [
            beat(
                "bt_amdahl_opt_share",
                s.cue("seg_amdahl_wrongpart", "10%"),
                act("reveal", "ent_amdahl_opt_share"),
            ),
            beat(
                "bt_amdahl_opt_factor",
                s.cue("seg_amdahl_wrongpart", "10,000"),
                act("reveal", "ent_amdahl_opt_factor"),
            ),
            beat(
                "bt_amdahl_opt_overall",
                s.cue("seg_amdahl_wrongpart", "1.11"),
                act("reveal", "ent_amdahl_opt_overall"),
            ),
            beat(
                "bt_amdahl_opt_hold",
                s.cue("seg_amdahl_wrongpart", "improvement.", relation="after", duration="short"),
                hold,
            ),
        ],
        claims=[
            "clm_opt_share",
            "clm_opt_factor",
            "clm_opt_overall",
            "clm_opt_gain",
            "clm_opt_model",
        ],
    )
    qs = cap_a.quote_id(Q_OPT_SMALL)
    v.source_scene(
        "scn_amdahl_src_small",
        "change_variable",
        "Show the article's own conclusion to the example.",
        a_wiki,
        cap_a,
        Q_OPT_SMALL,
        [
            beat(
                "bt_amdahl_small_show",
                s.cue("seg_amdahl_small", "Or,"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_amdahl_small_focus",
                s.cue("seg_amdahl_small", "concludes:"),
                QuoteAction(action="focus_passage", quote_id=qs),
            ),
            beat(
                "bt_amdahl_small_mark",
                s.cue("seg_amdahl_small", "despite"),
                QuoteAction(action="highlight_quote", quote_id=qs),
            ),
            beat(
                "bt_amdahl_small_hold",
                s.cue("seg_amdahl_small", "small.", relation="after", duration="medium"),
                hold,
            ),
        ],
        claims=["clm_opt_small"],
    )
    v.text_scene(
        "scn_amdahl_clock",
        "change_variable",
        "Contrast more cores with faster cores.",
        "list",
        [
            ("ent_amdahl_clock_cores", "Twice the cores: 60 seconds", "clm_tot_002c"),
            ("ent_amdahl_clock_fast", "Each core twice as fast: 50 seconds", "clm_tot_2xcore"),
        ],
        [
            beat(
                "bt_amdahl_clock_cores",
                s.cue("seg_amdahl_clock", "adding"),
                act("reveal", "ent_amdahl_clock_cores"),
            ),
            beat(
                "bt_amdahl_clock_fast",
                s.cue("seg_amdahl_clock", "50"),
                act("reveal", "ent_amdahl_clock_fast"),
            ),
            beat(
                "bt_amdahl_clock_hi",
                s.cue("seg_amdahl_clock", "faster", occurrence=-1),
                act("highlight", "ent_amdahl_clock_fast"),
            ),
            beat(
                "bt_amdahl_clock_hold",
                s.cue("seg_amdahl_clock", "it.", occurrence=-1, relation="after", duration="short"),
                hold,
            ),
        ],
        claims=[
            "clm_tot_002c",
            "clm_tot_2xcore",
            "clm_serial_2xcore",
            "clm_par_2xcore",
            "clm_total_1core",
            "clm_serial_20s",
            "clm_parallel_80s",
        ],
    )
    v.text_scene(
        "scn_amdahl_fixed_size",
        "show_limits",
        "Name the assumption the law makes.",
        "statement",
        [("ent_amdahl_fixed", "Amdahl assumes the job stays the same size.", None)],
        [
            beat(
                "bt_amdahl_fixed",
                s.cue("seg_amdahl_fixed", "assumes"),
                act("reveal", "ent_amdahl_fixed"),
            ),
            beat(
                "bt_amdahl_fixed_hold",
                s.cue("seg_amdahl_fixed", "cores.", relation="after", duration="short"),
                hold,
            ),
        ],
        claims=["clm_gus_contrast"],
    )
    qg, qgs = cap_g.quote_id(Q_GUS), cap_g.quote_id(Q_GUS_SERIAL)
    v.source_scene(
        "scn_amdahl_src_gustafson",
        "show_limits",
        "Set Gustafson's growing workloads against Amdahl's fixed size.",
        a_gus,
        cap_g,
        Q_GUS,
        [
            beat(
                "bt_amdahl_gus_show",
                s.cue("seg_amdahl_gustafson", "Gustafson"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_amdahl_gus_focus",
                s.cue("seg_amdahl_gustafson", "words"),
                QuoteAction(action="focus_passage", quote_id=qg),
            ),
            beat(
                "bt_amdahl_gus_mark",
                s.cue("seg_amdahl_gustafson", "contrast"),
                QuoteAction(action="highlight_quote", quote_id=qg),
            ),
            beat(
                "bt_amdahl_gus_hold",
                s.cue("seg_amdahl_gustafson", "execution.", relation="after", duration="short"),
                hold,
            ),
            beat(
                "bt_amdahl_gus_clear", s.cue("seg_amdahl_gus_serial", "He"), act("clear_highlight")
            ),
            beat(
                "bt_amdahl_gus_focus2",
                s.cue("seg_amdahl_gus_serial", "observed"),
                QuoteAction(action="focus_passage", quote_id=qgs),
            ),
            beat(
                "bt_amdahl_gus_mark2",
                s.cue("seg_amdahl_gus_serial", "serial"),
                QuoteAction(action="highlight_quote", quote_id=qgs),
            ),
            beat(
                "bt_amdahl_gus_hold2",
                s.cue("seg_amdahl_gus_serial", "faster.", relation="after", duration="medium"),
                hold,
            ),
        ],
        claims=["clm_gus_1988", "clm_gus_contrast", "clm_gus_serial"],
    )
    v.text_scene(
        "scn_amdahl_gus_worked",
        "show_limits",
        "Run Gustafson's view on our own program.",
        "list",
        [
            ("ent_amdahl_gus_one", "Bigger job on one core: 660 seconds", "clm_gus_onecore"),
            ("ent_amdahl_gus_eight", "Bigger job on eight cores: 100 seconds", "clm_gus_eight"),
            ("ent_amdahl_gus_scaled", "Scaled speedup: 6.6 times", "clm_gus_scaled"),
        ],
        [
            beat(
                "bt_amdahl_gusw_one",
                s.cue("seg_amdahl_gusworked", "660"),
                act("reveal", "ent_amdahl_gus_one"),
            ),
            beat(
                "bt_amdahl_gusw_eight",
                s.cue("seg_amdahl_gusworked", "100."),
                act("reveal", "ent_amdahl_gus_eight"),
            ),
            beat(
                "bt_amdahl_gusw_scaled",
                s.cue("seg_amdahl_gusworked", "6.6"),
                act("reveal", "ent_amdahl_gus_scaled"),
            ),
            beat(
                "bt_amdahl_gusw_hold",
                s.cue(
                    "seg_amdahl_gusworked",
                    "job.",
                    occurrence=-1,
                    relation="after",
                    duration="short",
                ),
                hold,
            ),
        ],
        claims=[
            "clm_cores_008",
            "clm_gus_par8",
            "clm_gus_onecore",
            "clm_gus_share8",
            "clm_gus_eight",
            "clm_gus_scaled",
            "clm_ceiling_5x",
        ],
    )
    v.text_scene(
        "scn_amdahl_leaves_out",
        "show_limits",
        "List what the ideal model ignores.",
        "list",
        [
            ("ent_amdahl_lo_coord", "Coordinating the cores", None),
            ("ent_amdahl_lo_memory", "Sharing memory", None),
            ("ent_amdahl_lo_data", "Moving data", None),
            ("ent_amdahl_lo_best", "So these curves are the best case", None),
        ],
        [
            beat(
                "bt_amdahl_lo_coord",
                s.cue("seg_amdahl_leaves_out", "coordinating"),
                act("reveal", "ent_amdahl_lo_coord"),
            ),
            beat(
                "bt_amdahl_lo_memory",
                s.cue("seg_amdahl_leaves_out", "memory"),
                act("reveal", "ent_amdahl_lo_memory"),
            ),
            beat(
                "bt_amdahl_lo_data",
                s.cue("seg_amdahl_leaves_out", "data."),
                act("reveal", "ent_amdahl_lo_data"),
            ),
            beat(
                "bt_amdahl_lo_best",
                s.cue("seg_amdahl_leaves_out", "best"),
                act("reveal", "ent_amdahl_lo_best"),
            ),
            beat(
                "bt_amdahl_lo_hold",
                s.cue("seg_amdahl_leaves_out", "forecast.", relation="after", duration="short"),
                hold,
            ),
        ],
    )
    v.text_scene(
        "scn_amdahl_answer",
        "synthesis",
        "Answer the question in one sentence.",
        "statement",
        [("ent_amdahl_answer", "Some work can only happen one step at a time.", None)],
        [
            beat(
                "bt_amdahl_answer",
                s.cue("seg_amdahl_answer", "Because"),
                act("reveal", "ent_amdahl_answer"),
            ),
            beat(
                "bt_amdahl_answer_hold",
                s.cue("seg_amdahl_answer", "have.", relation="after", duration="short"),
                hold,
            ),
        ],
    )
    v.text_scene(
        "scn_amdahl_advice",
        "synthesis",
        "Leave the viewer with the practical rule.",
        "statement",
        [("ent_amdahl_advice", "Find the serial part. Its share is your ceiling.", None)],
        [
            beat(
                "bt_amdahl_advice",
                s.cue("seg_amdahl_advice", "find"),
                act("reveal", "ent_amdahl_advice"),
            ),
            beat(
                "bt_amdahl_advice_hold",
                s.cue("seg_amdahl_advice", "buy.", relation="after", duration="long"),
                hold,
            ),
        ],
    )
    spec = v.build(frozen, script)
    order = cue_order_issues(spec, script)
    if order:
        raise SystemExit("\n".join(order))
    issues = check_episode(frozen, script, spec, manifests=(man_a, man_g))
    if issues:
        raise SystemExit("\n".join(str(i) for i in issues))

    print(json.dumps(write_episode(HERE, frozen, script, spec, [cap_a, cap_g])))


if __name__ == "__main__":
    sys.exit(build())
