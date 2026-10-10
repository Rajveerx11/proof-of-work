"""Re-run the real suite through the sandbox and read the TRUE result.

Never the agent's word: pass/fail comes from the process exit code, coverage from the
tool's own JSON. Always routes through `sandbox.run(...)` so isolation stays swappable.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

from ..types import TestResult
from .sandbox import RunOutput, Sandbox

_TAIL = 2000  # chars of output kept for debugging


def _tail(out: RunOutput) -> str:
    return (out.stdout + out.stderr)[-_TAIL:]


def run_tests(sandbox: Sandbox, root: str, languages: set[str], *,
              python_env: dict[str, str] | None = None,
              collect_coverage: bool = True) -> TestResult:
    python = "python" in languages
    javascript = bool(languages.intersection({"js", "ts"}))
    coverage_args = {} if collect_coverage else {"collect_coverage": False}
    if python and javascript:
        py = _run_python(sandbox, root, env=python_env, **coverage_args)
        js = _run_js(sandbox, root, **coverage_args)
        if py is None or js is None:
            missing = "Python" if py is None else "JS/TS"
            return TestResult(ran=False, framework="+".join(
                r.framework for r in (py, js) if r is not None),
                raw=f"required {missing} test suite unavailable; both language suites required")
        return TestResult(ran=py.ran and js.ran, passed=py.passed is True and js.passed is True,
                          coverage=py.coverage, js_coverage=js.coverage,
                          framework=f"{py.framework}+{js.framework}",
                          raw=f"Python: {py.raw}\nJS/TS: {js.raw}")
    if python:
        return _run_python(sandbox, root, env=python_env, **coverage_args) or TestResult(ran=False)
    if javascript:
        return _run_js(sandbox, root, **coverage_args) or TestResult(ran=False)
    return TestResult(ran=False)


# --- Python: pytest, coverage via coverage.py if present ---
def _has_module(sandbox: Sandbox, root: str, mod: str, env: dict[str, str] | None = None) -> bool:
    out = sandbox.run([sys.executable, "-c", f"import {mod}"], cwd=root, env=env, timeout=30)
    return out.code == 0


def _pytest_witness(directory: str, env: dict[str, str]) -> tuple[str, str, dict[str, str]]:
    module = os.path.basename(directory).replace("-", "_") + "_witness"
    path = os.path.join(directory, "executed.json")
    with open(os.path.join(directory, module + ".py"), "w", encoding="utf-8") as f:
        f.write(
            "import json\nimport pytest\nexecuted = 0\n"
            "def pytest_sessionstart(session):\n"
            "    if session.config.option.collectonly:\n"
            "        raise pytest.UsageError('proof-of-work requires test execution')\n"
            "def pytest_runtest_logreport(report):\n"
            "    global executed\n"
            "    if report.when == 'call' and not report.skipped:\n"
            "        executed += 1\n"
            "def pytest_sessionfinish(session, exitstatus):\n"
            f"    with open({path!r}, 'w', encoding='utf-8') as f:\n"
            "        json.dump({'executed': executed}, f)\n"
        )
    pythonpath = env.get("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
    return module, path, {**env, "PYTHONPATH": os.pathsep.join((directory, pythonpath)),
                         "PROOFOFWORK_PYTEST_WITNESS": path}


def _executed_pytest(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            count = json.load(f)["executed"]
        return isinstance(count, int) and not isinstance(count, bool) and count > 0
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _run_python(sandbox: Sandbox, root: str, *, env: dict[str, str] | None = None,
                collect_coverage: bool = True) -> TestResult | None:
    root = os.path.abspath(root)
    env = {**(env or {}), "COVERAGE_PROCESS_START": "", "COVERAGE_PROCESS_CONFIG": ""}
    if not _has_module(sandbox, root, "pytest", env):
        return None
    with tempfile.TemporaryDirectory(prefix="proofofwork-python-run-") as directory:
        module, witness, run_env = _pytest_witness(directory, env)
        args = ["-m", "pytest", "-q", "-p", module]
        coverage = None
        detail = ""
        if not collect_coverage or not _has_module(sandbox, root, "coverage", env):
            run = sandbox.run([sys.executable, *args], cwd=root, env=run_env)
            detail = "\ncoverage NOT verified (test-only or missing instrumentation)"
        else:
            config = os.path.join(directory, "coverage.ini")
            with open(config, "w", encoding="utf-8") as f:
                f.write("[run]\n")
            cov_path = os.path.join(directory, "coverage.json")
            run_env["COVERAGE_FILE"] = os.path.join(directory, "data")
            run = sandbox.run([sys.executable, "-m", "coverage", "run", "--rcfile", config,
                               "--source", root, *args], cwd=root, env=run_env)
            report = sandbox.run([sys.executable, "-m", "coverage", "json", "--rcfile", config,
                                  "-o", cov_path], cwd=root, env=run_env, timeout=120)
            coverage = _read_coverage_json(cov_path) if report.code == 0 and not report.timed_out else None
            if coverage is None:
                detail = "\ncoverage report unavailable/invalid: " + _tail(report)
        executed = _executed_pytest(witness)
        if not executed:
            detail += "\npytest execution unavailable (missing witness or non-execution mode)"
        return TestResult(ran=executed, passed=(run.code == 0 and not run.timed_out and executed),
                          coverage=coverage, framework="pytest", raw=_tail(run) + detail)


def _percentage(summary: dict, total_key: str, covered_key: str, percent_key: str) -> float | None:
    total, covered, percent = (summary[k] for k in (total_key, covered_key, percent_key))
    if (not isinstance(total, int) or isinstance(total, bool) or total <= 0
            or not isinstance(covered, int) or isinstance(covered, bool) or not 0 <= covered <= total
            or not isinstance(percent, (int, float)) or isinstance(percent, bool)
            or not 0 <= percent <= 100 or not math.isfinite(percent)
            or abs(percent - 100 * covered / total) > 0.02):
        return None
    return float(percent)


def _read_coverage_json(path: str) -> float | None:
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        if (report["meta"]["branch_coverage"] is not False
                or not isinstance(report["files"], dict) or not report["files"]
                or any(not isinstance(value, dict) for value in report["files"].values())):
            return None
        return _percentage(report["totals"], "num_statements", "covered_lines", "percent_covered")
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- JS/TS: vitest / jest ---
def _run_js(sandbox: Sandbox, root: str, *, collect_coverage: bool = True) -> TestResult | None:
    try:
        with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
            pkg = json.load(f)
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        blob = " ".join(deps) + " " + " ".join(pkg.get("scripts", {}).values())
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    framework = "vitest" if "vitest" in blob else "jest" if "jest" in blob else None
    if framework is None:
        return None
    with tempfile.TemporaryDirectory(prefix="proofofwork-js-run-") as directory:
        witness = os.path.join(directory, "tests.json")
        cmd = (["npx", "--no-install", "vitest", "run", "--reporter=json"] if framework == "vitest"
               else ["npx", "--no-install", "jest", "--json"])
        cmd.append(f"--outputFile={witness}")
        if collect_coverage:
            cmd.extend(["--coverage", "--coverage.reporter=json-summary",
                        f"--coverage.reportsDirectory={directory}"] if framework == "vitest" else
                       ["--coverage", "--coverageReporters=json-summary",
                        f"--coverageDirectory={directory}"])
        else:
            cmd.append("--coverage.enabled=false" if framework == "vitest" else "--coverage=false")
        run = sandbox.run(cmd, cwd=root)
        executed, successful = _executed_js(witness)
        coverage = (_read_js_summary(os.path.join(directory, "coverage-summary.json"))
                    if collect_coverage and run.code == 0 and not run.timed_out else None)
        detail = "\ncoverage NOT verified (explicit test-only)" if not collect_coverage else ""
        if collect_coverage and coverage is None:
            detail += "\ncoverage report unavailable/invalid"
        if not executed or not successful:
            detail += "\nJS execution unavailable or report indicates failure"
        return TestResult(ran=executed, passed=(run.code == 0 and not run.timed_out and successful),
                          coverage=coverage, framework=framework, raw=_tail(run) + detail)


def _executed_js(path: str) -> tuple[bool, bool]:
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        passed, failed, suites_failed = (report[k] for k in
                                        ("numPassedTests", "numFailedTests", "numFailedTestSuites"))
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0
                   for n in (passed, failed, suites_failed)):
            return False, False
        executed = passed + failed > 0  # totals also count skipped/todo tests
        return executed, (executed and report.get("success") is True
                          and failed == 0 and suites_failed == 0)
    except (OSError, ValueError, KeyError, TypeError):
        return False, False


def _read_js_summary(path: str) -> float | None:
    try:
        with open(path, encoding="utf-8") as f:
            return _percentage(json.load(f)["total"]["lines"], "total", "covered", "pct")
    except (OSError, ValueError, KeyError, TypeError):
        return None
