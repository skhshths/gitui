"""Small async GitHub REST API client built on httpx."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

import httpx

API = "https://api.github.com"
USER_AGENT = "gitui-tui/1.0"


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message

    def __str__(self) -> str:
        return f"GitHub {self.status}: {self.message}" if self.status else self.message


_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHub:
    def __init__(self, token: str):
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=API,
            timeout=httpx.Timeout(30.0, connect=15.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ core
    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(0, f"Network error: {exc}") from exc
        if resp.status_code >= 400:
            try:
                data = resp.json()
                msg = data.get("message", resp.text)
                errors = data.get("errors")
                if errors:
                    details = "; ".join(
                        e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors
                    )
                    msg = f"{msg} ({details})"
            except ValueError:
                msg = resp.text or resp.reason_phrase
            raise GitHubError(resp.status_code, msg)
        return resp

    async def get(self, url: str, **params: Any) -> Any:
        resp = await self.request("GET", url, params=params or None)
        return resp.json()

    async def paginate(self, url: str, max_pages: int = 10, **params: Any) -> list[Any]:
        params.setdefault("per_page", 100)
        items: list[Any] = []
        next_url: str | None = url
        first = True
        pages = 0
        while next_url and pages < max_pages:
            resp = await self.request("GET", next_url, params=params if first else None)
            first = False
            pages += 1
            data = resp.json()
            if isinstance(data, dict) and "items" in data:
                data = data["items"]
            items.extend(data)
            m = _LINK_NEXT.search(resp.headers.get("link", ""))
            next_url = m.group(1) if m else None
        return items

    # ------------------------------------------------------------------ user
    async def user(self) -> dict:
        return await self.get("/user")

    async def repos(self) -> list[dict]:
        return await self.paginate(
            "/user/repos",
            sort="updated",
            affiliation="owner,collaborator,organization_member",
        )

    async def starred(self) -> list[dict]:
        return await self.paginate("/user/starred", max_pages=5, sort="updated")

    async def search_repos(self, query: str) -> list[dict]:
        return await self.paginate("/search/repositories", max_pages=1, q=query, per_page=50)

    # ------------------------------------------------------------------ repo
    @staticmethod
    def _r(full_name: str) -> str:
        owner, _, name = full_name.partition("/")
        return f"/repos/{quote(owner)}/{quote(name)}"

    async def repo(self, full_name: str) -> dict:
        return await self.get(self._r(full_name))

    async def readme(self, full_name: str) -> str:
        try:
            resp = await self.request(
                "GET",
                self._r(full_name) + "/readme",
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except GitHubError as exc:
            if exc.status == 404:
                return "_This repository has no README._"
            raise
        return resp.text

    async def issues(self, full_name: str, state: str = "open") -> list[dict]:
        items = await self.paginate(self._r(full_name) + "/issues", max_pages=2, state=state)
        return [i for i in items if "pull_request" not in i]

    async def pulls(self, full_name: str, state: str = "open") -> list[dict]:
        return await self.paginate(self._r(full_name) + "/pulls", max_pages=2, state=state)

    async def pull(self, full_name: str, number: int) -> dict:
        return await self.get(f"{self._r(full_name)}/pulls/{number}")

    async def branches(self, full_name: str) -> list[dict]:
        return await self.paginate(self._r(full_name) + "/branches", max_pages=3)

    async def commits(self, full_name: str, sha: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"per_page": 60}
        if sha:
            params["sha"] = sha
        return await self.get(self._r(full_name) + "/commits", **params)

    async def releases(self, full_name: str) -> list[dict]:
        return await self.get(self._r(full_name) + "/releases", per_page=30)

    async def comments(self, full_name: str, number: int) -> list[dict]:
        return await self.paginate(f"{self._r(full_name)}/issues/{number}/comments", max_pages=3)

    async def create_issue(self, full_name: str, title: str, body: str) -> dict:
        resp = await self.request(
            "POST", self._r(full_name) + "/issues", json={"title": title, "body": body}
        )
        return resp.json()

    async def comment(self, full_name: str, number: int, body: str) -> dict:
        resp = await self.request(
            "POST", f"{self._r(full_name)}/issues/{number}/comments", json={"body": body}
        )
        return resp.json()

    async def set_issue_state(self, full_name: str, number: int, state: str) -> dict:
        resp = await self.request(
            "PATCH", f"{self._r(full_name)}/issues/{number}", json={"state": state}
        )
        return resp.json()

    async def merge_pull(self, full_name: str, number: int, method: str = "merge") -> dict:
        resp = await self.request(
            "PUT", f"{self._r(full_name)}/pulls/{number}/merge", json={"merge_method": method}
        )
        return resp.json()

    async def create_repo(
        self, name: str, description: str, private: bool, auto_init: bool
    ) -> dict:
        resp = await self.request(
            "POST",
            "/user/repos",
            json={
                "name": name,
                "description": description,
                "private": private,
                "auto_init": auto_init,
            },
        )
        return resp.json()

    async def delete_repo(self, full_name: str) -> None:
        await self.request("DELETE", self._r(full_name))

    async def fork(self, full_name: str) -> dict:
        resp = await self.request("POST", self._r(full_name) + "/forks")
        return resp.json()

    async def is_starred(self, full_name: str) -> bool:
        try:
            await self.request("GET", "/user/starred/" + full_name)
            return True
        except GitHubError as exc:
            if exc.status == 404:
                return False
            raise

    async def set_star(self, full_name: str, starred: bool) -> None:
        await self.request("PUT" if starred else "DELETE", "/user/starred/" + full_name)


# ---------------------------------------------------------------- auth flows
async def validate_token(token: str) -> dict:
    gh = GitHub(token)
    try:
        return await gh.user()
    finally:
        await gh.close()


async def device_code_start(client_id: str, scopes: str) -> dict:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        try:
            resp = await c.post(
                "https://github.com/login/device/code",
                data={"client_id": client_id, "scope": scopes},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GitHubError(0, f"Network error: {exc}") from exc
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or "device_code" not in data:
        msg = data.get("error_description") or data.get("error") or resp.text
        if resp.status_code == 404 or data.get("error") == "Not Found":
            msg = "Unknown client ID — check your OAuth App client ID."
        if data.get("error") == "device_flow_disabled":
            msg = "Device flow is disabled. Tick 'Enable Device Flow' in your OAuth App settings."
        raise GitHubError(resp.status_code, msg)
    return data


async def device_code_poll(client_id: str, device_code: str, interval: int, expires_in: int) -> str:
    """Poll until the user approves. Returns the access token."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + expires_in
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        while loop.time() < deadline:
            await asyncio.sleep(interval)
            try:
                resp = await c.post(
                    "https://github.com/login/oauth/access_token",
                    data={
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                continue
            if "access_token" in data:
                return data["access_token"]
            err = data.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = int(data.get("interval", interval + 5))
                continue
            if err == "expired_token":
                raise GitHubError(0, "The code expired. Please try again.")
            if err == "access_denied":
                raise GitHubError(0, "Sign-in was cancelled on GitHub.")
            raise GitHubError(0, data.get("error_description") or str(err))
    raise GitHubError(0, "The code expired. Please try again.")
