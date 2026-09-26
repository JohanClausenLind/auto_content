"""Build the bicycle-gearing episode: pack from a stored capture, locked script, visual spec."""

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
    FieldEncoding,
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
TABLE = "a cell of the captured article's gear table (170 mm cranks, 700C wheels, 25 mm tyres)"
OURS = "our arithmetic on the article's table"
URL = "https://en.wikipedia.org/wiki/Bicycle_gearing"

Q_DEF = (
    "Bicycle gearing is the aspect of a bicycle drivetrain that determines the relation between "
    "the cadence, the rate at which the rider pedals, and the rate at which the drive wheel turns."
)
Q_SAME = (
    "it is not immediately obvious that 53/19 and 39/14 represent effectively the same gear ratio."
)
Q_SAME_FULL = (
    "Front/rear gear measurement uses two numbers (e.g. 53/19) where the first is the number of "
    "teeth in the front chainring and the second is the number of teeth in the rear sprocket. "
    "Without doing some arithmetic, it is not immediately obvious that 53/19 and 39/14 represent "
    "effectively the same gear ratio."
)
Q_DEV = (
    "Metres of development corresponds to the distance (in metres) traveled by the bicycle for one "
    "rotation of the pedals."
)
Q_TRADE = (
    "For a bicycle to travel at the same speed, using a lower gear (larger mechanical advantage) "
    "requires the rider to pedal at a faster cadence, but with less force."
)
Q_SPRINT = "cadence above 100 rpm becomes less effective after short bursts, as during a sprint."
Q_TABLE_NOTE = (
    "the particular numbers are for bicycles with 170 mm cranks, 700C wheels, and 25 mm tyres"
)
Q_RPM_HEAD = "60 rpm 80 rpm 100 rpm 120 rpm"
ROWS = (  # label, gear inches .. as the table prints the row, development m, km/h at 80 rpm
    (
        "very_low",
        "Very low",
        "Very low 20 1.6 1.5 32/42 3.5 5.6 4.7 7.6 5.9 9.5 7.1 11.4",
        1.6,
        7.6,
    ),
    ("low", "Low", "Low 40 3.2 3.0 34/23 7.2 11.6 9.6 15.4 11.9 19.2 14.3 23", 3.2, 15.4),
    (
        "medium",
        "Medium",
        "Medium 70 5.6 5.2 53/19 or 39/14 12.5 20 16.6 26.7 21 33.6 25 40",
        5.6,
        26.7,
    ),
    ("high", "High", "High 100 8.0 7.5 53/14 18 29 24 38.6 30 48.3 36 57.9", 8.0, 38.6),
    (
        "very_high",
        "Very high",
        "Very high 125 10.0 9.4 53/11 22.3 36 29.7 47.8 37.1 59.7 44.5 72",
        10.0,
        47.8,
    ),
)


