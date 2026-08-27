"""Thin HTTP client used by CLI commands (same API the web app uses)."""

from __future__ import annotations

from typing import Any

import httpx

from content_factory.api.deps import COOKIE_NAME
from content_factory.cli.context import CliContext


class ApiError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class ApiClient:
    def __init__(self, ctx: CliContext) -> None:
        self.ctx = ctx
        cookies = {COOKIE_NAME: ctx.session_cookie} if ctx.session_cookie else {}
        self._http = httpx.Client(base_url=ctx.api_url, cookies=cookies, timeout=30)

    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        r = self._http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ApiError(r.status_code, detail)
        return r

    def login(self, username: str, password: str) -> dict[str, Any]:
        r = self.request("POST", "/v1/session", json={"username": username, "password": password})
        cookie = r.cookies.get(COOKIE_NAME)
        if cookie:
            self.ctx.session_cookie = cookie
            self._http.cookies.set(COOKIE_NAME, cookie)
        return r.json()

    def totp(self, code: str) -> dict[str, Any]:
        return self.request("POST", "/v1/session/totp", json={"code": code}).json()

    def session(self) -> dict[str, Any]:
        return self.request("GET", "/v1/session").json()

    def logout(self) -> None:
        self.request("DELETE", "/v1/session")
        self.ctx.session_cookie = None

    def switch_workspace(self, workspace_id: str) -> dict[str, Any]:
        return self.request(
            "POST", "/v1/session/workspace", json={"workspace_id": workspace_id}
        ).json()

    def workspaces(self) -> list[dict[str, Any]]:
        return self.request("GET", "/v1/workspaces").json()
