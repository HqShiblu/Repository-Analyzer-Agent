from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class GitHubAPIError(RuntimeError):
    """Raised when the GitHub API returns a non-success response."""


@dataclass
class TreeEntry:
    path: str
    type: str  # "blob" or "tree"
    size: int | None


class GitHubClient:

    def __init__(self, owner: str, repo: str, token: str | None = None) -> None:
        self.owner = owner
        self.repo = repo
        self._session = requests.Session()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codefusion-research-agent",
        }
        actual_token = token if token is not None else settings.GITHUB_TOKEN
        if actual_token:
            headers["Authorization"] = f"Bearer {actual_token}"
        self._session.headers.update(headers)


    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{settings.GITHUB_API_BASE}{path}"
        try:
            resp = self._session.get(url, params=params, timeout=20)
        except requests.RequestException as exc:
            raise GitHubAPIError(f"GitHub request failed: {exc}") from exc

        if resp.status_code == 404:
            raise GitHubAPIError(f"Not found: {url}")
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            raise GitHubAPIError(
                "GitHub API rate limit reached. Set GITHUB_TOKEN in .env."
            )
        if not resp.ok:
            raise GitHubAPIError(
                f"GitHub API {resp.status_code} for {url}: {resp.text[:200]}"
            )
        return resp.json()


    def get_directory_tree(self, ref: str = "HEAD") -> list[TreeEntry]:
        """Return the full recursive tree of the repo at `ref`."""
        data = self._get(
            f"/repos/{self.owner}/{self.repo}/git/trees/{ref}",
            params={"recursive": "1"},
        )
        tree = data.get("tree", []) or []
        return [
            TreeEntry(path=item["path"], type=item.get("type", "blob"), size=item.get("size"))
            for item in tree
        ]

    def list_contents(self, path: str = "") -> list[dict]:
        """List files/directories at `path`."""
        path = path.strip("/")
        data = self._get(f"/repos/{self.owner}/{self.repo}/contents/{path}")
        if isinstance(data, dict):
            return [data]
        return data

    def read_file_raw(self, path: str) -> str:
        """Return the file content as plain text (no line-number prefix)."""
        path = path.strip("/")
        data = self._get(f"/repos/{self.owner}/{self.repo}/contents/{path}")
        if isinstance(data, list):
            raise GitHubAPIError(f"{path} is a directory, not a file")
        content_b64 = data.get("content", "")
        if not content_b64:
            return ""
        try:
            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception as exc:
            raise GitHubAPIError(f"Could not decode {path}: {exc}") from exc

    def read_file(self, path: str) -> str:
        """Return the file content with a leading line-number column."""
        return _prepend_line_numbers(self.read_file_raw(path))

    def get_file_summary(self, path: str, max_lines: int = 80) -> str:
        """Return the first `max_lines` lines of a file (with line numbers)."""
        content = self.read_file(path)
        lines = content.splitlines()
        head = lines[:max_lines]
        suffix = "" if len(lines) <= max_lines else f"\n... [{len(lines) - max_lines} more lines truncated]"
        return "\n".join(head) + suffix

    def search_code(self, query: str, max_results: int = 10) -> list[dict]:
        """Search code inside this repo. Returns list of {file_path, snippet}."""
        q = f"{query} repo:{self.owner}/{self.repo}"
        try:
            data = self._get("/search/code", params={"q": q, "per_page": max_results})
        except GitHubAPIError as exc:
            logger.warning("search_code failed: %s", exc)
            return []
        results: list[dict] = []
        for item in (data.get("items") or [])[:max_results]:
            results.append(
                {
                    "file_path": item.get("path"),
                    "html_url": item.get("html_url"),
                    "snippet": (item.get("text_matches") or [{}])[0].get(
                        "fragment", ""
                    ),
                }
            )
        return results


def _prepend_line_numbers(text: str) -> str:
    lines = text.splitlines()
    width = max(3, len(str(len(lines))))
    return "\n".join(f"{i:>{width}} | {line}" for i, line in enumerate(lines, start=1))
