"""Full Git candidate context and JavaScript lexical regression coverage from PR #40."""
from __future__ import annotations

import subprocess

import pytest

from proofofwork import engine
from proofofwork.core.detector import tests_integrity
from proofofwork.core.gitdiff import collect_diff
from proofofwork.log import verify_chain
from proofofwork.types import Severity


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(
        "def test_x():\n    assert 1 == 1\n\n" + "# retained context\n" * 20,
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


@pytest.mark.parametrize("prefix", [
    'const re = /"/;', "const re = /'/;", 'const re = /["\\\']/;',
    'function pattern() { return /"/; }',
    'const quotient = left / right;',
    'for (const x of /"/.exec("")) {}',
    'for (const of of /"/.exec("")) {}',
    'for (of of /"/.exec("")) {}',
    'for (const [x] of /"/.exec("")) {}',
    'for (const {x} of /"/.exec("")) {}',
    'for (obj.in of /"/.exec("") || []) {}',
    'debugger\n/"/.test("");',
    'if (true) {} /"/.test("");',
    'if (true) { if (false) {} } /"/.test("");',
    'if (true) {} /* block end */ /["\\/]/.test("");',
    'if (true) /"/.test("");',
    'while (false) {} /"/.test("");',
    'for (; false;) {} /"/.test("");',
    'try {} finally {} /"/.test("");',
    'try {} catch {} /"/.test("");',
    'label: {} /"/.test("");',
    'switch (1) { case true ? 1 : 2: {} /"/.test(""); }',
    'switch (1) { default: {} /"/.test(""); }',
    'function pattern() {} /"/.test("");',
    'async function pattern() {} /"/.test("");',
    'class Pattern {} /"/.test("");',
    'const n = 1\n{} /"/.test("");',
    'const n = 1\nfunction pattern() {} /"/.test("");',
    'const o = {class: 1, function: 2}; if (true) {} /"/.test("");',
])
@pytest.mark.parametrize("separator", ["\n", " "])
def test_regex_quote_or_division_cannot_hide_added_js_skip(prefix, separator):
    source = prefix + separator + 'test.skip("x", () => {});'
    evidence = tests_integrity._skip_evidence(source.splitlines(), "js")
    assert evidence is not None
    assert "test.skip(" in evidence


@pytest.mark.parametrize("source", [
    r'const re = /test\.skip\(/;',
    r'function pattern() { return /test\.only\(/; }',
    'const example = "test.skip(\\\"x\\\", () => {})";',
    r'if (true) {} /test.skip("x", () => {})/.test("");',
    r'if (true) {} /["/]test.skip("x", () => {})/.test("");',
    'if (true) {} /* / test.skip("x", () => {}); */',
    'if (true) {} // / test.skip("x", () => {});',
    'const obj = {}; for (obj.in of /test.skip(1)/.exec("") || []) {}',
    'if (true) {} "test.skip(\\\"x\\\", () => {})";',
])
def test_regex_and_string_contents_are_not_added_js_skips(source):
    assert tests_integrity._skip_evidence([source], "js") is None


@pytest.mark.parametrize("value", [
    '{}', '{nested: {}}', '({})', '(() => {})',
    'function () {}', 'async function () {}', 'class {}', '{method() {}}',
    '{default: {}}', 'true ? {} : {}', 'object.if({})',
    'class extends (class {}) {}', 'class extends (function () {}) {}',
])
def test_object_expression_division_does_not_hide_executable_skip(value):
    source = f'const n = {value} / test.skip("x", () => {{}}) / 2;'
    assert tests_integrity._skip_evidence([source], "js") == source


@pytest.mark.parametrize("wrapper", ["(() => { BODY })();", "(function() { BODY })();"])
def test_expression_function_body_resumes_statement_context_after_semicolon(wrapper):
    executable = wrapper.replace(
        "BODY", 'const n = 1; function pattern() {} /"/.test(""); test.skip("x", () => {});')
    benign = wrapper.replace(
        "BODY", 'const n = 1; {} /test.skip("x", () => {})/.test("");')
    assert tests_integrity._skip_evidence([executable], "js") == executable
    assert tests_integrity._skip_evidence([benign], "js") is None


def test_for_await_block_regex_cannot_hide_added_skip():
    source = ('async function pattern() { for await (const x of []) {} '
              '/"/.test(""); test.skip("x", () => {}); }')
    assert tests_integrity._skip_evidence([source], "js") == source


def test_js_mask_preserves_offsets_and_added_line_provenance():
    source = ('if (true) {} /* retained comment\ncontinued */ /["/]/.test("");\n'
              'test\n.skip("x", () => {});\n')
    code = tests_integrity._js_code(source)
    assert len(code) == len(source)
    assert [i for i, c in enumerate(code) if c == "\n"] == [
        i for i, c in enumerate(source) if c == "\n"]
    assert code.index('test\n.skip(') == source.index('test\n.skip(')
    assert tests_integrity._skip_evidence(source.splitlines(), "js", [1, 2]) is None
    assert tests_integrity._skip_evidence(source.splitlines(), "js", [4]) == '.skip("x", () => {});'


@pytest.mark.parametrize("prefix", ["debugger\n", "debugger /* note */\n", "debugger;"])
def test_debugger_statement_followed_by_regex_content_is_not_a_skip(prefix):
    source = prefix + '/test.skip("x", () => {})/.test("");'
    assert tests_integrity._skip_evidence(source.splitlines(), "js") is None


@pytest.mark.parametrize("source", [
    'const ratio = object.of / test.skip("x", () => {}) / 2;',
    'const ratio = of / test.skip("x", () => {}) / 2;',
    'for (a = of / test.skip("x", () => {}) / 2;;) {}',
    'for (let x = typeof of / test.skip("x", () => {}) / 2; false;) {}',
    'for (let x = void of / test.skip("x", () => {}) / 2; false;) {}',
    'for (x in typeof of / test.skip("x", () => {}) / 2) {}',
    'const ratio = object.debugger / test.skip("x", () => {}) / 2;',
])
def test_contextual_identifiers_preserve_executable_division(source):
    assert tests_integrity._skip_evidence([source], "js") == source


def test_comment_opener_after_expression_prefix_stays_noncode():
    source = 'const n = /* / documentation\ntest.skip("x", () => {});\n*/ 1;'
    assert tests_integrity._skip_evidence(source.splitlines(), "js") is None


@pytest.mark.parametrize("addition,blocked", [
    ('const re = /"/;\ntest.skip("x", () => {});\n', True),
    ('if (true) {} /"/.test(""); test.skip("x", () => {});\n', True),
    ('if (true) {} /test.skip("x", () => {})/.test("");\n', False),
    (('(() => { const n = 1; function pattern() {} /"/.test(""); '
      'test.skip("x", () => {}); })();\n'), True),
    ('(() => { const n = 1; {} /test.skip("x", () => {})/.test(""); })();\n', False),
])
def test_real_git_regex_context_has_signed_strict_verdict(repo, addition, blocked):
    path = repo / "tests/example.test.js"
    path.write_text('test("honest", () => {});\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "JavaScript test baseline")
    path.write_text(path.read_text() + addition)
    verdict = engine.check(str(repo), run_tests=False, integrity_policy="strict")
    assert verdict.passed is not blocked
    assert verdict.integrity_policy == "strict"
    assert verdict.entry_hash
    assert verify_chain(str(repo / engine.DEFAULT_DB))
    assert (("added-skip", Severity.BLOCK) in {
        (f.rule, f.severity) for f in verdict.findings}) is blocked


def _docstring_baseline(repo, *, existing_skip=False):
    content = ('"""\n' + 'Documentation line\n' * 12 + '"""\nimport pytest\n\n'
               + '# unchanged gap\n' * 12
               + ("@pytest.mark.skip(reason='later')\n" if existing_skip else '')
               + 'def test_x():\n    assert 1 == 1\n\n'
               + 'def test_y():\n    assert 2 == 2\n# original tail\n')
    (repo / 'tests/test_x.py').write_text(content)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'docstring baseline')
    return content


@pytest.mark.parametrize('suppress_blank_context', [False, True])
def test_disjoint_docstring_opening_cannot_hide_added_skip(repo, suppress_blank_context):
    _git(repo, 'config', 'diff.suppressBlankEmpty', str(suppress_blank_context).lower())
    content = _docstring_baseline(repo)
    candidate = content.replace('"""', 'r"""', 1).replace(
        'def test_x():', "@pytest.mark.skip(reason='later')\ndef test_x():")
    (repo / 'tests/test_x.py').write_text(candidate)
    assert _git(repo, 'diff', '--unified=0').count('@@ -') == 2
    verdict = engine.check(str(repo), integrity_policy="strict", coverage_policy="test-only")
    assert verdict.tests.passed
    assert '1 skipped' in verdict.tests.raw
    assert verdict.entry_hash
    assert verdict.integrity_policy == 'strict'
    assert verify_chain(str(repo / engine.DEFAULT_DB))
    assert not verdict.passed
    assert ('added-skip', Severity.BLOCK) in {(f.rule, f.severity) for f in verdict.findings}
    changed = collect_diff(str(repo)).files[0]
    assert changed.candidate_lines == candidate.splitlines()
    assert changed.added_line_numbers == [1, candidate.splitlines().index(
        "@pytest.mark.skip(reason='later')") + 1]


def test_disjoint_added_docstring_text_is_not_executable_skip(repo):
    content = _docstring_baseline(repo)
    candidate = content.replace('Documentation line\n', "pytest.xfail('later')\n", 1).replace(
        '# original tail', '# changed tail')
    (repo / 'tests/test_x.py').write_text(candidate)
    assert _git(repo, 'diff', '--unified=0').count('@@ -') == 2
    verdict = engine.check(str(repo), integrity_policy="strict", coverage_policy="test-only")
    assert verdict.tests.passed
    assert '2 passed' in verdict.tests.raw
    assert verdict.entry_hash
    assert verdict.integrity_policy == 'strict'
    assert verify_chain(str(repo / engine.DEFAULT_DB))
    assert verdict.passed
    assert not any(f.rule == 'added-skip' for f in verdict.findings)


def test_added_docstring_duplicate_of_existing_marker_is_not_added_skip(repo):
    content = _docstring_baseline(repo, existing_skip=True)
    candidate = content.replace('Documentation line\n', "@pytest.mark.skip(reason='later')\n", 1)
    (repo / 'tests/test_x.py').write_text(candidate)
    verdict = engine.check(str(repo), run_tests=False, integrity_policy="strict")
    assert verdict.passed
    assert not verdict.findings


@pytest.mark.parametrize('staged_skip', [False, True])
def test_skip_lexical_context_uses_index_not_dirty_worktree(repo, staged_skip):
    content = _docstring_baseline(repo)
    skipped = content.replace('"""', 'r"""', 1).replace(
        'def test_x():', "@pytest.mark.skip(reason='later')\ndef test_x():")
    documented = content.replace('Documentation line\n', "pytest.xfail('later')\n", 1)
    path = repo / 'tests/test_x.py'
    path.write_text(skipped if staged_skip else documented)
    _git(repo, 'add', '-A')
    path.write_text(documented if staged_skip else skipped)
    staged_file = collect_diff(str(repo), staged=True).files[0]
    assert staged_file.candidate_lines == (skipped if staged_skip else documented).splitlines()
    staged_verdict = engine.check(str(repo), staged=True, run_tests=False, integrity_policy="strict")
    working_verdict = engine.check(str(repo), run_tests=False, integrity_policy="strict")
    assert staged_verdict.passed is not staged_skip
    assert working_verdict.passed is staged_skip
    assert any(f.rule == 'added-skip' for f in staged_verdict.findings) is staged_skip
    assert any(f.rule == 'added-skip' for f in working_verdict.findings) is not staged_skip


def test_multiline_marker_checks_added_tokens_with_unchanged_prefix(repo):
    content = _docstring_baseline(repo)
    content = content.replace('import pytest\n', "import pytest\npytestmark = (\n    pytest.mark\n"
                              "    .filterwarnings('ignore')\n)\n")
    path = repo / 'tests/test_x.py'
    path.write_text(content)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'multiline marker baseline')
    path.write_text(content.replace(".filterwarnings('ignore')", ".skip(reason='later')"))
    diff = collect_diff(str(repo))
    assert diff.files[0].added == ["    .skip(reason='later')"]
    verdict = engine.check(str(repo), run_tests=False, integrity_policy="strict")
    assert not verdict.passed
    assert [(f.rule, f.evidence) for f in verdict.findings] == [
        ('added-skip', ".skip(reason='later')")]


