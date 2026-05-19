from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


class InvalidRepositoryURL(ValueError):
    """Raised when the supplied URL is not a recognizable GitHub repository URL."""


@dataclass(frozen=True)
class ParsedRepo:
    url: str
    owner: str
    repo: str

    @property
    def name(self) -> str:
        return f"{self.owner}/{self.repo}"


_GITHUB_PATH_RE = re.compile(r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def sanitize_repo_url(raw_url: str) -> ParsedRepo:
    if not raw_url or not isinstance(raw_url, str):
        raise InvalidRepositoryURL("repository_url must be a non-empty string")

    cleaned = raw_url.strip()
    if not cleaned:
        raise InvalidRepositoryURL("repository_url cannot be blank")

    if not re.match(r"^https?://", cleaned, flags=re.IGNORECASE):
        cleaned = "https://" + cleaned

    parsed = urlparse(cleaned)
    host = (parsed.netloc or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise InvalidRepositoryURL(f"Not a github.com URL: {raw_url}")

    match = _GITHUB_PATH_RE.match(parsed.path or "")
    if not match:
        raise InvalidRepositoryURL(
            f"URL must look like https://github.com/<owner>/<repo>: {raw_url}"
        )

    owner = match.group("owner").strip()
    repo = match.group("repo").strip()
    if not owner or not repo:
        raise InvalidRepositoryURL(f"Missing owner or repo in URL: {raw_url}")

    canonical = f"https://github.com/{owner}/{repo}"
    return ParsedRepo(url=canonical, owner=owner, repo=repo)
