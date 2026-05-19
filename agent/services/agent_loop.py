"""Main tool-calling agent loop.

Runs only when the cache miss + LLM-knowledge + README-scan steps have all
failed. Implements the strict ordering described in SPECS.md:

    while tool_calls < MAX_LOOP:
        response = llm.chat(messages, tools=TOOL_DEFINITIONS)
        if finish_reason == "stop": break
        for tc in response.tool_calls:
            result = dispatch(tc)
            log_tool_call(...)
            messages.append(tool_result)
    final_answer = response.message.content
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from django.conf import settings

from agent.services import llm
from agent.services.tools import TOOL_DEFINITIONS, ToolContext, dispatch_tool_call

logger = logging.getLogger(__name__)


def _tool_log_suffix(tool_name: str, raw_arguments: str) -> str:
    """Short human-readable hint for stdout (paths, method names)."""
    try:
        args = json.loads(raw_arguments) if raw_arguments else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}

    path_keys = ("path", "file_path")
    path = next((args[k] for k in path_keys if args.get(k)), None)

    if tool_name in {"read_file", "get_file_summary", "get_file_outline"} and path:
        return f" ({path})"
    if tool_name == "read_method":
        bits = []
        if path:
            bits.append(path)
        if args.get("method_name"):
            bits.append(args["method_name"])
        return f" ({', '.join(bits)})" if bits else ""
    if tool_name == "list_files":
        p = args.get("path")
        label = p if p else "(root)"
        return f" ({label})"
    if tool_name == "save_finding" and path:
        return f" ({path})"
    if tool_name == "search_code" and args.get("query"):
        q = str(args["query"])
        if len(q) > 48:
            q = q[:45] + "..."
        return f" ({q})"
    return ""


SYSTEM_PROMPT_TEMPLATE = """You are a codebase research agent with tools to explore a GitHub repository.
Repository: {repo_url}
Question: {question}

Rules:
1. Always call get_directory_tree() first.
2. Always call get_previous_findings() before reading any files.
3. For any CODE file you suspect is relevant, follow the OUTLINE-FIRST strategy:
   a. Call get_file_outline(path) first — this returns ONLY method/class names and
      their starting line numbers, NOT the bodies. It is cheap.
   b. From the returned outline, pick the specific methods that look relevant to
      the question (use the method names and the question's intent as your guide).
   c. Call read_method(path, method_name) for each chosen method to load ONLY its
      body. This dramatically reduces tokens compared to loading the whole file.
   d. Only fall back to read_file when (a) the language is unsupported and the
      outline came back empty, or (b) the file is non-code (README, config,
      manifest), or (c) you genuinely need to see top-level code that isn't
      inside any method.
4. Call save_finding() whenever you learn something meaningful about a file.
   Include line_start/line_end whenever possible.
5. Cite files in your final answer as [[path/to/file.py:line_start-line_end]]
   (use just [[path/to/file.py]] when you don't have line numbers).
6. Stop calling tools once you can answer confidently. Do not over-explore.
7. If you cannot determine the answer, say so clearly. Do not hallucinate.
8. Your final answer must include specific file paths, function names, and
   (when possible) line numbers.

You have a hard limit of {max_loop} tool calls. Be efficient — the outline-first
strategy is the cheapest way to cover a large codebase.
"""


@dataclass
class AgentResult:
    answer: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tool_calls_made: int


def run_agent_loop(ctx: ToolContext, question: str) -> AgentResult:
    """Run the tool-calling loop and return the final answer + token totals.

    Loop shape:
        - While the budget allows, give the LLM tools and let it call them.
        - When the LLM finally responds without tool calls, that's the answer.
        - If the loop exits without a textual answer (budget exhausted, or LLM
          only ever emitted tool calls), do ONE final round-trip with
          `tools=None` and an explicit "produce your final answer now"
          instruction. This guarantees a textual result whenever the LLM is
          reachable at all.
    """
    max_loop = settings.AGENT_MAX_LOOP

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        repo_url=ctx.repository.url,
        question=question,
        max_loop=max_loop,
    )
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    tool_calls_made = 0
    cumulative_prompt = 0
    cumulative_completion = 0
    cumulative_total = 0
    last_message_content: str | None = None

    while tool_calls_made < max_loop:
        response = llm.chat(messages=messages, tools=TOOL_DEFINITIONS)
        cumulative_prompt += response.prompt_tokens
        cumulative_completion += response.completion_tokens
        cumulative_total += response.total_tokens

        msg = response.message
        content = getattr(msg, "content", None)
        if content:
            last_message_content = content

        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            break

        messages.append(llm.message_to_dict(msg))

        # Tokens for this LLM round only (same value for each tool dispatched
        # from this assistant message — attribution is per completion, not per tool).
        round_total = response.total_tokens

        for tc in tool_calls:
            tool_calls_made += 1
            tool_name = tc.function.name
            suffix = _tool_log_suffix(tool_name, tc.function.arguments)
            print(
                f"[Tool Call {tool_calls_made}/{max_loop}] "
                f"{tool_name}{suffix} |  tokens this round: {round_total:,}",
                flush=True,
            )
            result = dispatch_tool_call(ctx, tool_name, tc.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tool_name,
                    "content": result,
                }
            )
            if tool_calls_made >= max_loop:
                break

    if not last_message_content:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Tool-call budget exhausted ({tool_calls_made}/{max_loop})." if tool_calls_made >= max_loop
                    else "Produce your final answer now."
                )
                + " Reply with a clear textual answer based on the context you have."
                " Cite files as [[path:line_start-line_end]]. If you cannot answer"
                " from the context, say so honestly — do not call any more tools.",
            }
        )
        try:
            final = llm.chat(messages=messages, tools=None)
            cumulative_prompt += final.prompt_tokens
            cumulative_completion += final.completion_tokens
            cumulative_total += final.total_tokens
            forced = getattr(final.message, "content", None)
            if forced:
                last_message_content = forced
        except Exception as exc:
            logger.warning("Forced final-answer LLM call failed: %s", exc)

    print(f"\nTotal tokens used: {cumulative_total:,}\n", flush=True)

    return AgentResult(
        answer=(last_message_content or "").strip(),
        prompt_tokens=cumulative_prompt,
        completion_tokens=cumulative_completion,
        total_tokens=cumulative_total,
        tool_calls_made=tool_calls_made,
    )
