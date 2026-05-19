from __future__ import annotations

from unittest import mock

from django.test import Client, TestCase


class ErrorSanitizationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_invalid_url_returns_clean_400(self):
        resp = self.client.post(
            "/api/sessions/",
            data={"repository_url": "https://gitlab.com/x/y", "question": "?"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body, {"status": "400", "error": "Invalid repository URL"})

    @mock.patch("agent.views.run_pipeline")
    def test_llm_rate_limit_returns_504_without_leaking_details(self, mock_run):
        # Build a fake RateLimitError so we don't need the openai package
        # to manufacture a real one in the test environment.
        from openai import RateLimitError

        leaky_message = (
            "Error code: 429 - {'error': {'message': 'Provider returned error', "
            "'metadata': {'raw': 'rate-limited'}}, 'user_id': 'user_30jNJ3bYRcbXScJGCCJCGifqpj1'}"
        )
        mock_run.side_effect = RateLimitError(
            message=leaky_message,
            response=mock.MagicMock(status_code=429, headers={}),
            body=None,
        )

        resp = self.client.post(
            "/api/sessions/",
            data={"repository_url": "https://github.com/a/b", "question": "?"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 504)
        body = resp.json()
        self.assertEqual(
            body,
            {"status": "504", "error": "Upstream LLM provider is unavailable"},
        )
        # Critical: none of the upstream noise should leak through.
        self.assertNotIn("user_id", resp.content.decode())
        self.assertNotIn("user_30jNJ3bYRcbXScJGCCJCGifqpj1", resp.content.decode())
        self.assertNotIn("Provider returned error", resp.content.decode())

    @mock.patch("agent.views.run_pipeline")
    def test_github_api_error_returns_502(self, mock_run):
        from agent.services.github import GitHubAPIError

        mock_run.side_effect = GitHubAPIError("rate limit exceeded for token ghp_secret123")
        resp = self.client.post(
            "/api/sessions/",
            data={"repository_url": "https://github.com/a/b", "question": "?"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual(body, {"status": "502", "error": "Upstream GitHub API error"})
        self.assertNotIn("ghp_secret123", resp.content.decode())

    @mock.patch("agent.views.run_pipeline")
    def test_unknown_error_returns_clean_500(self, mock_run):
        mock_run.side_effect = RuntimeError("internal stack trace with sensitive info")
        resp = self.client.post(
            "/api/sessions/",
            data={"repository_url": "https://github.com/a/b", "question": "?"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body, {"status": "500", "error": "An error occurred"})
        self.assertNotIn("sensitive info", resp.content.decode())

    def test_missing_session_returns_clean_404(self):
        resp = self.client.get("/api/sessions/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body, {"status": "404", "error": "Session not found"})
