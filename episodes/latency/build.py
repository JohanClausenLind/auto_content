"""Build the transatlantic-latency episode: pack from stored captures, locked script, visual spec."""

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
    Quantity,
    QuoteAction,
    Scene,
    SeriesBinding,
    ShowSourceAction,
    TitlePromise,
)
from content_factory.schemas.research import SourceClass

HERE = Path(__file__).resolve().parent
DATE = "2026-09-26"
WHY = "stated in the captured Wikipedia article"
OURS = "our arithmetic on the sourced inputs"

SOL_URL = "https://en.wikipedia.org/wiki/Speed_of_light"
FIBER_URL = "https://en.wikipedia.org/wiki/Optical_fiber"
MAREA_URL = "https://en.wikipedia.org/wiki/MAREA"
Q_C = (
    "The constant c is defined in the International System of Units (SI) as exactly 299792458 m/s."
)
Q_INDEX_FULL = (
    "A typical single-mode fiber used for telecommunications has a cladding made of pure silica, "
    "with an index of 1.444 at 1500 nm, and a core of doped silica with an index around 1.4475."
)
Q_INDEX = "a core of doped silica with an index around 1.4475"
Q_RULE = (
    "a simple rule of thumb is that a signal using optical fiber for communication will travel at "
    "around 200,000 kilometers per second."
)
Q_SYDNEY = (
    "Thus a phone call carried by fiber between Sydney and New York, a 16,000-kilometer distance, "
    "means that there is a minimum delay of 80 milliseconds"
)
Q_MAREA_LEN = (
    "MAREA is a 6,605 km (4,104 miles)[1] long transatlantic communications cable connecting the "
    "United States with Spain."
)
Q_MAREA_ENDS = (
    "connecting Virginia Beach, Virginia, in the United States, with Sopelana, a town near Bilbao, "
    "Spain."
)
ROUTES = (
    ("Virginia–Spain", "clm_lat_marea_len", "clm_lat_vac_oneway", "clm_lat_fib_oneway"),
    ("Sydney–New York", "clm_lat_syd_dist", "clm_lat_syd_vac_one", "clm_lat_syd_fib_one"),
)


