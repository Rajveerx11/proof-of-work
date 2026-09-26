"""The one engine every surface calls. Facts decide the verdict; the judge only annotates.

Flow: parse diff -> deterministic detector checks -> re-run real tests -> coverage delta
-> (optional) mutation -> (optional) advisory judge -> decide on facts -> log tamper-evidently.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

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
          coverage_drop_threshold: float = 2.0,
          extra_findings: list[Finding] | None = None) -> Verdict:
    """Run the full gate against a changeset and return a fact-based Verdict."""
    from .core.detector import ALL_CHECKS
    from .core.gitdiff import _git, collect_diff

    root = os.path.abspath(root)

    try:
        diff = collect_diff(root, base_ref, staged=staged)
        if db_path is None:
            # Staged checks may not add log files to the working tree. An explicit
            # --db path is caller-requested output and is used unchanged.
            db_path = (os.path.join(root, _git(root, "rev-parse", "--git-path",
                                                  "proofofwork/log.db").strip())
                       if staged else os.path.join(root, DEFAULT_DB))
    except Exception as exc:  # noqa: BLE001 - git failures must not become empty passing diffs
        detail = f"cannot collect git diff: {exc}"
        return Verdict(passed=False, reasons=[f"BLOCK git-diff: {detail}"],
                       findings=[Finding("git-diff", Severity.BLOCK, detail)])

    findings: list[Finding] = list(extra_findings or ())
    for check_fn in ALL_CHECKS:
        try:
            findings.extend(check_fn(diff, root))
        except Exception as e:  # noqa: BLE001 - a broken check must never crash the gate
            findings.append(Finding(rule=f"check-error:{getattr(check_fn, '__name__', '?')}",
                                    severity=Severity.BLOCK, message=str(e)))

    # Learned rules (grown by the self-improving loop) run alongside the built-ins but stay
    # out of ALL_CHECKS so that set remains the fixed, human-authored core.
    try:
        from .core.detector import learned
        findings.extend(learned.check(diff, root))
    except Exception as e:  # noqa: BLE001 - broken learned checks must fail closed
        findings.append(Finding(rule="check-error:learned", severity=Severity.BLOCK,
                                message=str(e)))

    tests = TestResult()
    coverage_baseline: float | None = None
    if update_baseline and not run_tests:
        findings.append(Finding("baseline-bootstrap-error", Severity.BLOCK,
                                "--update-baseline requires a successful test run"))
    if run_tests:
        from .core.detector.coverage_delta import (
            baseline_exists,
            baseline_path,
            coverage_findings,
            read_baseline,
            valid_coverage,
            write_baseline,
        )
        from .core.runner import run_tests as _run
        from .core.sandbox import get_sandbox

        try:
            if staged:
                with tempfile.TemporaryDirectory(prefix="proofofwork-index-") as directory:
                    snapshot = os.path.join(directory, "tree")
                    _git(root, "worktree", "add", "--detach", "--no-checkout", snapshot, "HEAD")
                    try:
                        _git(root, "checkout-index", "--all", "--prefix=" + snapshot + os.sep)
                        _prepare_staged_dependencies(root, snapshot, diff.languages())
                        tests = _run(get_sandbox("local"), snapshot, diff.languages())
                    finally:
                        _git(root, "worktree", "remove", "--force", snapshot)
            else:
                tests = _run(get_sandbox("local"), root, diff.languages())
            # Explicit base comparisons trust only the verified base commit, never the
            # candidate worktree's (or candidate commit's) baseline.
            trusted_ref = (_git(root, "rev-parse", "--verify", "--end-of-options",
                                f"{base_ref}^{{commit}}").strip()
                           if not staged and base_ref != "HEAD" else None)
            coverage_baseline = read_baseline(root, staged=staged, base_ref=trusted_ref)
            # Suppress only the missing-baseline finding during explicit bootstrap;
            # always evaluate a present trusted baseline before deciding to write.
            if coverage_baseline is not None or not update_baseline:
                findings.extend(coverage_findings(tests, coverage_baseline,
                                                  threshold=coverage_drop_threshold))
            if update_baseline:
                if baseline_exists(root, staged=staged, base_ref=trusted_ref):
                    raise ValueError("coverage baseline already exists; --update-baseline is bootstrap-only")
                if not (tests.ran and tests.passed is True and valid_coverage(tests.coverage)):
                    raise ValueError("--update-baseline requires passing tests with coverage")
        except Exception as exc:  # noqa: BLE001 - execution and baseline errors fail closed
            findings.append(Finding("test-execution-error", Severity.BLOCK, str(exc)))

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

    passed, reasons = _decide(findings, tests, require_tests=run_tests)
    bootstrapped = None
    if passed and update_baseline:
        try:
            write_baseline(root, tests.coverage, staged=staged)
            path = baseline_path(root, staged=staged)
            bootstrapped = (path, os.stat(path).st_ino, tests.coverage)
            coverage_baseline = tests.coverage
        except Exception as exc:  # noqa: BLE001 - bootstrap failures must fail closed
            findings.append(Finding("baseline-bootstrap-error", Severity.BLOCK, str(exc)))
            passed, reasons = _decide(findings, tests, require_tests=run_tests)
    verdict = Verdict(passed=passed, reasons=reasons, findings=findings, tests=tests,
                      mutation=mutation, coverage_baseline=coverage_baseline, judge=judge_meta)

    try:  # a passing signed verdict requires a durable log record
        from .log import build_envelope, record
        env = build_envelope(subject=_changeset_sha(diff), verdict=verdict)
        verdict.entry_hash = record(env, db_path)
    except Exception as e:  # noqa: BLE001 - never claim a signed pass without its log
        if bootstrapped is not None:
            # Do not keep a newly bootstrapped baseline after a failed gate log.
            # Only remove our own unchanged file, never a concurrently replaced one.
            path, inode, coverage = bootstrapped
            try:
                with open(path, encoding="utf-8") as f:
                    unchanged = (os.fstat(f.fileno()).st_ino == inode
                                 and json.load(f) == {"coverage": coverage})
                if unchanged:
                    os.unlink(path)
            except (OSError, ValueError):
                pass
        verdict.passed = False
        verdict.entry_hash = ""
        verdict.reasons.append(f"BLOCK log-unavailable: {e}")

    return verdict


def _prepare_staged_dependencies(root: str, snapshot: str, languages: set[str]) -> None:
    """Bring only ignored installed JS packages into the isolated index tree."""
    from .core.gitdiff import _git

    for parent, dirs, files in os.walk(snapshot, followlinks=False):
        for name in (*dirs, *files):
            if (Path(parent) / name).is_symlink():
                raise ValueError("staged snapshot contains a symlink; cannot safely run tests")
    if not languages.intersection({"js", "ts"}):
        return
    packages = Path(root) / "node_modules"
    if not packages.is_dir() or packages.is_symlink():
        raise ValueError("staged JS tests require installed, ignored node_modules")
    try:
        _git(root, "check-ignore", "--quiet", "--", "node_modules")
    except RuntimeError as exc:
        raise ValueError("staged JS tests require ignored node_modules") from exc
    package_root = packages.resolve()
    for parent, dirs, files in os.walk(packages, followlinks=False):
        for name in (*dirs, *files):
            path = Path(parent) / name
            if path.is_symlink() and not path.resolve().is_relative_to(package_root):
                raise ValueError(f"node_modules link escapes installed dependencies: {path}")
    shutil.copytree(packages, Path(snapshot) / "node_modules")


def _decide(findings: list[Finding], tests: TestResult, *,
            require_tests: bool = False) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    blocked = [f for f in findings if f.severity == Severity.BLOCK]
    tests_failed = tests.ran and tests.passed is False
    tests_missing = require_tests and not (tests.ran and tests.passed is True)

    for f in blocked:
        reasons.append(f"BLOCK {f.rule}: {f.message}")
    if tests_failed:
        reasons.append("tests failed on a clean re-run")
    elif tests_missing:
        reasons.append(f"BLOCK tests-unavailable: {tests.raw or 'no successful test run'}")
    for f in findings:
        if f.severity == Severity.WARN:
            reasons.append(f"warn {f.rule}: {f.message}")

    passed = not blocked and not tests_failed and not tests_missing
    if passed and not require_tests:
        reasons.append("detector-only: tests were not run; no test pass claimed")
    if passed and not reasons:
        if any(f.rule == "coverage-baseline-missing" for f in findings):
            reasons.append("tests passed; coverage baseline unavailable (coverage not enforced)")
        else:
            reasons.append("no cheat signals; facts check out")
    return passed, reasons
