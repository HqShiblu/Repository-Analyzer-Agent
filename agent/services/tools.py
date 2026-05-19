from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agent.models import Finding, Repository, ResearchSession, ToolCallLog
from agent.services.github import GitHubAPIError, GitHubClient
from agent.services.outline import (
    detect_language,
    extract_method_body,
    extract_outline,
    format_outline,
)

logger = logging.getLogger(__name__)


TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_directory_tree",
            "description": (
                "Fetch the full recursive file tree of the repository. "
                "MUST be the first tool called in any traversal session."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories at a given path in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path inside the repo. Use empty string for repo root."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_outline",
            "description": (
                "PREFERRED FIRST STEP for any code file. Returns just the list of "
                "method/class names and their starting line numbers — NOT the bodies. "
                "Use this to decide which methods are worth reading before paying the "
                "cost of loading the full file. Language is detected from the file "
                "extension. Returns an empty result for unsupported languages and "
                "data/markup files (READMEs, JSON, YAML, etc.) — fall back to "
                "read_file for those."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file inside the repo."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_method",
            "description": (
                "Read the body of a single method/function (or class) from a file. "
                "Use this AFTER get_file_outline to pull only the specific methods "
                "you care about, instead of loading the entire file. Returns the "
                "body with line numbers prepended."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file inside the repo."},
                    "method_name": {
                        "type": "string",
                        "description": "Method or class name as it appears in the outline.",
                    },
                    "line_start": {
                        "type": "integer",
                        "description": "Optional: disambiguate when multiple methods share a name (overloads).",
                    },
                },
                "required": ["path", "method_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file's full content (with line numbers prepended). "
                "Prefer get_file_outline + read_method on code files; use read_file "
                "only for small files (<200 lines) or non-code files such as README, "
                "config, and manifest files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path of the file inside the repo."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_summary",
            "description": "Return the first 80 lines of a file. Use before read_file on large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search the repository for a keyword or symbol. Returns a list of matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword or symbol to search for."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_finding",
            "description": (
                "Persist a Finding for the current session. Call this whenever you "
                "learn something meaningful about a file. Always set line_start/line_end "
                "when you have them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "note": {"type": "string", "description": "Concise statement of what this file does relative to the question."},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                },
                "required": ["file_path", "note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_previous_findings",
            "description": (
                "Return all Findings recorded in prior sessions for this repository. "
                "Call this early to avoid re-reading files that have already been characterized."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_past_sessions",
            "description": "Return summaries of all past ResearchSessions for this repository.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


_OUTPUT_TRUNCATE = 2000


@dataclass
class ToolContext:

    session: ResearchSession
    repository: Repository
    github: GitHubClient
    file_reads: set[str]
    max_file_reads: int

    # Cached state across calls
    _tree_cache: list[str] | None = None
    _raw_content_cache: dict[str, str] | None = None  # path -> raw file text

    def get_raw_content(self, path: str) -> str:
        """Fetch (and memoize within this session) the raw text of a file.

        Reused by `get_file_outline` and `read_method` so the same file isn't
        downloaded twice during a single agent run.
        """
        if self._raw_content_cache is None:
            self._raw_content_cache = {}
        if path not in self._raw_content_cache:
            self._raw_content_cache[path] = self.github.read_file_raw(path)
        return self._raw_content_cache[path]


def _tool_get_directory_tree(ctx: ToolContext, _args: dict) -> str:
    if ctx._tree_cache is not None:
        paths = ctx._tree_cache
    else:
        entries = ctx.github.get_directory_tree()
        paths = [e.path for e in entries if e.type == "blob"]
        ctx._tree_cache = paths
    listing = "\n".join(paths[:1000])
    extra = "" if len(paths) <= 1000 else f"\n... [{len(paths) - 1000} more paths truncated]"
    return f"Repository tree ({len(paths)} files):\n{listing}{extra}"


def _tool_list_files(ctx: ToolContext, args: dict) -> str:
    path = args.get("path", "") or ""
    items = ctx.github.list_contents(path)
    lines = [f"{i.get('type', '?'):<5} {i.get('path', '')}" for i in items]
    return "\n".join(lines) if lines else "(empty)"


def _tool_read_file(ctx: ToolContext, args: dict) -> str:
    path = args["path"]
    if path in ctx.file_reads:
        return ctx.github.read_file(path) + "\n\n[note] this file was already read earlier in the session."
    if len(ctx.file_reads) >= ctx.max_file_reads:
        return (
            f"[blocked] File-read cap reached ({ctx.max_file_reads}). "
            "Produce your final answer from the files already collected."
        )
    content = ctx.github.read_file(path)
    ctx.file_reads.add(path)
    return content


def _tool_get_file_summary(ctx: ToolContext, args: dict) -> str:
    return ctx.github.get_file_summary(args["path"])


def _tool_get_file_outline(ctx: ToolContext, args: dict) -> str:
    """Return just the method/class signatures of a file.

    Cheap relative to read_file: no body text is sent back. Use this to let
    the LLM pick which specific methods are worth loading. Does NOT consume
    the file-read budget — only `read_method` and `read_file` do.
    """
    path = args["path"]
    language = detect_language(path)
    if language is None:
        return (
            f"[unsupported] No outline available for {path} (unknown language). "
            "Fall back to read_file or get_file_summary."
        )
    raw = ctx.get_raw_content(path)
    methods = extract_outline(raw, language)
    return format_outline(path, language, methods)


def _tool_read_method(ctx: ToolContext, args: dict) -> str:
    """Return only the body of a single named method/class.

    Counts against the file-read budget on the FIRST method extracted from a
    given file (subsequent methods in the same file are free, since the file
    is already fetched and cached).
    """
    path = args["path"]
    name = args["method_name"]
    hint_line = args.get("line_start")

    language = detect_language(path)
    if language is None:
        return (
            f"[unsupported] Cannot extract methods from {path} (unknown language). "
            "Fall back to read_file."
        )

    is_new_file = path not in ctx.file_reads
    if is_new_file and len(ctx.file_reads) >= ctx.max_file_reads:
        return (
            f"[blocked] File-read cap reached ({ctx.max_file_reads}). "
            "Produce your final answer from the files already collected."
        )

    raw = ctx.get_raw_content(path)
    methods = extract_outline(raw, language)
    matches = [m for m in methods if m.name == name]
    if not matches:
        available = ", ".join(sorted({m.name for m in methods})[:30]) or "(none)"
        return (
            f"[not found] No method named {name!r} in {path}. "
            f"Available: {available}"
        )

    if hint_line is not None:
        matches.sort(key=lambda m: abs(m.line_start - int(hint_line)))
    method = matches[0]

    body, line_start, line_end = extract_method_body(raw, method)
    if is_new_file:
        ctx.file_reads.add(path)

    header = (
        f"{path} :: {method.name} (lines {line_start}-{line_end}, {method.kind}, {method.language})\n"
        f"{'-' * 60}\n"
    )
    return header + body


def _tool_search_code(ctx: ToolContext, args: dict) -> str:
    results = ctx.github.search_code(args["query"])
    if not results:
        return "(no results)"
    return json.dumps(results, indent=2)


def _tool_save_finding(ctx: ToolContext, args: dict) -> str:
    finding = Finding.objects.create(
        session=ctx.session,
        file_path=args["file_path"],
        note=args["note"],
        line_start=args.get("line_start"),
        line_end=args.get("line_end"),
    )
    return f"saved finding {finding.id} for {finding.file_path}"


def _tool_get_previous_findings(ctx: ToolContext, _args: dict) -> str:
    qs = (
        Finding.objects.filter(session__repository=ctx.repository)
        .exclude(session=ctx.session)
        .order_by("-created_at")[:50]
    )
    out = [
        {
            "file_path": f.file_path,
            "line_start": f.line_start,
            "line_end": f.line_end,
            "note": f.note,
        }
        for f in qs
    ]
    if not out:
        return "(no prior findings for this repository)"
    return json.dumps(out, indent=2)


def _tool_list_past_sessions(ctx: ToolContext, _args: dict) -> str:
    qs = (
        ResearchSession.objects.filter(repository=ctx.repository, answer__isnull=False)
        .exclude(id=ctx.session.id)
        .order_by("-started_at")[:25]
    )
    out = [
        {
            "id": str(s.id),
            "question": s.question,
            "source": s.source,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        }
        for s in qs
    ]
    if not out:
        return "(no past completed sessions for this repository)"
    return json.dumps(out, indent=2)


_DISPATCH = {
    "get_directory_tree": _tool_get_directory_tree,
    "list_files": _tool_list_files,
    "get_file_outline": _tool_get_file_outline,
    "read_method": _tool_read_method,
    "read_file": _tool_read_file,
    "get_file_summary": _tool_get_file_summary,
    "search_code": _tool_search_code,
    "save_finding": _tool_save_finding,
    "get_previous_findings": _tool_get_previous_findings,
    "list_past_sessions": _tool_list_past_sessions,
}


def dispatch_tool_call(ctx: ToolContext, tool_name: str, raw_arguments: str) -> str:
    """Run the tool, log it, and return its (possibly truncated) output."""
    try:
        args = json.loads(raw_arguments) if raw_arguments else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}

    fn = _DISPATCH.get(tool_name)
    if fn is None:
        output = f"[error] Unknown tool: {tool_name}"
    else:
        try:
            output = fn(ctx, args)
        except GitHubAPIError as exc:
            output = f"[github error] {exc}"
        except Exception as exc:
            logger.exception("Tool %s raised", tool_name)
            output = f"[error] {exc}"

    summary = output if len(output) <= _OUTPUT_TRUNCATE else output[:_OUTPUT_TRUNCATE] + "\n... [truncated]"

    try:
        ToolCallLog.objects.create(
            session=ctx.session,
            tool_name=tool_name,
            input_params=args,
            output_summary=summary,
        )
    except Exception:
        logger.exception("Failed to persist ToolCallLog for %s", tool_name)

    return summary
