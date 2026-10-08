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
_JS_REGEX = re.compile(
    r"/(?![/*])(?:\\[^\r\n]|\[(?:\\[^\r\n]|[^\]\\\r\n])*\]|[^/\\[\r\n])+/[dgimsuvy]*"
)
_JS_COMMENT = re.compile(r"//[^\n]*|/\*[\s\S]*?(?:\*/|$)")
_JS_STRING = re.compile(
    r"\"(?:\\[\s\S]|[^\"\\\r\n])*\"|'(?:\\[\s\S]|[^'\\\r\n])*'|`(?:\\[\s\S]|[^`\\])*`"
)
_JS_TOKEN = re.compile(r"[\w$]+|\.\.\.|===|!==|=>|\+\+|--|&&|\|\||\?\?|\?\.|==|!=|<=|>=|[^\s]")
_JS_CONTROLS = {"if", "while", "for", "with", "switch", "catch"}


def _js_code(source: str) -> str:
    """Mask noncode without changing offsets; distinguish block ends from values.

    ponytail: lightweight lexical context, not a JS/TS parser. Templates remain
    opaque and aliases/shadowing are unresolved; deeper semantics need a parser.
    """
    code = list(source)
    # Frames remember whether a closing delimiter ends a statement or a value.
    frames: list[tuple[str, str]] = []
    # A classic initializer or for-in RHS cannot later turn an identifier `of`
    # into a for-of separator. Depth keys keep nested loop headers independent.
    for_expressions: set[int] = set()
    expression_start = statement_start = True
    previous = closed_paren = ""
    pending_function = pending_control = label = async_kind = ""
    pending_classes: list[tuple[int, str]] = []
    case_depth: int | None = None
    ternaries: list[int] = []
    pos = 0
    while pos < len(source):
        if source[pos].isspace():
            pos += 1
            continue
        comment = _JS_COMMENT.match(source, pos)
        literal = None if comment else _JS_STRING.match(source, pos)
        if not comment and not literal and expression_start:
            literal = _JS_REGEX.match(source, pos)
        if masked := comment or literal:
            code[pos:masked.end()] = ["\n" if c == "\n" else " " for c in masked.group()]
            pos = masked.end()
            if literal:
                expression_start = statement_start = False
                previous, closed_paren = "literal", ""
            continue
        match = _JS_TOKEN.match(source, pos)
        assert match is not None  # whitespace was handled above
        token = match.group()
        pos = match.end()
        was_statement_start = statement_start
        if frames and frames[-1] == ("(", "for") and token in {"=", ";", ",", "in"}:
            for_expressions.add(len(frames))
        declaration_kind = "block" if statement_start or not expression_start else "expression-block"
        if token == "(":
            kind = pending_function or pending_control or "value"
            frames.append(("(", kind))
            pending_function = ""
            expression_start, statement_start = True, False
        elif token == ")":
            for_expressions.discard(len(frames))
            closed_paren = frames.pop()[1] if frames and frames[-1][0] == "(" else "value"
            expression_start = statement_start = closed_paren in _JS_CONTROLS
        elif token == "{":
            if pending_classes and pending_classes[-1][0] == len(frames):
                kind = pending_classes.pop()[1]
            elif previous == ")" and closed_paren == "switch":
                kind = "switch"
            elif previous == "=>":
                kind = "expression-block"
            elif previous == ")" and closed_paren in {"block", "expression-block"}:
                kind = closed_paren
            else:
                kind = "block" if statement_start or not expression_start else "object"
            frames.append(("{", kind))
            expression_start, statement_start = True, kind != "object"
        elif token == "}":
            kind = frames.pop()[1] if frames and frames[-1][0] == "{" else "block"
            expression_start = statement_start = kind in {"block", "switch"}
        elif token == "[":
            frames.append(("[", "value"))
            expression_start, statement_start = True, False
        elif token == "]":
            if frames and frames[-1][0] == "[":
                frames.pop()
            expression_start = statement_start = False
        elif token == ";":
            expression_start = True
            statement_start = not frames or frames[-1] in {
                ("{", "block"), ("{", "switch"), ("{", "expression-block")}

        elif token == "?":
            ternaries.append(len(frames))
            expression_start, statement_start = True, False
        elif token == ":":
            expression_start = True
            if ternaries and ternaries[-1] == len(frames):
                ternaries.pop()
                statement_start = False
            else:
                statement_start = previous == label or case_depth == len(frames)
                if case_depth == len(frames):
                    case_depth = None
        elif token in {"function", "class"} and previous not in {".", "?."}:
            kind = async_kind if previous == "async" else declaration_kind
            if token == "function":
                pending_function = kind
            else:
                pending_classes.append((len(frames), kind))
            expression_start = statement_start = False
        elif (token in {"case", "default"} and frames and frames[-1] == ("{", "switch")
              and previous not in {".", "?."}):
            case_depth = len(frames)
            expression_start, statement_start = True, False
        elif token in {"async", "export", "default"} and statement_start:
            # Declaration prefixes must not turn function/class bodies into values.
            expression_start = True
        elif token == "debugger" and statement_start:
            # A debugger statement ends at its semicolon or automatic insertion;
            # a following regex is a new expression, not division by an identifier.
            expression_start = statement_start = True
        elif re.fullmatch(r"[\w$]+", token):
            binding_target = (previous in {"]", "}"}
                              or (bool(re.fullmatch(r"[\w$]+", previous))
                                  and previous not in {"const", "let", "var", "literal"}))
            for_of = (token == "of" and bool(frames) and frames[-1] == ("(", "for")
                      and len(frames) not in for_expressions
                      and (not expression_start or previous in {"}", "]"})
                      and binding_target)
            expression_start = for_of or (previous not in {".", "?."} and token in {
                "return", "throw", "case", "yield", "await", "delete", "void", "typeof",
                "new", "in", "instanceof", "else", "do"})
            statement_start = token in {"else", "do", "try", "finally"}
        elif token in {".", "?."}:
            expression_start = statement_start = False
        elif token in {"++", "--"}:
            statement_start = False  # prefix still expects a value; postfix does not
        else:
            expression_start, statement_start = True, False
        if token in {":", ",", ";", "}", ")", "]"}:
            # Property names like `class:` are not declarations; discard stale
            # headers when leaving their depth, without losing an outer class.
            header_depth = len(frames) + int(token in {"}", ")", "]"})
            while pending_classes and pending_classes[-1][0] >= header_depth:
                pending_classes.pop()
            if token in {":", ",", ";"}:
                pending_function = ""
        if token in _JS_CONTROLS and previous not in {".", "?."}:
            pending_control = token
        elif not (token == "await" and pending_control == "for"):
            pending_control = ""
        async_kind = declaration_kind if token == "async" else ""
        label = token if was_statement_start and re.fullmatch(r"[\w$]+", token) else ""
        if token != ")":
            closed_paren = ""
        previous = token
    return "".join(code)


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
        code = _js_code(source)
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
