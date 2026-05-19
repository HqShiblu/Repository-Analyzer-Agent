from __future__ import annotations

from agent.services.pipeline import parse_references


class TestParseReferences:
    def test_single_reference_with_lines(self):
        ans = "See [[fastapi/dependencies/utils.py:42-78]] for details."
        refs = parse_references(ans)
        assert refs == [
            {
                "file_path": "fastapi/dependencies/utils.py",
                "line_start": 42,
                "line_end": 78,
                "note": None,
            }
        ]

    def test_single_reference_single_line(self):
        ans = "Look at [[a/b/c.py:10]]."
        refs = parse_references(ans)
        assert refs[0]["line_start"] == 10
        assert refs[0]["line_end"] == 10

    def test_reference_without_lines(self):
        ans = "Check [[README.md]]."
        refs = parse_references(ans)
        assert refs[0]["file_path"] == "README.md"
        assert refs[0]["line_start"] is None
        assert refs[0]["line_end"] is None

    def test_multiple_references_dedup(self):
        ans = "[[a.py:1-2]] and again [[a.py:1-2]] plus [[b.py]]."
        refs = parse_references(ans)
        assert len(refs) == 2
        paths = {r["file_path"] for r in refs}
        assert paths == {"a.py", "b.py"}

    def test_empty_answer(self):
        assert parse_references("") == []
        assert parse_references(None) == []  # type: ignore[arg-type]
