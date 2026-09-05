"""No credentials in the finished film.

Every other QC line here is about whether the deliverable is *good*. This one is about whether
publishing it is safe, and it is the only check whose failure cannot be undone: a caption, an
on-screen line or a screenshot caption that carries an API key has been published the moment the
file leaves the machine, and rotating the key afterwards is damage control rather than a fix.

The channel playbook's software-tutorial recipe has carried a "no credentials in output" line since
it was written, with no implementation anywhere in this repo.

Two deliberate limits, because a check that overstates what it covers is worse than one that does
not exist:

* **Text only.** This reads the words that will be rendered — the story plan's display text and
  spoken text, the caption cues, the drafted copy, and any text sidecar beside a capture. It does
  **not** read pixels: there is no OCR in the offline core, so a key visible in a screenshot but
  written nowhere in the plan is not caught here. `facts["ocr"]` says so, in the report, rather
  than leaving a reader to assume it was covered.
* **Named patterns first, entropy only in context.** Every pattern below is a shape that is a
  credential and cannot be anything else — a private-key header, a provider's own token prefix, a
  URL with a password in it. The entropy test fires only on the right-hand side of something that
  reads like an assignment of a secret (``api_key = …``), because a bare high-entropy string in a
  script is a hash, an id or a digest, and blocking a film for one of those teaches an operator to
  ignore the check.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from content_factory.qc.media import Finding, QCResult, Severity


@dataclass(frozen=True)
class SecretScan:
    """A ``QCResult`` with its own facts typed, so a caller can read them without a cast.

    ``scanned`` is what was read and ``images_not_read`` is what was not: there is no OCR in the
    offline core, so a pass here means "no credential in the WORDS" and the shape says so.
    """

    result: QCResult
    scanned: tuple[str, ...]
    images_not_read: tuple[str, ...]

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.result.findings

    @property
    def facts(self) -> dict[str, object]:
        return self.result.facts

    @property
    def passed(self) -> bool:
        return self.result.passed


@dataclass(frozen=True)
class SecretPattern:
    name: str
    pattern: re.Pattern[str]
    why: str


# Shapes that are a credential and cannot be anything else. Each is anchored enough that a match is
# a finding rather than a suspicion.
NAMED_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY"),
        "a private key header",
    ),
    SecretPattern("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "an AWS key"),
    SecretPattern(
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        "a GitHub token",
    ),
    SecretPattern("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "a Slack token"),
    SecretPattern(
        "provider_api_key",
        re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}\b"),
        "an API key in a provider's own format",
    ),
    SecretPattern(
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "a Google API key",
    ),
    SecretPattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "a signed JWT",
    ),
    SecretPattern(
        "bearer_header",
        re.compile(r"\b[Aa]uthorization\s*:\s*(?:Bearer|Basic)\s+\S{8,}"),
        "an Authorization header with its value",
    ),
    SecretPattern(
        "url_password",
        # scheme://user:secret@host — the one form where a password is structurally unambiguous.
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]{3,}@[^\s/]+"),
        "a password inside a URL",
    ),
)

ASSIGNMENT = re.compile(
    r"(?i)\b([a-z0-9_.-]*(?:secret|passwd|password|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|auth)[a-z0-9_.-]*)\s*[:=]\s*"
    r"[\"']?([^\s\"',;]{12,})[\"']?"
)
"""``api_key = <value>`` and its spellings. The name is what makes the value a credential: the same
string under ``sha256 =`` is a digest."""

ENTROPY_MIN = 3.2
"""Shannon bits per character above which a 12+ character assigned value is treated as a secret.
A word ("correcthorse") sits near 2.8; a base64 key sits above 4. Measured on the placeholders this
repo actually contains, so ``password = changeme`` and ``token = REPLACE_ME`` do not fire."""

PLACEHOLDERS = frozenset(
    {
        "changeme",
        "your_api_key",
        "your-api-key",
        "replace_me",
        "replace-me",
        "xxxxxxxxxxxx",
        "placeholder",
        "example",
        "redacted",
        "<redacted>",
        "none",
        "null",
    }
)
"""Values that are visibly not a credential. An operator who writes ``token = REPLACE_ME`` into a
tutorial slide is doing the right thing and must not be blocked for it."""


def shannon_bits(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def scan_text(text: str, *, where: str = "text") -> list[Finding]:
    """Every credential-shaped thing in one string, as blocker findings.

    The finding never repeats the secret. It names where it is and what shape it has, which is
    everything an operator needs to find it — and a QC report is itself an artifact that gets read,
    copied into a ticket and pasted into a chat, so quoting the value would leak it a second time.
    """
    out: list[Finding] = []
    for spec in NAMED_PATTERNS:
        hits = spec.pattern.findall(text)
        if hits:
            out.append(
                Finding(
                    f"secret:{spec.name}",
                    Severity.blocker,
                    f"{where} contains {spec.why} ({len(hits)} occurrence(s))."
                    " The value is not repeated here; search the source for the pattern.",
                )
            )
    for name, value in ASSIGNMENT.findall(text):
        if value.strip().lower().strip("<>\"'") in PLACEHOLDERS:
            continue
        bits = shannon_bits(value)
        if bits >= ENTROPY_MIN:
            out.append(
                Finding(
                    "secret:assigned_value",
                    Severity.blocker,
                    f"{where} assigns {name!r} a {len(value)}-character high-entropy value"
                    f" ({bits:.1f} bits/char). Redact it before this ships.",
                )
            )
    return out


def _story_texts(project_dir: Path, deliverable_id: str | None) -> Iterable[tuple[str, str]]:
    for candidate in (
        project_dir / "story" / "shorts" / f"{deliverable_id}.plan.json",
        project_dir / "story" / "plan.json",
    ):
        if not candidate.exists():
            continue
        try:
            plan = json.loads(candidate.read_text())
        except ValueError:
            return
        for beat in plan.get("beats", []):
            for field in ("display_text", "spoken_text"):
                if beat.get(field):
                    yield f"story beat {beat.get('beat_id')} {field}", beat[field]
        if plan.get("visual_subject"):
            yield "story visual_subject", plan["visual_subject"]
        if plan.get("hook_text"):
            yield "story hook_text", plan["hook_text"]
        return


def scan_deliverable(
    project_dir: Path, ddir: Path, deliverable_id: str | None = None
) -> SecretScan:
    """Every rendered word of one deliverable, plus the text sidecars beside its captures.

    ``facts["scanned"]`` names what was read and ``facts["ocr"]`` says plainly that pixels were
    not, so a passing report cannot be mistaken for "no key is visible in the video".
    """
    sources: list[tuple[str, str]] = list(_story_texts(project_dir, deliverable_id))
    for name in ("captions.srt", "captions.vtt", "captions.ass"):
        path = ddir / "captions" / name
        if path.exists():
            sources.append((f"captions/{name}", path.read_text(errors="replace")))
    copy_path = ddir / "copy.json"
    if copy_path.exists():
        sources.append(("copy.json", copy_path.read_text(errors="replace")))
    script = ddir / "script" / "locked.json"
    if script.exists():
        sources.append(("script/locked.json", script.read_text(errors="replace")))
    # A capture's own sidecar: whatever the capture step wrote about what is on screen.
    captures = ddir / "captures"
    for sidecar in sorted(captures.glob("**/*.json")) + sorted(captures.glob("**/*.txt")):
        sources.append(
            (f"captures/{sidecar.relative_to(captures)}", sidecar.read_text(errors="replace"))
        )

    findings: list[Finding] = []
    for where, text in sources:
        findings.extend(scan_text(text, where=where))
    images = tuple(sorted(p.name for p in captures.glob("**/*.png"))) if captures.exists() else ()
    scanned = tuple(where for where, _ in sources)
    return SecretScan(
        QCResult(
            tuple(findings),
            {
                "scanned": list(scanned),
                "patterns": len(NAMED_PATTERNS) + 1,
                # Said out loud rather than implied: a pass here is "no credential in the WORDS".
                "ocr": False,
                "unscanned_images": list(images),
            },
        ),
        scanned,
        images,
    )
