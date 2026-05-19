from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from django.test import TestCase

from agent.models import Finding, Repository, ResearchSession, ToolCallLog
from agent.services.tools import ToolContext, dispatch_tool_call


@dataclass
class _TreeEntry:
    path: str
    type: str = "blob"
    size: int | None = 100


@dataclass
class FakeGitHubClient:
    tree: list[_TreeEntry] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)

    def get_directory_tree(self):
        return list(self.tree)

    def list_contents(self, path: str):
        return [{"path": p.path, "type": p.type} for p in self.tree if p.path.startswith(path)]

    def read_file_raw(self, path: str) -> str:
        return self.files.get(path, "")

    def read_file(self, path: str) -> str:
        return self.read_file_raw(path)

    def get_file_summary(self, path: str, max_lines: int = 80) -> str:
        return "\n".join(self.read_file(path).splitlines()[:max_lines])

    def search_code(self, query: str, max_results: int = 10):
        return []


def _make_ctx(extra_files: dict | None = None) -> ToolContext:
    repo = Repository.objects.create(url="https://github.com/x/y", name="x/y")
    session = ResearchSession.objects.create(repository=repo, question="q")
    gh = FakeGitHubClient(
        tree=[_TreeEntry(path="src/main.py"), _TreeEntry(path="README.md")],
        files={"README.md": "hello\nworld", "src/main.py": "def f():\n    pass", **(extra_files or {})},
    )
    return ToolContext(
        session=session,
        repository=repo,
        github=gh,
        file_reads=set(),
        max_file_reads=2,
    )


class ToolDispatchTests(TestCase):
    def test_get_directory_tree_logs_tool_call(self):
        ctx = _make_ctx()
        out = dispatch_tool_call(ctx, "get_directory_tree", "{}")
        self.assertIn("src/main.py", out)
        self.assertIn("README.md", out)
        log = ToolCallLog.objects.get(session=ctx.session)
        self.assertEqual(log.tool_name, "get_directory_tree")

    def test_save_finding_creates_row(self):
        ctx = _make_ctx()
        out = dispatch_tool_call(
            ctx,
            "save_finding",
            json.dumps(
                {
                    "file_path": "src/main.py",
                    "note": "entry point",
                    "line_start": 1,
                    "line_end": 2,
                }
            ),
        )
        self.assertIn("saved finding", out)
        self.assertEqual(Finding.objects.filter(session=ctx.session).count(), 1)
        f = Finding.objects.first()
        self.assertEqual(f.file_path, "src/main.py")
        self.assertEqual(f.line_start, 1)

    def test_get_previous_findings_excludes_current_session(self):
        ctx = _make_ctx()
        prior_session = ResearchSession.objects.create(
            repository=ctx.repository, question="prior?"
        )
        Finding.objects.create(
            session=prior_session, file_path="src/main.py", note="prior insight"
        )
        Finding.objects.create(
            session=ctx.session, file_path="src/main.py", note="current insight"
        )

        out = dispatch_tool_call(ctx, "get_previous_findings", "{}")
        self.assertIn("prior insight", out)
        self.assertNotIn("current insight", out)

    def test_read_file_cap(self):
        ctx = _make_ctx(extra_files={"a.py": "1", "b.py": "2", "c.py": "3"})
        ctx.max_file_reads = 2

        dispatch_tool_call(ctx, "read_file", json.dumps({"path": "a.py"}))
        dispatch_tool_call(ctx, "read_file", json.dumps({"path": "b.py"}))
        out = dispatch_tool_call(ctx, "read_file", json.dumps({"path": "c.py"}))
        self.assertIn("File-read cap reached", out)

    def test_unknown_tool(self):
        ctx = _make_ctx()
        out = dispatch_tool_call(ctx, "nope", "{}")
        self.assertIn("Unknown tool", out)

    def test_get_file_outline_returns_signatures_only(self):
        ctx = _make_ctx(
            extra_files={
                "lib.py": (
                    "def alpha():\n"
                    "    return 1\n"
                    "\n"
                    "def beta(x):\n"
                    "    return x * 2\n"
                ),
            }
        )
        out = dispatch_tool_call(ctx, "get_file_outline", json.dumps({"path": "lib.py"}))
        self.assertIn("alpha", out)
        self.assertIn("beta", out)
        # Importantly: the bodies are NOT in the outline.
        self.assertNotIn("return 1", out)
        self.assertNotIn("return x * 2", out)
        # Outline alone should not consume the file-read budget.
        self.assertNotIn("lib.py", ctx.file_reads)

    def test_read_method_returns_only_the_method_body(self):
        ctx = _make_ctx(
            extra_files={
                "lib.py": (
                    "def alpha():\n"
                    "    return 1\n"
                    "\n"
                    "def beta(x):\n"
                    "    return x * 2\n"
                ),
            }
        )
        out = dispatch_tool_call(
            ctx,
            "read_method",
            json.dumps({"path": "lib.py", "method_name": "beta"}),
        )
        self.assertIn("def beta", out)
        self.assertIn("return x * 2", out)
        self.assertNotIn("def alpha", out)
        self.assertIn("lib.py", ctx.file_reads)

    def test_read_method_not_found_lists_available(self):
        ctx = _make_ctx(extra_files={"lib.py": "def alpha():\n    return 1\n"})
        out = dispatch_tool_call(
            ctx,
            "read_method",
            json.dumps({"path": "lib.py", "method_name": "nope"}),
        )
        self.assertIn("not found", out.lower())
        self.assertIn("alpha", out)

    def test_outline_then_read_method_shares_single_fetch(self):
        """The same file should be downloaded only once across outline +
        read_method calls within one session."""
        ctx = _make_ctx(extra_files={"lib.py": "def alpha():\n    return 1\n"})
        calls = {"n": 0}
        original = ctx.github.read_file_raw

        def counted_read(path: str) -> str:
            calls["n"] += 1
            return original(path)

        ctx.github.read_file_raw = counted_read  # type: ignore[method-assign]

        dispatch_tool_call(ctx, "get_file_outline", json.dumps({"path": "lib.py"}))
        dispatch_tool_call(
            ctx,
            "read_method",
            json.dumps({"path": "lib.py", "method_name": "alpha"}),
        )
        self.assertEqual(calls["n"], 1)
