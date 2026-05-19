from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from agent.models import Repository, ResearchSession
from agent.serializers import (
    CreateSessionRequestSerializer,
    RepositorySerializer,
    ResearchSessionDetailSerializer,
    ResearchSessionSummarySerializer,
)
from agent.services.github import GitHubAPIError
from agent.services.pipeline import run_pipeline
from agent.services.sanitizer import InvalidRepositoryURL, sanitize_repo_url

logger = logging.getLogger(__name__)


def _error_response(http_status: int, message: str = "An error occurred") -> Response:
    """Return a sanitized error payload.

    The shape is intentionally minimal — `status` + `error` — so we never leak
    upstream provider details (API keys, user IDs, raw error metadata, etc.).
    The actual exception is logged server-side via `logger.exception`.
    """
    return Response(
        {"status": str(http_status), "error": message},
        status=http_status,
    )


def _classify_pipeline_error(exc: Exception) -> tuple[int, str]:
    """Map an exception coming out of the pipeline to (http_status, message).

    Upstream provider failures (LLM rate limits, GitHub API errors, network
    issues) are surfaced as 5xx gateway errors. Everything else is a 500.
    """
    # OpenAI-compatible client errors. Import lazily so the module loads even
    # if the openai package is not installed in some test environments.
    try:
        from openai import (
            APIConnectionError,
            APIError,
            APITimeoutError,
            RateLimitError,
        )
    except ImportError:  # pragma: no cover - openai is in requirements
        APIError = APIConnectionError = APITimeoutError = RateLimitError = ()  # type: ignore[assignment]

    if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
        return status.HTTP_504_GATEWAY_TIMEOUT, "Upstream LLM provider is unavailable"
    if isinstance(exc, APIError):
        return status.HTTP_502_BAD_GATEWAY, "Upstream LLM provider returned an error"
    if isinstance(exc, GitHubAPIError):
        return status.HTTP_502_BAD_GATEWAY, "Upstream GitHub API error"
    return status.HTTP_500_INTERNAL_SERVER_ERROR, "An error occurred"


@api_view(["GET", "POST"])
def create_session_list(request):
    if request.method == "POST":
        serializer = CreateSessionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = run_pipeline(
                repository_url=serializer.validated_data["repository_url"],
                question=serializer.validated_data["question"],
            )
        except InvalidRepositoryURL:
            logger.info("Rejected invalid repository_url", exc_info=True)
            return _error_response(status.HTTP_400_BAD_REQUEST, "Invalid repository URL")
        except Exception as exc:
            logger.exception("Pipeline crashed")
            http_status, message = _classify_pipeline_error(exc)
            return _error_response(http_status, message)

        return Response(
            {
                "session_id": str(result.session.id),
                "repository_url": result.session.repository.url,
                "question": result.session.question,
                "answer": result.answer,
                "source": result.source,
                "references": result.references,
                "token_usage": result.token_usage,
                "created_at": result.session.started_at.isoformat(),
                "completed_at": (
                    result.session.completed_at.isoformat()
                    if result.session.completed_at
                    else None
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    repo_url = request.query_params.get("repo")
    qs = ResearchSession.objects.select_related("repository")
    if repo_url:
        try:
            parsed = sanitize_repo_url(repo_url)
        except InvalidRepositoryURL:
            return _error_response(status.HTTP_400_BAD_REQUEST, "Invalid repository URL")
        qs = qs.filter(repository__url=parsed.url)
    qs = qs.order_by("-started_at")[:100]
    data = ResearchSessionSummarySerializer(qs, many=True).data
    return Response(data)


@api_view(["GET"])
def session_detail(request, session_id):
    try:
        session = (
            ResearchSession.objects.select_related("repository")
            .prefetch_related("findings", "tool_calls")
            .get(pk=session_id)
        )
    except ResearchSession.DoesNotExist:
        return _error_response(status.HTTP_404_NOT_FOUND, "Session not found")
    return Response(ResearchSessionDetailSerializer(session).data)


@api_view(["GET"])
def repos_list(_request):
    qs = Repository.objects.order_by("-last_analyzed_at", "-created_at")[:200]
    return Response(RepositorySerializer(qs, many=True).data)