def build() -> None:
    pack = PackDraft(
        pack_id="pk_latency_2026",
        topic="The physical floor under transatlantic latency",
        checked_at=DATE,
    )
    sol = captured_page("speed-of-light", SOL_URL, "Speed of light")
    fiber = captured_page("optical-fiber", FIBER_URL, "Optical fiber")
    marea = captured_page("marea", MAREA_URL, "MAREA")
    pack.source("src_wiki_speedlight", sol, source_class=SourceClass.reference)
    pack.source("src_wiki_fiber", fiber, source_class=SourceClass.reference)
    pack.source("src_wiki_marea", marea, source_class=SourceClass.reference)
    for item, source, text in (
        ("ev_lat_c_exact", "src_wiki_speedlight", Q_C),
        ("ev_lat_core_index", "src_wiki_fiber", Q_INDEX_FULL),
        ("ev_lat_rule_thumb", "src_wiki_fiber", Q_RULE),
        ("ev_lat_sydney_nyc", "src_wiki_fiber", Q_SYDNEY),
        ("ev_lat_marea_len", "src_wiki_marea", Q_MAREA_LEN),
        ("ev_lat_marea_ends", "src_wiki_marea", Q_MAREA_ENDS),
    ):
        pack.passage(item, source, text)

    pack.observation(
        "clm_lat_c_vacuum",
        "The speed of light in vacuum is defined as exactly 299,792,458 m/s.",
        ["ev_lat_c_exact"],
        rationale=WHY,
        value=qty(299792458, "m/s"),
        short_label="c = 299,792,458 m/s",
    )
    pack.observation(
        "clm_lat_core_index",
        "A typical telecom fibre core of doped silica has a refractive index around 1.4475.",
        ["ev_lat_core_index"],
        rationale=WHY,
        value=Quantity(magnitude=1.4475, unit="ratio", uncertainty="estimate"),
        short_label="Core index ≈ 1.4475",
    )
    pack.observation(
        "clm_lat_rule_speed",
        "A fibre signal travels at around 200,000 km/s as a rule of thumb.",
        ["ev_lat_rule_thumb"],
        rationale=WHY,
        value=Quantity(magnitude=200000, unit="km/s", uncertainty="estimate"),
    )
    pack.observation(
        "clm_lat_syd_dist",
        "Sydney to New York by fibre is a 16,000-kilometre distance in the article's example.",
        ["ev_lat_sydney_nyc"],
        rationale=WHY,
        value=qty(16000, "km"),
    )
    pack.observation(
        "clm_lat_syd_delay",
        "The article gives a minimum delay of 80 milliseconds for that call.",
        ["ev_lat_sydney_nyc"],
        rationale=WHY,
        value=qty(80, "ms"),
    )
    pack.observation(
        "clm_lat_marea_len",
        "MAREA is a 6,605 km transatlantic cable between the United States and Spain.",
        ["ev_lat_marea_len"],
        rationale=WHY,
        value=qty(6605, "km"),
        short_label="MAREA: 6,605 km",
    )
    pack.observation(
        "clm_lat_marea_ends",
        "MAREA connects Virginia Beach in the United States with Sopelana, near Bilbao, Spain.",
        ["ev_lat_marea_ends"],
        rationale=WHY,
    )

    pack.derive(
        "clm_lat_fiber_speed",
        "Light in that fibre travels at c divided by the core index.",
        "div",
        ["clm_lat_c_vacuum", "clm_lat_core_index"],
        rationale=OURS,
        unit="km/s",
        short_label="Speed in fibre",
    )
    pack.derive(
        "clm_lat_fiber_share",
        "Light in the fibre moves at this share of its vacuum speed.",
        "div",
        ["clm_lat_fiber_speed", "clm_lat_c_vacuum"],
        rationale=OURS,
        unit="%",
    )
    pack.derive(
        "clm_lat_fib_oneway",
        "Crossing MAREA's 6,605 km in fibre takes this long, one way.",
        "div",
        ["clm_lat_marea_len", "clm_lat_fiber_speed"],
        rationale=OURS,
        unit="ms",
        short_label="One way in fibre",
    )
    pack.derive(
        "clm_lat_fib_round",
        "The fastest possible round trip over MAREA in fibre.",
        "mul",
        ["clm_lat_fib_oneway", qty(2, "ratio")],
        rationale=OURS,
        short_label="Round trip in fibre",
    )
    pack.derive(
        "clm_lat_vac_oneway",
        "Crossing 6,605 km at the vacuum speed of light takes this long, one way.",
        "div",
        ["clm_lat_marea_len", "clm_lat_c_vacuum"],
        rationale=OURS,
        unit="ms",
    )
    pack.derive(
        "clm_lat_vac_round",
        "The round trip over 6,605 km at the vacuum speed of light.",
        "mul",
        ["clm_lat_vac_oneway", qty(2, "ratio")],
        rationale=OURS,
    )
    pack.derive(
        "clm_lat_glass_extra",
        "The glass adds this much to every MAREA round trip.",
        "sub",
        ["clm_lat_fib_round", "clm_lat_vac_round"],
        rationale=OURS,
    )
    pack.derive(
        "clm_lat_syd_check",
        "16,000 km at 200,000 km/s takes this long, one way.",
        "div",
        ["clm_lat_syd_dist", "clm_lat_rule_speed"],
        rationale=OURS,
        unit="ms",
    )
    pack.derive(
        "clm_lat_syd_fib_one",
        "Sydney to New York in fibre at c/1.4475, one way.",
        "div",
        ["clm_lat_syd_dist", "clm_lat_fiber_speed"],
        rationale=OURS,
        unit="ms",
    )
    pack.derive(
        "clm_lat_syd_fib_rt",
        "Sydney to New York in fibre, round trip.",
        "mul",
        ["clm_lat_syd_fib_one", qty(2, "ratio")],
        rationale=OURS,
    )
    pack.derive(
        "clm_lat_syd_vac_one",
        "Sydney to New York at the vacuum speed of light, one way.",
        "div",
        ["clm_lat_syd_dist", "clm_lat_c_vacuum"],
        rationale=OURS,
        unit="ms",
    )

    rows = []
    for route, _dist, vac, fib in ROUTES:
        for medium, claim in (("vacuum", vac), ("fibre", fib)):
            ms = pack.value(claim).magnitude
            for leg in ("there", "back"):
                rows.append(
                    (f"{route}-{medium}-{leg}", [route, f"{route}, {medium}", leg, ms], [claim])
                )
    ds_paths = pack.dataset(
        "ds_latency_routes",
        "Fastest possible travel times (computed)",
        [
            DatasetColumn(name="route", kind="nominal"),
            DatasetColumn(name="label", kind="nominal"),
            DatasetColumn(name="leg", kind="nominal"),
            DatasetColumn(name="ms", kind="quantitative", unit="ms"),
        ],
        rows,
    )
    frozen = pack.build(frozen_at=f"{DATE}T09:00:00Z")

    cap_sol = capture_quotes(sol, "src_wiki_speedlight", [(Q_C, ["clm_lat_c_vacuum"])])
    cap_fib = capture_quotes(
        fiber,
        "src_wiki_fiber",
        [
            (Q_INDEX, ["clm_lat_core_index"]),
            (Q_RULE, ["clm_lat_rule_speed"]),
            (Q_SYDNEY, ["clm_lat_syd_dist", "clm_lat_syd_delay"]),
        ],
    )
    cap_mar = capture_quotes(marea, "src_wiki_marea", [(Q_MAREA_ENDS, ["clm_lat_marea_ends"])])
    frozen = link_captures(
        frozen,
        {"src_wiki_speedlight": cap_sol, "src_wiki_fiber": cap_fib, "src_wiki_marea": cap_mar},
    )

    s = ScriptDraft()
    s.say(
        "seg_latency_hook",
        "cold_open",
        "Send a message from Virginia to Spain and wait for the reply. However good the network, that round trip can never take less than about 64 milliseconds.",
        claims=["clm_lat_fib_round"],
    )
    s.say(
        "seg_latency_hook_two",
        "cold_open",
        "That floor is not set by any engineer. It is set by light, and by the glass that light has to travel through.",
    )
    s.say(
        "seg_latency_question",
        "question_stakes",
        "So how fast can a signal really cross an ocean? The answer puts a limit under every video call, online game and cloud service that talks across the Atlantic.",
    )
    s.say(
        "seg_latency_vacuum",
        "build_model",
        "Start with light itself. Its speed in a vacuum is no longer measured; it is fixed. The International System of Units defines it as exactly 299,792,458 metres per second.",
        claims=["clm_lat_c_vacuum"],
    )
    s.say(
        "seg_latency_glass",
        "build_model",
        "But an undersea cable does not carry light through a vacuum. It carries it through glass, and glass slows light down. The Wikipedia article on optical fibre gives a typical telecom fibre a core of doped silica with an index around 1.4475.",
        claims=["clm_lat_core_index"],
    )
    s.say(
        "seg_latency_speed",
        "build_model",
        "The index tells you how many times slower light moves. Divide by it, and light in the fibre travels at about 207,000 kilometres per second, roughly 69% of its speed in a vacuum.",
        claims=["clm_lat_fiber_speed", "clm_lat_fiber_share", "clm_lat_core_index"],
    )
    s.say(
        "seg_latency_cable",
        "build_model",
        "Now pick a real cable. MAREA runs along the floor of the Atlantic from Virginia Beach in the United States to Sopelana in Spain. Its article gives its length as 6,605 kilometres.",
        claims=["clm_lat_marea_ends", "clm_lat_marea_len"],
    )
    s.say(
        "seg_latency_oneway",
        "build_model",
        "Time is distance divided by speed. 6,605 kilometres at about 207,000 kilometres per second takes 31.9 milliseconds, one way.",
        claims=["clm_lat_marea_len", "clm_lat_fiber_speed", "clm_lat_fib_oneway"],
    )
    s.say(
        "seg_latency_roundtrip",
        "build_model",
        "The reply has to come back the same way, so the fastest possible round trip is about 64 milliseconds.",
        claims=["clm_lat_fib_round"],
    )
    s.say(
        "seg_latency_compare",
        "run_system",
        "Compare that with light in a vacuum. Over the same distance it would need 22.0 milliseconds each way, or 44.1 for the round trip. The glass alone adds almost 20 milliseconds to every exchange.",
        claims=[
            "clm_lat_vac_oneway",
            "clm_lat_vac_round",
            "clm_lat_glass_extra",
            "clm_lat_fib_oneway",
            "clm_lat_fib_round",
        ],
    )
    s.say(
        "seg_latency_rule",
        "run_system",
        "The fibre article offers the same arithmetic as a rule of thumb: a signal using optical fiber for communication will travel at around 200,000 kilometers per second.",
        claims=["clm_lat_rule_speed"],
    )
    s.say(
        "seg_latency_sydney",
        "run_system",
        "Its own example is a phone call between Sydney and New York, a distance of 16,000 kilometres, with a minimum delay of 80 milliseconds. Our arithmetic gives the same answer.",
        claims=["clm_lat_syd_dist", "clm_lat_syd_delay", "clm_lat_syd_check"],
    )
    s.say(
        "seg_latency_far",
        "change_variable",
        "Distance is the one thing that moves the floor. Stretch the path to Sydney and New York, and the fastest possible round trip through fibre becomes about 155 milliseconds.",
        claims=[
            "clm_lat_syd_fib_rt",
            "clm_lat_syd_dist",
            "clm_lat_syd_fib_one",
            "clm_lat_syd_vac_one",
        ],
    )
    s.say(
        "seg_latency_limits",
        "show_limits",
        "This is a floor, not a forecast. It assumes the signal runs along the cable without a single stop. Real connections add routers, detours and processing on top, and every one of those can only add time.",
    )
    s.say(
        "seg_latency_answer",
        "synthesis",
        "So the fastest possible round trip between Virginia and Spain is about 64 milliseconds, because light in glass covers only about 207,000 kilometres a second. To talk faster, you have to be closer.",
        claims=["clm_lat_fib_round", "clm_lat_fiber_speed"],
    )
    promises = [
        TitlePromise(
            title="Why a reply from Spain can never beat 64 ms",
            thumbnail_promise="Light in glass sets a floor under every transatlantic call",
            claim_ids=(
                "clm_lat_c_vacuum",
                "clm_lat_core_index",
                "clm_lat_marea_len",
                "clm_lat_fib_round",
            ),
        ),
        TitlePromise(
            title="The internet runs on slow light",
            thumbnail_promise="Light in fibre moves at 69% of its vacuum speed",
            claim_ids=("clm_lat_core_index", "clm_lat_fiber_share"),
        ),
        TitlePromise(
            title="The speed limit under the Atlantic",
            thumbnail_promise="6,605 km of glass, 31.9 ms each way",
            claim_ids=("clm_lat_marea_len", "clm_lat_fib_oneway"),
        ),
    ]
    script = s.build(
        frozen,
        script_id="scr_latency_ep02",
        channel_id="ch_explainer_demo",
        question="How fast can a signal really cross the Atlantic, and what sets that limit?",
        contribution="Computes the physical floor under a transatlantic round trip from light's defined speed, a real fibre's refractive index and a real cable's length, checks the method against the fibre article's own worked example, and shows how distance alone moves the floor.",
        promises=promises,
        locked_at=f"{DATE}T10:00:00Z",
    )

    v = SpecDraft(spec_id="vs_latency_ep02")
    there = v.entity("ent_latency_leg_there", "Signal going there", "series", short_label="There")
    back = v.entity("ent_latency_leg_back", "Reply coming back", "series", short_label="Back")
    a_routes = v.dataset_asset("ast_latency_routes", ds_paths)
    a_sol = v.capture_asset(
        "ast_wiki_speedlight", cap_sol.manifest.capture_id, cap_sol.manifest.artifact_sha256
    )
    a_fib = v.capture_asset(
        "ast_wiki_fiber", cap_fib.manifest.capture_id, cap_fib.manifest.artifact_sha256
    )
    a_mar = v.capture_asset(
        "ast_wiki_marea", cap_mar.manifest.capture_id, cap_mar.manifest.artifact_sha256
    )

    v.text_scene(
        "scn_latency_hook_number",
        "cold_open",
        "Open on the number the episode explains.",
        "big_number",
        [
            ("ent_latency_hook_num", "64 ms", "clm_lat_fib_round"),
            ("ent_latency_hook_label", "fastest possible round trip, Virginia to Spain", None),
        ],
        [
            beat(
                "bt_latency_hook_num",
                s.cue("seg_latency_hook", "64"),
                act("reveal", "ent_latency_hook_num", "ent_latency_hook_label"),
            ),
            beat(
                "bt_latency_hook_hold",
                s.cue("seg_latency_hook", "milliseconds.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_lat_fib_round"],
    )
    v.text_scene(
        "scn_latency_hook_line",
        "cold_open",
        "Name what sets the floor.",
        "statement",
        [("ent_latency_hook_line", "Set by light, and by glass.", None)],
        [
            beat(
                "bt_latency_hook_line",
                s.cue("seg_latency_hook_two", "light,"),
                act("reveal", "ent_latency_hook_line"),
            ),
            beat(
                "bt_latency_hook_line_hold",
                s.cue("seg_latency_hook_two", "through.", relation="after", duration="short"),
                HOLD,
            ),
        ],
    )
    v.text_scene(
        "scn_latency_question",
        "question_stakes",
        "Pose the question.",
        "statement",
        [("ent_latency_question", "How fast can a signal really cross an ocean?", None)],
        [
            beat(
                "bt_latency_question",
                s.cue("seg_latency_question", "fast"),
                act("reveal", "ent_latency_question"),
            ),
            beat(
                "bt_latency_question_hold",
                s.cue("seg_latency_question", "Atlantic.", relation="after", duration="short"),
                HOLD,
            ),
        ],
    )
    qc = cap_sol.quote_id(Q_C)
    v.source_scene(
        "scn_latency_src_speed",
        "build_model",
        "Show that c is defined, not measured.",
        a_sol,
        cap_sol,
        Q_C,
        [
            beat(
                "bt_latency_c_show",
                s.cue("seg_latency_vacuum", "Start"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_latency_c_focus",
                s.cue("seg_latency_vacuum", "International"),
                QuoteAction(action="focus_passage", quote_id=qc),
            ),
            beat(
                "bt_latency_c_mark",
                s.cue("seg_latency_vacuum", "exactly"),
                QuoteAction(action="highlight_quote", quote_id=qc),
            ),
            beat(
                "bt_latency_c_hold",
                s.cue("seg_latency_vacuum", "second.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_lat_c_vacuum"],
    )
    qi = cap_fib.quote_id(Q_INDEX)
    v.source_scene(
        "scn_latency_src_index",
        "build_model",
        "Show the refractive index of a real telecom fibre.",
        a_fib,
        cap_fib,
        Q_INDEX,
        [
            beat(
                "bt_latency_idx_show",
                s.cue("seg_latency_glass", "glass,"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_latency_idx_focus",
                s.cue("seg_latency_glass", "Wikipedia"),
                QuoteAction(action="focus_passage", quote_id=qi),
            ),
            beat(
                "bt_latency_idx_mark",
                s.cue("seg_latency_glass", "core"),
                QuoteAction(action="highlight_quote", quote_id=qi),
            ),
            beat(
                "bt_latency_idx_hold",
                s.cue("seg_latency_glass", "1.4475.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_lat_core_index"],
    )
    v.text_scene(
        "scn_latency_speed_formula",
        "build_model",
        "Write the speed in fibre as c divided by the index.",
        "formula",
        [
            ("ent_latency_f_lhs", r"v =", None),
            ("ent_latency_f_c", r"c", None),
            ("ent_latency_f_div", r"\div", None),
            ("ent_latency_f_n", r"n", None),
        ],
        [
            beat(
                "bt_latency_f_show",
                s.cue("seg_latency_speed", "index"),
                act(
                    "reveal",
                    "ent_latency_f_lhs",
                    "ent_latency_f_c",
                    "ent_latency_f_div",
                    "ent_latency_f_n",
                ),
            ),
            beat(
                "bt_latency_f_n",
                s.cue("seg_latency_speed", "Divide"),
                act("highlight", "ent_latency_f_n"),
            ),
            beat(
                "bt_latency_f_hold",
                s.cue("seg_latency_speed", "Divide", relation="after", duration="short"),
                HOLD,
            ),
        ],
    )
    v.text_scene(
        "scn_latency_speed_number",
        "build_model",
        "State light's speed in the fibre.",
        "big_number",
        [
            ("ent_latency_speed_num", "207,111 km/s", "clm_lat_fiber_speed"),
            (
                "ent_latency_speed_label",
                "light in telecom fibre, about 69% of its vacuum speed",
                None,
            ),
        ],
        [
            beat(
                "bt_latency_speed_num",
                s.cue("seg_latency_speed", "207,000"),
                act("reveal", "ent_latency_speed_num"),
            ),
            beat(
                "bt_latency_speed_label",
                s.cue("seg_latency_speed", "69%"),
                act("reveal", "ent_latency_speed_label"),
            ),
            beat(
                "bt_latency_speed_hold",
                s.cue("seg_latency_speed", "vacuum.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_lat_fiber_speed", "clm_lat_fiber_share"],
    )
    v.entity("ent_latency_node_usa", "Virginia Beach, USA", "node")
    v.entity("ent_latency_node_spain", "Sopelana, Spain", "node")
    v.entity("ent_latency_edge_marea", "MAREA cable", "edge", claims=["clm_lat_marea_len"])
    v.add(
        Scene(
            scene_id="scn_latency_route",
            section="build_model",
            purpose="Draw the cable's two landing points.",
            template=DiagramTemplate(
                template="diagram",
                direction="LR",
                nodes=(
                    DiagramNodeSpec(entity_id="ent_latency_node_usa", label="Virginia Beach, USA"),
                    DiagramNodeSpec(entity_id="ent_latency_node_spain", label="Sopelana, Spain"),
                ),
                edges=(
                    DiagramEdgeSpec(
                        entity_id="ent_latency_edge_marea",
                        source_entity_id="ent_latency_node_usa",
                        target_entity_id="ent_latency_node_spain",
                        label="MAREA cable",
                    ),
                ),
            ),
            beats=(
                beat(
                    "bt_latency_route_usa",
                    s.cue("seg_latency_cable", "Virginia"),
                    act("reveal", "ent_latency_node_usa"),
                ),
                beat(
                    "bt_latency_route_spain",
                    s.cue("seg_latency_cable", "Sopelana"),
                    act("reveal", "ent_latency_node_spain", "ent_latency_edge_marea"),
                ),
                beat(
                    "bt_latency_route_flow",
                    s.cue("seg_latency_cable", "Spain."),
                    act("flow", "ent_latency_edge_marea"),
                ),
                beat(
                    "bt_latency_route_hold",
                    s.cue("seg_latency_cable", "Spain.", relation="after", duration="medium"),
                    HOLD,
                ),
            ),
            claim_ids=("clm_lat_marea_ends", "clm_lat_marea_len"),
        )
    )
    qm = cap_mar.quote_id(Q_MAREA_ENDS)
    v.source_scene(
        "scn_latency_src_marea",
        "build_model",
        "Show the cable's article.",
        a_mar,
        cap_mar,
        Q_MAREA_ENDS,
        [
            beat(
                "bt_latency_marea_show",
                s.cue("seg_latency_cable", "article"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_latency_marea_focus",
                s.cue("seg_latency_cable", "length"),
                QuoteAction(action="focus_passage", quote_id=qm),
            ),
            beat(
                "bt_latency_marea_hold",
                s.cue("seg_latency_cable", "kilometres.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_lat_marea_ends", "clm_lat_marea_len"],
    )
    v.text_scene(
        "scn_latency_sum",
        "build_model",
        "Work the crossing time out line by line.",
        "list",
        [
            ("ent_latency_sum_dist", "Distance: 6,605 km", "clm_lat_marea_len"),
            ("ent_latency_sum_speed", "Speed in fibre: 207,111 km/s", "clm_lat_fiber_speed"),
            ("ent_latency_sum_one", "One way: 31.9 ms", "clm_lat_fib_oneway"),
            ("ent_latency_sum_round", "Round trip: 63.8 ms", "clm_lat_fib_round"),
        ],
        [
            beat(
                "bt_latency_sum_dist",
                s.cue("seg_latency_oneway", "6,605"),
                act("reveal", "ent_latency_sum_dist"),
            ),
            beat(
                "bt_latency_sum_speed",
                s.cue("seg_latency_oneway", "207,000"),
                act("reveal", "ent_latency_sum_speed"),
            ),
            beat(
                "bt_latency_sum_one",
                s.cue("seg_latency_oneway", "31.9"),
                act("reveal", "ent_latency_sum_one"),
            ),
            beat(
                "bt_latency_sum_round",
                s.cue("seg_latency_roundtrip", "64"),
                act("reveal", "ent_latency_sum_round"),
                act("highlight", "ent_latency_sum_round"),
            ),
            beat(
                "bt_latency_sum_hold",
                s.cue("seg_latency_roundtrip", "milliseconds.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=[
            "clm_lat_marea_len",
            "clm_lat_fiber_speed",
            "clm_lat_fib_oneway",
            "clm_lat_fib_round",
        ],
    )
    bars = ChartTemplate(
        template="chart",
        chart_kind="stacked_bar",
        title="Fastest possible round trips (computed)",
        dataset_asset_id=a_routes,
        x=FieldEncoding(field="label", kind="nominal"),
        y=FieldEncoding(field="ms", kind="quantitative", unit="ms", title="milliseconds"),
        series_field="leg",
        series=(
            SeriesBinding(value="there", entity_id=there),
            SeriesBinding(value="back", entity_id=back),
        ),
    )
    v.add(
        Scene(
            scene_id="scn_latency_bars_marea",
            section="run_system",
            purpose="Compare the MAREA round trip in vacuum and in glass.",
            template=bars,
            beats=(
                beat(
                    "bt_latency_bars_show",
                    s.cue("seg_latency_compare", "vacuum."),
                    act("reveal", there, back),
                    FilterAction(action="filter", field="route", op="eq", value="Virginia–Spain"),
                ),
                beat(
                    "bt_latency_bars_back",
                    s.cue("seg_latency_compare", "44.1"),
                    act("highlight", back),
                ),
                beat(
                    "bt_latency_bars_hold",
                    s.cue("seg_latency_compare", "exchange.", relation="after", duration="short"),
                    act("clear_highlight"),
                    HOLD,
                ),
            ),
            claim_ids=(
                "clm_lat_vac_oneway",
                "clm_lat_vac_round",
                "clm_lat_glass_extra",
                "clm_lat_fib_oneway",
                "clm_lat_fib_round",
            ),
        )
    )
    qr, qs = cap_fib.quote_id(Q_RULE), cap_fib.quote_id(Q_SYDNEY)
    v.source_scene(
        "scn_latency_src_rule",
        "run_system",
        "Check the method against the article's own rule and example.",
        a_fib,
        cap_fib,
        Q_RULE,
        [
            beat(
                "bt_latency_rule_show",
                s.cue("seg_latency_rule", "fibre"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_latency_rule_focus",
                s.cue("seg_latency_rule", "thumb:"),
                QuoteAction(action="focus_passage", quote_id=qr),
            ),
            beat(
                "bt_latency_rule_mark",
                s.cue("seg_latency_rule", "signal"),
                QuoteAction(action="highlight_quote", quote_id=qr),
            ),
            beat(
                "bt_latency_rule_hold",
                s.cue("seg_latency_rule", "second.", relation="after", duration="beat"),
                HOLD,
            ),
            beat(
                "bt_latency_syd_clear",
                s.cue("seg_latency_sydney", "example"),
                act("clear_highlight"),
            ),
            beat(
                "bt_latency_syd_focus",
                s.cue("seg_latency_sydney", "Sydney"),
                QuoteAction(action="focus_passage", quote_id=qs),
            ),
            beat(
                "bt_latency_syd_mark",
                s.cue("seg_latency_sydney", "16,000"),
                QuoteAction(action="highlight_quote", quote_id=qs),
            ),
            beat(
                "bt_latency_syd_hold",
                s.cue("seg_latency_sydney", "answer.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_lat_rule_speed", "clm_lat_syd_dist", "clm_lat_syd_delay", "clm_lat_syd_check"],
    )
    v.add(
        Scene(
            scene_id="scn_latency_bars_far",
            section="change_variable",
            purpose="Add the longer route and show distance moving the floor.",
            template=bars,
            initial_visible=(there, back),
            beats=(
                beat(
                    "bt_latency_far_filter",
                    s.cue("seg_latency_far", "Stretch"),
                    FilterAction(action="filter", field="route", op="neq", value="none"),
                ),
                beat(
                    "bt_latency_far_hold",
                    s.cue("seg_latency_far", "milliseconds.", relation="after", duration="medium"),
                    HOLD,
                ),
            ),
            claim_ids=(
                "clm_lat_syd_fib_rt",
                "clm_lat_syd_dist",
                "clm_lat_syd_fib_one",
                "clm_lat_syd_vac_one",
            ),
        )
    )
    v.text_scene(
        "scn_latency_limits",
        "show_limits",
        "List what a real connection adds on top of the floor.",
        "list",
        [
            ("ent_latency_lim_routers", "Routers", None),
            ("ent_latency_lim_detours", "Detours", None),
            ("ent_latency_lim_processing", "Processing", None),
            ("ent_latency_lim_only", "Each one can only add time", None),
        ],
        [
            beat(
                "bt_latency_lim_routers",
                s.cue("seg_latency_limits", "routers,"),
                act("reveal", "ent_latency_lim_routers"),
            ),
            beat(
                "bt_latency_lim_detours",
                s.cue("seg_latency_limits", "detours"),
                act("reveal", "ent_latency_lim_detours"),
            ),
            beat(
                "bt_latency_lim_processing",
                s.cue("seg_latency_limits", "processing"),
                act("reveal", "ent_latency_lim_processing"),
            ),
            beat(
                "bt_latency_lim_only",
                s.cue("seg_latency_limits", "only"),
                act("reveal", "ent_latency_lim_only"),
            ),
            beat(
                "bt_latency_lim_hold",
                s.cue("seg_latency_limits", "time.", relation="after", duration="short"),
                HOLD,
            ),
        ],
    )
    v.text_scene(
        "scn_latency_answer",
        "synthesis",
        "Close on the practical rule.",
        "statement",
        [("ent_latency_answer", "To talk faster, be closer.", None)],
        [
            beat(
                "bt_latency_answer",
                s.cue("seg_latency_answer", "To"),
                act("reveal", "ent_latency_answer"),
            ),
            beat(
                "bt_latency_answer_hold",
                s.cue("seg_latency_answer", "closer.", relation="after", duration="long"),
                HOLD,
            ),
        ],
    )
    spec = v.build(frozen, script)
    order = cue_order_issues(spec, script)
    if order:
        raise SystemExit("\n".join(order))
    issues = check_episode(
        frozen, script, spec, manifests=(cap_sol.manifest, cap_fib.manifest, cap_mar.manifest)
    )
    if issues:
        raise SystemExit("\n".join(str(i) for i in issues))
    print(json.dumps(write_episode(HERE, frozen, script, spec, [cap_sol, cap_fib, cap_mar])))


if __name__ == "__main__":
    sys.exit(build())
