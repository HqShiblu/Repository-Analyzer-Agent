"""Language-aware method/function outline extraction.


    1. `detect_language(path)`    → identify the language by extension.
    2. `extract_outline(content)` → return a list of `MethodSignature` rows
                                     (name + start line + signature text).
    3. `extract_method_body(...)` → given a chosen method, slice its body out
                                     of the file using language-aware rules
                                     (indentation for Python, brace balancing
                                     for C-family, `def...end` for Ruby).

Regex-based; intentionally not a real parser. Good enough to give the LLM
a navigable map without paying the cost of loading the entire file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


# --- Language detection ------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
}


def detect_language(path: str) -> str | None:
    """Return a stable language id for `path` or None if unknown."""
    if not path:
        return None
    suffix = PurePosixPath(path).suffix.lower()
    return _EXT_TO_LANG.get(suffix)


_PYTHON_PATTERNS = [
    re.compile(r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("),
    re.compile(r"^(?P<indent>[ \t]*)class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[\(:]"),
]

_JS_TS_PATTERNS = [
    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"),
    # Class methods / object methods: indented `name(args) {`
    re.compile(r"^\s{2,}(?:public\s+|private\s+|protected\s+|static\s+|async\s+|get\s+|set\s+)*\s*(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{?\s*$"),
]

_GO_PATTERNS = [
    re.compile(r"^func\s+(?:\([^)]*\)\s+)?(?P<name>[A-Za-z_][\w]*)\s*\("),
    re.compile(r"^type\s+(?P<name>[A-Za-z_][\w]*)\s+(?:struct|interface)\b"),
]

_RUST_PATTERNS = [
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|const\s+|unsafe\s+|extern\s+\"[^\"]+\"\s+)*fn\s+(?P<name>[A-Za-z_][\w]*)\s*[<(]"),
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|impl)\s+(?P<name>[A-Za-z_][\w]*)"),
]

_JAVA_CSHARP_PATTERNS = [
    re.compile(
        r"^\s*(?:public|private|protected|internal|static|final|abstract|virtual|override|async|sealed|partial)"
        r"(?:\s+(?:public|private|protected|internal|static|final|abstract|virtual|override|async|sealed|partial))*"
        r"\s+[\w<>\[\],\s\.\?]+\s+(?P<name>[A-Za-z_][\w]*)\s*\([^;{]*\)\s*(?:throws\s+[\w,\s\.]+)?\s*\{?\s*$"
    ),
    re.compile(r"^\s*(?:public|private|protected|internal|abstract|sealed|static|final)*\s*class\s+(?P<name>[A-Za-z_][\w]*)"),
    re.compile(r"^\s*(?:public|private|protected|internal)?\s*interface\s+(?P<name>[A-Za-z_][\w]*)"),
]

_RUBY_PATTERNS = [
    re.compile(r"^\s*def\s+(?:self\.)?(?P<name>[A-Za-z_][\w]*[?!=]?)"),
    re.compile(r"^\s*(?:class|module)\s+(?P<name>[A-Za-z_][\w:]*)"),
]

_PHP_PATTERNS = [
    re.compile(r"^\s*(?:public|private|protected|static|abstract|final)*\s*function\s+(?P<name>[A-Za-z_][\w]*)\s*\("),
    re.compile(r"^\s*(?:abstract|final)?\s*class\s+(?P<name>[A-Za-z_][\w]*)"),
]

_KOTLIN_SWIFT_PATTERNS = [
    re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|protected\s+|open\s+|override\s+|final\s+|fileprivate\s+|inline\s+|operator\s+|suspend\s+|static\s+)*\s*fun(?:c)?\s+(?P<name>[A-Za-z_][\w]*)\s*[<(]"),
    re.compile(r"^\s*(?:public|private|internal|open|final)?\s*(?:class|struct|interface|protocol|extension)\s+(?P<name>[A-Za-z_][\w]*)"),
]

_C_CPP_PATTERNS = [
    re.compile(
        r"^\s*(?:[\w\*&:<>,\s]+?)\s+(?P<name>[A-Za-z_][\w]*)\s*\([^;{}]*\)\s*(?:const)?\s*\{\s*$"
    ),
    re.compile(r"^\s*(?:class|struct)\s+(?P<name>[A-Za-z_][\w]*)"),
]


_LANG_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": _PYTHON_PATTERNS,
    "javascript": _JS_TS_PATTERNS,
    "typescript": _JS_TS_PATTERNS,
    "go": _GO_PATTERNS,
    "rust": _RUST_PATTERNS,
    "java": _JAVA_CSHARP_PATTERNS,
    "csharp": _JAVA_CSHARP_PATTERNS,
    "ruby": _RUBY_PATTERNS,
    "php": _PHP_PATTERNS,
    "kotlin": _KOTLIN_SWIFT_PATTERNS,
    "swift": _KOTLIN_SWIFT_PATTERNS,
    "c": _C_CPP_PATTERNS,
    "cpp": _C_CPP_PATTERNS,
}


@dataclass(frozen=True)
class MethodSignature:
    name: str
    line_start: int
    signature: str
    kind: str
    language: str


def _classify_kind(line: str) -> str:
    head = line.lstrip()
    if head.startswith(("class ", "struct ", "interface ", "trait ", "module ", "enum ", "protocol ", "extension ")):
        return "class"
    if "class " in head and " class " not in head.replace("class ", " class "):
        return "class"
    return "function"


def extract_outline(content: str, language: str) -> list[MethodSignature]:
    patterns = _LANG_PATTERNS.get(language)
    if not patterns:
        return []

    seen: set[tuple[str, int]] = set()
    out: list[MethodSignature] = []
    for lineno, raw in enumerate(content.splitlines(), start=1):
        for pat in patterns:
            m = pat.match(raw)
            if not m:
                continue
            name = m.group("name")
            key = (name, lineno)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                MethodSignature(
                    name=name,
                    line_start=lineno,
                    signature=raw.rstrip(),
                    kind=_classify_kind(raw),
                    language=language,
                )
            )
            break
    return out


def extract_method_body(
    content: str,
    method: MethodSignature,
    max_lines: int = 400,
) -> tuple[str, int, int]:
    """Return (body_text, line_start, line_end) for `method` inside `content`.

    Uses indentation tracking for Python and Ruby and brace balancing for the
    C-family of languages. Falls back to "signature plus next `max_lines`
    lines" when the language has no body delimiter we recognize.
    """
    lines = content.splitlines()
    start_idx = method.line_start - 1
    if start_idx < 0 or start_idx >= len(lines):
        return "", method.line_start, method.line_start

    if method.language == "python":
        end_idx = _python_block_end(lines, start_idx)
    elif method.language == "ruby":
        end_idx = _ruby_block_end(lines, start_idx)
    else:
        end_idx = _brace_block_end(lines, start_idx)

    if end_idx is None or end_idx <= start_idx:
        end_idx = min(start_idx + max_lines, len(lines) - 1)
    end_idx = min(end_idx, start_idx + max_lines)

    body_lines = lines[start_idx : end_idx + 1]
    width = max(3, len(str(end_idx + 1)))
    numbered = "\n".join(
        f"{i:>{width}} | {ln}"
        for i, ln in zip(range(method.line_start, end_idx + 2), body_lines)
    )
    return numbered, method.line_start, end_idx + 1


def _leading_indent(line: str) -> int:
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4
        else:
            break
    return n


def _python_block_end(lines: list[str], start_idx: int) -> int | None:
    """Find the last line of a Python def/class block by indentation."""
    base = _leading_indent(lines[start_idx])
    last = start_idx
    for i in range(start_idx + 1, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            last = i
            continue
        if _leading_indent(raw) <= base:
            return last
        last = i
    return last


def _ruby_block_end(lines: list[str], start_idx: int) -> int | None:
    """Find the matching `end` for a Ruby def/class/module block."""
    depth = 0
    opener_re = re.compile(r"\b(?:def|class|module|do|if|unless|while|until|begin|case)\b")
    end_re = re.compile(r"^\s*end\b")
    for i in range(start_idx, len(lines)):
        line = lines[i]
        for _ in opener_re.findall(line):
            depth += 1
        if end_re.match(line):
            depth -= 1
            if depth <= 0:
                return i
    return None


def _brace_block_end(lines: list[str], start_idx: int) -> int | None:
    """Find the matching `}` for a brace-block language."""
    depth = 0
    started = False
    for i in range(start_idx, len(lines)):
        line = lines[i]
        sanitized = re.sub(r"//.*", "", line)
        sanitized = re.sub(r"/\*.*?\*/", "", sanitized)
        for ch in sanitized:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return i
    return None

def format_outline(path: str, language: str, methods: list[MethodSignature]) -> str:
    """Pretty-print an outline for an LLM tool result."""
    if not methods:
        return f"(no method/class signatures detected in {path} for language {language})"
    lines = [f"Outline of {path} (language: {language}):"]
    for m in methods:
        kind = m.kind
        lines.append(f"  L{m.line_start:>5}  [{kind:<8}] {m.name}    | {m.signature.strip()[:120]}")
    return "\n".join(lines)

