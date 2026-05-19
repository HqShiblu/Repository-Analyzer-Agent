from __future__ import annotations

import logging

from django.conf import settings

from agent.models import ResearchSession

logger = logging.getLogger(__name__)


_LEGACY_FAILURE_PREFIXES = (
    "(agent did not produce an answer)",
    "The agent was unable to produce an answer",
)


def find_cached_answer(repo_url: str, question_vector: list[float]):
    threshold = settings.SEMANTIC_CACHE_THRESHOLD
    try:
        from pgvector.django import CosineDistance
    except ImportError:
        logger.warning("pgvector not available; semantic cache disabled")
        return None

    try:
        qs = (
            ResearchSession.objects.alias(
                similarity=1 - CosineDistance("question_embedding", question_vector)
            )
            .filter(
                repository__url=repo_url,
                answer__isnull=False,
                similarity__gte=threshold,
            )
            .order_by("-started_at")
        )
        for candidate in qs[:5]:
            ans = (candidate.answer or "").strip()
            if not ans:
                continue
            if any(ans.startswith(p) for p in _LEGACY_FAILURE_PREFIXES):
                logger.info(
                    "Skipping legacy failure-sentinel cache row %s", candidate.id
                )
                continue
            return candidate
        return None
    except Exception as exc:
        logger.debug("Semantic cache lookup skipped: %s", exc)
        return None
