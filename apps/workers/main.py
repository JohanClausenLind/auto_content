"""Worker entry point: `uv run python apps/workers/main.py --queue control`."""

from __future__ import annotations

import argparse

from content_factory.workflows.worker import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default="control")
    main(parser.parse_args().queue)
