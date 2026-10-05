"""Coverage authority, freshness, parser and actual CLI comparison regressions."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from proofofwork import engine
from proofofwork.core import runner
from proofofwork.core.detector.coverage_delta import METRIC
from proofofwork.core.sandbox import RunOutput
from proofofwork.core.sandbox.local import LocalSandbox
from proofofwork.log import build_envelope
from proofofwork.types import TestResult


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def project(root):
    git(root, "init", "-q")
    git(root, "config", "user.name", "fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "app.py").write_text("def value():\n    return 1\n")
    (root / "test_app.py").write_text("from app import value\n\ndef test_value():\n    assert value() == 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return git(root, "rev-parse", "HEAD")


def baseline(root, value=90, languages=None, metric=METRIC):
    path = root / ".proofofwork" / "baseline.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"coverage": value, "languages": languages or ["python"],
                                "metric": metric}))
    git(root, "add", "-f", str(path))
    git(root, "commit", "-qm", "trusted baseline")


@pytest.mark.parametrize("framework", ["pytest", "vitest", "pytest+vitest"])
def test_required_missing_measurements_block_and_opt_out_is_signed(monkeypatch, tmp_path, framework):
    project(tmp_path)
    baseline(tmp_path, languages=["js", "python"] if "+" in framework else
             ["js"] if framework == "vitest" else ["python"])
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(
        ran=True, passed=True, framework=framework))
    verdict = engine.check(str(tmp_path))
    assert not verdict.passed and verdict.coverage_status == "unavailable"
    assert any(f.rule == "coverage-unavailable" for f in verdict.findings)
    opt_out = engine.check(str(tmp_path), coverage_policy="test-only")
    assert opt_out.passed and "NOT verified" in " ".join(opt_out.reasons)
    predicate = build_envelope("synthetic", opt_out)["predicate"]
    assert predicate["coverage_policy"] == "test-only"
    assert predicate["coverage_status"] == "test-only"
    assert opt_out.as_dict()["coverage_status"] != "verified"


@pytest.mark.parametrize("value", [None, "90", True, -1, 101, float("nan"), float("inf")])
@pytest.mark.parametrize("reader", ["python", "js"])
def test_report_percentage_rejects_invalid_values(tmp_path, value, reader):
    report = ({"meta": {"branch_coverage": False}, "files": {"a.py": {}},
               "totals": {"num_statements": 100, "covered_lines": 90, "percent_covered": value}}
              if reader == "python" else
              {"total": {"lines": {"total": 100, "covered": 90, "pct": value}}})
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    read = runner._read_coverage_json if reader == "python" else runner._read_js_summary
    assert read(str(path)) is None


@pytest.mark.parametrize("content", ["[]", "{}", "bad", '{"total":null}'])
def test_malformed_reports_and_missing_reports_rejected(tmp_path, content):
    path = tmp_path / "report.json"
    for read in (runner._read_coverage_json, runner._read_js_summary):
        assert read(str(path)) is None
        path.write_text(content)
        assert read(str(path)) is None
        path.unlink()


@pytest.mark.parametrize("framework", ["vitest", "jest"])
def test_js_stale_root_report_not_used_and_fresh_destination_is_explicit(tmp_path, framework):
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {framework: "1"}}))
    old = tmp_path / "coverage" / "coverage-summary.json"
    old.parent.mkdir()
    old.write_text('{"total":{"lines":{"total":10,"covered":10,"pct":100}}}')

    class Fake:
        def run(self, cmd, **kwargs):
            assert cmd[:2] == ["npx", "--no-install"]
            destination = next(c.split("=", 1)[1] for c in cmd if
                               c.startswith(("--coverageDirectory=", "--coverage.reportsDirectory=")))
            assert Path(destination).is_dir() and not Path(destination, "coverage-summary.json").exists()
            self.destination = destination
            if self.write:
                Path(destination, "coverage-summary.json").write_text(old.read_text())
            return RunOutput(code=0, stdout="", stderr="")
    fake = Fake()
    fake.write = False
    assert runner._run_js(fake, str(tmp_path)).coverage is None
    fake.write = True
    assert runner._run_js(fake, str(tmp_path)).coverage == 100
    assert not Path(fake.destination).exists() and old.exists()


@pytest.mark.parametrize("report_code", [0, 1])
def test_python_report_failure_or_no_new_data_cannot_reuse_artifacts(tmp_path, report_code):
    stale = '{"meta":{"branch_coverage":false},"files":{"a.py":{}},"totals":' \
            '{"num_statements":10,"covered_lines":10,"percent_covered":100}}'
    (tmp_path / ".coverage").write_text("old data")
    (tmp_path / "coverage.json").write_text(stale)

    class Fake:
        def run(self, cmd, **kwargs):
            if "-c" in cmd:
                return RunOutput(code=0, stdout="", stderr="")
            env = kwargs["env"]
            if "-q" in cmd:
                Path(env["PROOFOFWORK_PYTEST_WITNESS"]).write_text(
                    '{"executed":1,"collect_only":false}')
            assert str(tmp_path) not in env["COVERAGE_FILE"]
            assert not Path(env["COVERAGE_FILE"]).exists()
            if "json" in cmd:
                # Even valid report bytes cannot excuse a failed report command.
                if report_code:
                    Path(cmd[-1]).write_text(stale)
                return RunOutput(code=report_code, stdout="", stderr="report failure")
            return RunOutput(code=0, stdout="", stderr="")
    result = runner._run_python(Fake(), str(tmp_path))
    assert result.passed and result.coverage is None
    assert (tmp_path / ".coverage").read_text() == "old data"


def test_missing_coverage_tool_is_not_silently_verified(tmp_path):
    class Fake:
        def run(self, cmd, **kwargs):
            if "-q" in cmd:
                Path(kwargs["env"]["PROOFOFWORK_PYTEST_WITNESS"]).write_text(
                    '{"executed":1,"collect_only":false}')
            return RunOutput(code=1 if "import coverage" in cmd else 0, stdout="", stderr="")
    result = runner._run_python(Fake(), str(tmp_path))
    assert result.passed and result.coverage is None and "tool unavailable" in result.raw


@pytest.mark.parametrize("metadata", [None, {"languages": ["js"], "metric": METRIC},
                                       {"languages": ["python"], "metric": "branches"}])
def test_legacy_and_incomparable_baselines_never_fallback(monkeypatch, tmp_path, metadata):
    project(tmp_path)
    path = tmp_path / ".proofofwork" / "baseline.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"coverage": 90, **(metadata or {})}))
    git(tmp_path, "add", "-f", str(path))
    git(tmp_path, "commit", "-qm", "incomparable baseline")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(
        ran=True, passed=True, coverage=100, framework="pytest"))
    monkeypatch.setattr(engine, "_measure_base", lambda *a: pytest.fail("must not fallback"))
    verdict = engine.check(str(tmp_path), measure_base_coverage=True)
    assert not verdict.passed and any(f.rule == "coverage-incomparable" for f in verdict.findings)


def test_actual_cli_frozen_base_positive_and_negative(tmp_path):
    base = project(tmp_path)
    # Candidate baseline cannot set the comparison floor.
    baseline(tmp_path, 0)
    before = git(tmp_path, "worktree", "list", "--porcelain")
    (tmp_path / "app.py").write_text("def value():\n    return 1\n\ndef uncovered():\n" +
                                   "".join(f"    v{i} = {i}\n" for i in range(60)))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    cmd = [sys.executable, "-m", "proofofwork.interfaces.cli", "check", "--root", str(tmp_path),
           "--base", base, "--measure-base-coverage", "--json"]
    negative = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120,
                              check=False)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    result = json.loads(negative.stdout)
    assert result["coverage_baseline"] == 100
    assert any(f["rule"] == "coverage-drop" for f in result["findings"])
    (tmp_path / "app.py").write_text("# honest candidate\ndef value():\n    return 1\n")
    positive = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120,
                              check=False)
    assert positive.returncode == 0, positive.stdout + positive.stderr
    result = json.loads(positive.stdout)
    assert result["coverage_status"] == "verified" and result["coverage_baseline_source"] == "measured:" + base
    # Inspect only this fixture's synthetic retained envelope, never a user signing store.
    conn = sqlite3.connect(tmp_path / ".proofofwork" / "log.db")
    try:
        predicate = json.loads(conn.execute(
            "SELECT envelope_json FROM entries ORDER BY id DESC LIMIT 1").fetchone()[0])["predicate"]
        assert predicate["coverage_baseline"] == result["coverage_baseline"]
        assert predicate["coverage_baseline_source"] == "measured:" + base
    finally:
        conn.close()
    assert git(tmp_path, "worktree", "list", "--porcelain") == before


@pytest.mark.parametrize("result", [TestResult(), TestResult(ran=True, passed=False,
                           coverage=100, framework="pytest"),
                           TestResult(ran=True, passed=True, framework="pytest"),
                           TestResult(ran=True, passed=True, coverage=float("nan"),
                                      framework="pytest"),
                           TestResult(ran=True, passed=True, coverage=100, framework="jest")])
def test_failed_empty_invalid_or_incomparable_base_measurement_blocks(monkeypatch, tmp_path, result):
    project(tmp_path)
    (tmp_path / "app.py").write_text("# candidate\ndef value():\n    return 1\n")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(
        ran=True, passed=True, coverage=100, framework="pytest"))
    monkeypatch.setattr(engine, "_measure_base", lambda *a: result)
    verdict = engine.check(str(tmp_path), measure_base_coverage=True)
    assert not verdict.passed and verdict.coverage_status != "verified"
    assert any(f.rule == "test-execution-error" for f in verdict.findings)


def test_malformed_trusted_baseline_never_falls_back(monkeypatch, tmp_path):
    project(tmp_path)
    path = tmp_path / ".proofofwork" / "baseline.json"
    path.parent.mkdir()
    path.write_text("not JSON")
    git(tmp_path, "add", "-f", str(path))
    git(tmp_path, "commit", "-qm", "malformed baseline")
    monkeypatch.setattr(engine, "_measure_base", lambda *a: pytest.fail("must not fallback"))
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(
        ran=True, passed=True, coverage=100, framework="pytest"))
    verdict = engine.check(str(tmp_path), measure_base_coverage=True)
    assert not verdict.passed and any(f.rule == "coverage-baseline-invalid" for f in verdict.findings)


def test_base_ref_frozen_before_candidate_execution(monkeypatch, tmp_path):
    project(tmp_path)
    baseline(tmp_path, 90)
    trusted = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "app.py").write_text("# candidate\ndef value():\n    return 1\n")

    def run(*a, **k):
        baseline(tmp_path, 0)  # simulate moving HEAD while tests execute
        return TestResult(ran=True, passed=True, coverage=70, framework="pytest")
    monkeypatch.setattr(runner, "run_tests", run)
    verdict = engine.check(str(tmp_path))
    assert not verdict.passed and verdict.coverage_baseline == 90
    assert verdict.coverage_baseline_source == trusted


@pytest.mark.parametrize("threshold", [3, float("inf"), float("nan"), -1, True])
def test_coverage_threshold_cannot_weaken_policy(tmp_path, threshold):
    project(tmp_path)
    assert not engine.check(str(tmp_path), coverage_policy="test-only",
                            coverage_drop_threshold=threshold).passed


def test_actual_base_src_imports_not_candidate_pythonpath(tmp_path, monkeypatch):
    project(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "frozen_feature.py").write_text("value = 1\n")
    (tmp_path / "test_src.py").write_text("from frozen_feature import value\n\n"
                                        "def test_base_value():\n    assert value == 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "src base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (src / "frozen_feature.py").write_text("value = 2\n")
    monkeypatch.setenv("PYTHONPATH", str(src))
    before = git(tmp_path, "worktree", "list", "--porcelain")
    measured = engine._measure_base(str(tmp_path), base, {"python"})
    assert measured.ran and measured.passed, measured.raw
    assert measured.coverage == 100
    assert (src / "frozen_feature.py").read_text() == "value = 2\n"
    assert git(tmp_path, "worktree", "list", "--porcelain") == before


@pytest.mark.parametrize("source", ["config", "environment"])
@pytest.mark.parametrize("mode", ["instrumented", "missing-tool", "test-only"])
def test_actual_python_collection_only_never_counts_as_execution(
        monkeypatch, tmp_path, source, mode):
    project(tmp_path)
    if source == "config":
        (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    else:
        monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    if mode == "missing-tool":
        original = runner._has_module
        monkeypatch.setattr(runner, "_has_module", lambda sandbox, root, mod, env=None:
                            False if mod == "coverage" else original(sandbox, root, mod, env))
    result = runner._run_python(LocalSandbox(), str(tmp_path),
                                collect_coverage=mode != "test-only")
    assert not result.ran and result.passed is False
    assert "execution unavailable" in result.raw


@pytest.mark.parametrize("passed", [True, False])
@pytest.mark.parametrize("collect_coverage", [True, False])
def test_actual_python_execution_controls(tmp_path, passed, collect_coverage):
    project(tmp_path)
    if not passed:
        (tmp_path / "app.py").write_text("def value():\n    return 2\n")
    result = runner._run_python(LocalSandbox(), str(tmp_path), collect_coverage=collect_coverage)
    assert result.ran and result.passed is passed, result.raw
    if not collect_coverage:
        assert result.coverage is None


def _cli(root, base, *flags):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.run([sys.executable, "-m", "proofofwork.interfaces.cli", "check",
                           "--root", str(root), "--base", base, "--json", *flags],
                          env=env, capture_output=True, text=True, timeout=120, check=False)
    assert proc.stdout, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def test_actual_cli_failing_collect_only_base_cannot_authorize_floor(tmp_path):
    project(tmp_path)
    (tmp_path / "test_app.py").write_text("from app import value\n\ndef test_value():\n"
                                        "    assert value() == 2\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "failing collect-only base")
    base = git(tmp_path, "rev-parse", "HEAD")
    # Candidate genuinely executes/passes the SAME test; frozen base cannot.
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "app.py").write_text("def value():\n    return 2\n")
    code, verdict = _cli(tmp_path, base, "--measure-base-coverage")
    assert code == 1 and verdict["tests"]["passed"] is True
    assert verdict["coverage_status"] != "verified"
    assert any(f["rule"] == "test-execution-error" for f in verdict["findings"])


@pytest.mark.parametrize("source", ["config", "environment"])
def test_actual_cli_candidate_collection_only_against_stored_floor(monkeypatch, tmp_path, source):
    project(tmp_path)
    baseline(tmp_path, 0)
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "app.py").write_text("# candidate\ndef value():\n    return 1\n")
    if source == "config":
        (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    else:
        monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    code, verdict = _cli(tmp_path, base)
    assert code == 1 and not verdict["tests"]["ran"]
    assert verdict["coverage_status"] != "verified"


@pytest.mark.parametrize("framework", ["vitest", "jest"])
@pytest.mark.parametrize("test_failure", [False, True])
def test_modelled_js_test_only_omits_provider_but_cannot_hide_failure(
        monkeypatch, tmp_path, framework, test_failure):
    """Command model only: no real JS provider/tool is installed by this regression."""
    project(tmp_path)
    (tmp_path / "package.json").write_text(json.dumps({"devDependencies": {framework: "1"}}))
    (tmp_path / "app.js").write_text("const value = 1;\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "JS fixture")
    (tmp_path / "app.js").write_text("const value = 2;\n")
    commands = []

    class Model:
        def run(self, cmd, **kwargs):
            commands.append(cmd)
            # Absent provider fails instrumentation; ordinary tests fail only if requested.
            return RunOutput(code=1 if "--coverage" in cmd or test_failure else 0,
                             stdout="", stderr="synthetic provider/test outcome")
    monkeypatch.setattr("proofofwork.core.sandbox.get_sandbox", lambda *a: Model())
    required = engine.check(str(tmp_path))
    assert not required.passed and "--coverage" in commands[-1]
    opt_out = engine.check(str(tmp_path), coverage_policy="test-only")
    assert opt_out.passed is not test_failure
    assert opt_out.coverage_status == "test-only"
    assert not any("coverage" in arg for arg in commands[-1])
    assert "NOT verified" in " ".join(opt_out.reasons)


def test_python_test_only_never_probes_or_invokes_coverage(tmp_path):
    commands = []

    class Model:
        def run(self, cmd, **kwargs):
            commands.append(cmd)
            assert not any("coverage" == arg or "import coverage" == arg for arg in cmd)
            if "-q" in cmd:
                env = kwargs["env"]
                assert env["COVERAGE_PROCESS_START"] == env["COVERAGE_PROCESS_CONFIG"] == ""
                Path(env["PROOFOFWORK_PYTEST_WITNESS"]).write_text(
                    '{"executed":1,"collect_only":false}')
            return RunOutput(code=0, stdout="", stderr="")
    result = runner.run_tests(Model(), str(tmp_path), {"python"}, collect_coverage=False)
    assert result.ran and result.passed and result.coverage is None
    assert len(commands) == 2


def test_mixed_baseline_single_language_diff_ceiling_is_fail_closed(monkeypatch, tmp_path):
    project(tmp_path)
    baseline(tmp_path, languages=["js", "python"])
    (tmp_path / "app.py").write_text("# Python-only diff\ndef value():\n    return 1\n")
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: TestResult(
        ran=True, passed=True, coverage=100, framework="pytest"))
    monkeypatch.setattr(engine, "_measure_base", lambda *a: pytest.fail("must not relax identity"))
    verdict = engine.check(str(tmp_path), measure_base_coverage=True)
    assert not verdict.passed and verdict.coverage_status == "unavailable"
    assert any(f.rule == "coverage-incomparable" for f in verdict.findings)
