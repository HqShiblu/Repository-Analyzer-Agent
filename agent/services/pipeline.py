"""Top-level pipeline orchestrator.

This is the single function the HTTP view calls. It implements steps 1-8
of the SPECS.md flow:

    1. Sanitize input
    2. Persist Repository + ResearchSession (answer=NULL) + question embedding
    3. Semantic cache hit?  -> early return
    4. (Inside the agent's tools: get_previous_findings)
    5. LLM self-assessment of own-knowledge
    6. README/manifest scan for summary-style questions
    7. Full tool-calling traversal
    8. Persist final answer + token usage
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from django.conf import settings
from django.db import transaction

from agent.models import Repository, ResearchSession
from agent.services import embeddings, llm
from agent.services.agent_loop import run_agent_loop
from agent.services.cache import find_cached_answer
from agent.services.classifier import classify_question
from agent.services.github import GitHubAPIError, GitHubClient
from agent.services.sanitizer import ParsedRepo, sanitize_repo_url
from agent.services.tools import ToolContext

logger = logging.getLogger(__name__)


FAILED_ANSWER_MESSAGE = (
    "The agent was unable to produce an answer for this question. "
    "Try rephrasing the question or increase AGENT_MAX_LOOP."
)



@dataclass
class TokenCounter:
    prompt: int = 0
    completion: int = 0
    total: int = 0

    def add(self, resp: llm.LLMResponse) -> None:
        self.prompt += resp.prompt_tokens
        self.completion += resp.completion_tokens
        self.total += resp.total_tokens


@dataclass
class PipelineResult:
    session: ResearchSession
    answer: str
    source: str
    references: list[dict] = field(default_factory=list)
    token_usage: dict = field(default_factory=dict)


def run_pipeline(repository_url: str, question: str) -> PipelineResult:
    parsed = sanitize_repo_url(repository_url)
    session, repo = _create_or_get_session(parsed, question)
    return _execute_pipeline(parsed, session, repo, question)


def _create_or_get_session(parsed: ParsedRepo, question: str) -> tuple[ResearchSession, Repository]:
    question_vec = embeddings.embed(question)
    with transaction.atomic():
        repo, _ = Repository.objects.get_or_create(
            url=parsed.url,
            defaults={"name": parsed.name},
        )
        session = ResearchSession.objects.create(
            repository=repo,
            question=question,
            question_embedding=question_vec,
        )
    return session, repo

def _try_cache(session: ResearchSession, repo: Repository) -> ResearchSession | None:
    return find_cached_answer(repo.url, list(session.question_embedding or []))


_CONFIDENT_RE = re.compile(r"^\s*confident\b", re.IGNORECASE)


def _llm_self_assessment(question: str, repo_name: str, counter: TokenCounter) -> str | None:
    """Ask the LLM whether it can answer confidently from its own training data.

    The LLM must reply with a JSON object: {"confident": true/false, "answer": "..."}
    Only the strict "confident: true" branch returns an answer; anything else
    triggers the next pipeline step.
    """
    prompt = (
        "You will be asked a question about a public GitHub repository. "
        "Decide whether you can answer it confidently and correctly from your "
        "own training data ALONE, without exploring the codebase.\n\n"
        f"Repository: {repo_name}\n"
        f"Question: {question}\n\n"
        "Reply with strict JSON only, no markdown, no extra prose:\n"
        '{"confident": true, "answer": "<your answer with file references when possible>"}\n'
        "or\n"
        '{"confident": false, "answer": null}\n\n'
        "Be conservative. Only set confident=true for very well-known, stable "
        "facts about the repository. If you would need to inspect the code to "
        "be sure, set confident=false."
    )
    try:
        resp = llm.chat(
            messages=[
                {"role": "system", "content": "You are precise and conservative. Output strict JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning("LLM self-assessment failed: %s", exc)
        return None
    counter.add(resp)

    content = (getattr(resp.message, "content", None) or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.info("Self-assessment was not valid JSON: %s", content[:200])
        return None

    if data.get("confident") is True and data.get("answer"):
        return str(data["answer"]).strip()
    return None


_MANIFEST_FILES = [
    "README.md",
    "README.rst",
    "Readme.md",
    "readme.md",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "CONTRIBUTING.md",
]


def _try_readme_scan(
    parsed: ParsedRepo,
    question: str,
    classification,
    counter: TokenCounter,
    gh: GitHubClient,
) -> str | None:
    if not classification.is_summary:
        return None

    collected: list[tuple[str, str]] = []
    for name in _MANIFEST_FILES:
        try:
            content = gh.read_file(name)
        except GitHubAPIError:
            continue
        snippet = "\n".join(content.splitlines()[:200])
        collected.append((name, snippet))
        if len(collected) >= 3:
            break

    if not collected:
        return None

    joined = "\n\n".join(f"=== {name} ===\n{body}" for name, body in collected)
    user_prompt = (
        "You are answering a question about a GitHub repository using ONLY the "
        "meta-files below (README/manifest/contributing). If these are NOT "
        "sufficient to answer accurately, reply with strict JSON "
        '{"sufficient": false, "answer": null}. Otherwise reply with '
        '{"sufficient": true, "answer": "<your answer, cite files like [[README.md]]>"}.\n\n'
        f"Question: {question}\n\nFiles:\n{joined}"
    )
    try:
        resp = llm.chat(
            messages=[
                {"role": "system", "content": "Output strict JSON only."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning("README-scan LLM call failed: %s", exc)
        return None
    counter.add(resp)

    content = (getattr(resp.message, "content", None) or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if data.get("sufficient") is True and data.get("answer"):
        return str(data["answer"]).strip()
    return None

_REFERENCE_RE = re.compile(
    r"\[\[\s*(?P<path>[^\]\s:]+(?:\s+[^\]\s:]+)*?)\s*(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?\s*\]\]"
)


def parse_references(answer: str) -> list[dict]:
    """Pull `[[path:line_start-line_end]]` markers out of the final answer."""
    if not answer:
        return []
    seen: set[tuple[str, int | None, int | None]] = set()
    refs: list[dict] = []
    for m in _REFERENCE_RE.finditer(answer):
        path = m.group("path").strip()
        start = int(m.group("start")) if m.group("start") else None
        end = int(m.group("end")) if m.group("end") else (start if start is not None else None)
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"file_path": path, "line_start": start, "line_end": end, "note": None})
    return refs



def _execute_pipeline(
    parsed: ParsedRepo,
    session: ResearchSession,
    repo: Repository,
    question: str,
) -> PipelineResult:
    counter = TokenCounter()

    cached = _try_cache(session, repo)
    if cached is not None and cached.answer:
        return _finalize(
            session=session,
            repo=repo,
            answer=cached.answer,
            source=ResearchSession.SOURCE_CACHE,
            counter=counter,
            extra_refs=cached.token_usage and [],
        )

    knowledge_answer = _llm_self_assessment(question, parsed.name, counter)
    if knowledge_answer:
        return _finalize(
            session=session,
            repo=repo,
            answer=knowledge_answer,
            source=ResearchSession.SOURCE_LLM_KNOWLEDGE,
            counter=counter,
        )

    gh = GitHubClient(parsed.owner, parsed.repo)

    classification = classify_question(question)
    readme_answer = _try_readme_scan(parsed, question, classification, counter, gh)
    if readme_answer:
        return _finalize(
            session=session,
            repo=repo,
            answer=readme_answer,
            source=ResearchSession.SOURCE_README_SCAN,
            counter=counter,
        )

    ctx = ToolContext(
        session=session,
        repository=repo,
        github=gh,
        file_reads=set(),
        max_file_reads=settings.AGENT_MAX_FILE_READS,
    )
    result = run_agent_loop(ctx, question)
    counter.prompt += result.prompt_tokens
    counter.completion += result.completion_tokens
    counter.total += result.total_tokens

    agent_answer = (result.answer or "").strip()
    is_failure = not agent_answer
    return _finalize(
        session=session,
        repo=repo,
        answer=agent_answer or FAILED_ANSWER_MESSAGE,
        source=ResearchSession.SOURCE_FULL_TRAVERSAL,
        counter=counter,
        is_failure=is_failure,
    )


def _finalize(
    session: ResearchSession,
    repo: Repository,
    answer: str,
    source: str,
    counter: TokenCounter,
    extra_refs: list[dict] | None = None,
    is_failure: bool = False,
) -> PipelineResult:
    now = datetime.now(timezone.utc)
    token_usage = {
        "prompt_tokens": counter.prompt,
        "completion_tokens": counter.completion,
        "total_tokens": counter.total,
    }

    session.answer = None if is_failure else answer
    session.source = source
    session.token_usage = token_usage
    session.completed_at = now
    session.save(update_fields=["answer", "source", "token_usage", "completed_at"])

    repo.last_analyzed_at = now
    repo.save(update_fields=["last_analyzed_at"])

    references = parse_references(answer)
    findings = {(f.file_path, f.line_start, f.line_end): f.note for f in session.findings.all()}
    for ref in references:
        key = (ref["file_path"], ref["line_start"], ref["line_end"])
        if key in findings:
            ref["note"] = findings[key]
        else:
            for (path, _, _), note in findings.items():
                if path == ref["file_path"]:
                    ref["note"] = note
                    break

    return PipelineResult(
        session=session,
        answer=answer,
        source=source,
        references=references,
        token_usage=token_usage,
    )
