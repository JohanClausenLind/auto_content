"""The words half of reference retrieval: lexicon expansion, FTS5 syntax, ranked rows.

The index itself is built by another module, so these tests build a small one from the shipped
schema in ``tmp_path`` and query that. Its rows are the real baked CMU clips, numbers and
descriptions copied from ``/mnt/fast/models/blender-assets/clips/manifest.json``, so a ranking
assertion here is a ranking assertion about material that exists.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from content_factory.reference.lexicon import (
    LEXICON_PATH,
    Expansion,
    LexiconError,
    expand,
    lexicon_sha256,
    load_lexicon,
    normalize,
)
from content_factory.reference.query import (
    RETRIEVER_VERSION,
    UNKNOWN_LIBRARY,
    build_match_expression,
    search,
)
from content_factory.schemas.reference import (
    ABSENT_INTERACTIONS,
    Affection,
    ContactTag,
    InteractionTag,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceFile,
    ReferenceQuery,
    ReferenceSource,
    UsageClass,
)

OPERATOR_SENTENCE = "She sits beside him, rests her head on his shoulder"

TOKENIZE = "unicode61 remove_diacritics 2 tokenchars '_'"

# clip_id, interaction, contact, posture, affection, frames, duration_s, closest, caption.
# Copied from the baked cf.clip.v2 library manifest; every number is measured, not invented.
ROWS: tuple[tuple[str, str, str, str, str, int, float, float, str], ...] = (
    (
        "cmu_18_19_04",
        "pull_resist",
        "arm hands",
        "standing",
        "aggression",
        81,
        3.375,
        0.1857,
        "A pulls B; B resists",
    ),
    (
        "cmu_18_19_12",
        "meet_and_sit",
        "none",
        "sitting standing walking",
        "affection",
        251,
        10.458,
        0.0604,
        "friends meet, hang out; A sits, B joins A",
    ),
    (
        "cmu_22_23_03",
        "comfort_kneeling",
        "arm shoulder",
        "kneeling sitting",
        "affection",
        163,
        6.792,
        0.2577,
        "A sits, holds face in hands; B kneels, comforts A",
    ),
    (
        "cmu_22_23_04",
        "hand_on_shoulder",
        "shoulder",
        "standing",
        "affection",
        104,
        4.333,
        0.0532,
        "B comforts A, puts one hand on A's shoulder",
    ),
    (
        "cmu_22_23_05",
        "hands_on_shoulders",
        "shoulder",
        "standing",
        "affection",
        105,
        4.375,
        0.6851,
        "A comforts B, puts both hands on B's shoulders",
    ),
    (
        "cmu_22_23_06",
        "hands_on_shoulders",
        "shoulder",
        "standing",
        "affection",
        101,
        4.208,
        0.5835,
        "B comforts A, puts both hands on A's shoulders",
    ),
    (
        "cmu_22_23_07",
        "hand_on_shoulder",
        "shoulder",
        "standing",
        "affection",
        61,
        2.542,
        0.0892,
        "A comforts B, puts one hand on B's shoulder",
    ),
    (
        "cmu_22_23_08",
        "hold_hands_walk",
        "hands",
        "walking",
        "affection",
        46,
        1.917,
        0.0525,
        "hold hands, swing arms, walk",
    ),
    (
        "cmu_22_23_09",
        "shoulder_rub",
        "back shoulder",
        "sitting standing",
        "affection",
        108,
        4.5,
        0.6298,
        "A gives B a shoulder rub; B sits, A stands",
    ),
    (
        "cmu_33_34_01",
        "throw_catch",
        "object",
        "standing",
        "neutral",
        1352,
        56.333,
        1.7321,
        "football - throw, catch",
    ),
)

COMFORT_CLIPS = ("cmu_22_23_04", "cmu_22_23_05", "cmu_22_23_06", "cmu_22_23_07")


def _clip(row) -> ReferenceClip:
    """One ROWS tuple as the contract sees it, so the fixture cannot drift from the real thing."""
    clip_id, interaction, contact, posture, affection, frames, duration, closest, caption = row
    contacts = tuple(sorted({ContactTag(c) for c in contact.split()}))
    return ReferenceClip(
        clip_id=clip_id,
        source=ReferenceSource.cmu_mocap,
        source_ref=f"subjects/{clip_id}.amc",
        modality=Modality.mocap_segments,
        usage=UsageClass.pose_derivable,
        people_count=2,
        affection=Affection(affection),
        interaction_tags=tuple(sorted({InteractionTag(i) for i in interaction.split()})),
        contact_tags=contacts,
        postures=tuple(sorted({Posture(x) for x in posture.split()})),
        frame_count=frames,
        native_fps=120.0,
        sampled_fps=24.0,
        duration_s=duration,
        pose_format="cf_clip_v2",
        pose_root="/mnt/fast/models/blender-assets/clips",
        retargeted_clip=clip_id,
        caption=caption,
        caption_source="parsed_index",
        measured={"closest_wrists_m": closest},
        files=(
            ReferenceFile(
                role="clip_json",
                path=f"clips/{clip_id}.json",
                sha256=hashlib.sha256(clip_id.encode()).hexdigest(),
                size_bytes=1,
            ),
        ),
        ingested_at="2026-09-07T00:00:00Z",
        ingester_version="0.1.0",
    )


def _build_index(path: Path, rows=ROWS, library_id: str = "reference_test") -> Path:
    """A tiny index built through the real builder, so there is one schema and one insert path."""
    from content_factory.reference import index as index_mod

    clips = [_clip(row) for row in rows]
    index_mod.build(
        clips,
        path,
        lexicon_sha256="0" * 64,
        built_at="2026-09-07T00:00:00Z",
        builder_version="1.0.0",
        sources=(ReferenceSource.cmu_mocap,),
        skipped=(),
        library_id=library_id,
    )
    return path


@pytest.fixture
def index_path(tmp_path: Path) -> Path:
    return _build_index(tmp_path / "index.sqlite")


# --- the lexicon ---------------------------------------------------------------------------


def test_lexicon_loads_and_hashes_the_committed_bytes() -> None:
    lexicon = load_lexicon()
    assert lexicon.path == LEXICON_PATH
    assert lexicon.version == "1.0.0"
    assert lexicon.sha256 == hashlib.sha256(LEXICON_PATH.read_bytes()).hexdigest()
    assert lexicon_sha256() == lexicon.sha256
    assert len(lexicon.phrases) > 300
    assert lexicon.stopwords


def test_every_lexicon_term_is_in_the_contract() -> None:
    """The load-time guarantee, asserted: no phrase can name a tag no clip can carry."""
    allowed = {
        "interaction": {t.value for t in InteractionTag},
        "contact": {t.value for t in ContactTag},
        "posture": {p.value for p in Posture},
    }
    for tokens, terms in load_lexicon().phrases.items():
        for term in terms:
            field, _, value = term.partition(":")
            assert field in allowed, f"{' '.join(tokens)} targets unknown field {field}"
            assert value in allowed[field], f"{' '.join(tokens)} targets unknown {term}"


def test_every_absent_tag_is_still_reachable() -> None:
    """The gap is the shooting list, so the words for it have to expand to something."""
    reachable = {t.partition(":")[2] for terms in load_lexicon().phrases.values() for t in terms}
    assert {t.value for t in ABSENT_INTERACTIONS} <= reachable


def test_a_broken_lexicon_names_the_phrase(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"version": "1.0.0", "min_word_length": 3, "stopwords": [],'
        ' "entries": [{"phrase": "hug", "targets": ["interaction:snuggle"]}]}'
    )
    with pytest.raises(LexiconError, match="snuggle"):
        load_lexicon(bad)


def test_normalize_folds_case_diacritics_and_apostrophes() -> None:
    assert normalize("She's at the CAFÉ") == ("shes", "at", "the", "cafe")
    assert normalize("head_shoulder") == ("head_shoulder",)
    assert normalize("slow-dance, kiss!") == ("slow", "dance", "kiss")


def test_longest_phrase_first_is_consuming() -> None:
    """Once "head on shoulder" matches, the bare word "head" must not fire from inside it."""
    phrase = expand("head on shoulder")
    assert phrase.matched_phrases == ("head on shoulder",)
    assert phrase.terms == ("interaction:head_on_shoulder", "contact:head_shoulder")

    bare = expand("head")
    assert "contact:head_chest" in bare.terms
    assert "contact:head_chest" not in phrase.terms


def test_expansion_is_sorted_deduped_and_deterministic() -> None:
    first = expand("hug, hugging, an embrace and a hug")
    second = expand("hug, hugging, an embrace and a hug")
    assert first == second
    assert first.terms == ("interaction:hug", "contact:torso")
    # Field order (interaction, contact, posture) then alphabetical, never insertion order.
    walking = expand("they walk together holding hands")
    assert walking.terms == (
        "interaction:hold_hands_walk",
        "interaction:walk_together",
        "contact:hands",
        "posture:walking",
    )


def test_stopwords_and_short_residue_are_dropped() -> None:
    expansion = expand("a shot of two people in an unlit barn")
    assert expansion.unmatched_words == ("barn", "people", "unlit")


def test_operator_sentence_expands_to_the_comfort_and_head_shoulder_terms() -> None:
    expansion = expand(OPERATOR_SENTENCE)
    assert expansion.terms == (
        "interaction:hand_on_shoulder",
        "interaction:hands_on_shoulders",
        "interaction:head_on_shoulder",
        "interaction:meet_and_sit",
        "contact:head_shoulder",
        "contact:shoulder",
        "posture:sitting",
    )
    assert expansion.absent_terms == ("head_on_shoulder",)
    assert expansion.unmatched_words == ()


# --- the MATCH expression ------------------------------------------------------------------


def test_build_match_expression_is_fts5_syntax() -> None:
    expression = build_match_expression(expand(OPERATOR_SENTENCE))
    assert expression == (
        'interaction:"hand_on_shoulder" OR interaction:"hands_on_shoulders"'
        ' OR interaction:"head_on_shoulder" OR interaction:"meet_and_sit"'
        ' OR contact:"head_shoulder" OR contact:"shoulder" OR posture:"sitting"'
    )


def test_unmatched_words_are_searched_in_the_caption() -> None:
    expression = build_match_expression(expand("hug in a barn"))
    assert expression == 'interaction:"hug" OR contact:"torso" OR caption:"barn"'


def test_an_expression_asking_for_nothing_is_empty() -> None:
    assert build_match_expression(Expansion(text="")) == ""
    assert build_match_expression(expand("of the and to")) == ""


def test_a_word_can_never_be_read_as_fts_syntax() -> None:
    expression = build_match_expression(expand('hug OR "malformed'))
    assert expression == 'interaction:"hug" OR contact:"torso" OR caption:"malformed"'


# --- the tokenizer -------------------------------------------------------------------------


def test_the_tokenizer_keeps_the_underscore() -> None:
    """``tokenchars \'_\'`` is mandatory, and this is what it buys.

    With it, ``head_shoulder`` is one token, so a tag search means the tag. Without it unicode61
    splits on the underscore and the same MATCH string quietly turns into the two-word phrase
    "head shoulder", which real prose contains: a caption reading "her head, shoulder and back"
    then answers a query for head-on-shoulder contact.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(f'CREATE VIRTUAL TABLE kept USING fts5(contact, caption, tokenize = "{TOKENIZE}")')
    conn.execute('CREATE VIRTUAL TABLE split USING fts5(contact, caption, tokenize = "unicode61")')
    rows = (
        (1, "head_shoulder", "she rests her head on his shoulder"),
        (2, "back shoulder", "his hand moves over her head, shoulder and back"),
    )
    for table in ("kept", "split"):
        for rowid, contact, caption in rows:
            conn.execute(
                f"INSERT INTO {table}(rowid, contact, caption) VALUES(?, ?, ?)",
                (rowid, contact, caption),
            )

    def hits(table: str, expression: str) -> list[int]:
        sql = f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY rowid"
        return [row[0] for row in conn.execute(sql, (expression,))]

    assert hits("kept", 'contact:"head_shoulder"') == [1]
    assert hits("kept", '"head_shoulder"') == [1], "one token, so only the tagged clip"
    assert hits("split", '"head_shoulder"') == [1, 2], "shredded into a phrase, wrong clip matches"
    assert hits("kept", 'caption:"head_shoulder"') == []
    assert hits("split", 'caption:"head_shoulder"') == [2]
    conn.close()


