from __future__ import annotations

import pytest

from agent.services.sanitizer import InvalidRepositoryURL, sanitize_repo_url


class TestSanitizer:
    def test_strips_trailing_slash(self):
        result = sanitize_repo_url("https://github.com/tiangolo/fastapi/")
        assert result.url == "https://github.com/tiangolo/fastapi"
        assert result.owner == "tiangolo"
        assert result.repo == "fastapi"

    def test_strips_dot_git_suffix(self):
        result = sanitize_repo_url("https://github.com/owner/repo.git")
        assert result.url == "https://github.com/owner/repo"
        assert result.repo == "repo"

    def test_trims_whitespace(self):
        result = sanitize_repo_url("   https://github.com/a/b   ")
        assert result.url == "https://github.com/a/b"

    def test_adds_https_scheme(self):
        result = sanitize_repo_url("github.com/a/b")
        assert result.url == "https://github.com/a/b"

    def test_name_combines_owner_and_repo(self):
        result = sanitize_repo_url("https://github.com/django/django")
        assert result.name == "django/django"

    def test_rejects_non_github(self):
        with pytest.raises(InvalidRepositoryURL):
            sanitize_repo_url("https://gitlab.com/owner/repo")

    def test_rejects_missing_repo(self):
        with pytest.raises(InvalidRepositoryURL):
            sanitize_repo_url("https://github.com/owner")

    def test_rejects_empty(self):
        with pytest.raises(InvalidRepositoryURL):
            sanitize_repo_url("")

    def test_rejects_none(self):
        with pytest.raises(InvalidRepositoryURL):
            sanitize_repo_url(None)  # type: ignore[arg-type]
