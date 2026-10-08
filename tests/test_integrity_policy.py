"""Integrity policy and real Git move/copy fixtures; heuristics are not cheating proofs."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

from proofofwork import engine
from proofofwork.core import gitdiff, runner
from proofofwork.core.detector import asserts, coverage_delta, tests_integrity
from proofofwork.core.gitdiff import _parse_name_status, _parse_unified, collect_diff, parse_patch
from proofofwork.interfaces import cli
from proofofwork.log import verify_chain
from proofofwork.types import Diff, DiffFile, Finding, Severity, TestResult


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


@pytest.mark.parametrize("destination", ["helper.py", "helper.txt", "helper.js",
                                         "tests/helper.py", "tests/test_x.txt",
                                         "tests/test_x.js"])
def test_renaming_test_out_of_discovery_is_reviewable_but_strict_blocks(repo, destination):
    _git(repo, "mv", "tests/test_x.py", destination)
    diff = collect_diff(str(repo), staged=True)
    moved = diff.files[0]
    assert moved.status == "R"
    assert moved.is_test
    assert moved.old_path == "tests/test_x.py"
    assert cli.main(["check", "--root", str(repo), "--staged", "--no-tests",
                     "--strict-integrity"]) == 1
    findings = tests_integrity.check(diff, str(repo))
    assert {f.rule for f in findings} == {"renamed-test", "test-path-removed"}
    assert all(f.severity == Severity.WARN for f in findings)
    findings = tests_integrity.apply_policy(findings, strict=True)
    assert next(f for f in findings if f.rule == "test-path-removed").severity == Severity.BLOCK


@pytest.mark.parametrize("destination", ["tests/test_renamed.py", "tests/renamed_test.py",
                                         "test_renamed.py", "tests/nested/test_x.py",
                                         "distribution/test_x.py", "node_modules_extra/test_x.py",
                                         "pkg.egg-info/test_x.py"])
def test_preserved_move_between_test_paths_is_not_strict_block(repo, destination):
    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", "tests/test_x.py", destination)
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
    assert verdict.passed
    assert [(f.rule, f.severity) for f in verdict.findings] == [("renamed-test", Severity.WARN)]


def test_modified_rename_checks_removed_functions_using_source_language(repo):
    _git(repo, "mv", "tests/test_x.py", "helper.js")
    target = repo / "helper.js"
    target.write_text(target.read_text().replace("def test_x():\n    assert 1 == 1", "// removed"))
    _git(repo, "add", "-A")
    diff = collect_diff(str(repo), staged=True)
    assert diff.files[0].status == "R"
    assert diff.files[0].old_language == "python"
    rules = {f.rule for f in tests_integrity.check(diff, str(repo)) + asserts.check(diff, str(repo))}
    assert {"test-path-removed", "removed-test-fn", "removed-assert"} <= rules


def test_copies_classify_source_but_do_not_claim_source_test_removal(repo):
    target = repo / "helper.py"
    target.write_text((repo / "tests/test_x.py").read_text().replace("    assert 1 == 1", "    pass"))
    _git(repo, "add", "-A")
    diff = collect_diff(str(repo), staged=True)
    copied = diff.files[0]
    assert copied.status == "C"
    assert copied.old_path == "tests/test_x.py"
    assert copied.is_test
    assert not tests_integrity.check(diff, str(repo))
    assert not asserts.check(diff, str(repo))
    assert (repo / "tests/test_x.py").exists()


def test_copy_to_test_path_classifies_destination(repo):
    _git(repo, "mv", "tests/test_x.py", "helper.py")
    _git(repo, "commit", "-qm", "source outside test path")
    (repo / "tests/test_copy.py").write_text((repo / "helper.py").read_text())
    _git(repo, "add", "-A")
    copied = collect_diff(str(repo), staged=True).files[0]
    assert copied.status == "C"
    assert copied.old_path == "helper.py"
    assert copied.is_test


@pytest.mark.parametrize("filename", ["test space café.py", "test_tab\t.py", 'test_quote".py',
                                      "test_line\n.py", "test_cr\r.py", "test_back\\slash.py"])
def test_live_unusual_git_paths_keep_removed_assertions(repo, filename):
    if os.name == "nt" and any(c in filename for c in '\t\n\r"\\'):
        pytest.skip("filename cannot be created on Windows; parser fixture covers it")
    path = repo / "tests" / filename
    path.write_text("def test_x():\n    assert 1 == 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unusual test path")
    path.write_text("def test_x():\n    pass\n", encoding="utf-8")
    changed = collect_diff(str(repo)).files[0]
    assert changed.path == "tests/" + filename
    assert changed.removed == ["    assert 1 == 1"]
    assert "removed-assert" in {f.rule for f in asserts.check(Diff([changed]), str(repo))}


def test_git_preserves_nul_delimited_raw_paths(monkeypatch):
    raw = b"M\x00tests/test_cr\r_non_utf8_\xff.py\x00"
    monkeypatch.setattr(gitdiff.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, stdout=raw, stderr=b""))
    output = gitdiff._git(".", "diff", "--name-status", "-z")
    assert output.encode("utf-8", errors="surrogateescape") == raw
    path = _parse_name_status(output)[0][1]
    assert len(engine._changeset_sha(Diff([DiffFile(path, "M")]))) == 64


def test_quoted_patch_paths_and_header_like_source_lines():
    path = 'tests/test_tab\tline\ncr\rquote"slash\\café.py'
    quoted = r'"a/tests/test_tab\tline\ncr\rquote\"slash\\caf\303\251.py"'
    patch = (f"diff --git {quoted} {quoted.replace('a/', 'b/', 1)}\n"
             f"--- {quoted}\n+++ {quoted.replace('a/', 'b/', 1)}\n"
             "@@ -1,2 +1,2 @@\n--- old source\n-    assert answer == 42\n"
             "+++ new source\n+    pass\n")
    assert _parse_unified(patch)[path] == (["++ new source", "    pass"],
                                         ["-- old source", "    assert answer == 42"])
    assert parse_patch(patch).files[0].path == path
    statuses = _parse_name_status(f"R100\0tests/test_old.py\0{path}\0")
    assert statuses == [("R", path, "tests/test_old.py")]


@pytest.mark.parametrize("prefix", [
    'const re = /"/;', "const re = /'/;", 'const re = /["\\\']/;',
    'function pattern() { return /"/; }',
    'const quotient = left / right;',
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
])
def test_regex_and_string_contents_are_not_added_js_skips(source):
    assert tests_integrity._skip_evidence([source], "js") is None


def test_object_expression_division_does_not_hide_executable_skip():
    source = 'const n = {} / test.skip("x", () => {}) / 2;'
    assert tests_integrity._skip_evidence([source], "js") == source


def test_comment_opener_after_expression_prefix_stays_noncode():
    source = 'const n = /* / documentation\ntest.skip("x", () => {});\n*/ 1;'
    assert tests_integrity._skip_evidence(source.splitlines(), "js") is None


def test_real_git_regex_quote_does_not_allow_signed_strict_pass(repo):
    path = repo / "tests/example.test.js"
    path.write_text('test("honest", () => {});\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "JavaScript test baseline")
    path.write_text(path.read_text() + 'const re = /"/;\ntest.skip("x", () => {});\n')
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
    assert not verdict.passed
    assert verdict.integrity_policy == "strict-v1"
    assert verdict.entry_hash
    assert verify_chain(str(repo / engine.DEFAULT_DB))
    assert ("added-skip", Severity.BLOCK) in {(f.rule, f.severity) for f in verdict.findings}


def test_malformed_quoted_patch_path_rejects_trailing_escape():
    with pytest.raises(ValueError, match="unterminated escape"):
        parse_patch('--- "a/tests/test_x.py' + "\\")


@pytest.mark.parametrize("rule", ["added-skip", "removed-assert", "removed-test-fn",
                                  "test-path-removed"])
def test_strict_policy_blocks_even_with_passing_tests(monkeypatch, tmp_path, rule):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    monkeypatch.setattr(runner, "run_tests", lambda *a: TestResult(ran=True, passed=True))
    original = Finding(rule, Severity.WARN, "review")
    advisory = engine.check(str(tmp_path), extra_findings=[original])
    strict = engine.check(str(tmp_path), strict_integrity=True, extra_findings=[original])
    assert original.severity == Severity.WARN
    assert advisory.passed
    assert not strict.passed
    assert strict.entry_hash
    assert f"BLOCK {rule}: review" in strict.reasons
    assert strict.findings[0].severity == Severity.BLOCK


def test_strict_policy_preserves_unrelated_warns(monkeypatch, tmp_path):
    monkeypatch.setattr("proofofwork.core.gitdiff.collect_diff", lambda *a, **k: Diff())
    verdict = engine.check(str(tmp_path), run_tests=False, strict_integrity=True,
                           extra_findings=[Finding("mock-under-test", Severity.WARN, "review")])
    assert verdict.passed
    assert verdict.findings[0].severity == Severity.WARN


@pytest.mark.parametrize("change,rule", [
    ("@pytest.mark.skip(reason='later')\ndef test_x():\n    assert 1 == 1\n", "added-skip"),
    ("def test_x():\n    pass\n", "removed-assert"),
    ("# removed test\n", "removed-test-fn"),
])
def test_cli_strict_fixtures_fail_not_pass(repo, capsys, change, rule):
    (repo / "tests/test_x.py").write_text(change + "# retained context\n" * 20)
    argv = ["check", "--root", str(repo), "--no-tests", "--json"]
    assert cli.main(argv) == 0  # legacy advisory behavior is unchanged
    advisory = json.loads(capsys.readouterr().out)
    assert any(f["rule"] == rule and f["severity"] == "warn" for f in advisory["findings"])
    assert cli.main([*argv, "--strict-integrity"]) == 1
    strict = json.loads(capsys.readouterr().out)
    assert not strict["passed"]
    assert any(f["rule"] == rule and f["severity"] == "block" for f in strict["findings"])


def test_edited_function_and_replaced_assertion_do_not_strict_block(repo):
    (repo / "tests/test_x.py").write_text("def test_x():\n    assert 2 == 2\n")
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
    assert verdict.passed
    assert not verdict.findings


def test_copy_additions_still_check_skips():
    diff = Diff([DiffFile("helper.py", "C", old_path="tests/test_x.py", is_test=True,
                          language="python", added=["@pytest.mark.skip(reason='later')"])])
    assert [f.rule for f in tests_integrity.check(diff, ".")] == ["added-skip"]


@pytest.mark.parametrize("destination", ["helper.py", "tests/helper.txt"])
def test_test_related_helper_move_does_not_claim_discovery_loss(repo, destination):
    _git(repo, "mv", "tests/test_x.py", "tests/helper.py")
    _git(repo, "commit", "-qm", "helper baseline")
    _git(repo, "mv", "tests/helper.py", destination)
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
    assert verdict.passed
    assert [(f.rule, f.severity) for f in verdict.findings] == [("renamed-test", Severity.WARN)]


@pytest.mark.parametrize("source,destination,loses_discovery", [
    ("tests/x.test.ts", "tests/helper.ts", True),
    ("tests/x.spec.js", "tests/x.spec.txt", True),
    ("tests/x.test.ts", "tests/x.spec.tsx", False),
    ("__tests__/x.js", "tests/helper.js", True),
    ("__tests__/x.js", "__tests__/renamed.js", False),
    ("tests/helper.js", "helper.js", False),
])
def test_js_default_discovery_move_semantics(repo, source, destination, loses_discovery):
    (repo / source).parent.mkdir(parents=True, exist_ok=True)
    (repo / source).write_text("test('works', () => { expect(2).toBe(2); });\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "JS baseline")
    _git(repo, "mv", source, destination)
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
    assert verdict.passed is not loses_discovery
    assert any(f.rule == "test-path-removed" for f in verdict.findings) is loses_discovery


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("content,rule,strict_passes", [
    ("import pytest\n@pytest.mark.skip(reason='later')\ndef test_copy():\n    assert 2 == 2\n",
     "added-skip", False),
    ("import os\nos._exit(0)\n", "fake-pass:sys-exit", False),
    ("def test_copy():\n    assert 2 == 2\n", None, True),
])
def test_git_exact_copy_checks_complete_additions_and_cli(
    repo, capsys, staged, content, rule, strict_passes,
):
    (repo / "unused_helper.py").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unused source")
    (repo / "tests/test_copy.py").write_text(content)
    _git(repo, "add", "-A")
    assert "C100\tunused_helper.py\ttests/test_copy.py" in _git(
        repo, "diff", "--cached", "--name-status", "--find-copies-harder",
    )
    copy_patch = _git(repo, "diff", "--cached", "--find-copies-harder")
    assert "@@" not in copy_patch  # the reproduced detector bypass
    copied = collect_diff(str(repo), staged=staged).files[0]
    assert copied.status == "C"
    assert copied.added == content.splitlines()
    assert not copied.removed
    argv = ["check", "--root", str(repo), "--no-tests", "--strict-integrity", "--json"]
    if staged:
        # The destination must come from the index, not unstaged content.
        (repo / "tests/test_copy.py").write_text("# unstaged decoy\n")
        argv.append("--staged")
    assert cli.main(argv) == (0 if strict_passes else 1)
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["passed"] is strict_passes
    assert verdict["integrity_policy"] == "strict-v1"
    rules = {f["rule"] for f in verdict["findings"]}
    assert not {"removed-test-fn", "removed-assert", "test-path-removed"} & rules
    assert rules == ({rule} if rule else set())
    assert (repo / "unused_helper.py").read_text() == content


def test_modified_copy_checks_retained_source_content_as_additions(repo):
    content = ("import pytest\npytestmark = pytest.mark.skip(reason='later')\n"
               "def test_copy():\n    assert 2 == 2\n" + "# retained context\n" * 20)
    (repo / "tests/test_x.py").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "copy source")
    target = content.replace("def test_copy():\n    assert 2 == 2", "# removed in destination")
    (repo / "helper.py").write_text(target)
    _git(repo, "add", "-A")
    copied = collect_diff(str(repo), staged=True).files[0]
    assert copied.status == "C"
    assert copied.added == target.splitlines()
    assert not copied.removed
    verdict = engine.check(str(repo), staged=True, run_tests=False, strict_integrity=True)
    assert not verdict.passed
    assert {f.rule for f in verdict.findings} == {"added-skip"}


@pytest.mark.parametrize("change", [
    "import pytest\npytestmark = pytest.mark.skip(reason='later')\n",
    "import pytest\npytestmark = [pytest.mark.skipif(True, reason='later')]\n",
    "    pytest.xfail('later')\n",
    "    self.skipTest('later')\n",
])
def test_cli_common_python_skip_forms_block(repo, capsys, change):
    content = ("def test_x():\n" + change + "    assert 2 == 2\n" if change.startswith("    ")
               else change + "def test_x():\n    assert 2 == 2\n")
    (repo / "tests/test_x.py").write_text(content + "# retained context\n" * 20)
    argv = ["check", "--root", str(repo), "--no-tests", "--json"]
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["integrity_policy"] == "advisory-v1"
    assert cli.main([*argv, "--strict-integrity"]) == 1
    verdict = json.loads(capsys.readouterr().out)
    assert any(f["rule"] == "added-skip" and f["severity"] == "block"
               for f in verdict["findings"])


def test_cli_js_test_only_blocks(repo, capsys):
    path = repo / "tests/x.test.ts"
    path.write_text("test('works', () => { expect(2).toBe(2); });\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "JS baseline")
    path.write_text("test.only('works', () => { expect(2).toBe(2); });\n")
    assert cli.main(["check", "--root", str(repo), "--no-tests", "--json",
                     "--strict-integrity"]) == 1
    verdict = json.loads(capsys.readouterr().out)
    assert {f["rule"] for f in verdict["findings"]} >= {"added-skip"}


@pytest.mark.parametrize("strict,policy", [(False, "advisory-v1"), (True, "strict-v1")])
def test_passing_cli_json_and_signed_envelope_attest_enforced_policy(repo, capsys, strict, policy):
    db = repo / "policy-log.db"
    argv = ["check", "--root", str(repo), "--no-tests", "--json", "--db", str(db)]
    if strict:
        argv.append("--strict-integrity")
    assert cli.main(argv) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["passed"]
    assert verdict["integrity_policy"] == policy
    with sqlite3.connect(db) as connection:
        envelope_json, entry_hash = connection.execute(
            "SELECT envelope_json, entry_hash FROM entries",
        ).fetchone()
    assert entry_hash == verdict["entry_hash"]
    predicate = json.loads(envelope_json)["predicate"]
    assert predicate["verdict"] == "pass"
    assert predicate["integrity_policy"] == policy
    assert verify_chain(str(db))


def test_failed_diff_preserves_requested_policy(tmp_path):
    verdict = engine.check(str(tmp_path), strict_integrity=True)
    assert not verdict.passed
    assert verdict.as_dict()["integrity_policy"] == "strict-v1"


@pytest.mark.parametrize("content,expected_rule", [
    ("def helper():\n    assert True\n", "weak-assert"),
    ("x = 1  # pragma: no cover\n" * 3, "fake-pass:coverage-disabled"),
    ("def helper():\n    return 2\n", None),
])
def test_exact_copy_to_source_path_preserves_additive_signals(repo, content, expected_rule):
    (repo / "unused_helper.py").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unused source")
    (repo / "app.py").write_text(content)
    _git(repo, "add", "-A")
    copied = collect_diff(str(repo), staged=True).files[0]
    assert copied.status == "C"
    assert not copied.is_test
    assert copied.added == content.splitlines()
    verdict = engine.check(str(repo), staged=True, run_tests=False, strict_integrity=True)
    assert verdict.passed  # unrelated WARNs keep their existing advisory policy
    assert {f.rule for f in verdict.findings} == ({expected_rule} if expected_rule else set())


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
    verdict = engine.check(str(repo), strict_integrity=True)
    assert verdict.tests.passed
    assert '1 skipped' in verdict.tests.raw
    assert verdict.entry_hash
    assert verdict.integrity_policy == 'strict-v1'
    assert verify_chain(str(repo / engine.DEFAULT_DB))
    assert not verdict.passed
    assert ('added-skip', Severity.BLOCK) in {(f.rule, f.severity) for f in verdict.findings}
    changed = collect_diff(str(repo)).files[0]
    assert changed.candidate_lines == candidate.splitlines()
    assert changed.added_line_numbers == [1, candidate.splitlines().index(
        "@pytest.mark.skip(reason='later')") + 1]


def test_disjoint_added_docstring_text_is_not_executable_skip(repo):
    content = _docstring_baseline(repo)
    # Keep this integrity regression independent of optional coverage instrumentation.
    coverage_delta.write_baseline(str(repo), 0.0)
    candidate = content.replace('Documentation line\n', "pytest.xfail('later')\n", 1).replace(
        '# original tail', '# changed tail')
    (repo / 'tests/test_x.py').write_text(candidate)
    assert _git(repo, 'diff', '--unified=0').count('@@ -') == 2
    verdict = engine.check(str(repo), strict_integrity=True)
    assert verdict.tests.passed
    assert '2 passed' in verdict.tests.raw
    assert verdict.entry_hash
    assert verdict.integrity_policy == 'strict-v1'
    assert verify_chain(str(repo / engine.DEFAULT_DB))
    assert verdict.passed
    assert not any(f.rule == 'added-skip' for f in verdict.findings)


def test_added_docstring_duplicate_of_existing_marker_is_not_added_skip(repo):
    content = _docstring_baseline(repo, existing_skip=True)
    candidate = content.replace('Documentation line\n', "@pytest.mark.skip(reason='later')\n", 1)
    (repo / 'tests/test_x.py').write_text(candidate)
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
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
    staged_verdict = engine.check(str(repo), staged=True, run_tests=False, strict_integrity=True)
    working_verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
    assert staged_verdict.passed is not staged_skip
    assert working_verdict.passed is staged_skip
    assert any(f.rule == 'added-skip' for f in staged_verdict.findings) is staged_skip
    assert any(f.rule == 'added-skip' for f in working_verdict.findings) is not staged_skip


@pytest.mark.parametrize('directory', [
    '.hidden', 'tests/.hidden', 'dist', 'node_modules', 'build', 'venv',
    'pkg.egg', '_darcs', 'CVS', '{arch}',
])
def test_python_move_into_default_excluded_directory_strict_blocks(repo, directory):
    destination = f'{directory}/test_x.py'
    (repo / directory).mkdir(parents=True, exist_ok=True)
    _git(repo, 'mv', 'tests/test_x.py', destination)
    verdict = engine.check(str(repo), staged=True, run_tests=False, strict_integrity=True)
    assert not verdict.passed
    assert ('test-path-removed', Severity.BLOCK) in {(f.rule, f.severity) for f in verdict.findings}
    collected = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q'],
                               cwd=repo, capture_output=True, text=True, check=False)
    assert collected.returncode == 5, collected.stdout + collected.stderr
    assert 'no tests collected' in collected.stdout


@pytest.mark.parametrize('extension', ['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs', 'mts', 'cts',
                                       'mjsx', 'cjsx', 'mtsx', 'ctsx'])
@pytest.mark.parametrize('kind', ['test', 'spec'])
def test_js_module_test_names_are_detected_and_preserve_discovery(repo, extension, kind):
    source = f'x.{kind}.{extension}'
    destination = f'renamed.{kind}.{extension}'
    (repo / source).write_text("test('works', () => {});\n")
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'module test baseline')
    _git(repo, 'mv', source, destination)
    verdict = engine.check(str(repo), staged=True, run_tests=False, strict_integrity=True)
    assert verdict.passed
    assert [(f.rule, f.severity) for f in verdict.findings] == [('renamed-test', Severity.WARN)]
    (repo / destination).write_text("test.skip('works', () => {});\n")
    _git(repo, 'add', '-A')
    verdict = engine.check(str(repo), staged=True, run_tests=False, strict_integrity=True)
    assert not verdict.passed
    assert any(f.rule == 'added-skip' for f in verdict.findings)


@pytest.mark.parametrize('path', [
    'test.mjs', 'x.test.json', 'x.spec.css', 'x.testing.mjs', 'x.test.mjs.txt',
    'x.TEST.mjs', 'x.test.mmjs', '__tests__/x.txt',
])
def test_non_default_js_module_names_do_not_claim_discovery(path):
    assert not gitdiff._test_discovery(path)
    if path != '__tests__/x.txt' and path != 'x.TEST.mjs':
        assert not gitdiff._is_test(path)


@pytest.mark.parametrize('extension', ['mjs', 'cjs', 'mts', 'cts'])
def test_js_module_test_move_to_helper_loses_discovery(repo, extension):
    source = f'x.test.{extension}'
    destination = f'helper.{extension}'
    (repo / source).write_text("test('works', () => {});\n")
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'module test baseline')
    _git(repo, 'mv', source, destination)
    verdict = engine.check(str(repo), staged=True, run_tests=False, strict_integrity=True)
    assert not verdict.passed
    assert ('test-path-removed', Severity.BLOCK) in {(f.rule, f.severity) for f in verdict.findings}


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
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
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
    verdict = engine.check(str(repo), run_tests=False, strict_integrity=True)
    assert verdict.passed
    assert not verdict.findings
