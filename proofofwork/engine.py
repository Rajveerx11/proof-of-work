"""The one engine every surface calls. Facts decide the verdict; the judge only annotates.

Flow: parse diff -> deterministic detector checks -> re-run real tests -> coverage delta
-> (optional) mutation -> (optional) advisory judge -> decide on facts -> log tamper-evidently.
"""
from __future__ import annotations

import hashlib
import os

from .types import Finding, MutationResult, Severity, TestResult, Verdict

DEFAULT_DB = ".proofofwork/log.db"


def _changeset_sha(diff) -> str:
    h = hashlib.sha256()
    for f in diff.files:
        h.update(f.status.encode())
        h.update(b"\0")
        h.update(f.path.encode())
        h.update(b"\0")
        for line in f.added:
            h.update(b"+" + line.encode("utf-8", "replace") + b"\n")
        for line in f.removed:
            h.update(b"-" + line.encode("utf-8", "replace") + b"\n")
    return h.hexdigest()


def check(root: str = ".", base_ref: str = "HEAD", *, staged: bool = False,
          run_tests: bool = True, run_mutation: bool = False, use_judge: bool = False,
          update_baseline: bool = False, db_path: str | None = None,
          coverage_drop_threshold: float = 2.0) -> Verdict:
    """Run the full gate against a changeset and return a fact-based Verdict."""
    from .core.detector import ALL_CHECKS
    from .core.gitdiff import collect_diff

    root = os.path.abspath(root)
    db_path = db_path or os.path.join(root, DEFAULT_DB)

    diff = collect_diff(root, base_ref, staged=staged)

    findings: list[Finding] = []
    for check_fn in ALL_CHECKS:
        try:
            findings.extend(check_fn(diff, root))
        except Exception as e:  # a broken check must never crash the gate
            findings.append(Finding(rule=f"check-error:{getattr(check_fn, '__name__', '?')}",
                                    severity=Severity.INFO, message=str(e)))

    tests = TestResult()
    coverage_baseline: float | None = None
    if run_tests:
        from .core.detector.coverage_delta import (
            coverage_findings, read_baseline, write_baseline,
        )
        from .core.runner import run_tests as _run
        from .core.sandbox import get_sandbox

        tests = _run(get_sandbox("local"), root, diff.languages())
        coverage_baseline = read_baseline(root)
        findings.extend(coverage_findings(tests, coverage_baseline,
                                          threshold=coverage_drop_threshold))
        if update_baseline and tests.coverage is not None:
            write_baseline(root, tests.coverage)

    mutation = MutationResult()
    if run_mutation:
        from .core.mutation import run_mutation as _mut
        mutation = _mut(root, diff.languages())
        if mutation.ran and mutation.survived:
            findings.append(Finding(rule="mutation:survivors", severity=Severity.WARN,
                message=f"{mutation.survived} mutant(s) survived — tests may be gutted"))

    judge_meta = None
    if use_judge:
        from .judge import review
        judge_meta = review(diff)  # advisory ONLY — logged as metadata, never signed

    passed, reasons = _decide(findings, tests)
    verdict = Verdict(passed=passed, reasons=reasons, findings=findings, tests=tests,
                      mutation=mutation, coverage_baseline=coverage_baseline, judge=judge_meta)

    try:  # tamper-evident record (facts only); logging must never change the verdict
        from .log import build_envelope, record
        env = build_envelope(subject=_changeset_sha(diff), verdict=verdict)
        verdict.entry_hash = record(env, db_path)
    except Exception as e:
        verdict.reasons.append(f"(log unavailable: {e})")

    return verdict


def _decide(findings: list[Finding], tests: TestResult) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    blocked = [f for f in findings if f.severity == Severity.BLOCK]
    tests_failed = tests.ran and tests.passed is False

    for f in blocked:
        reasons.append(f"BLOCK {f.rule}: {f.message}")
    if tests_failed:
        reasons.append("tests failed on a clean re-run")
    for f in findings:
        if f.severity == Severity.WARN:
            reasons.append(f"warn {f.rule}: {f.message}")

    passed = not blocked and not tests_failed
    if passed and not reasons:
        reasons.append("no cheat signals; facts check out")
    return passed, reasons