# --- ranked rows ---------------------------------------------------------------------------


def test_the_operator_sentence_returns_the_comfort_clips(index_path: Path) -> None:
    query = ReferenceQuery(
        text=OPERATOR_SENTENCE,
        people_count=2,
        require_affection=Affection.affection,
        limit=10,
    )
    result = search(index_path, query)

    assert result.library_id == "reference_test"
    assert result.retriever == "fts5_bm25"
    assert result.retriever_version == RETRIEVER_VERSION
    assert result.lexicon_sha256 == lexicon_sha256()
    assert result.absent_terms == ("head_on_shoulder",)
    assert result.unmatched_words == ()

    returned = [m.clip_id for m in result.matches]
    assert "cmu_33_34_01" not in returned, "a football clip is not an answer to this"
    assert "cmu_18_19_04" not in returned, "the aggression clip is filtered out by SQL"
    # The four one-and-two-hands-on-shoulders comfort clips, on an interaction term and a contact
    # term each, with identical text and so identical scores, tied apart by clip id.
    assert returned[1:5] == list(COMFORT_CLIPS)
    assert len({m.score for m in result.matches[1:5]}) == 1
    for match in result.matches[1:5]:
        assert match.matched_contact == ("shoulder",)
        assert match.matched_interaction in (("hand_on_shoulder",), ("hands_on_shoulders",))
        assert "comforts" in match.snippet
    # "beside" is what puts meet_and_sit first: it is the rarest tag in the query, so bm25 gives
    # it the most idf. The two clips matched on shoulder contact alone come last.
    assert returned[0] == "cmu_18_19_12"
    assert returned[5:] == ["cmu_22_23_03", "cmu_22_23_09"]
    assert result.matches[5].score < result.matches[4].score