def test_js_added_string_duplicate_of_unchanged_skip_is_not_added_skip(repo):
    content = ('const doc = `\n' + 'Documentation line\n' * 12 + '`;\n'
               + "test.skip('later', () => {});\n")
    path = repo / 'x.test.mjs'
    path.write_text(content)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'JS string baseline')
    path.write_text(content.replace('Documentation line\n', "test.skip('later', () => {});\n", 1))
    verdict = engine.check(str(repo), run_tests=False, integrity_policy="strict")
    assert verdict.passed
    assert not verdict.findings


@pytest.mark.parametrize("newline", [b"\r", b"\r\n", b"\n"])
@pytest.mark.parametrize("staged", [False, True])
def test_python_universal_newlines_preserve_added_git_coordinates(repo, newline, staged):
    path = repo / "tests/test_x.py"
    baseline = newline.join([b"import pytest", b"def test_x():", b"    assert 1 == 1", b""])
    path.write_bytes(baseline)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "newline baseline")
    path.write_bytes(baseline.replace(b"def test_x():", b"@pytest.mark.skip(reason='later')"
                                     + newline + b"def test_x():"))
    if staged:
        _git(repo, "add", "-A")
        path.write_bytes(baseline)
    verdict = engine.check(str(repo), staged=staged, run_tests=False)
    assert not verdict.passed
    assert ("added-skip", Severity.BLOCK) in {(f.rule, f.severity) for f in verdict.findings}


