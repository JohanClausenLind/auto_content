"""A local fixture site for the capture tests; the article can be swapped under the same URL."""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

ARTICLE_PATH = "/article.html"


class _Site(ThreadingHTTPServer):
    article_name = "article.html"


class _Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        # Scoop's proxy forwards "GET http://host:port/x" verbatim; http.server 404s that form.
        path = urlsplit(path).path
        if path == ARTICLE_PATH:
            server: Any = self.server
            path = "/" + server.article_name
        return super().translate_path(path)

    def log_message(self, format: str, *args: Any) -> None:
        return


class FixtureSite:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._server: _Site | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    def serve(self, article_name: str) -> None:
        """Swap the file behind /article.html, standing in for a live page that changed."""
        assert self._server is not None
        assert (self.root / article_name).is_file()
        self._server.article_name = article_name

    def __enter__(self) -> Self:
        handler = partial(_Handler, directory=str(self.root))
        self._server = _Site(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}{ARTICLE_PATH}"
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
