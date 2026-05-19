from __future__ import annotations

from rest_framework import serializers

from agent.models import Finding, Repository, ResearchSession, ToolCallLog


class CreateSessionRequestSerializer(serializers.Serializer):
    repository_url = serializers.CharField(max_length=2048)
    question = serializers.CharField(max_length=4000)

    def validate_question(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("question cannot be blank")
        return value


class ReferenceSerializer(serializers.Serializer):
    file_path = serializers.CharField()
    line_start = serializers.IntegerField(allow_null=True, required=False)
    line_end = serializers.IntegerField(allow_null=True, required=False)
    note = serializers.CharField(allow_null=True, required=False)


class FindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Finding
        fields = ("id", "file_path", "line_start", "line_end", "note", "created_at")
        read_only_fields = fields


class ToolCallLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolCallLog
        fields = ("id", "tool_name", "input_params", "output_summary", "called_at")
        read_only_fields = fields


class RepositorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Repository
        fields = ("id", "url", "name", "last_analyzed_at", "created_at")
        read_only_fields = fields


class ResearchSessionSummarySerializer(serializers.ModelSerializer):
    repository_url = serializers.CharField(source="repository.url", read_only=True)

    class Meta:
        model = ResearchSession
        fields = (
            "id",
            "repository_url",
            "question",
            "answer",
            "source",
            "token_usage",
            "started_at",
            "completed_at",
        )
        read_only_fields = fields


class ResearchSessionDetailSerializer(serializers.ModelSerializer):
    repository_url = serializers.CharField(source="repository.url", read_only=True)
    findings = FindingSerializer(many=True, read_only=True)
    tool_calls = ToolCallLogSerializer(many=True, read_only=True)

    class Meta:
        model = ResearchSession
        fields = (
            "id",
            "repository_url",
            "question",
            "answer",
            "source",
            "token_usage",
            "started_at",
            "completed_at",
            "findings",
            "tool_calls",
        )
        read_only_fields = fields
