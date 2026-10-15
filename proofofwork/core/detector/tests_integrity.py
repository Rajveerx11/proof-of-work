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
_JS_REGEX = re.compile(
    r"/(?![/*])(?:\\[^\r\n\u2028\u2029]|\[(?:\\[^\r\n\u2028\u2029]|[^\]\\\r\n\u2028\u2029])*\]"
    r"|[^/\\[\r\n\u2028\u2029])+/[dgimsuvy]*"
)
_JS_COMMENT = re.compile(r"//[^\r\n\u2028\u2029]*|/\*[\s\S]*?(?:\*/|$)")
_JS_STRING = re.compile(
    r"\"(?:\\[\s\S]|[^\"\\\r\n])*\"|'(?:\\[\s\S]|[^'\\\r\n])*'|`(?:\\[\s\S]|[^`\\])*`"
)
_JS_TOKEN = re.compile(r"[\w$]+|\.\.\.|===|!==|=>|\+\+|--|&&|\|\||\?\?|\?\.|==|!=|<=|>=|[^\s]")
_JS_CONTROLS = {"if", "while", "for", "with", "switch", "catch"}


def _js_code(source: str) -> str:
    """Mask noncode without changing offsets; distinguish block ends from values.

    Lightweight lexical context, not a JS/TS parser. Templates remain
    opaque and aliases/shadowing are unresolved; deeper semantics need a parser.
    """
    code = list(source)
    # Frames remember whether a closing delimiter ends a statement or a value.
    frames: list[tuple[str, str]] = []
    # A classic initializer or for-in RHS cannot later turn an identifier `of`
    # into a for-of separator. Depth keys keep nested loop headers independent.
    for_expressions: set[int] = set()
    expression_start = statement_start = True
    jump_statement = False
    line_terminated = False
    module_depth: int | None = None
    previous = closed_paren = ""
    pending_function = pending_control = label = async_kind = ""
    pending_classes: list[tuple[int, str]] = []
    case_depth: int | None = None
    ternaries: list[int] = []
    pos = 0
    while pos < len(source):
        if source[pos].isspace():
            line_terminated |= source[pos] in "\r\n\u2028\u2029"
            if jump_statement and source[pos] in "\r\n\u2028\u2029":
                expression_start = statement_start = True
                jump_statement = False
            pos += 1
            continue
        comment = _JS_COMMENT.match(source, pos)
        literal = None if comment else _JS_STRING.match(source, pos)
        if not comment and not literal and expression_start:
            literal = _JS_REGEX.match(source, pos)
        if masked := comment or literal:
            code[pos:masked.end()] = [c if c in "\r\n\u2028\u2029" else " " for c in masked.group()]
            pos = masked.end()
            if comment:
                line_terminated |= any(c in comment.group() for c in "\r\n\u2028\u2029")
            if comment and jump_statement and line_terminated:
                expression_start = statement_start = True
                jump_statement = False
            if literal:
                line_terminated = False
                module_source = module_depth == len(frames) and previous in {"import", "from"}
                expression_start = statement_start = module_source
                if module_source:
                    module_depth = None
                previous, closed_paren = "literal", ""
            continue
        match = _JS_TOKEN.match(source, pos)
        assert match is not None  # whitespace was handled above
        token = match.group()
        pos = match.end()
        was_statement_start = statement_start
        statement_boundary = statement_start or (
            line_terminated and previous not in {".", "?."} and (
                not frames or frames[-1] in {
                    ("{", "block"), ("{", "switch"), ("{", "expression-block")}
            )
        )
        if token in {"import", "export"} and statement_boundary:
            module_depth = len(frames)
        elif module_depth == len(frames) and (
            token in {"default", "const", "let", "var", "function", "class", ";"}
            or (token == "(" and previous == "import")
        ):
            module_depth = None
        if (frames and frames[-1] == ("(", "for") and token in {"=", ";", ",", "in"}
                and previous not in {".", "?."}):
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
            jump_statement = False
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
            jump_statement = False
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
        elif token in {"break", "continue"} and statement_boundary:
            # The optional label belongs to this jump statement. Its next line
            # starts a new statement even without a written semicolon (ASI).
            jump_statement = True
            expression_start = statement_start = False
        elif token == "debugger" and statement_boundary:
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
        line_terminated = False
    return "".join(code)

def _python_code(lines: list[str], *, full_context: bool = False):
    """Legacy fragments retain complete tokens; omitted lexical context is unknowable."""
    raw = "\n".join(lines if full_context else (line.lstrip() for line in lines)).encode(
        "utf-8", "surrogateescape")
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
        # Tokenization normalizes Python universal newlines; Git coordinates count
        # only LF. A bare CR must not shift a marker out of its changed Git row.
        token_rows = [1]
        for newline in re.finditer(r"\r\n|\r|\n", source):
            token_rows.append(token_rows[-1] + int("\n" in newline.group()))
        _, lexical = _python_code(lines, full_context=added_line_numbers is not None)
        tokens = [t for t in lexical if t.type not in
                  (tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT)]
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
            if name in _PY_MARKS or (name in _PY_CALLS and (is_call or is_decorator)):
                marker_tokens = tokens[i - int(is_decorator):end + int(is_call)]
                marker_rows = {token_rows[row - 1] for t in marker_tokens
                               for row in range(t.start[0], t.end[0] + 1)
                               if row <= len(token_rows)}
                if changed := marker_rows & added_rows:
                    return lines[min(changed) - 1].strip()
    elif language in ("js", "ts"):
        code = _js_code(source)
        tokens = iter(_JS_TOKEN.finditer(code))
        current, previous = next(tokens, None), ""
        for match in _JS_SKIP.finditer(code):
            while current is not None and current.start() < match.start():
                previous, current = current.group(), next(tokens, None)
            if previous in {".", "?."}:
                continue
            # Comments/whitespace may split an existing marker across lines;
            # only newly added executable marker tokens establish a new skip.
            marker_rows = {code.count("\n", 0, token.start()) + 1
                           for token in _JS_TOKEN.finditer(code, match.start(), match.end())}
            if changed := marker_rows & added_rows:
                return lines[min(changed) - 1].strip()
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
            candidate = f.candidate_lines if f.candidate_lines is not None else f.added
            positions = f.added_line_numbers if f.candidate_lines is not None else None
            if f.added and (evidence := _skip_evidence(candidate, language, positions)):
                out.append(Finding("added-skip", Severity.WARN,
                                   "recognized framework skip/focus syntax added; review intended collection",
                                   file=f.path, evidence=evidence))
        except (UnicodeError, LookupError, SyntaxError) as exc:
            out.append(Finding("integrity-analysis-incomplete", Severity.WARN,
                               f"cannot decode patch fragment for integrity analysis: {exc}; "
                               "complete source/encoding context is required", file=f.path))
    return out
