"""Integrity policy and real Git move/copy fixtures; heuristics are not cheating proofs."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess

import pytest

from proofofwork import engine
from proofofwork.core import gitdiff, runner
from proofofwork.core.detector import asserts, tests_integrity
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
                                         "test_renamed.py"])
def test_preserved_move_between_test_paths_is_not_strict_block(repo, destination):
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
