from __future__ import annotations

from agent.services.classifier import classify_question, rank_paths


class TestClassifier:
    def test_auth_theme(self):
        c = classify_question("How does authentication work in this app?")
        assert "auth" in c.themes

    def test_summary_question(self):
        c = classify_question("What is this project about?")
        assert c.is_summary is True

    def test_database_theme(self):
        c = classify_question("Where are the database migrations stored?")
        assert "database" in c.themes

    def test_entry_points_ranked_first(self):
        c = classify_question("Tell me about this code")
        paths = [
            "src/utils/helpers.py",
            "src/auth/login.py",
            "main.py",
            "tests/test_main.py",
        ]
        ranked = rank_paths(paths, c)
        assert ranked[0] == "main.py"

    def test_auth_paths_ranked_higher_than_unrelated(self):
        c = classify_question("How does authentication work?")
        paths = [
            "docs/random.md",
            "src/auth/login.py",
            "tests/test_general.py",
        ]
        ranked = rank_paths(paths, c)
        assert ranked.index("src/auth/login.py") < ranked.index("docs/random.md")
