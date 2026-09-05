"""No credentials in the finished film.

Every other QC line in this repo asks whether the deliverable is good. This one asks whether
publishing it is safe, and it is the only check whose failure cannot be undone: a caption or an
on-screen line carrying an API key is published the moment the file leaves the machine, and
rotating the key afterwards is damage control rather than a fix. The channel playbook's
software-tutorial recipe has carried a "no credentials in output" line since it was written and
nothing in this repo implemented it.

Half these tests are about *not* firing. A check that blocks a film for a git sha teaches an
operator to reach for --force, which is the one response it must never get.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.qc.secrets import (
    ENTROPY_MIN,
    NAMED_PATTERNS,
    scan_deliverable,
    scan_text,
    shannon_bits,
)

LEAKS: dict[str, str] = {
    "private_key": "then paste -----BEGIN RSA PRIVATE KEY----- into the field",
    "aws_access_key_id": "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
    "github_token": "git remote set-url origin https://ghp_" + "a" * 36 + "@github.com/x/y",
    "slack_token": "SLACK=xoxb-1234567890-abcdefghij",
    "provider_api_key": "client = Foo(api_key='sk-" + "b" * 32 + "')",
    "google_api_key": "maps key AIza" + "c" * 35,
    "jwt": "Cookie: session=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dBjftJeZ4CVPmB92K27uhbUJU1p",
    "bearer_header": "Authorization: Bearer 9f8a7b6c5d4e3f2a1b",
    "url_password": "psql postgres://admin:h7Qz2p9Lx@db.internal/app",
}


def test_every_named_pattern_has_a_case_that_fires_it() -> None:
    """A pattern nothing exercises is a pattern nobody knows is wrong."""
    assert {p.name for p in NAMED_PATTERNS} == set(LEAKS)
    for name, text in LEAKS.items():
        findings = scan_text(text, where="slide")
        checks = {f.check for f in findings}
        assert f"secret:{name}" in checks, (name, findings)
        assert all(f.severity.value in ("blocker", "critical") for f in findings)


def test_a_finding_never_repeats_the_secret() -> None:
    """A QC report is itself an artifact: it gets read, pasted into a ticket and into chat. A
    finding that quotes the value leaks it a second time."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    findings = scan_text(f"export AWS_ACCESS_KEY_ID={secret}", where="captions/captions.srt")
    assert findings and all(secret not in f.message for f in findings)
    assert "captions/captions.srt" in findings[0].message


def test_an_assigned_high_entropy_value_fires_and_a_low_entropy_one_does_not() -> None:
    hot = scan_text("api_key = 'Zq8Xv3Lp0Rt7Ke2Ny5Mb'", where="slide")
    assert [f.check for f in hot] == ["secret:assigned_value"]
    assert "20-character" in hot[0].message and "api_key" in hot[0].message
    # The name is what makes the value a credential. The same string under a digest key is a digest.
    assert scan_text("sha256 = 'Zq8Xv3Lp0Rt7Ke2Ny5Mb'", where="slide") == []
    # And a visible placeholder is an operator doing the right thing.
    for value in ("changeme", "REPLACE_ME", "<redacted>", "your_api_key"):
        assert scan_text(f"password = {value}", where="slide") == [], value


def test_the_ordinary_words_of_a_film_do_not_fire_it() -> None:
    """The false-positive budget. Every one of these appears in this repo's own fixtures, docs or
    commit messages, and a check that blocks a film for one of them will be turned off."""
    clean = [
        "In 2025, wind supplied about a fifth of Sweden's electricity.",
        "the commit is 9f4af66a1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f",
        "sha256: 34e69da8b2c663ef1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7081",
        "see https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/",
        "the file is at /mnt/fast/models/ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf",
        "set CF__IMAGE_SEQUENCES__BACKEND=hidream before the run",
        "password reset takes about thirty seconds",
        "Authorization is handled by the platform adapter",
    ]
    for line in clean:
        assert scan_text(line, where="slide") == [], line
    assert shannon_bits("correcthorse") < ENTROPY_MIN < shannon_bits("Zq8Xv3Lp0Rt7Ke2Ny5Mb")


