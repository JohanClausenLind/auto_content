"""Post-chain runner: one job file in, a normalised frame directory + one JSON summary line out.

    uv run --project skills/video/postchain python skills/video/postchain/run.py <job.json>

job.json: {"tool": "cutie|propainter|seedvr2|gimm_vfi|rife", "frames_dir": "...", "out_dir": "...",
           "params": {...}}   (see tools/*.py for each tool's params)

Every tool writes ``<out_dir>/frames/%04d.png`` (Cutie writes masks there) so the stages can chain
them without caring which tool ran. Exit codes: 0 ok · 2 bad job · 3 tool failed · 4 missing
interpreter/weights.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from common import Job, ToolError, emit  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        emit({"ok": False, "exit": 2, "error": "usage: run.py <job.json>"})
        return 2
    started = time.time()
    try:
        job = Job.load(Path(argv[0]))
    except (OSError, KeyError, ValueError, ToolError) as exc:
        emit({"ok": False, "exit": 2, "error": f"bad job: {exc}"})
        return 2
    try:
        from tools import cutie, gimm_vfi, propainter, rife, seedvr2

        runner = {
            "cutie": cutie.run,
            "propainter": propainter.run,
            "seedvr2": seedvr2.run,
            "gimm_vfi": gimm_vfi.run,
            "rife": rife.run,
        }[job.tool]
        result = runner(job, started)
    except ToolError as exc:
        code = 4 if "interpreter" in str(exc) or "weights" in str(exc) else 3
        emit({"ok": False, "exit": code, "tool": job.tool, "error": str(exc)})
        return code
    except Exception as exc:
        emit(
            {
                "ok": False,
                "exit": 3,
                "tool": job.tool,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-1500:],
            }
        )
        return 3
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