def build() -> None:
    pack = PackDraft(
        pack_id="pk_gears_2026", topic="What bicycle gears actually trade", checked_at=DATE
    )
    page = captured_page("gearing", URL, "Bicycle gearing")
    pack.source("src_wiki_gearing", page, source_class=SourceClass.reference)
    for item, text in (
        ("ev_gears_definition", Q_DEF),
        ("ev_gears_same_ratio", Q_SAME_FULL),
        ("ev_gears_development", Q_DEV),
        ("ev_gears_trade_off", Q_TRADE),
        ("ev_gears_sprint_rpm", Q_SPRINT),
        ("ev_gears_table_note", Q_TABLE_NOTE),
        ("ev_gears_rpm_header", Q_RPM_HEAD),
    ):
        pack.passage(item, "src_wiki_gearing", text)
    for key, _label, row, _dev, _kmh in ROWS:
        pack.passage(f"ev_gears_row_{key}", "src_wiki_gearing", row)

    pack.observation(
        "clm_gears_definition",
        "Gearing sets the relation between the rider's cadence and the drive wheel's rate.",
        ["ev_gears_definition"],
        rationale=WHY,
    )
    for teeth, name in (
        (53, "big_ring"),
        (19, "sprocket_19"),
        (39, "small_ring"),
        (14, "sprocket_14"),
    ):
        pack.observation(
            f"clm_gears_{name}",
            f"The article's example gear uses {teeth} teeth.",
            ["ev_gears_same_ratio"],
            rationale=WHY,
            value=qty(teeth, "count"),
        )
    pack.observation(
        "clm_gears_same_ratio",
        "The article says 53/19 and 39/14 are effectively the same gear ratio.",
        ["ev_gears_same_ratio"],
        rationale=WHY,
    )
    pack.observation(
        "clm_gears_devdef",
        "Metres of development is the distance travelled for one rotation of the pedals.",
        ["ev_gears_development"],
        rationale=WHY,
    )
    pack.observation(
        "clm_gears_trade_off",
        "A lower gear needs a faster cadence but less force for the same speed.",
        ["ev_gears_trade_off"],
        rationale=WHY,
    )
    pack.observation(
        "clm_gears_sprint_rpm",
        "Cadence above 100 rpm becomes less effective after short bursts.",
        ["ev_gears_sprint_rpm"],
        rationale=WHY,
        value=qty(100, "count"),
    )
    pack.observation(
        "clm_gears_cadence80",
        "The article's table gives speeds at 80 pedal turns a minute.",
        ["ev_gears_rpm_header", "ev_gears_table_note"],
        rationale=TABLE,
        value=qty(80, "count"),
        short_label="80 pedal turns a minute",
    )
    pack.derive(
        "clm_gears_ratio53_19",
        "53 teeth over 19 teeth.",
        "div",
        ["clm_gears_big_ring", "clm_gears_sprocket_19"],
        rationale=OURS,
        unit="ratio",
    )
    pack.derive(
        "clm_gears_ratio39_14",
        "39 teeth over 14 teeth.",
        "div",
        ["clm_gears_small_ring", "clm_gears_sprocket_14"],
        rationale=OURS,
        unit="ratio",
    )
    pack.assumption(
        "clm_gears_one_km",
        "The comparison distance is one kilometre.",
        qty(1, "km"),
        rationale="a round distance chosen to compare gears",
        short_label="1 km",
    )
    for key, label, _row, dev, kmh in ROWS:
        pack.observation(
            f"clm_gears_dev_{key}",
            f"The table's {label.lower()} gear travels {dev} m per pedal turn.",
            [f"ev_gears_row_{key}", "ev_gears_table_note"],
            rationale=TABLE,
            value=qty(dev, "m"),
            short_label=f"{label}: {dev} m",
        )
        pack.observation(
            f"clm_gears_kmh_{key}",
            f"The table's {label.lower()} gear at 80 rpm goes {kmh} km/h.",
            [f"ev_gears_row_{key}", "ev_gears_rpm_header"],
            rationale=TABLE,
            value=qty(kmh, "km/h"),
            short_label=f"{label}: {kmh} km/h",
        )
        pack.derive(
            f"clm_gears_turns_{key}",
            f"Pedal turns to cover 1 km in the {label.lower()} gear.",
            "div",
            ["clm_gears_one_km", f"clm_gears_dev_{key}"],
            rationale=OURS,
            unit="ratio",
        )
    pack.derive(
        "clm_gears_turn_ratio",
        "The lowest gear needs this many times more turns per km than the highest.",
        "div",
        ["clm_gears_turns_very_low", "clm_gears_turns_very_high"],
        rationale=OURS,
        unit="ratio",
        short_label="6.25× the turns",
    )

    def gear_dataset(dataset_id: str, title: str, column: str, unit: str, claim: str) -> object:
        return pack.dataset(
            dataset_id,
            title,
            [
                DatasetColumn(name="gear", kind="ordinal"),
                DatasetColumn(name=column, kind="quantitative", unit=unit),
            ],
            [
                (key, [label, pack.value(claim.format(key=key)).magnitude], [claim.format(key=key)])
                for key, label, *_ in ROWS
            ],
        )

    ds_dev = gear_dataset(
        "ds_gears_development",
        "Metres per pedal turn (article's table)",
        "metres",
        "m",
        "clm_gears_dev_{key}",
    )
    ds_kmh = gear_dataset(
        "ds_gears_speed_80rpm",
        "Speed at 80 rpm (article's table)",
        "kmh",
        "km/h",
        "clm_gears_kmh_{key}",
    )
    ds_turns = gear_dataset(
        "ds_gears_turns_per_km",
        "Pedal turns per kilometre (computed)",
        "turns",
        "ratio",
        "clm_gears_turns_{key}",
    )
    frozen = pack.build(frozen_at=f"{DATE}T09:00:00Z")

    cap = capture_quotes(
        page,
        "src_wiki_gearing",
        [
            (Q_DEF, ["clm_gears_definition"]),
            (Q_SAME, ["clm_gears_same_ratio"]),
            (Q_DEV, ["clm_gears_devdef"]),
            (Q_TRADE, ["clm_gears_trade_off"]),
            (Q_SPRINT, ["clm_gears_sprint_rpm"]),
        ],
    )
    frozen = link_captures(frozen, {"src_wiki_gearing": cap})

    s = ScriptDraft()
    s.say(
        "seg_gears_hook",
        "cold_open",
        "On a steep hill, you shift to your smallest front gear and your biggest rear gear. The bike slows down, but suddenly you can keep pedalling. What did the gears actually give you?",
    )
    s.say(
        "seg_gears_question",
        "question_stakes",
        "Gears cannot add energy. So what exactly are they trading, and how much of it?",
    )
    s.say(
        "seg_gears_definition",
        "build_model",
        "The Wikipedia article on bicycle gearing defines it as the aspect of a bicycle drivetrain that determines the relation between the cadence, the rate at which the rider pedals, and the rate at which the drive wheel turns.",
        claims=["clm_gears_definition"],
    )
    s.say(
        "seg_gears_ratio",
        "build_model",
        "The simplest measure is the gear ratio: teeth on the front chainring divided by teeth on the rear sprocket. The article points out that it is not obvious that 53 teeth over 19, and 39 over 14, are effectively the same gear. Divide them out, and both come to about 2.79.",
        claims=[
            "clm_gears_big_ring",
            "clm_gears_sprocket_19",
            "clm_gears_small_ring",
            "clm_gears_sprocket_14",
            "clm_gears_same_ratio",
            "clm_gears_ratio53_19",
            "clm_gears_ratio39_14",
        ],
    )
    s.say(
        "seg_gears_devdef",
        "build_model",
        "A more useful measure is metres of development: the distance the bicycle travels for one rotation of the pedals.",
        claims=["clm_gears_devdef"],
    )
    s.say(
        "seg_gears_devtable",
        "build_model",
        "The article's table gives it for a typical road bike. The lowest gear it lists moves the bike 1.6 metres for every turn of the pedals. The highest moves it 10.0 metres.",
        claims=["clm_gears_dev_very_low", "clm_gears_dev_very_high"],
    )
    s.say(
        "seg_gears_speeds",
        "run_system",
        "Pedal at a steady 80 turns a minute, and those gears carry you at very different speeds. The table lists 7.6 kilometres an hour in the lowest gear, 26.7 in the middle one, and 47.8 in the highest.",
        claims=[
            "clm_gears_cadence80",
            "clm_gears_kmh_very_low",
            "clm_gears_kmh_medium",
            "clm_gears_kmh_very_high",
        ],
    )
    s.say(
        "seg_gears_turns",
        "run_system",
        "Now flip the question around. To cover 1 kilometre, the lowest gear needs 625 turns of the pedals. The highest needs just 100.",
        claims=["clm_gears_one_km", "clm_gears_turns_very_low", "clm_gears_turns_very_high"],
    )
    s.say(
        "seg_gears_trade",
        "run_system",
        "That is the trade, and the article states it directly: for a bicycle to travel at the same speed, using a lower gear, with its larger mechanical advantage, requires the rider to pedal at a faster cadence, but with less force.",
        claims=["clm_gears_trade_off"],
    )
    s.say(
        "seg_gears_force",
        "change_variable",
        "Here is why. The energy it takes to climb a hill is the same in every gear. Spread it over 625 turns instead of 100, which is 6.25 times as many, and each turn needs only about a sixth of the force. Your legs make more turns, and every one of them is lighter.",
        claims=["clm_gears_turns_very_low", "clm_gears_turns_very_high", "clm_gears_turn_ratio"],
    )
    s.say(
        "seg_gears_limits",
        "show_limits",
        "Real riding is messier. Rolling resistance, wind and the effort of spinning your legs faster all take their share. And spinning has a limit too: the article notes that cadence above 100 rpm becomes less effective after short bursts, as during a sprint.",
        claims=["clm_gears_sprint_rpm"],
    )
    s.say(
        "seg_gears_answer",
        "synthesis",
        "So gears never add energy. They choose how it is delivered: many light pedal strokes, or a few heavy ones. On a hill, the small front gear keeps your legs in the range where they work best.",
    )
    promises = [
        TitlePromise(
            title="Your gears trade force for pedal turns",
            thumbnail_promise="625 turns or 100 turns for the same kilometre",
            claim_ids=(
                "clm_gears_dev_very_low",
                "clm_gears_dev_very_high",
                "clm_gears_turns_very_low",
            ),
        ),
        TitlePromise(
            title="Why the small front gear gets you up the hill",
            thumbnail_promise="Lower gear: faster pedalling, less force per turn",
            claim_ids=("clm_gears_trade_off", "clm_gears_dev_very_low"),
        ),
        TitlePromise(
            title="53/19 and 39/14 are the same gear",
            thumbnail_promise="Both come to 2.79",
            claim_ids=("clm_gears_same_ratio", "clm_gears_big_ring", "clm_gears_ratio53_19"),
        ),
    ]
    script = s.build(
        frozen,
        script_id="scr_gears_ep03",
        channel_id="ch_explainer_demo",
        question="Gears cannot add energy, so what exactly do they trade, and how much?",
        contribution="Turns the article's gear table around, from metres per pedal turn into pedal turns per kilometre, and uses the fact that the energy for a climb is the same in every gear to show how much force each turn saves.",
        promises=promises,
        locked_at=f"{DATE}T10:00:00Z",
    )

    v = SpecDraft(spec_id="vs_gears_ep03")
    a_dev = v.dataset_asset("ast_gears_development", ds_dev)
    a_kmh = v.dataset_asset("ast_gears_speed_80rpm", ds_kmh)
    a_turns = v.dataset_asset("ast_gears_turns_per_km", ds_turns)
    a_wiki = v.capture_asset(
        "ast_wiki_gearing", cap.manifest.capture_id, cap.manifest.artifact_sha256
    )
    dev = v.entity(
        "ent_gears_series_dev", "Metres per pedal turn", "series", short_label="Metres per turn"
    )
    kmh = v.entity(
        "ent_gears_series_kmh", "Speed at 80 rpm", "series", short_label="km/h at 80 rpm"
    )
    turns = v.entity(
        "ent_gears_series_turns", "Pedal turns per kilometre", "series", short_label="Turns per km"
    )

    def gear_chart(
        scene_id: str,
        section: str,
        purpose: str,
        title: str,
        asset: str,
        column: str,
        unit: str,
        axis: str,
        series: str,
        beats: list,
        claims: list[str],
    ) -> None:
        chart = ChartTemplate(
            template="chart",
            chart_kind="bar",
            title=title,
            dataset_asset_id=asset,
            x=FieldEncoding(field="gear", kind="ordinal", title="gear"),
            y=FieldEncoding(field=column, kind="quantitative", unit=unit, title=axis),
            series=(SeriesBinding(value=column, entity_id=series),),
        )
        v.add(
            Scene(
                scene_id=scene_id,
                section=section,
                purpose=purpose,
                template=chart,
                beats=tuple(beats),
                claim_ids=tuple(claims),
            )
        )

    v.text_scene(
        "scn_gears_hook",
        "cold_open",
        "Name the everyday puzzle.",
        "statement",
        [("ent_gears_hook", "Slower bike. Easier climb. What changed?", None)],
        [
            beat(
                "bt_gears_hook", s.cue("seg_gears_hook", "slows"), act("reveal", "ent_gears_hook")
            ),
            beat(
                "bt_gears_hook_hold",
                s.cue("seg_gears_hook", "you?", occurrence=-1, relation="after", duration="short"),
                HOLD,
            ),
        ],
    )
    v.text_scene(
        "scn_gears_question",
        "question_stakes",
        "Pose the question.",
        "statement",
        [("ent_gears_question", "Gears can't add energy. So what do they trade?", None)],
        [
            beat(
                "bt_gears_question",
                s.cue("seg_gears_question", "Gears"),
                act("reveal", "ent_gears_question"),
            ),
            beat(
                "bt_gears_question_hold",
                s.cue("seg_gears_question", "it?", relation="after", duration="short"),
                HOLD,
            ),
        ],
    )
    qd = cap.quote_id(Q_DEF)
    v.source_scene(
        "scn_gears_src_definition",
        "build_model",
        "Show the article's definition.",
        a_wiki,
        cap,
        Q_DEF,
        [
            beat(
                "bt_gears_def_show",
                s.cue("seg_gears_definition", "Wikipedia"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_gears_def_focus",
                s.cue("seg_gears_definition", "defines"),
                QuoteAction(action="focus_passage", quote_id=qd),
            ),
            beat(
                "bt_gears_def_hold",
                s.cue("seg_gears_definition", "turns.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_gears_definition"],
    )
    qs = cap.quote_id(Q_SAME)
    v.source_scene(
        "scn_gears_src_same",
        "build_model",
        "Quote the article's two equivalent gears.",
        a_wiki,
        cap,
        Q_SAME,
        [
            beat(
                "bt_gears_same_show",
                s.cue("seg_gears_ratio", "simplest"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_gears_same_focus",
                s.cue("seg_gears_ratio", "points"),
                QuoteAction(action="focus_passage", quote_id=qs),
            ),
            beat(
                "bt_gears_same_mark",
                s.cue("seg_gears_ratio", "obvious"),
                QuoteAction(action="highlight_quote", quote_id=qs),
            ),
            beat(
                "bt_gears_same_hold",
                s.cue("seg_gears_ratio", "gear.", occurrence=-1, relation="after", duration="beat"),
                HOLD,
            ),
        ],
        claims=[
            "clm_gears_same_ratio",
            "clm_gears_big_ring",
            "clm_gears_sprocket_19",
            "clm_gears_small_ring",
            "clm_gears_sprocket_14",
        ],
    )
    v.text_scene(
        "scn_gears_ratios",
        "build_model",
        "Do the division for both gears.",
        "list",
        [
            (
                "ent_gears_ratio_big",
                "Big chainring, 19-tooth sprocket: 2.79",
                "clm_gears_ratio53_19",
            ),
            (
                "ent_gears_ratio_small",
                "Small chainring, 14-tooth sprocket: 2.79",
                "clm_gears_ratio39_14",
            ),
        ],
        [
            beat(
                "bt_gears_ratio_big",
                s.cue("seg_gears_ratio", "Divide"),
                act("reveal", "ent_gears_ratio_big"),
            ),
            beat(
                "bt_gears_ratio_small",
                s.cue("seg_gears_ratio", "2.79."),
                act("reveal", "ent_gears_ratio_small"),
            ),
            beat(
                "bt_gears_ratio_hold",
                s.cue("seg_gears_ratio", "2.79.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_gears_ratio53_19", "clm_gears_ratio39_14"],
    )
    qv = cap.quote_id(Q_DEV)
    v.source_scene(
        "scn_gears_src_development",
        "build_model",
        "Define metres of development on the page.",
        a_wiki,
        cap,
        Q_DEV,
        [
            beat(
                "bt_gears_dev_show",
                s.cue("seg_gears_devdef", "useful"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_gears_dev_focus",
                s.cue("seg_gears_devdef", "development:"),
                QuoteAction(action="focus_passage", quote_id=qv),
            ),
            beat(
                "bt_gears_dev_mark",
                s.cue("seg_gears_devdef", "distance"),
                QuoteAction(action="highlight_quote", quote_id=qv),
            ),
            beat(
                "bt_gears_dev_hold",
                s.cue("seg_gears_devdef", "pedals.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_gears_devdef"],
    )
    gear_chart(
        "scn_gears_chart_development",
        "build_model",
        "Show how far each gear moves the bike per pedal turn.",
        "Metres per pedal turn (article's table)",
        a_dev,
        "metres",
        "m",
        "metres",
        dev,
        [
            beat(
                "bt_gears_devchart_show", s.cue("seg_gears_devtable", "table"), act("reveal", dev)
            ),
            beat(
                "bt_gears_devchart_low", s.cue("seg_gears_devtable", "1.6"), act("highlight", dev)
            ),
            beat(
                "bt_gears_devchart_hold",
                s.cue(
                    "seg_gears_devtable",
                    "metres.",
                    occurrence=-1,
                    relation="after",
                    duration="short",
                ),
                HOLD,
            ),
        ],
        ["clm_gears_dev_very_low", "clm_gears_dev_very_high"],
    )
    gear_chart(
        "scn_gears_chart_speed",
        "run_system",
        "Show the speed each gear gives at the same cadence.",
        "Speed at 80 rpm (article's table)",
        a_kmh,
        "kmh",
        "km/h",
        "km/h",
        kmh,
        [
            beat("bt_gears_speed_show", s.cue("seg_gears_speeds", "80"), act("reveal", kmh)),
            beat(
                "bt_gears_speed_hold",
                s.cue("seg_gears_speeds", "highest.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        [
            "clm_gears_cadence80",
            "clm_gears_kmh_very_low",
            "clm_gears_kmh_medium",
            "clm_gears_kmh_very_high",
        ],
    )
    gear_chart(
        "scn_gears_chart_turns",
        "run_system",
        "Flip the table: pedal turns needed for one kilometre.",
        "Pedal turns per kilometre (computed)",
        a_turns,
        "turns",
        "ratio",
        "turns",
        turns,
        [
            beat("bt_gears_turns_show", s.cue("seg_gears_turns", "flip"), act("reveal", turns)),
            beat("bt_gears_turns_low", s.cue("seg_gears_turns", "625"), act("highlight", turns)),
            beat(
                "bt_gears_turns_hold",
                s.cue("seg_gears_turns", "100.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        ["clm_gears_one_km", "clm_gears_turns_very_low", "clm_gears_turns_very_high"],
    )
    qt = cap.quote_id(Q_TRADE)
    v.source_scene(
        "scn_gears_src_trade",
        "run_system",
        "Quote the trade-off in the article's words.",
        a_wiki,
        cap,
        Q_TRADE,
        [
            beat(
                "bt_gears_trade_show",
                s.cue("seg_gears_trade", "trade,"),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_gears_trade_focus",
                s.cue("seg_gears_trade", "directly:"),
                QuoteAction(action="focus_passage", quote_id=qt),
            ),
            beat(
                "bt_gears_trade_mark",
                s.cue("seg_gears_trade", "lower"),
                QuoteAction(action="highlight_quote", quote_id=qt),
            ),
            beat(
                "bt_gears_trade_hold",
                s.cue("seg_gears_trade", "force.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_gears_trade_off"],
    )
    v.text_scene(
        "scn_gears_turn_ratio",
        "change_variable",
        "State how many more turns the low gear takes.",
        "big_number",
        [
            ("ent_gears_ratio_num", "6.25×", "clm_gears_turn_ratio"),
            ("ent_gears_ratio_label", "more pedal turns per kilometre, each with less force", None),
        ],
        [
            beat(
                "bt_gears_ratio_num",
                s.cue("seg_gears_force", "6.25"),
                act("reveal", "ent_gears_ratio_num"),
            ),
            beat(
                "bt_gears_ratio_label",
                s.cue("seg_gears_force", "force."),
                act("reveal", "ent_gears_ratio_label"),
            ),
            beat(
                "bt_gears_ratio_numhold",
                s.cue("seg_gears_force", "lighter.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_gears_turns_very_low", "clm_gears_turns_very_high", "clm_gears_turn_ratio"],
    )
    qp = cap.quote_id(Q_SPRINT)
    v.source_scene(
        "scn_gears_src_sprint",
        "show_limits",
        "Show where spinning faster stops helping.",
        a_wiki,
        cap,
        Q_SPRINT,
        [
            beat(
                "bt_gears_sprint_show",
                s.cue("seg_gears_limits", "messier."),
                ShowSourceAction(action="show_source"),
            ),
            beat(
                "bt_gears_sprint_focus",
                s.cue("seg_gears_limits", "notes"),
                QuoteAction(action="focus_passage", quote_id=qp),
            ),
            beat(
                "bt_gears_sprint_mark",
                s.cue("seg_gears_limits", "cadence"),
                QuoteAction(action="highlight_quote", quote_id=qp),
            ),
            beat(
                "bt_gears_sprint_hold",
                s.cue("seg_gears_limits", "sprint.", relation="after", duration="short"),
                HOLD,
            ),
        ],
        claims=["clm_gears_sprint_rpm"],
    )
    v.text_scene(
        "scn_gears_answer",
        "synthesis",
        "Close on the answer.",
        "statement",
        [("ent_gears_answer", "Gears don't add energy. They choose how it's delivered.", None)],
        [
            beat(
                "bt_gears_answer",
                s.cue("seg_gears_answer", "never"),
                act("reveal", "ent_gears_answer"),
            ),
            beat(
                "bt_gears_answer_hold",
                s.cue("seg_gears_answer", "best.", relation="after", duration="long"),
                HOLD,
            ),
        ],
    )
    spec = v.build(frozen, script)
    order = cue_order_issues(spec, script)
    if order:
        raise SystemExit("\n".join(order))
    issues = check_episode(frozen, script, spec, manifests=(cap.manifest,))
    if issues:
        raise SystemExit("\n".join(str(i) for i in issues))
    print(json.dumps(write_episode(HERE, frozen, script, spec, [cap])))


if __name__ == "__main__":
    sys.exit(build())
