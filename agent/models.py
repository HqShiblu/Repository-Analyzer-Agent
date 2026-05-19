"""Database models for the codebase research agent.

Four entities:

- `Repository`        : one row per unique repo URL.
- `ResearchSession`   : one row per question asked against a repo. Holds the
                        question text, its embedding (for semantic cache),
                        the final answer, source category, and token usage.
- `Finding`           : agent-written notes about specific files learned during
                        a session. Re-used across sessions to skip files the
                        agent has already characterized.
- `ToolCallLog`       : auditable log of every tool the agent invoked.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class Repository(models.Model):
    """A GitHub repository that has been researched at least once."""

    SOURCE_CHOICES = (
        ("cache", "cache"),
        ("llm_knowledge", "llm_knowledge"),
        ("readme_scan", "readme_scan"),
        ("full_traversal", "full_traversal"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.TextField(unique=True)
    name = models.CharField(max_length=255)
    last_analyzed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-last_analyzed_at", "-created_at")
        verbose_name_plural = "repositories"

    def __str__(self) -> str:
        return self.name or self.url


class ResearchSession(models.Model):
    """A single question asked against a repository."""

    SOURCE_CACHE = "cache"
    SOURCE_LLM_KNOWLEDGE = "llm_knowledge"
    SOURCE_README_SCAN = "readme_scan"
    SOURCE_FULL_TRAVERSAL = "full_traversal"
    SOURCE_CHOICES = (
        (SOURCE_CACHE, "cache"),
        (SOURCE_LLM_KNOWLEDGE, "llm_knowledge"),
        (SOURCE_README_SCAN, "readme_scan"),
        (SOURCE_FULL_TRAVERSAL, "full_traversal"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    question = models.TextField()
    question_embedding = VectorField(
        dimensions=settings.EMBEDDING_DIMENSIONS,
        null=True,
        blank=True,
    )
    answer = models.TextField(null=True, blank=True)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, null=True, blank=True)
    token_usage = models.JSONField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"{self.repository.name} :: {self.question[:60]}"


class Finding(models.Model):
    """A note the agent recorded about a specific file during a session."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ResearchSession,
        on_delete=models.CASCADE,
        related_name="findings",
    )
    file_path = models.TextField()
    line_start = models.IntegerField(null=True, blank=True)
    line_end = models.IntegerField(null=True, blank=True)
    note = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=["file_path"])]

    def __str__(self) -> str:
        return f"{self.file_path} ({self.note[:40]})"


class ToolCallLog(models.Model):
    """Audit log of every tool invocation made by the agent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ResearchSession,
        on_delete=models.CASCADE,
        related_name="tool_calls",
    )
    tool_name = models.CharField(max_length=64)
    input_params = models.JSONField()
    output_summary = models.TextField()
    called_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("called_at",)
        indexes = [models.Index(fields=["tool_name"])]

    def __str__(self) -> str:
        return f"{self.tool_name} @ {self.called_at:%Y-%m-%d %H:%M:%S}"
