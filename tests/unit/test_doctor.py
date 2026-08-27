from __future__ import annotations

from content_factory.doctor import Status, run_doctor


def test_doctor_reports_every_check_with_plain_language_fix() -> None:
    report = run_doctor()
    names = {c.name for c in report.checks}
    assert {"config", "docker", "ffmpeg", "node", "uv", "gpu", "disk"} <= names
    for c in report.checks:
        if c.status in {Status.fail, Status.warn}:
            assert c.fix, f"{c.name} has no remediation text"
    assert report.model_dump(mode="json")["checks"]