def test_head_on_shoulder_matches_nothing_but_is_reported(index_path: Path) -> None:
    """The library's honest gap: understood, searched for, absent."""
    result = search(index_path, ReferenceQuery(text="she rests her head on his shoulder"))
    assert "head_on_shoulder" in result.absent_terms
    assert 'interaction:"head_on_shoulder"' in result.match_expression
    for match in result.matches:
        assert "head_on_shoulder" not in match.matched_interaction


def test_an_absent_tag_with_no_near_miss_returns_nothing(index_path: Path) -> None:
    result = search(index_path, ReferenceQuery(text="a couple slow dancing"))
    assert result.absent_terms == ("slow_dance",)
    assert result.matches == ()


def test_scalar_constraints_are_sql_not_fts_terms(index_path: Path) -> None:
    text = "they walk holding hands"
    wide = search(index_path, ReferenceQuery(text=text, limit=10))
    narrow = search(
        index_path,
        ReferenceQuery(text=text, limit=10, max_duration_s=2.0, require_pose=True),
    )
    assert wide.match_expression == narrow.match_expression
    for word in ("affection", "people_count", "duration", "pose_format"):
        assert word not in wide.match_expression
    assert [m.clip_id for m in narrow.matches] == ["cmu_22_23_08"]
    assert len(wide.matches) > 1


