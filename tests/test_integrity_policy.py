"""Policy and real Git fixtures: collection heuristics are not accusations of cheating."""
from __future__ import annotations

import fnmatch
import ntpath
import os
import posixpath
import shutil
import subprocess
import sys

import pytest

from proofofwork.core import gitdiff
from proofofwork.core.detector import asserts, tests_integrity
from proofofwork.core.detector.integrity_policy import (
    HIGH_RISK_INTEGRITY_RULES,
    apply_integrity_policy,
)
from proofofwork.types import Finding, Severity


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git required for live fixtures")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_original.py").write_bytes(
        b"def test_original():\n    value = 42\n    assert value == 42\n"
        + b"# retained context\n" * 16)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "fixture base")
    return tmp_path


def _policy_for(root, *, staged=False, strict=True):
    diff = gitdiff.collect_diff(str(root), staged=staged)
    return diff, apply_integrity_policy(tests_integrity.check(diff, str(root)), strict=strict)


@pytest.mark.parametrize("rule", sorted(HIGH_RISK_INTEGRITY_RULES))
def test_strict_default_promotes_high_risk_warn_without_mutating(rule):
    original = Finding(rule, Severity.WARN, "review", file="test_x.py", line=2, evidence="source")
    promoted = apply_integrity_policy([original])[0]
    assert original.severity == Severity.WARN
    assert promoted is not original
    assert promoted.as_dict() == {**original.as_dict(), "severity": "block"}
    assert apply_integrity_policy([original], strict=False)[0].as_dict() == original.as_dict()


def test_policy_preserves_unrelated_info_warn_and_existing_blocks():
    findings = [Finding("renamed-test", Severity.WARN, "preserved move"),
                Finding("other", Severity.INFO, "context"),
                Finding("deleted-test", Severity.BLOCK, "deleted")]
    assert apply_integrity_policy(iter(findings)) == findings
    assert apply_integrity_policy(findings, strict=False) == findings
    assert apply_integrity_policy([]) == []
    with pytest.raises(TypeError):
        apply_integrity_policy(findings, overrides={"deleted-test": "approved"})


@pytest.mark.parametrize("strict", ["false", "true", None, 0, 1])
def test_policy_rejects_ambiguous_mode_values(strict):
    with pytest.raises(TypeError, match="explicit bool"):
        apply_integrity_policy([], strict=strict)


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("destination", [
    "helper.py", "helper.txt", "tests/helper.py", "tests/test_original.js",
    ".hidden/test_original.py", "node_modules/test_original.py",
])
def test_unchanged_rename_escape_is_blocked_but_advisory_warns(repo, staged, destination):
    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", "tests/test_original.py", destination)
    diff, findings = _policy_for(repo, staged=staged)
    moved = diff.files[0]
    assert moved.status == "R" and moved.old_path == "tests/test_original.py"
    assert moved.is_test and moved.language == ("js" if destination.endswith(".js") else "python")
    assert moved.added == moved.removed == []  # R100: no source hunks
    assert ("test-path-removed", Severity.BLOCK) in {(f.rule, f.severity) for f in findings}
    assert all(f.severity == Severity.WARN for f in
               apply_integrity_policy(tests_integrity.check(diff, str(repo)), strict=False))


@pytest.mark.parametrize("destination", [
    "tests/test_moved.py", "tests/nested/test_original.py", "renamed_test.py",
    "distribution/test_original.py", "pkg.egg-info/test_original.py",
])
def test_legitimate_unchanged_move_preserves_collection(repo, destination):
    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", "tests/test_original.py", destination)
    diff, findings = _policy_for(repo, staged=True)
    assert diff.files[0].added == diff.files[0].removed == []
    assert [(f.rule, f.severity) for f in findings] == [("renamed-test", Severity.WARN)]
    collection = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                                cwd=repo, capture_output=True, text=True, check=False)
    assert collection.returncode == 0 and "1 test collected" in collection.stdout


@pytest.mark.parametrize("modified", [False, True])
def test_copy_preserves_source_and_checks_entire_destination(repo, modified):
    source = (repo / "tests/test_original.py").read_bytes()
    candidate = source.replace(b"assert value == 42", b"pass") if modified else source
    (repo / "helper.txt").write_bytes(candidate)
    _git(repo, "add", "helper.txt")
    diff, findings = _policy_for(repo, staged=True)
    copied = diff.files[0]
    assert copied.status == "C" and copied.old_path == "tests/test_original.py"
    assert copied.is_test and copied.language == "python"
    assert copied.added == candidate.decode().splitlines() and not copied.removed
    assert not {"removed-test-fn", "test-path-removed", "deleted-test"} & {f.rule for f in findings}
    assert (repo / "tests/test_original.py").read_bytes() == source


def test_copy_to_test_path_classifies_destination(repo):
    _git(repo, "mv", "tests/test_original.py", "helper.py")
    _git(repo, "commit", "-qm", "fixture source helper")
    (repo / "tests/test_copy.py").write_bytes((repo / "helper.py").read_bytes())
    _git(repo, "add", "tests/test_copy.py")
    copied = gitdiff.collect_diff(str(repo), staged=True).files[0]
    assert copied.status == "C" and copied.is_test and copied.old_path == "helper.py"


def test_modified_move_uses_source_language_for_removed_function(repo):
    _git(repo, "mv", "tests/test_original.py", "helper.js")
    target = repo / "helper.js"
    target.write_bytes(target.read_bytes().replace(
        b"def test_original():\n    value = 42\n    assert value == 42\n", b"// removed\n"))
    _git(repo, "add", "helper.js")
    diff, findings = _policy_for(repo, staged=True)
    assert diff.files[0].status == "R" and diff.files[0].language == "js"
    assert {"removed-test-fn", "test-path-removed"} <= {f.rule for f in findings}


