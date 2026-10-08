"""Test-suite integrity: deleted/renamed test files, removed test fns, added skips."""
from __future__ import annotations

import io
import re
import tokenize
from dataclasses import replace

from ...types import Diff, Finding, Severity
from ..gitdiff import _test_discovery

# Trusted caller policy only: never read enforcement settings from the candidate tree.
STRICT_RULES = frozenset({"test-path-removed", "removed-test-fn", "added-skip", "removed-assert"})


def apply_policy(findings: list[Finding], *, strict: bool) -> list[Finding]:
    """Blocking-only policy; do not mutate findings supplied by the caller."""
    return [replace(f, severity=Severity.BLOCK)
            if strict and f.rule in STRICT_RULES and f.severity == Severity.WARN else f
            for f in findings]


_PY_TEST_FN = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_JS_TEST_FN = re.compile(r"""^\s*(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)""")

_PY_SKIP_CALLS = {"pytest.skip", "pytest.xfail", "self.skipTest",
                  "unittest.skip", "unittest.skipIf", "unittest.skipUnless"}
_PY_MARKS = {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}
_JS_SKIP = re.compile(r"(?<![\w.$])(?:it|test|describe|context)\s*\.\s*(?:skip|only)\s*\(")
# Recognize regex literals at expression starts before interpreting their quotes
# as string delimiters. Division must remain code, not swallow later skip calls.
_JS_REGEX = r"/(?![/*])(?:\\[^\r\n]|\[(?:\\[^\r\n]|[^\]\\\r\n])*\]|[^/\\[\r\n])+/[dgimsuvy]*"
_JS_NONCODE = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/|"
    r"(?:[=(:,!&|?{;\[]\s*|(?<![\w.$])(?:return|throw|case|yield|await)\s+|^\s*)"
    + _JS_REGEX
    + r"|\"(?:\\[\s\S]|[^\"\\\r\n])*\"|'(?:\\[\s\S]|[^'\\\r\n])*'|`(?:\\.|[^`\\])*`",
    re.MULTILINE,
)


def _skip_evidence(
    lines: list[str], language: str, added_line_numbers: list[int] | None = None,
) -> str | None:
    """Match added framework syntax in candidate lexical context, not strings/comments.

    Real Git diffs supply the complete candidate and added-line coordinates. Legacy
    hand-built/corpus diffs have only fragments and retain best-effort tokenization.
    Aliases/shadowed bindings and JS template interpolations are not resolved.
    """
    added_rows = set(added_line_numbers) if added_line_numbers is not None else set(
        range(1, len(lines) + 1))
    source = "\n".join(lines)
    if language == "python":
        tokens = []
        try:
            # Only legacy fragments need indentation normalization. Full candidate
            # context must stay intact, including multiline strings across hunks.
            lexical_source = (source if added_line_numbers is not None else
                              "\n".join(line.lstrip() for line in lines))
            for token in tokenize.generate_tokens(io.StringIO(lexical_source).readline):
                if token.type not in (tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
                                      tokenize.COMMENT):
                    tokens.append(token)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass  # retained tokens still cover complete calls in a partial diff
        for i, token in enumerate(tokens):
            if token.type != tokenize.NAME or (i and tokens[i - 1].string == "."):
                continue
            name, end = token.string, i + 1
            while (end + 1 < len(tokens) and tokens[end].string == "."
                   and tokens[end + 1].type == tokenize.NAME):
                name += "." + tokens[end + 1].string
                end += 2
            is_call = end < len(tokens) and tokens[end].string == "("
            is_decorator = i > 0 and tokens[i - 1].string == "@"
            if name in _PY_MARKS or (name in _PY_SKIP_CALLS and (is_call or is_decorator)):
                marker_tokens = tokens[i - int(is_decorator):end + int(is_call)]
                marker_rows = {row for t in marker_tokens
                               for row in range(t.start[0], t.end[0] + 1)}
                if changed := marker_rows & added_rows:
                    return lines[min(changed) - 1].strip()
    elif language in ("js", "ts"):
        code = _JS_NONCODE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), source)
        for match in _JS_SKIP.finditer(code):
            start = code.count("\n", 0, match.start()) + 1
            end = code.count("\n", 0, match.end()) + 1
            if changed := set(range(start, end + 1)) & added_rows:
                return lines[min(changed) - 1].strip()
    return None


def _test_fn_names(lines: list[str], language: str) -> set[str]:
    pat = _PY_TEST_FN if language == "python" else _JS_TEST_FN if language in ("js", "ts") else None
    if pat is None:
        return set()
    return {m.group(1) for ln in lines if (m := pat.match(ln))}


def check(diff: Diff, root: str) -> list[Finding]:
    out: list[Finding] = []
    for f in diff.files:
        if not f.is_test:
            continue

        if f.status == "D":
            out.append(Finding("deleted-test", Severity.BLOCK,
                               f"test file deleted: {f.path}", file=f.path))
            continue  # nothing else to say about a gone file
        if f.status == "R":
            out.append(Finding("renamed-test", Severity.WARN,
                               f"test file renamed: {f.old_path} -> {f.path}",
                               file=f.path))
            old_discovery = _test_discovery(f.old_path)
            if old_discovery and old_discovery != _test_discovery(f.path):
                out.append(Finding("test-path-removed", Severity.WARN,
                                   "test rename loses known default discovery semantics; "
                                   "verify custom collection/configuration for the new path",
                                   file=f.path, evidence=f"{f.old_path} -> {f.path}"))

        # Copies leave the source test intact; edits with the same name are not removals.
        gone = (set() if f.status == "C" else
                _test_fn_names(f.removed, f.old_language or f.language)
                - _test_fn_names(f.added, f.language))
        if gone:
            out.append(Finding("removed-test-fn", Severity.WARN,
                               f"test function(s) removed: {', '.join(sorted(gone))}",
                               file=f.path))

        candidate = f.candidate_lines if f.candidate_lines is not None else f.added
        positions = f.added_line_numbers if f.candidate_lines is not None else None
        if f.added and (evidence := _skip_evidence(candidate, f.language, positions)):
            out.append(Finding("added-skip", Severity.WARN,
                               "a skip/only marker was added to a test", file=f.path,
                               evidence=evidence))
    return out