@pytest.mark.parametrize("separator", [" ", " /* comment */ ", "\n", "\r", "\u2028"])
@pytest.mark.parametrize("receiver", ["obj.", "obj?."])
def test_full_context_retains_unrelated_js_receiver_guard(receiver, separator):
    source = receiver + separator + "test.skip('ordinary method');"
    assert tests_integrity._skip_evidence(source.split("\n"), "js", [1, 2]) is None


@pytest.mark.parametrize("argument", ['""', "''"])
@pytest.mark.parametrize("jump", ["break", "continue", "break outer", "continue outer"])
@pytest.mark.parametrize("terminator", ["\n", "\r", "\u2028", "\u2029", " /*\n*/ ", ";"])
def test_jump_statement_regex_context_preserves_real_skip_and_ignores_regex_text(
    jump, terminator, argument,
):
    prefix = "outer: for (;;) { " + jump + terminator
    executable = prefix + '/"/.test(' + argument + '); } test.skip("x", () => {});'
    benign = prefix + '/test.skip("x", () => {})/.test(""); }'
    assert tests_integrity._skip_evidence(executable.split("\n"), "js") is not None
    assert tests_integrity._skip_evidence(benign.split("\n"), "js") is None


@pytest.mark.parametrize("skip", [True, False])
def test_unchanged_jump_regex_prefix_does_not_poison_full_candidate(repo, skip):
    path = repo / "x.test.js"
    prefix = 'outer: for (;;) { break outer\n/"/.test(""); }\n'
    path.write_text(prefix)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "jump regex baseline")
    addition = 'test.skip("x", () => {});' if skip else 'test("x", () => {});'
    path.write_text(prefix + addition + "\n")
    verdict = engine.check(str(repo), run_tests=False)
    assert verdict.passed is not skip
    assert any(f.rule == "added-skip" for f in verdict.findings) is skip