def test_required_tags_filter_rather_than_rank(index_path: Path) -> None:
    query = ReferenceQuery(
        text="two people sitting",
        require_contact=(ContactTag.shoulder,),
        require_posture=(Posture.sitting,),
        limit=10,
    )
    result = search(index_path, query)
    # Both survivors carry shoulder contact and a sitting posture; the standing comfort clips and
    # the sitting-but-untouched meet_and_sit clip are gone, and neither tag is in the expression.
    assert [m.clip_id for m in result.matches] == ["cmu_22_23_03", "cmu_22_23_09"]
    assert result.match_expression == 'posture:"sitting" OR caption:"people"'
    assert "shoulder" not in result.match_expression


def test_source_filter_uses_the_clip_table(index_path: Path) -> None:
    query = ReferenceQuery(text="hold hands", sources=(ReferenceSource.tv_human_interactions,))
    assert search(index_path, query).matches == ()
    query = ReferenceQuery(text="hold hands", sources=(ReferenceSource.cmu_mocap,))
    # The hold-hands clip first on its interaction tag, then a clip that merely has hand contact.
    assert [m.clip_id for m in search(index_path, query).matches] == [
        "cmu_22_23_08",
        "cmu_18_19_04",
    ]


def test_ties_break_on_source_then_clip_id(tmp_path: Path) -> None:
    """Two clips with identical text score identically, so the order has to be decided."""
    path = tmp_path / "ties.sqlite"
    # Two clips with the same searchable text and different sources, inserted through the builder
    # so this test cannot carry its own idea of the schema.
    twins = [
        _clip(
            (
                clip_id,
                "kiss",
                "face",
                "standing",
                "affection",
                50,
                2.0,
                0.05,
                "a kiss",
            )
        ).model_copy(update={"source": ReferenceSource(source)})
        for clip_id, source in (("aaa_twin", "tv_human_interactions"), ("zzz_twin", "cmu_mocap"))
    ]
    _build_index(path, rows=ROWS)
    from content_factory.reference import index as index_mod

    with sqlite3.connect(path) as conn:
        for order, twin in enumerate(twins, start=99):
            index_mod.insert_clip(conn, twin, order)
    result = search(path, ReferenceQuery(text="kissing"))
    assert [m.clip_id for m in result.matches] == ["zzz_twin", "aaa_twin"]
    assert result.matches[0].score == result.matches[1].score