@pytest.fixture
def deliverable(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    ddir = project / "deliverables" / "dlv_short0000001"
    (project / "story").mkdir(parents=True)
    (project / "story" / "plan.json").write_text(
        json.dumps(
            {
                "beats": [
                    {"beat_id": "bet_1", "display_text": "First, open the settings page."},
                    {
                        "beat_id": "bet_2",
                        "display_text": "Paste your key.",
                        "spoken_text": "Paste your key.",
                    },
                ],
                "visual_subject": "a laptop on a desk",
                "hook_text": "Set this up in two minutes",
            }
        )
    )
    (ddir / "captions").mkdir(parents=True)
    (ddir / "captions" / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n")
    return project, ddir


def test_a_clean_deliverable_passes_and_says_what_it_did_not_read(
    deliverable: tuple[Path, Path],
) -> None:
    project, ddir = deliverable
    (ddir / "captures").mkdir()
    (ddir / "captures" / "step-01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    result = scan_deliverable(project, ddir, "dlv_short0000001")
    assert result.passed and result.findings == ()
    assert "story beat bet_1 display_text" in result.scanned
    assert "captions/captions.srt" in result.scanned
    # Said out loud: a pass here is "no credential in the WORDS". There is no OCR in the offline
    # core, so a key visible only in a screenshot is not covered, and the report must not imply it.
    assert result.facts["ocr"] is False
    assert result.images_not_read == ("step-01.png",)


def test_a_key_in_a_beat_a_caption_or_a_capture_sidecar_blocks_the_deliverable(
    deliverable: tuple[Path, Path],
) -> None:
    project, ddir = deliverable
    plan = json.loads((project / "story" / "plan.json").read_text())
    plan["beats"][1]["display_text"] = "Paste sk-" + "d" * 32 + " into the field"
    (project / "story" / "plan.json").write_text(json.dumps(plan))
    (ddir / "captions" / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nAuthorization: Bearer 9f8a7b6c5d4e3f2a\n"
    )
    (ddir / "captures").mkdir()
    (ddir / "captures" / "step-01.json").write_text(
        json.dumps({"note": "shell shows export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"})
    )
    result = scan_deliverable(project, ddir, "dlv_short0000001")
    assert not result.passed
    wheres = " ".join(f.message for f in result.findings)
    assert "story beat bet_2 display_text" in wheres
    assert "captions/captions.srt" in wheres
    assert "captures/step-01.json" in wheres
    # Two of the three fire twice, by design: `AWS_ACCESS_KEY_ID=AKIA…` is both a named AWS key
    # and a high-entropy value assigned to a key-shaped name, and both facts are true.
    assert {
        "secret:provider_api_key",
        "secret:bearer_header",
        "secret:aws_access_key_id",
    } <= {f.check for f in result.findings}


def test_a_short_reads_its_own_plan(deliverable: tuple[Path, Path]) -> None:
    """A documentary's shorts narrate their own derived plans (item 5b), so the scan has to read
    the one this deliverable actually speaks — not the episode's."""
    project, ddir = deliverable
    shorts = project / "story" / "shorts"
    shorts.mkdir()
    (shorts / "dlv_short0000001.plan.json").write_text(
        json.dumps(
            {"beats": [{"beat_id": "bet_9", "display_text": "token = Zq8Xv3Lp0Rt7Ke2Ny5Mb"}]}
        )
    )
    result = scan_deliverable(project, ddir, "dlv_short0000001")
    assert not result.passed
    assert any("bet_9" in f.message for f in result.findings)
    # And the episode plan's beats were not read for this deliverable.
    assert not any("bet_1" in where for where in result.scanned)


def test_the_qc_stage_blocks_on_a_leak(tmp_path: Path) -> None:
    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import stage_qc_deliverable

    ctx = make_context(project_dir=tmp_path / "project", brief={"topic": "how to set up a key"})
    story = ctx.project_dir / "story"
    story.mkdir(parents=True)
    (story / "plan.json").write_text(
        json.dumps(
            {"beats": [{"beat_id": "bet_1", "display_text": "use ghp_" + "e" * 36 + " here"}]}
        )
    )
    with pytest.raises(RuntimeError, match="no_credentials_in_output"):
        stage_qc_deliverable(ctx)
    report = json.loads((ctx.ddir() / "qc" / "report.json").read_text())
    check = report["checks"]["no_credentials_in_output"]
    assert check["passed"] is False
    assert check["findings"][0]["check"] == "secret:github_token"
    assert check["facts"]["ocr"] is False
    # The plan written above is a stub, not a valid StoryPlan. QC records that as its own finding
    # and carries on: an unreadable input must never suppress the one check whose failure cannot
    # be undone. This is what an earlier version of the scene-kind check did by raising.
    kinds = report["checks"]["scene_kinds_implemented"]
    assert kinds["passed"] is False and kinds["facts"]["story_plan"] == "unreadable"
