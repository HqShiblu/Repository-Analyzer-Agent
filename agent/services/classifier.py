"""Simple keyword-based question classification.

Maps a natural-language question to a theme and a ranked list of file/path
patterns the agent should prioritize. Lives outside the LLM call because it is
cheap, deterministic, and lets us pre-score the directory tree before any LLM
reasoning happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (theme, keyword list, glob-ish patterns the theme cares about)
_THEMES: list[tuple[str, list[str], list[str]]] = [
    (
        "auth",
        ["auth", "login", "signin", "sign-in", "jwt", "oauth", "session", "permission"],
        ["auth/", "login", "middleware", "jwt", "session", "permission", "security"],
    ),
    (
        "database",
        ["database", "schema", "migration", "model", "orm", "query", "sql"],
        ["migrations/", "models/", "schema", "db.", "database"],
    ),
    (
        "api",
        ["api", "endpoint", "route", "controller", "view", "rest", "graphql", "http"],
        ["routes/", "controllers/", "views/", "api/", "urls", "handlers/"],
    ),
    (
        "config",
        ["config", "settings", "environment", "env", "deployment", "docker"],
        [".env.example", "config/", "settings", "docker-compose", "dockerfile"],
    ),
    (
        "testing",
        ["test", "spec", "fixture", "mock", "pytest", "jest"],
        ["tests/", "__tests__/", "test_", ".test.", ".spec."],
    ),
    (
        "dependencies",
        ["dependency", "dependencies", "package", "library", "import"],
        ["package.json", "requirements.txt", "pipfile", "cargo.toml", "go.mod", "pom.xml"],
    ),
    (
        "ci",
        ["ci", "cd", "pipeline", "workflow", "build", "deploy", "release"],
        [".github/workflows/", "jenkinsfile", ".circleci/", ".gitlab-ci"],
    ),
    (
        "summary",
        ["what is", "summary", "overview", "purpose", "about", "describe", "intro"],
        ["readme", "contributing", "license"],
    ),
]


_ENTRY_POINT_PATTERNS = [
    r"(^|/)main\.[a-z]+$",
    r"(^|/)index\.[a-z]+$",
    r"(^|/)app\.[a-z]+$",
    r"(^|/)server\.[a-z]+$",
    r"(^|/)manage\.py$",
    r"(^|/)__main__\.py$",
]


@dataclass
class Classification:
    themes: list[str]
    is_summary: bool
    priority_patterns: list[str]


def classify_question(question: str) -> Classification:
    """Return the detected themes and the prioritized patterns for fetching."""
    q = question.lower()
    matched_themes: list[str] = []
    patterns: list[str] = []
    for theme, keywords, theme_patterns in _THEMES:
        if any(k in q for k in keywords):
            matched_themes.append(theme)
            patterns.extend(theme_patterns)

    is_summary = "summary" in matched_themes or any(
        kw in q for kw in ("what does", "what is", "overview", "purpose")
    )

    seen = set()
    deduped = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return Classification(
        themes=matched_themes,
        is_summary=is_summary,
        priority_patterns=deduped,
    )


def rank_paths(paths: list[str], classification: Classification) -> list[str]:
    """Return `paths` ordered by relevance to the classification."""
    entry_re = re.compile("|".join(_ENTRY_POINT_PATTERNS))

    def score(path: str) -> tuple[int, int, str]:
        lower = path.lower()
        # smaller = better
        s = 100
        if entry_re.search(lower):
            s = 0
        else:
            for i, pattern in enumerate(classification.priority_patterns):
                if pattern in lower:
                    s = 10 + i
                    break
        return (s, len(path), lower)

    return sorted(paths, key=score)