def test_the_limit_is_respected(index_path: Path) -> None:
    result = search(index_path, ReferenceQuery(text="comfort her on the shoulder", limit=2))
    assert len(result.matches) == 2
    assert [m.rank for m in result.matches] == [0, 1]


def test_the_same_query_twice_gives_the_same_bytes(index_path: Path) -> None:
    query = ReferenceQuery(text=OPERATOR_SENTENCE, people_count=2)
    assert search(index_path, query).canonical_json() == search(index_path, query).canonical_json()


def test_a_missing_index_is_an_empty_answer_not_a_crash(tmp_path: Path) -> None:
    result = search(tmp_path / "not_built_yet.sqlite", ReferenceQuery(text=OPERATOR_SENTENCE))
    assert result.matches == ()
    assert result.library_id == UNKNOWN_LIBRARY
    assert result.expanded_terms
    assert result.absent_terms == ("head_on_shoulder",)


def test_an_empty_library_is_an_empty_answer(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    _build_index(path, rows=())
    result = search(path, ReferenceQuery(text=OPERATOR_SENTENCE))
    assert result.matches == ()
    assert result.library_id == "reference_test"
    assert result.match_expression


def test_the_library_id_is_read_from_either_manifest_shape(tmp_path: Path) -> None:
    """The builder may store its manifest as key-value rows or as one document. Both are read."""
    path = _build_index(tmp_path / "doc_shaped.sqlite")
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE library")
        conn.execute("CREATE TABLE library (id INTEGER PRIMARY KEY CHECK (id = 1), doc TEXT)")
        conn.execute(
            "INSERT INTO library (id, doc) VALUES (1, ?)",
            (json.dumps({"library_id": "reference_v1", "clip_count": len(ROWS)}),),
        )
    assert search(path, ReferenceQuery(text="hold hands")).library_id == "reference_v1"

    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM library")
    assert search(path, ReferenceQuery(text="hold hands")).library_id == UNKNOWN_LIBRARY


def test_a_sentence_of_nothing_but_stopwords_is_an_empty_answer(index_path: Path) -> None:
    result = search(index_path, ReferenceQuery(text="of the and to"))
    assert result.match_expression == ""
    assert result.matches == ()


# --- against what is actually on disk ------------------------------------------------------

MANIFEST = Path("/mnt/fast/models/blender-assets/clips/manifest.json")


@pytest.mark.skipif(
    not Path("/mnt/fast/reference").is_dir(), reason="reference library not on this host"
)
def test_the_lexicon_can_name_every_baked_clip_and_every_verified_class() -> None:
    """A vocabulary the operator cannot reach is a clip nobody will ever retrieve."""
    if not MANIFEST.is_file():
        pytest.skip("baked clip manifest not on this host")
    clips = json.loads(MANIFEST.read_text())["clips"]
    reachable = {t for terms in load_lexicon().phrases.values() for t in terms}
    for clip in clips:
        assert f"interaction:{clip['interaction']}" in reachable, clip["name"]
        for tag in clip["contact"]:
            assert f"contact:{tag}" in reachable, clip["name"]
        for tag in clip["posture"]:
            assert f"posture:{tag}" in reachable, clip["name"]

    # The verified class maps, in the words someone would actually type for them.
    for words, term in (
        ("hugging", "interaction:hug"),
        ("shaking hands", "interaction:handshake"),
        ("exchanging objects", "interaction:exchange_object"),
        ("approaching", "interaction:approach"),
        ("departing", "interaction:depart"),
        ("kicking", "interaction:kick"),
        ("pushing", "interaction:push"),
        ("punching", "interaction:punch"),
        ("a kiss", "interaction:kiss"),
        ("high five", "interaction:high_five"),
        ("pointing at him", "interaction:point"),
    ):
        assert term in expand(words).terms, words


def test_require_pose_excludes_a_bounding_box(tmp_path: Path) -> None:
    """ "Something can be driven from it" is a higher bar than "it has some geometry".

    A bounding box says where a person was, not how they were standing, so no rig can be aimed by
    one. Admitting bbox_only made a search for drivable material return television clips whose only
    geometry is a rectangle.
    """
    from content_factory.reference import index as index_mod

    def clip(clip_id: str, pose_format: str) -> ReferenceClip:
        return ReferenceClip(
            clip_id=clip_id,
            source=ReferenceSource.cmu_mocap,
            source_ref=clip_id,
            modality=Modality.mocap_segments,
            usage=UsageClass.pose_derivable,
            people_count=2,
            affection=Affection.affection,
            interaction_tags=(InteractionTag.hug,),
            contact_tags=(ContactTag.torso,),
            postures=(Posture.standing,),
            frame_count=10,
            native_fps=24.0,
            duration_s=1.0,
            pose_format=pose_format,  # type: ignore[arg-type]
            pose_root=None if pose_format == "none" else "/tmp/poses",
            caption="two people hug",
            caption_source="operator",
            files=(
                ReferenceFile(
                    role="annotation", path=f"{clip_id}.json", sha256="0" * 64, size_bytes=1
                ),
            ),
            ingested_at="2026-09-07T00:00:00Z",
            ingester_version="0.1.0",
        )

    rows = [
        clip("drivable", "cf_clip_v2"),
        clip("skeleton", "kinect15"),
        clip("boxed", "bbox_only"),
        clip("nothing", "none"),
    ]
    path = tmp_path / "poses.sqlite"
    conn = index_mod.create(path)
    for order, entry in enumerate(rows):
        index_mod.insert_clip(conn, entry, order)
    conn.commit()
    conn.close()

    found = search(path, ReferenceQuery(text="hug", require_pose=True, limit=10))
    assert {m.clip_id for m in found.matches} == {"drivable", "skeleton"}

    everything = search(path, ReferenceQuery(text="hug", limit=10))
    assert {m.clip_id for m in everything.matches} == {c.clip_id for c in rows}
