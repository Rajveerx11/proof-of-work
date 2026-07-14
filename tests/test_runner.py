"""Self-checks for the execution layer: real re-run + coverage-delta findings."""
from __future__ import annotations

import textwrap

from proofofwork.core.detector.coverage_delta import coverage_findings
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


def test_missing_baseline_is_info():
    tests = TestResult(ran=True, passed=True, coverage=80.0)
    findings = coverage_findings(tests, baseline=None)

    assert len(findings) == 1
    assert findings[0].rule == "coverage-baseline-missing"
    assert findings[0].severity == Severity.INFO
