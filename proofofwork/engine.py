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
        h.update(f.path.encode("utf-8", "surrogateescape"))
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
          extra_findings: list[Finding] | None = None,
          strict_integrity: bool = False) -> Verdict:
    """Run the full gate against a changeset and return a fact-based Verdict."""
    from .core.detector import ALL_CHECKS
    from .core.gitdiff import _git, collect_diff

    root = os.path.abspath(root)
    integrity_policy = "strict-v1" if strict_integrity else "advisory-v1"

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
                       findings=[Finding("git-diff", Severity.BLOCK, detail)],
                       integrity_policy=integrity_policy)

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
                        python_env = (_staged_python_env(root, snapshot, directory)
                                      if "python" in diff.languages() else None)
                        tests = _run(get_sandbox("local"), snapshot, diff.languages(),
                                     **({"python_env": python_env} if python_env else {}))
                    finally:
                        try:
                            _git(root, "worktree", "remove", "--force", snapshot)
                        except RuntimeError as remove_error:
                            shutil.rmtree(snapshot, ignore_errors=True)
                            # Removing the directory alone leaves a stale Git registration.
                            # Retry only this worktree; a global prune could remove unrelated ones.
                            try:
                                _git(root, "worktree", "remove", "--force", snapshot)
                            except RuntimeError:
                                raise remove_error
            else:
                tests = _run(get_sandbox("local"), root, diff.languages())
            # Explicit base comparisons trust only the verified base commit, never the
            # candidate worktree's (or candidate commit's) baseline.
            trusted_ref = (_git(root, "rev-parse", "--verify", "--end-of-options",
                                f"{base_ref}^{{commit}}").strip()
                           if not staged and base_ref != "HEAD" else None)
            coverage_baseline = read_baseline(root, staged=staged, base_ref=trusted_ref)
            existing_baseline = baseline_exists(root, staged=staged, base_ref=trusted_ref)
            if existing_baseline and coverage_baseline is None:
                findings.append(Finding("coverage-baseline-invalid", Severity.BLOCK,
                                        "existing coverage baseline is malformed or invalid"))
            # Suppress only the missing-baseline finding during explicit bootstrap;
            # always evaluate a present trusted baseline before deciding to write.
            if (not (existing_baseline and coverage_baseline is None)
                    and (coverage_baseline is not None or not update_baseline)):
                findings.extend(coverage_findings(tests, coverage_baseline,
                                                  threshold=coverage_drop_threshold))
            if "+" in tests.framework and tests.passed is True:
                # The scalar baseline is a conservative floor for each measured suite,
                # never an average that could hide a material drop in either one.
                if tests.js_coverage is not None and not valid_coverage(tests.js_coverage):
                    findings.append(Finding("coverage-invalid", Severity.BLOCK,
                                            "JS/TS coverage must be a finite percentage "
                                            "between 0 and 100"))
                if (coverage_baseline is None and not update_baseline and not existing_baseline
                        and tests.coverage is None and tests.js_coverage is not None):
                    findings.append(Finding("coverage-baseline-missing", Severity.BLOCK,
                                            "measured JS/TS coverage requires an explicit baseline bootstrap"))
                if coverage_baseline is not None:
                    if tests.coverage is None or tests.js_coverage is None:
                        findings.append(Finding("coverage-incomparable", Severity.BLOCK,
                                                "both Python and JS/TS coverage are required "
                                                "to compare mixed suites to the baseline"))
                    elif (valid_coverage(tests.js_coverage)
                          and coverage_baseline - tests.js_coverage > coverage_drop_threshold):
                        findings.append(Finding("coverage-drop", Severity.BLOCK,
                                                "JS/TS tests pass but coverage fell "
                                                f"{coverage_baseline - tests.js_coverage:.1f} pts "
                                                "against the conservative scalar baseline",
                                                evidence=f"baseline={coverage_baseline} "
                                                         f"js_current={tests.js_coverage}"))
            if update_baseline:
                if existing_baseline:
                    raise ValueError("coverage baseline already exists; --update-baseline is bootstrap-only")
                if not (tests.ran and tests.passed is True and valid_coverage(tests.coverage)):
                    raise ValueError("--update-baseline requires passing tests with coverage")
        except Exception as exc:  # noqa: BLE001 - execution and baseline errors fail closed
            findings.append(Finding("test-execution-error", Severity.BLOCK, str(exc)))

    mutation = MutationResult()
    if run_mutation:
        if staged:
            findings.append(Finding("mutation-staged-unavailable", Severity.BLOCK,
                                    "staged mutation cannot run against the index snapshot"))
        else:
            from .core.mutation import run_mutation as _mut
            mutation = _mut(root, diff.languages())
            if mutation.ran and mutation.survived:
                findings.append(Finding(rule="mutation:survivors", severity=Severity.WARN,
                    message=f"{mutation.survived} mutant(s) survived — tests may be gutted"))

    judge_meta = None
    if use_judge:
        from .judge import review
        judge_meta = review(diff)  # advisory ONLY — logged as metadata, never signed

    from .core.detector.tests_integrity import apply_policy
    findings = apply_policy(findings, strict=strict_integrity)
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
                      mutation=mutation, coverage_baseline=coverage_baseline, judge=judge_meta,
                      integrity_policy=integrity_policy)

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


