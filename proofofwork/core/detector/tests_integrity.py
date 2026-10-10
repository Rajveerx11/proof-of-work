"""Patch-based test integrity heuristics; findings are review signals, not accusations."""
from __future__ import annotations

import io
import re
import tokenize

from ...types import Diff, Finding, Severity
from ..gitdiff import _is_test, _language, _test_discovery

_PY_TEST_FN = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_JS_TEST_FN = re.compile(r"""^\s*(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)""")
_PY_MARKS = {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail"}
_PY_CALLS = {"pytest.skip", "pytest.xfail", "self.skipTest", "unittest.skip",
             "unittest.skipIf", "unittest.skipUnless"}
_JS_SKIP = re.compile(r"(?<![\w$])(?:it|test|describe|context)\s*\.\s*(?:skip|only)\s*\(")
_JS_NONCODE = re.compile(
    r"//[^\r\n\u2028\u2029]*|/\*[\s\S]*?(?:\*/|$)"
    r'|"(?:\\[\s\S]|[^"\\\r\n])*"|\'(?:\\[\s\S]|[^\'\\\r\n])*\''
    r"|`(?:\\[\s\S]|[^`\\])*`"
)
_JS_REGEX = re.compile(
    r"/(?![/*])(?:\\[^\r\n\u2028\u2029]|\[(?:\\[^\r\n\u2028\u2029]|[^\]\\\r\n\u2028\u2029])*\]"
    r"|[^/\\[\r\n\u2028\u2029])+/[dgimsuvy]*"
)
_JS_TOKEN = re.compile(r"[\w$]+|\.\.\.|\?\.|=>|[^\s]")
_JS_REGEX_PREFIX = {"", "(", "[", "{", "=", ":", ",", ";", "!", "?", "=>",
                    "return", "throw", "yield", "case", "else", "do"}


def _js_code(source: str) -> str:
    """Mask ordinary noncode without changing offsets or Git LF coordinates.

    Lightweight heuristic: templates are opaque; ambiguous regex/division contexts,
    aliases and shadowed framework names require a parser/collection-aware analysis.
    """
    code = list(source)
    pos, previous = 0, ""
    while pos < len(source):
        if source[pos].isspace():
            pos += 1
            continue
        masked = _JS_NONCODE.match(source, pos)
        if masked is None and previous in _JS_REGEX_PREFIX:
            masked = _JS_REGEX.match(source, pos)
        if masked:
            code[pos:masked.end()] = [c if c in "\r\n\u2028\u2029" else " "
                                      for c in masked.group()]
            if not masked.group().startswith(("//", "/*")):
                previous = "literal"
            pos = masked.end()
            continue
        token = _JS_TOKEN.match(source, pos)
        assert token is not None  # whitespace was handled above
        previous, pos = token.group(), token.end()
    return "".join(code)


def _python_code(lines: list[str]):
    """Legacy fragments retain complete tokens; omitted lexical context is unknowable."""
    raw = "\n".join(line.lstrip() for line in lines).encode("utf-8", "surrogateescape")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    source = raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
    source_lines = source.split("\n")
    code = [[" "] * len(line) for line in source_lines]
    tokens = []
    try:
        tokens.extend(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass  # Changed-line fragments need not be complete Python programs.
    noncode = {tokenize.STRING, tokenize.COMMENT,
               *(getattr(tokenize, kind, tokenize.STRING)
                 for kind in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END",
                              "TSTRING_START", "TSTRING_MIDDLE", "TSTRING_END"))}
    for token in tokens:
        if token.type in noncode:
            continue
        for row in range(token.start[0], min(token.end[0], len(code)) + 1):
            start = token.start[1] if row == token.start[0] else 0
            end = token.end[1] if row == token.end[0] else len(code[row - 1])
            code[row - 1][start:end] = source_lines[row - 1][start:end]
    return ["".join(line) for line in code], tokens


def _test_fn_names(lines: list[str], language: str) -> set[str]:
    if language == "python":
        code, _ = _python_code(lines)
        return {m.group(1) for line in code if (m := _PY_TEST_FN.match(line))}
    if language in ("js", "ts"):
        code = _js_code("\n".join(lines)).split("\n")
        # Keep the raw call description, but require executable call-prefix syntax.
        return {m.group(1) for line, masked in zip(lines, code)
                if (m := _JS_TEST_FN.match(line))
                and masked[:m.start(1) - 1] == line[:m.start(1) - 1]}
    return set()


def _skip_evidence(lines: list[str], language: str) -> str | None:
    if language == "python":
        _, lexical = _python_code(lines)
        tokens = [t for t in lexical if t.type not in
                  (tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT)]
        for i, token in enumerate(tokens):
            if token.type != tokenize.NAME or (i and tokens[i - 1].string == "."):
                continue
            name, end = token.string, i + 1
            while end + 1 < len(tokens) and tokens[end].string == "." and tokens[end + 1].type == tokenize.NAME:
                name += "." + tokens[end + 1].string
                end += 2
            if name in _PY_MARKS or (name in _PY_CALLS and (
                (end < len(tokens) and tokens[end].string == "(")
                or (i and tokens[i - 1].string == "@")
            )):
                return name  # Normalized matched syntax, not authorization evidence.
    elif language in ("js", "ts"):
        code = _js_code("\n".join(lines))
        tokens = iter(_JS_TOKEN.finditer(code))
        current, previous = next(tokens, None), ""
        for match in _JS_SKIP.finditer(code):
            while current is not None and current.start() < match.start():
                previous, current = current.group(), next(tokens, None)
            if previous not in {".", "?."}:
                return match.group().strip()
    return None


def check(diff: Diff, root: str) -> list[Finding]:
    """Analyze only diff facts; never read candidate policy, overrides or dirty files."""
    out: list[Finding] = []
    for f in diff.files:
        if not (f.is_test or _is_test(f.path) or _is_test(f.old_path)):
            continue
        if f.status == "D":
            out.append(Finding("deleted-test", Severity.BLOCK,
                               f"test-related file deleted: {f.path}; review suite coverage",
                               file=f.path))
            continue
        if f.status == "R":
            out.append(Finding("renamed-test", Severity.WARN,
                               f"test-related file renamed: {f.old_path} -> {f.path}", file=f.path))
            old_discovery = _test_discovery(f.old_path)
            if old_discovery and old_discovery != _test_discovery(f.path):
                out.append(Finding("test-path-removed", Severity.WARN,
                                   "rename loses known default test discovery semantics; "
                                   "review configured collection and preserved verification",
                                   file=f.path, evidence=f"{f.old_path} -> {f.path}"))
        language = f.language or _language(f.path) or _language(f.old_path)
        try:
            # Copies introduce additions but leave all source tests intact.
            gone = (set() if f.status == "C" else
                    _test_fn_names(f.removed, _language(f.old_path) or language)
                    - _test_fn_names(f.added, language))
            if gone:
                out.append(Finding("removed-test-fn", Severity.WARN,
                                   f"test-like definition(s) removed from patch: {', '.join(sorted(gone))}; "
                                   "verify before/after collection", file=f.path))
            if evidence := _skip_evidence(f.added, language):
                out.append(Finding("added-skip", Severity.WARN,
                                   "recognized framework skip/focus syntax added; review intended collection",
                                   file=f.path, evidence=evidence))
        except (UnicodeError, LookupError, SyntaxError) as exc:
            out.append(Finding("integrity-analysis-incomplete", Severity.WARN,
                               f"cannot decode patch fragment for integrity analysis: {exc}; "
                               "complete source/encoding context is required", file=f.path))
    return out
