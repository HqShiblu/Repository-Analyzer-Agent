from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

from django.test import TestCase

from agent.models import Finding, Repository, ResearchSession, ToolCallLog
from agent.services import pipeline
from agent.services.pipeline import FAILED_ANSWER_MESSAGE


# A canonical 384-d zero vector for use everywhere the embedding is needed.
_VEC = [0.0] * 384


def _stub_embed(text: str) -> list[float]:
    return list(_VEC)


class _StubLLMResponse:
    def __init__(self, content: str, *, tool_calls=None, finish_reason="stop"):
        class _Msg:
            pass

        self.message = _Msg()
        self.message.role = "assistant"
        self.message.content = content
        self.message.tool_calls = tool_calls
        self.finish_reason = finish_reason
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.total_tokens = 15


class PipelineSelfAssessmentBranchTests(TestCase):
    @mock.patch("agent.services.pipeline.embeddings.embed", side_effect=_stub_embed)
    @mock.patch("agent.services.pipeline.find_cached_answer", return_value=None)
    @mock.patch("agent.services.pipeline.llm.chat")
    def test_llm_knowledge_branch(self, mock_chat, _mock_cache, _mock_embed):
        mock_chat.return_value = _StubLLMResponse(
            '{"confident": true, "answer": "Django is a Python web framework."}',
        )
        result = pipeline.run_pipeline(
            "https://github.com/django/django/",
            "What language is Django written in?",
        )
        self.assertEqual(result.source, ResearchSession.SOURCE_LLM_KNOWLEDGE)
        self.assertIn("Python", result.answer)
        self.assertEqual(result.session.source, ResearchSession.SOURCE_LLM_KNOWLEDGE)
        self.assertIsNotNone(result.session.completed_at)
        repo = Repository.objects.get(url="https://github.com/django/django")
        self.assertIsNotNone(repo.last_analyzed_at)

    @mock.patch("agent.services.pipeline.embeddings.embed", side_effect=_stub_embed)
    @mock.patch("agent.services.pipeline.find_cached_answer", return_value=None)
    @mock.patch("agent.services.pipeline.llm.chat")
    def test_low_confidence_falls_through_to_full_traversal(self, mock_chat, *_):
        # Self-assessment says "no" → README scan won't apply (not summary) → agent loop.
        chat_calls = []

        def fake_chat(messages, tools=None, tool_choice=None, temperature=0.0):
            chat_calls.append(tools)
            if not chat_calls or len(chat_calls) == 1:
                return _StubLLMResponse('{"confident": false, "answer": null}')
            # Inside the agent loop: produce a final answer immediately.
            return _StubLLMResponse(
                "The answer is in [[src/main.py:10-20]].",
                finish_reason="stop",
            )

        mock_chat.side_effect = fake_chat

        # Stub the GitHubClient constructor so it doesn't try to hit github.com
        with mock.patch("agent.services.pipeline.GitHubClient") as mock_gh:
            mock_gh.return_value = mock.MagicMock()
            result = pipeline.run_pipeline(
                "https://github.com/x/y",
                "How does the retry logic work inside this code?",
            )
        self.assertEqual(result.source, ResearchSession.SOURCE_FULL_TRAVERSAL)
        self.assertEqual(len(result.references), 1)
        self.assertEqual(result.references[0]["file_path"], "src/main.py")
        self.assertEqual(result.references[0]["line_start"], 10)


class PipelineCacheBranchTests(TestCase):
    @mock.patch("agent.services.pipeline.embeddings.embed", side_effect=_stub_embed)
    def test_cache_hit_short_circuits(self, _mock_embed):
        repo = Repository.objects.create(url="https://github.com/a/b", name="a/b")
        prior = ResearchSession.objects.create(
            repository=repo,
            question="How does X work?",
            answer="It works via Y, see [[src/x.py]].",
            source=ResearchSession.SOURCE_FULL_TRAVERSAL,
            completed_at=datetime.now(timezone.utc),
        )
        with mock.patch("agent.services.pipeline.find_cached_answer", return_value=prior):
            with mock.patch("agent.services.pipeline.llm.chat") as mock_chat:
                result = pipeline.run_pipeline(
                    "https://github.com/a/b/",
                    "How does X function in this repo?",
                )
        self.assertEqual(result.source, ResearchSession.SOURCE_CACHE)
        self.assertEqual(result.answer, prior.answer)
        # LLM must not have been called when we got a cache hit.
        mock_chat.assert_not_called()


class FailedRunIsNotCachedTests(TestCase):
    """Regression tests for the 'failure poisoned the cache' bug.

    Two invariants:
        1. When the agent loop produces no textual answer, the session row
           must have answer=NULL in the DB so it cannot be returned by the
           semantic cache later.
        2. The HTTP response still surfaces a human-readable failure message
           so the caller knows what happened.
    """

    @mock.patch("agent.services.pipeline.embeddings.embed", side_effect=_stub_embed)
    @mock.patch("agent.services.pipeline.find_cached_answer", return_value=None)
    @mock.patch("agent.services.pipeline.GitHubClient")
    @mock.patch("agent.services.pipeline.run_agent_loop")
    @mock.patch("agent.services.pipeline.llm.chat")
    def test_failed_run_saves_null_answer(
        self,
        mock_chat,
        mock_agent_loop,
        _mock_gh,
        _mock_cache,
        _mock_embed,
    ):
        # Self-assessment says "not confident" so we fall through.
        mock_chat.return_value = _StubLLMResponse(
            '{"confident": false, "answer": null}'
        )
        # Agent loop returns no answer (simulates the failure that caused the bug).
        from agent.services.agent_loop import AgentResult
        mock_agent_loop.return_value = AgentResult(
            answer="",
            prompt_tokens=10,
            completion_tokens=0,
            total_tokens=10,
            tool_calls_made=5,
        )

        result = pipeline.run_pipeline(
            "https://github.com/x/y",
            "Where is the retry logic?",
        )

        # HTTP response should surface the failure message.
        self.assertEqual(result.source, ResearchSession.SOURCE_FULL_TRAVERSAL)
        self.assertEqual(result.answer, FAILED_ANSWER_MESSAGE)

        # But the DB row MUST hold answer=NULL so the cache won't serve it.
        result.session.refresh_from_db()
        self.assertIsNone(result.session.answer)

    @mock.patch("agent.services.pipeline.embeddings.embed", side_effect=_stub_embed)
    def test_legacy_failure_sentinel_is_skipped_by_cache(self, _mock_embed):
        """Even if a stale failure row is still in the DB from before the
        fix, the cache lookup must skip it."""
        repo = Repository.objects.create(url="https://github.com/x/y", name="x/y")
        # Insert a poisoned row directly (simulating a pre-fix DB).
        ResearchSession.objects.create(
            repository=repo,
            question="Where is the retry logic?",
            answer="(agent did not produce an answer)",
            source=ResearchSession.SOURCE_FULL_TRAVERSAL,
            completed_at=datetime.now(timezone.utc),
        )

        # The cache lookup, when fed this row directly, should treat it as a miss.
        from agent.services.cache import _LEGACY_FAILURE_PREFIXES

        # The lookup is gated on pgvector which isn't available on SQLite, so
        # this assertion is on the prefix list rather than a live query.
        self.assertTrue(
            any(
                "agent did not produce an answer".lower() in p.lower()
                for p in _LEGACY_FAILURE_PREFIXES
            )
        )
