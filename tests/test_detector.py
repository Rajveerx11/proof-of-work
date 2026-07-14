"""Exercises each detector check against hand-built Diff objects, plus a live git roundtrip."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from proofofwork.core.detector import asserts, fake_pass, mocks, tests_integrity
from proofofwork.core.detector import ALL_CHECKS
from proofofwork.core.gitdiff import collect_diff
from proofofwork.types import Diff, DiffFile, Severity


def _f(**kw) -> DiffFile:
    kw.setdefault("status", "M")
    return DiffFile(**kw)


def _rules(findings):
    return {(x.rule, x.severity) for x in findings}


def test_all_checks_are_exactly_four():
    assert ALL_CHECKS == [tests_integrity.check, fake_pass.check, asserts.check, mocks.check]
    # coverage_delta must never be wired in here
    names = {c.__module__ for c in ALL_CHECKS}
    assert not any("coverage" in n for n in names)


# --- tests_integrity ---

def test_deleted_test_blocks():
    d = Diff(files=[_f(path="tests/test_pay.py", status="D", is_test=True, language="python")])
    assert ("deleted-test", Severity.BLOCK) in _rules(tests_integrity.check(d, "."))


def test_renamed_test_warns():
    d = Diff(files=[_f(path="tests/test_new.py", status="R", old_path="tests/test_old.py",
                       is_test=True, language="python")])
    assert ("renamed-test", Severity.WARN) in _rules(tests_integrity.check(d, "."))


def test_removed_test_fn_py_and_js():
    py = Diff(files=[_f(path="tests/test_a.py", is_test=True, language="python",
                        removed=["def test_charge():"])])
    js = Diff(files=[_f(path="a.test.js", is_test=True, language="js",
                        removed=["  it('charges', () => {"])])
    assert ("removed-test-fn", Severity.WARN) in _rules(tests_integrity.check(py, "."))
    assert ("removed-test-fn", Severity.WARN) in _rules(tests_integrity.check(js, "."))


def test_added_skip_warns():
    d = Diff(files=[_f(path="tests/test_a.py", is_test=True, language="python",
                       added=["    @pytest.mark.skip(reason='later')"])])
    assert ("added-skip", Severity.WARN) in _rules(tests_integrity.check(d, "."))
    js = Diff(files=[_f(path="a.spec.ts", is_test=True, language="ts",
                        added=["it.only('one', () => {})"])])
    assert ("added-skip", Severity.WARN) in _rules(tests_integrity.check(js, "."))


# --- fake_pass ---

def test_sys_exit_blocks_only_bare_or_zero():
    good = Diff(files=[_f(path="tests/test_a.py", is_test=True, language="python",
                          added=["    sys.exit(0)"])])
    assert ("fake-pass:sys-exit", Severity.BLOCK) in _rules(fake_pass.check(good, "."))
    # sys.exit(1) is a legit failure signal, not a cheat
    legit = Diff(files=[_f(path="tests/test_a.py", is_test=True, language="python",
                           added=["    sys.exit(1)"])])
    assert ("fake-pass:sys-exit", Severity.BLOCK) not in _rules(fake_pass.check(legit, "."))


def test_process_exit_blocks_only_bare_or_zero():
    good = Diff(files=[_f(path="a.test.js", is_test=True, language="js",
                          added=["  process.exit(0);"])])
    assert ("fake-pass:process-exit", Severity.BLOCK) in _rules(fake_pass.check(good, "."))
    # process.exit(1) is a real failure signal, not a cheat
    legit = Diff(files=[_f(path="a.test.js", is_test=True, language="js",
                           added=["  process.exit(1);"])])
    assert ("fake-pass:process-exit", Severity.BLOCK) not in _rules(fake_pass.check(legit, "."))


def test_coverage_disabled_warns():
    d = Diff(files=[_f(path="pytest.ini", added=["addopts = --no-cov"])])
    assert ("fake-pass:coverage-disabled", Severity.WARN) in _rules(fake_pass.check(d, "."))
    spree = Diff(files=[_f(path="app.py", added=["x  # pragma: no cover"] * 3)])
    assert ("fake-pass:coverage-disabled", Severity.WARN) in _rules(fake_pass.check(spree, "."))


# --- asserts ---

def test_weak_assert_warns():
    for lang, line in [("python", "    assert True"),
                       ("python", "    self.assertTrue(True)"),
                       ("js", "  expect(true).toBe(true)")]:
        d = Diff(files=[_f(path="tests/test_a.py", is_test=True, language=lang, added=[line])])
        assert ("weak-assert", Severity.WARN) in _rules(asserts.check(d, ".")), line


def test_removed_assert_net_warns():
    d = Diff(files=[_f(path="tests/test_a.py", is_test=True, language="python",
                       removed=["    assert x == 1", "    assert y == 2"],
                       added=["    pass"])])
    assert ("removed-assert", Severity.WARN) in _rules(asserts.check(d, "."))
    # replacing an assert 1:1 is not a net removal
    even = Diff(files=[_f(path="tests/test_a.py", is_test=True, language="python",
                          removed=["    assert x == 1"], added=["    assert x == 2"])])
    assert ("removed-assert", Severity.WARN) not in _rules(asserts.check(even, "."))


# --- mocks ---

def test_mock_under_test_warns():
    d = Diff(files=[
        _f(path="app/payments.py", status="M", language="python", added=["def charge(): ..."]),
        _f(path="tests/test_payments.py", is_test=True, language="python",
           added=["@patch('app.payments.charge')"]),
    ])
    assert ("mock-under-test", Severity.WARN) in _rules(mocks.check(d, "."))


def test_mock_of_untouched_module_is_quiet():
    d = Diff(files=[
        _f(path="app/payments.py", status="M", language="python", added=["def charge(): ..."]),
        _f(path="tests/test_payments.py", is_test=True, language="python",
           added=["@patch('app.email.send')"]),
    ])
    assert ("mock-under-test", Severity.WARN) not in _rules(mocks.check(d, "."))


# --- gitdiff live roundtrip ---

@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_collect_diff_roundtrip(tmp_path):
    root = str(tmp_path)
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                    capture_output=True, text=True)
    run("init", "-q")
    run("config", "user.email", "t@t.t")
    run("config", "user.name", "t")
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    (tmp_path / "test_app.py").write_text("def test_f():\n    assert f() == 1\n")
    run("add", "-A")
    run("commit", "-qm", "init")

    # modify source, delete the test file
    (tmp_path / "app.py").write_text("def f():\n    return 2\n")
    (tmp_path / "test_app.py").unlink()

    diff = collect_diff(root, "HEAD")
    by_path = {f.path: f for f in diff.files}
    assert set(by_path) == {"app.py", "test_app.py"}
    assert by_path["test_app.py"].status == "D"
    assert by_path["test_app.py"].is_test is True
    assert by_path["app.py"].status == "M"
    assert by_path["app.py"].language == "python"
    assert "    return 2" in by_path["app.py"].added
    assert "    return 1" in by_path["app.py"].removed


def test_collect_diff_empty_on_non_repo(tmp_path):
    # graceful degradation: not a git repo -> empty diff, no crash
    assert collect_diff(str(tmp_path), "HEAD").files == []
