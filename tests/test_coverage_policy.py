"""Required coverage contracts; fixtures never trust a candidate-proposed baseline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from proofofwork.core import runner
from proofofwork.core.detector.coverage_delta import (
    METRIC,
    coverage_findings,
    coverage_identity,
    read_baseline,
    read_baseline_metadata,
    write_baseline,
)
from proofofwork.core.sandbox import RunOutput
from proofofwork.core.sandbox.local import LocalSandbox
from proofofwork.types import Severity, TestResult

PY_REPORT = {"meta": {"branch_coverage": False}, "files": {"app.py": {}},
             "totals": {"num_statements": 10, "covered_lines": 10, "percent_covered": 100}}
JS_REPORT = {"total": {"lines": {"total": 10, "covered": 10, "pct": 100}}}
JS_WITNESS = {"numPassedTests": 1, "numFailedTests": 0, "numFailedTestSuites": 0, "success": True}


@pytest.mark.parametrize("framework", ["pytest", "jest", "vitest", "pytest+vitest"])
def test_required_missing_coverage_blocks_without_changing_legacy_default(framework):
    result = TestResult(ran=True, passed=True, framework=framework)
    assert coverage_findings(result, 90)[0].severity == Severity.INFO
    assert coverage_findings(result, 90, require_coverage=True)[0].severity == Severity.BLOCK


def test_required_mixed_missing_and_dropped_measurements_block():
    result = TestResult(ran=True, passed=True, coverage=100, js_coverage=None,
                        framework="pytest+vitest")
    assert coverage_findings(result, 90, require_coverage=True)[0].rule == "coverage-unavailable"
    result.js_coverage = 70
    assert coverage_findings(result, 90, require_coverage=True)[0].rule == "coverage-drop"
    result.js_coverage = 90
    assert coverage_findings(result, 90, require_coverage=True) == []


@pytest.mark.parametrize("baseline,framework,rule", [
    (None, "pytest", "coverage-baseline-missing"),
    (float("nan"), "pytest", "coverage-baseline-invalid"),
    (90, "configured", "coverage-incomparable"),
])
def test_required_missing_invalid_or_unknown_identity_blocks(baseline, framework, rule):
    result = TestResult(ran=True, passed=True, coverage=100, framework=framework)
    assert coverage_findings(result, baseline, require_coverage=True)[0].rule == rule


@pytest.mark.parametrize("reader,report,key", [
    (runner._read_coverage_json, PY_REPORT, "percent_covered"),
    (runner._read_js_summary, JS_REPORT, "pct"),
])
@pytest.mark.parametrize("value", [None, "100", True, -1, 101, float("nan"), float("inf"), 80])
def test_report_percentage_must_be_finite_bounded_and_consistent(tmp_path, reader, report, key, value):
    data = json.loads(json.dumps(report))
    summary = data["totals"] if key == "percent_covered" else data["total"]["lines"]
    summary[key] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data))
    assert reader(str(path)) is None


@pytest.mark.parametrize("content", ["bad", "[]", "{}", '{"total":null}'])
def test_missing_malformed_and_empty_reports_rejected(tmp_path, content):
    path = tmp_path / "report.json"
    for reader in (runner._read_coverage_json, runner._read_js_summary):
        assert reader(str(path)) is None
        path.write_text(content)
        assert reader(str(path)) is None
        path.unlink()


@pytest.mark.parametrize("report_code,write_report,timed_out", [(0, False, False), (1, True, False), (0, True, True)])
def test_python_report_failure_cannot_reuse_stale_data(tmp_path, report_code, write_report, timed_out):
    (tmp_path / ".coverage").write_text("stale data")
    (tmp_path / "coverage.json").write_text(json.dumps(PY_REPORT))
    destinations = []

    class Model:
        def run(self, cmd, **kwargs):
            if "-c" in cmd:
                return RunOutput(code=0, stdout="", stderr="")
            env = kwargs["env"]
            assert env["COVERAGE_PROCESS_START"] == env["COVERAGE_PROCESS_CONFIG"] == ""
            assert Path(env["COVERAGE_FILE"]).parent != tmp_path
            assert not Path(env["COVERAGE_FILE"]).exists()
            if "-q" in cmd:
                Path(env["PROOFOFWORK_PYTEST_WITNESS"]).write_text('{"executed":1}')
            if "json" in cmd:
                destinations.append(cmd[-1])
                assert not Path(cmd[-1]).exists()
                if write_report:
                    Path(cmd[-1]).write_text(json.dumps(PY_REPORT))
                return RunOutput(code=report_code, stdout="", stderr="report error", timed_out=timed_out)
            return RunOutput(code=0, stdout="", stderr="")
    result = runner._run_python(Model(), str(tmp_path))
    assert result.passed and result.coverage is None
    assert coverage_findings(result, 90, require_coverage=True)[0].severity == Severity.BLOCK
    assert (tmp_path / ".coverage").read_text() == "stale data"
    assert destinations and not Path(destinations[0]).exists()


@pytest.mark.parametrize("collect_coverage", [True, False])
def test_missing_python_coverage_tool_and_explicit_test_only_are_not_verified(tmp_path, collect_coverage):
    commands = []

    class Model:
        def run(self, cmd, **kwargs):
            commands.append(cmd)
            if "-q" in cmd:
                Path(kwargs["env"]["PROOFOFWORK_PYTEST_WITNESS"]).write_text('{"executed":1}')
            return RunOutput(code=1 if "import coverage" in cmd else 0, stdout="", stderr="")
    result = runner._run_python(Model(), str(tmp_path), collect_coverage=collect_coverage)
    assert result.passed and result.coverage is None and "NOT verified" in result.raw
    if not collect_coverage:
        assert not any("import coverage" in cmd for cmd in commands)


@pytest.mark.parametrize("framework", ["jest", "vitest"])
@pytest.mark.parametrize("mode", ["stale", "failed-report", "failed-test-zero-exit", "test-only", "pass"])
def test_modelled_js_fresh_reports_status_and_explicit_opt_out(tmp_path, framework, mode):
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {framework: "1"}}))
    old = tmp_path / "coverage/coverage-summary.json"
    old.parent.mkdir()
    old.write_text(json.dumps(JS_REPORT))
    commands = []
    outputs = []

    class Model:
        def run(self, cmd, **kwargs):
            commands.append(cmd)
            assert cmd[:2] == ["npx", "--no-install"]
            output = next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("--outputFile="))
            outputs.append(output)
            assert not Path(output).exists()
            witness = {**JS_WITNESS}
            if mode == "failed-test-zero-exit":
                witness.update(numFailedTests=1, numFailedTestSuites=1, success=False)
            Path(output).write_text(json.dumps(witness))
            if "--coverage" in cmd:
                directory = next(arg.split("=", 1)[1] for arg in cmd if
                                 arg.startswith(("--coverageDirectory=", "--coverage.reportsDirectory=")))
                assert Path(directory).is_dir() and Path(directory) != old.parent
                if mode != "stale":
                    Path(directory, "coverage-summary.json").write_text(json.dumps(JS_REPORT))
            return RunOutput(code=1 if mode == "failed-report" else 0, stdout="", stderr="")
    result = runner._run_js(Model(), str(tmp_path), collect_coverage=mode != "test-only")
    assert result.passed is (mode not in {"failed-report", "failed-test-zero-exit"})
    if mode == "stale":
        assert result.coverage is None
        assert coverage_findings(result, 90, require_coverage=True)[0].rule == "coverage-unavailable"
    if mode == "test-only":
        disabled = "--coverage.enabled=false" if framework == "vitest" else "--coverage=false"
        assert disabled in commands[0] and "--coverage" not in commands[0]
        assert result.coverage is None and "NOT verified" in result.raw
    assert old.exists() and outputs and not Path(outputs[0]).exists()


@pytest.mark.parametrize("updates", [
    {"numPassedTests": 0, "numPendingTests": 10},
    {"numFailedTestSuites": 1},
    {"success": False},
    {"numPassedTests": True},
    {"numFailedTests": -1},
    {"numFailedTestSuites": "0"},
])
def test_js_report_cannot_infer_success_from_zero_exit_or_skipped_totals(tmp_path, updates):
    report = tmp_path / "tests.json"
    report.write_text(json.dumps({**JS_WITNESS, **updates}))
    assert runner._executed_js(str(report))[1] is False


@pytest.mark.parametrize("framework,threshold", [("pytest", float("nan")), ("jest", -1)])
def test_invalid_required_policy_threshold_blocks(framework, threshold):
    result = TestResult(ran=True, passed=True, coverage=100, framework=framework)
    assert coverage_findings(result, 90, require_coverage=True, threshold=threshold)[0].rule == "coverage-policy-invalid"


def test_baseline_identity_is_opt_in_and_legacy_read_write_compatible(tmp_path):
    legacy = tmp_path / "legacy"
    strict = tmp_path / "strict"
    write_baseline(str(legacy), 90)
    assert read_baseline_metadata(str(legacy)) == {"coverage": 90}
    write_baseline(str(strict), 90, identity=["js", "python"])
    assert read_baseline(str(strict)) == 90
    assert read_baseline_metadata(str(strict)) == {
        "coverage": 90, "metric": METRIC, "languages": ["js", "python"]}
    assert coverage_identity(TestResult(framework="pytest+jest")) == ["js", "python"]
    with pytest.raises(FileExistsError):
        write_baseline(str(strict), 0, identity=["python"])


def test_actual_baseline_comparison_positive_and_negative(tmp_path):
    """Reviewed fixture floor is fixed at 100, never taken from candidate baseline bytes."""
    (tmp_path / "app.py").write_text("def value():\n    return 1\n")
    (tmp_path / "test_app.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value() == 1\n")
    # Candidate config cannot switch metrics or omit uncovered app lines.
    (tmp_path / ".coveragerc").write_text("[run]\nbranch = True\nomit = app.py\n")
    positive = runner.run_tests(LocalSandbox(), str(tmp_path), {"python"})
    assert positive.ran and positive.passed and positive.coverage == 100, positive.raw
    assert coverage_findings(positive, 100, require_coverage=True) == []
    (tmp_path / "app.py").write_text("def value():\n    return 1\n\ndef uncovered():\n" +
                                   "".join(f"    v{i} = {i}\n" for i in range(40)))
    negative = runner.run_tests(LocalSandbox(), str(tmp_path), {"python"})
    assert negative.ran and negative.passed, negative.raw
    assert coverage_findings(negative, 100, require_coverage=True)[0].rule == "coverage-drop"


@pytest.mark.parametrize("collect_coverage", [True, False])
def test_actual_python_collection_only_cannot_claim_execution(tmp_path, collect_coverage):
    (tmp_path / "test_app.py").write_text("def test_value():\n    assert 1 == 1\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    result = runner._run_python(LocalSandbox(), str(tmp_path), collect_coverage=collect_coverage)
    assert not result.ran and result.passed is False, result.raw