def _staged_python_env(root: str, snapshot: str, directory: str) -> dict[str, str]:
    """Prefer the index tree and discard inherited worktree/editable import paths."""
    bootstrap = Path(directory) / "python-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "sitecustomize.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"source = Path({root!r}).resolve()\n"
        "prefix = Path(sys.prefix).resolve()\n"
        "sys.path[:] = [p for p in sys.path if not ("
        "Path(p).resolve().is_relative_to(source) and "
        "not Path(p).resolve().is_relative_to(prefix))]\n"
        "def maps_source(module):\n"
        "    paths = list(getattr(module, 'MAPPING', {}).values())\n"
        "    paths.extend(p for values in getattr(module, 'NAMESPACES', {}).values() "
        "for p in values)\n"
        "    return any(Path(p).resolve().is_relative_to(source) for p in paths)\n"
        "def points_to_source(finder):\n"
        "    module = sys.modules.get(getattr(finder, '__module__', ''))\n"
        "    return (getattr(module, '__name__', '').startswith('__editable__') "
        "and maps_source(module))\n"
        "own_placeholders = {getattr(module, 'PATH_PLACEHOLDER', None) "
        "for module in list(sys.modules.values()) "
        "if getattr(module, '__name__', '').startswith('__editable__') "
        "and maps_source(module)}\n"
        "own_placeholders.discard(None)\n"
        "sys.path[:] = [p for p in sys.path if p not in own_placeholders]\n"
        "for p in own_placeholders: sys.path_importer_cache.pop(p, None)\n"
        "sys.meta_path[:] = [finder for finder in sys.meta_path "
        "if not points_to_source(finder)]\n"
        "sys.path_hooks[:] = [hook for hook in sys.path_hooks "
        "if not points_to_source(hook)]\n",
        encoding="utf-8",
    )
    return {"PYTHONPATH": os.pathsep.join((str(bootstrap), str(Path(snapshot) / "src"), snapshot)),
            "PYTHONNOUSERSITE": "1"}


def _prepare_staged_dependencies(root: str, snapshot: str, languages: set[str]) -> None:
    """Bring only ignored installed JS packages into the isolated index tree."""
    from .core.gitdiff import _git

    snapshot_root = Path(snapshot).resolve()
    for parent, dirs, files in os.walk(snapshot, followlinks=False):
        for name in (*dirs, *files):
            path = Path(parent) / name
            if not path.resolve().is_relative_to(snapshot_root):
                raise ValueError(f"staged snapshot link escapes snapshot: {path}")
    if not languages.intersection({"js", "ts"}):
        return
    packages = Path(root) / "node_modules"
    if (not packages.is_dir() or packages.is_symlink()
            or not packages.resolve().is_relative_to(Path(root).resolve())
            or packages.resolve() == Path(root).resolve()):
        raise ValueError("staged JS tests require installed, ignored node_modules")
    try:
        _git(root, "check-ignore", "--quiet", "--", "node_modules")
    except RuntimeError as exc:
        raise ValueError("staged JS tests require ignored node_modules") from exc
    package_root = packages.resolve()
    for parent, dirs, files in os.walk(packages, followlinks=False):
        for name in (*dirs, *files):
            path = Path(parent) / name
            if not path.resolve().is_relative_to(package_root):
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