@pytest.mark.parametrize("comment", ["// documentation only", "/* documentation only */"])
@pytest.mark.parametrize("staged", [False, True])
def test_comment_added_inside_unchanged_multiline_js_skip_does_not_block(repo, comment, staged):
    path = repo / "x.test.js"
    baseline = 'test\n.skip("old skip", () => {});\n'
    path.write_text(baseline)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "existing multiline skip")
    path.write_text(baseline.replace("test\n", "test\n" + comment + "\n"))
    if staged:
        _git(repo, "add", "-A")
        path.write_text(baseline)
    changed = collect_diff(str(repo), staged=staged).files[0]
    assert changed.added_line_numbers == [2]
    verdict = engine.check(str(repo), staged=staged, run_tests=False)
    assert verdict.passed
    assert not any(f.rule == "added-skip" for f in verdict.findings)


@pytest.mark.parametrize("declaration", [
    'import "foo"', 'import x from "foo"', 'import {x} from "foo"',
    'import {"x" as x} from "foo"', 'export * from "foo"', 'export {x} from "foo"',
])
def test_static_module_declaration_ends_before_next_regex_statement(declaration):
    executable = declaration + "\n" + '/"/.test(\'\'); test.skip("x", () => {});'
    benign = declaration + "\n" + '/test.skip("x", () => {})/.test("");'
    assert tests_integrity._skip_evidence(executable.split("\n"), "js") is not None
    assert tests_integrity._skip_evidence(benign.split("\n"), "js") is None


