"""Uvicorn entry point: `uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000`."""

from content_factory.api.app import app

__all__ = ["app"]
