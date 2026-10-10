"""Self-checks for the execution layer: real re-run + coverage-delta findings."""
from __future__ import annotations

import textwrap

import pytest

from proofofwork.core import runner
from proofofwork.core.detector.coverage_delta import (
    coverage_findings,
    read_baseline,
    write_baseline,
)
from proofofwork.core.runner import run_tests
from proofofwork.core.sandbox.local import LocalSandbox
from proofofwork.types import Severity, TestResult


def test_run_tests_executes_real_passing_suite(tmp_path):
    (tmp_path / "test_ok.py").write_text(textwrap.dedent("""
        def test_math():
            assert 1 + 1 == 2
    """))

    result = run_tests(LocalSandbox(), str(tmp_path), {"python"})

    assert result.ran
    assert result.passed
    assert result.framework == "pytest"
    # coverage may be None if coverage.py isn't installed in this env — don't require it


def test_run_tests_reads_real_failure(tmp_path):
    (tmp_path / "test_bad.py").write_text(textwrap.dedent("""
        def test_lie():
            assert False, "the agent claimed this passed"
    """))

    result = run_tests(LocalSandbox(), str(tmp_path), {"python"})

    assert result.ran
    assert result.passed is False  # truth read from exit code, not the agent's word


def test_no_test_setup_returns_not_ran(tmp_path):
    assert run_tests(LocalSandbox(), str(tmp_path), set()).ran is False


def test_coverage_drop_blocks():
    tests = TestResult(ran=True, passed=True, coverage=80.0, framework="pytest")
    findings = coverage_findings(tests, baseline=90.0, threshold=2.0)

    assert len(findings) == 1
    assert findings[0].rule == "coverage-drop"
    assert findings[0].severity == Severity.BLOCK


def test_coverage_within_threshold_no_block():
    tests = TestResult(ran=True, passed=True, coverage=89.5, framework="pytest")
    assert coverage_findings(tests, baseline=90.0, threshold=2.0) == []


def test_missing_baseline_blocks_measured_pass():
    tests = TestResult(ran=True, passed=True, coverage=80.0)
    findings = coverage_findings(tests, baseline=None)

    assert len(findings) == 1
    assert findings[0].rule == "coverage-baseline-missing"
    assert findings[0].severity == Severity.BLOCK


def test_missing_baseline_without_coverage_is_transparent():
    findings = coverage_findings(TestResult(ran=True, passed=True), baseline=None)
    assert findings[0].severity == Severity.INFO
    assert "coverage unavailable" in findings[0].message


def test_mixed_suites_both_run_without_combining_incomparable_coverage(monkeypatch, tmp_path):
    calls = []

    def python(*args, **kwargs):
        calls.append("python")
        return TestResult(ran=True, passed=True, coverage=90, framework="pytest")

    def javascript(*args):
        calls.append("js")
        return TestResult(ran=True, passed=False, coverage=75, framework="vitest")

    monkeypatch.setattr(runner, "_run_python", python)
    monkeypatch.setattr(runner, "_run_js", javascript)
    result = run_tests(LocalSandbox(), str(tmp_path), {"python", "js"})
    assert calls == ["python", "js"]
    assert result.ran and result.passed is False
    assert result.framework == "pytest+vitest" and result.coverage == 90
    assert result.js_coverage == 75
    monkeypatch.setattr(runner, "_run_js", lambda *args: None)
    missing = run_tests(LocalSandbox(), str(tmp_path), {"python", "ts"})
    assert not missing.ran and "JS/TS test suite unavailable" in missing.raw


def test_mixed_suites_preserve_python_coverage_for_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_run_python", lambda *a, **k: TestResult(
        ran=True, passed=True, coverage=70, framework="pytest"))
    monkeypatch.setattr(runner, "_run_js", lambda *a: TestResult(
        ran=True, passed=True, coverage=95, framework="vitest"))
    result = run_tests(LocalSandbox(), str(tmp_path), {"python", "js"})
    assert result.coverage == 70 and result.js_coverage == 95
    assert coverage_findings(result, 90)[0].rule == "coverage-drop"


def test_invalid_measured_coverage_blocks_and_cannot_be_written(tmp_path):
    for value in (float("nan"), float("inf"), -1, 101, True):
        findings = coverage_findings(TestResult(ran=True, passed=True, coverage=value), 90)
        assert findings[0].rule == "coverage-invalid"
        with pytest.raises(ValueError, match="finite percentage"):
            write_baseline(str(tmp_path), value)
    assert coverage_findings(TestResult(ran=True, passed=True), 90)[0].rule == "coverage-unavailable"


def test_read_baseline_ignores_malformed_file(tmp_path):
    path = tmp_path / ".proofofwork" / "baseline.json"
    path.parent.mkdir()
    path.write_text("not json")
    assert read_baseline(str(tmp_path)) is None
    path.write_text('{"coverage": NaN}')
    assert read_baseline(str(tmp_path)) is None
    path.write_text('{"coverage": 200}')
    assert read_baseline(str(tmp_path)) is None
    path.write_text('{"coverage": true}')
    assert read_baseline(str(tmp_path)) is None


def test_required_coverage_is_explicit_and_legacy_information_remains():
    result = TestResult(ran=True, passed=True, framework="pytest")
    assert coverage_findings(result, 90)[0].severity == Severity.INFO
    assert coverage_findings(result, 90, require_coverage=True)[0].severity == Severity.BLOCK


def test_mixed_test_only_routes_policy_without_collecting_coverage(monkeypatch, tmp_path):
    calls = []

    def python(*args, **kwargs):
        assert kwargs["collect_coverage"] is False
        calls.append("python")
        return TestResult(ran=True, passed=True, framework="pytest")

    def javascript(*args, **kwargs):
        assert kwargs["collect_coverage"] is False
        calls.append("js")
        return TestResult(ran=True, passed=True, framework="jest")

    monkeypatch.setattr(runner, "_run_python", python)
    monkeypatch.setattr(runner, "_run_js", javascript)
    result = run_tests(LocalSandbox(), str(tmp_path), {"python", "ts"}, collect_coverage=False)
    assert calls == ["python", "js"] and result.ran and result.passed
    assert result.coverage is None and result.js_coverage is None