@pytest.mark.parametrize("value", [
    'import("foo")', '(() => { while (true) { break } })()',
])
def test_dynamic_import_and_jump_expression_preserve_following_division(value):
    source = value + '\n/ test.skip("x", () => {}) / 2;'
    assert tests_integrity._skip_evidence(source.split("\n"), "js", [2]) is not None


@pytest.mark.parametrize("jump", ["break", "continue"])
def test_jump_after_semicolonless_statement_cannot_hide_real_skip(jump):
    prefix = "for (;;) { let x = 0\n" + jump + "\n"
    executable = prefix + '/"/.test(\'\'); } test.skip("x", () => {});'
    benign = prefix + '/test.skip("x", () => {})/.test(""); }'
    assert tests_integrity._skip_evidence(executable.split("\n"), "js") is not None
    assert tests_integrity._skip_evidence(benign.split("\n"), "js") is None


@pytest.mark.parametrize("value", ['obj.break', 'obj.continue', '{break: 1}', '{continue: 1}'])
def test_jump_property_names_preserve_executable_division(value):
    source = 'const result = ' + value + '\n/ test.skip("x", () => {}) / 2;'
    assert tests_integrity._skip_evidence(source.split("\n"), "js", [2]) is not None


@pytest.mark.parametrize("statement", ['import "foo"', 'export * from "foo"', 'debugger'])
def test_statement_keyword_after_semicolonless_statement_restores_regex_context(statement):
    prefix = "const x = 1\n" + statement + "\n"
    executable = prefix + '/"/.test(\'\'); test.skip("x", () => {});'
    benign = prefix + '/test.skip("x", () => {})/.test("");'
    assert tests_integrity._skip_evidence(executable.split("\n"), "js") is not None
    assert tests_integrity._skip_evidence(benign.split("\n"), "js") is None
