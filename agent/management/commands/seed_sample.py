"""Seed a sample ResearchSession + Finding + ToolCallLog row for demo purposes.

Usage:
    python manage.py seed_sample

Useful in environments where you want a fixture without an LLM key configured.
For *real* agent output, run a POST /api/sessions/ against a running server.
"""

from __future__ import annotations

from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from agent.models import Finding, Repository, ResearchSession, ToolCallLog


SAMPLE_QUESTION = "How does FastAPI handle dependency injection internally?"
SAMPLE_ANSWER = (
    "FastAPI resolves dependencies by introspecting the type-annotated parameters of "
    "path operation functions. The core resolver walks every parameter, recurses into "
    "nested Depends(...) declarations, caches sub-dependencies per request, and yields "
    "them in order. See [[fastapi/dependencies/utils.py:42-78]] for the resolver and "
    "[[fastapi/dependencies/models.py:10-40]] for the Dependant data class."
)


class Command(BaseCommand):
    help = "Create a sample Repository + ResearchSession + Finding + ToolCallLog for demos."

    def handle(self, *args, **options):
        repo, _ = Repository.objects.get_or_create(
            url="https://github.com/tiangolo/fastapi",
            defaults={"name": "tiangolo/fastapi"},
        )
        session = ResearchSession.objects.create(
            repository=repo,
            question=SAMPLE_QUESTION,
            answer=SAMPLE_ANSWER,
            source=ResearchSession.SOURCE_FULL_TRAVERSAL,
            token_usage={"prompt_tokens": 3200, "completion_tokens": 410, "total_tokens": 3610},
            completed_at=datetime.now(timezone.utc),
        )
        Finding.objects.create(
            session=session,
            file_path="fastapi/dependencies/utils.py",
            line_start=42,
            line_end=78,
            note="Core dependency resolver entry point.",
        )
        Finding.objects.create(
            session=session,
            file_path="fastapi/dependencies/models.py",
            line_start=10,
            line_end=40,
            note="Dependant dataclass that the resolver walks.",
        )
        ToolCallLog.objects.create(
            session=session,
            tool_name="get_directory_tree",
            input_params={},
            output_summary="Repository tree (4123 files)...",
        )
        ToolCallLog.objects.create(
            session=session,
            tool_name="read_file",
            input_params={"path": "fastapi/dependencies/utils.py"},
            output_summary="(truncated) dependency resolver source",
        )
        ToolCallLog.objects.create(
            session=session,
            tool_name="save_finding",
            input_params={"file_path": "fastapi/dependencies/utils.py", "note": "Core dependency resolver entry point."},
            output_summary=f"saved finding for {session.id}",
        )
        repo.last_analyzed_at = session.completed_at
        repo.save(update_fields=["last_analyzed_at"])

        self.stdout.write(self.style.SUCCESS(f"Seeded sample session {session.id}"))