@pytest.mark.parametrize("filename", [
    'test_quote".py', "test_tab\t.py", "test_line\n.py", "test_cr\r.py",
    "test space café.py", r"test_\literal.py", "test_nonutf8_\udcff.py",
])
def test_unusual_git_paths_roundtrip_assertions(repo, filename):
    if os.name == "nt" and (any(c in filename for c in '\t\n\r"\\') or
                            any(0xD800 <= ord(c) <= 0xDFFF for c in filename)):
        pytest.skip("Native Windows cannot create this filename")
    path = repo / "tests" / filename
    path.write_bytes(b"def test_x():\n    assert value == 42\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fixture unusual path")
    path.write_bytes(b"def test_x():\n    pass\n")
    changed = gitdiff.collect_diff(str(repo)).files[0]
    assert changed.path == "tests/" + filename
    assert changed.removed == ["    assert value == 42"]
    assert changed.added == ["    pass"] and changed.is_test


@pytest.mark.parametrize("staged", [False, True])
def test_staged_copy_marker_ignores_dirty_worktree(repo, staged):
    source = (repo / "tests/test_original.py").read_bytes()
    path = repo / "tests/test_copy.py"
    path.write_bytes(source + b"import pytest\npytestmark = pytest.mark.skip(reason='later')\n")
    _git(repo, "add", "tests/test_copy.py")
    path.write_bytes(source)
    _, findings = _policy_for(repo, staged=staged)
    assert any(f.rule == "added-skip" and f.severity == Severity.BLOCK for f in findings) is staged


@pytest.mark.parametrize("platform", ["posix", "windows"])
def test_python_discovery_is_platform_aware(monkeypatch, platform):
    normcase = ntpath.normcase if platform == "windows" else posixpath.normcase
    monkeypatch.setattr(gitdiff.fnmatch, "fnmatch", lambda name, pattern:
                        fnmatch.fnmatchcase(normcase(name), normcase(pattern)))
    assert gitdiff._test_discovery("TEST_moved.py") == ("python" if platform == "windows" else "")
    assert gitdiff._test_discovery("test_moved.PY") == ""  # pytest literal suffix guard


@pytest.mark.parametrize("path,expected", [(r"test_\x.py", "python"), (r"odd\test_x.py", "")])
def test_git_backslashes_are_literal_not_directory_separators(path, expected):
    assert gitdiff._test_discovery(path) == expected


def test_hunk_source_prefixes_are_not_file_headers():
    patch = ('diff --git a/test_x.py b/test_x.py\n--- a/test_x.py\n+++ b/test_x.py\n'
             '@@ -1 +1 @@\n--- source\n+++ source\n')
    assert gitdiff._parse_unified(patch) == {"test_x.py": (["++ source"], ["-- source"])}


@pytest.mark.parametrize("raw", ["M\0", "R100\0source.py\0", "M\0test_x.py", "U\0test_x.py\0"])
def test_incomplete_or_unmerged_name_status_fails_closed(raw):
    with pytest.raises(ValueError):
        gitdiff._parse_name_status(raw)


def test_source_related_helper_move_does_not_claim_known_discovery_loss(repo):
    _git(repo, "mv", "tests/test_original.py", "tests/helper.py")
    _git(repo, "commit", "-qm", "fixture helper baseline")
    _git(repo, "mv", "tests/helper.py", "helper.py")
    _, findings = _policy_for(repo, staged=True)
    assert [(f.rule, f.severity) for f in findings] == [("renamed-test", Severity.WARN)]


def test_rename_detection_is_explicit_despite_git_configuration(repo):
    _git(repo, "config", "diff.renames", "false")
    _git(repo, "mv", "tests/test_original.py", "helper.py")
    diff, findings = _policy_for(repo, staged=True)
    assert diff.files[0].status == "R" and not diff.files[0].added
    assert ("test-path-removed", Severity.BLOCK) in {(f.rule, f.severity) for f in findings}


def test_existing_weakened_assert_warning_is_promoted_on_real_git_diff(repo):
    path = repo / "tests/test_original.py"
    path.write_bytes(path.read_bytes().replace(b"assert value == 42", b"assert True"))
    diff = gitdiff.collect_diff(str(repo))
    warnings = asserts.check(diff, str(repo))
    assert ("weak-assert", Severity.WARN) in {(f.rule, f.severity) for f in warnings}
    assert ("weak-assert", Severity.BLOCK) in {
        (f.rule, f.severity) for f in apply_integrity_policy(warnings)}


def test_option_looking_base_ref_fails_without_changing_git_behavior(repo):
    with pytest.raises(RuntimeError, match="git rev-parse"):
        gitdiff.collect_diff(str(repo), "--quiet")


@pytest.mark.parametrize("destination", ["helper.js", "nested/x.spec.mjs"])
def test_js_unchanged_moves_distinguish_escape_from_preserved_family(repo, destination):
    path = repo / "x.test.js"
    path.write_bytes(b"test('real', () => { expect(42).toBe(42); });\n")
    _git(repo, "add", "x.test.js")
    _git(repo, "commit", "-qm", "fixture JS base")
    (repo / destination).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", "x.test.js", destination)
    diff, findings = _policy_for(repo, staged=True)
    assert diff.files[0].status == "R" and diff.files[0].is_test
    assert diff.files[0].added == diff.files[0].removed == []
    assert any(f.severity == Severity.BLOCK for f in findings) is (destination == "helper.js")
