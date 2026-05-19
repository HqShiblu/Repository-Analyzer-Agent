"""Unit tests for the language-aware outline / method-body extractor.

These verify that we can pull method signatures and bodies out of a file
for the common languages we care about — Python (indent-based), JavaScript
/ TypeScript (braces), Go (braces), and Ruby (`def...end`).
"""

from __future__ import annotations

import textwrap

import pytest

from agent.services.outline import (
    MethodSignature,
    detect_language,
    extract_method_body,
    extract_outline,
)


class TestLanguageDetection:
    def test_python(self):
        assert detect_language("src/main.py") == "python"

    def test_typescript_tsx(self):
        assert detect_language("ui/Button.tsx") == "typescript"

    def test_go(self):
        assert detect_language("server/main.go") == "go"

    def test_unknown(self):
        assert detect_language("README.md") is None
        assert detect_language("noext") is None


class TestPythonOutline:
    SRC = textwrap.dedent(
        '''
        """Module docstring."""

        class Greeter:
            def __init__(self, name: str) -> None:
                self.name = name

            async def greet(self, greeting="hi"):
                return f"{greeting}, {self.name}"

        def top_level(x, y):
            return x + y
        '''
    ).strip()

    def test_extracts_class_and_methods(self):
        methods = extract_outline(self.SRC, "python")
        names = [m.name for m in methods]
        assert "Greeter" in names
        assert "__init__" in names
        assert "greet" in names
        assert "top_level" in names

    def test_method_body_uses_indent(self):
        methods = extract_outline(self.SRC, "python")
        init = next(m for m in methods if m.name == "__init__")
        body, start, end = extract_method_body(self.SRC, init)
        assert "self.name = name" in body
        assert end >= start
        # The body should NOT include the next method's signature line.
        assert "async def greet" not in body


class TestJSOutline:
    SRC = textwrap.dedent(
        """
        export function add(a, b) {
            return a + b;
        }

        export const sub = (a, b) => {
            return a - b;
        };

        class Calc {
            mul(a, b) {
                return a * b;
            }
        }
        """
    ).strip()

    def test_finds_function_arrow_class_method(self):
        methods = extract_outline(self.SRC, "javascript")
        names = {m.name for m in methods}
        assert {"add", "sub", "Calc", "mul"}.issubset(names)

    def test_braces_terminate_body(self):
        methods = extract_outline(self.SRC, "javascript")
        add = next(m for m in methods if m.name == "add")
        body, _, _ = extract_method_body(self.SRC, add)
        assert "return a + b" in body
        assert "export const sub" not in body


class TestGoOutline:
    SRC = textwrap.dedent(
        """
        package main

        type Server struct {
            Addr string
        }

        func (s *Server) Start() error {
            return nil
        }

        func main() {
            println("hi")
        }
        """
    ).strip()

    def test_finds_methods_and_types(self):
        methods = extract_outline(self.SRC, "go")
        names = {m.name for m in methods}
        assert "Start" in names
        assert "main" in names
        assert "Server" in names


class TestRubyOutline:
    SRC = textwrap.dedent(
        """
        class Hello
          def greet(name)
            puts "hi #{name}"
          end

          def bye
            puts "bye"
          end
        end
        """
    ).strip()

    def test_def_end_block(self):
        methods = extract_outline(self.SRC, "ruby")
        names = {m.name for m in methods}
        assert {"Hello", "greet", "bye"}.issubset(names)

    def test_body_includes_only_one_method(self):
        methods = extract_outline(self.SRC, "ruby")
        greet = next(m for m in methods if m.name == "greet")
        body, _, _ = extract_method_body(self.SRC, greet)
        assert 'hi #{name}' in body
        assert "def bye" not in body


class TestUnsupportedLanguage:
    def test_returns_empty(self):
        assert extract_outline("hello world", "brainfuck") == []
