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
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from .types import Diff, Finding, MutationResult, Severity, TestResult, Verdict

DEFAULT_DB = ".proofofwork/log.db"


def _changeset_sha(diff) -> str:
    h = hashlib.sha256()
    h.update(diff.base_commit.encode("utf-8", "surrogateescape"))
    h.update(b"\0")
    for f in diff.files:
        h.update(f.old_path.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        h.update(f.status.encode())
        h.update(b"\0")
        h.update(f.path.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        for line in f.added:
            h.update(b"+" + line.encode("utf-8", "replace") + b"\n")
        for line in f.removed:
            h.update(b"-" + line.encode("utf-8", "replace") + b"\n")
    return h.hexdigest()


def _capture_coverage_baseline(root: str, diff: Diff, base_ref: str,
                               staged: bool) -> tuple[bool, float | None, dict | None]:
    """Snapshot policy inputs before any candidate-controlled code can execute.

    Ordinary HEAD is the documented trusted-local mode. Explicit bases and staged
    HEAD use the collector's immutable commit. Staged metadata is a fallback only
    when that commit has no baseline; never re-resolve HEAD after this decision.
    Empty commits retain local reads for synthetic Diff/non-Git test callers.
    """
    from .core.detector.coverage_delta import (
        baseline_exists,
        baseline_path,
        read_baseline_metadata,
        valid_coverage,
    )

    baseline_base = diff.base_commit if staged or base_ref != "HEAD" else None
    existing = False
    metadata = None
    if baseline_base:
        existing = baseline_exists(root, base_ref=baseline_base)
        metadata = read_baseline_metadata(root, base_ref=baseline_base) if existing else None
    if not baseline_base or (staged and not existing):
        existing = os.path.lexists(baseline_path(root, staged=staged))
        metadata = read_baseline_metadata(root, staged=staged, local_only=True) if existing else None
    if not baseline_base and not staged and not existing and diff.base_commit:
        # A deleted committed floor is not permission to opt out in local HEAD mode.
        # Keep the local value missing: required mode must restore/review the floor.
        existing = baseline_exists(root, base_ref=diff.base_commit)
    value = metadata.get("coverage") if metadata is not None else None
    return existing, float(value) if valid_coverage(value) else None, metadata


def check(root: str = ".", base_ref: str = "HEAD", *, staged: bool = False,
          run_tests: bool = True, run_mutation: bool = False, use_judge: bool = False,
          update_baseline: bool = False, db_path: str | None = None,
          coverage_drop_threshold: float = 2.0,
          extra_findings: list[Finding] | None = None,
          suite_base: str | None = None, coverage_policy: str = "required",
          integrity_policy: str = "strict", sandbox_kind: str = "local",
          sandbox_image: str | None = None) -> Verdict:
    """Run the full gate against a changeset and return a fact-based Verdict."""
    from .core.detector import ALL_CHECKS
    from .core.gitdiff import _git, collect_diff

    root = os.path.abspath(root)
    if integrity_policy not in ("strict", "advisory"):
        detail = "integrity policy must be 'strict' or 'advisory'"
        return Verdict(passed=False, reasons=[f"BLOCK integrity-policy-invalid: {detail}"],
                       findings=[Finding("integrity-policy-invalid", Severity.BLOCK, detail)],
                       coverage_policy=coverage_policy, integrity_policy=integrity_policy)
    if sandbox_kind != "local" or sandbox_image is not None:
        detail = ("Docker backend is experimental and not integrated with gate runners; "
                  "isolated execution is unavailable; refusing local fallback")
        return Verdict(passed=False, reasons=[f"BLOCK isolation-unavailable: {detail}"],
                       findings=[Finding("isolation-unavailable", Severity.BLOCK, detail)],
                       coverage_policy=coverage_policy, integrity_policy=integrity_policy,
                       execution_mode="unavailable")
    execution_mode = "trusted-local" if run_tests else "detector-only"
    coverage_status = "not-verified" if run_tests else "not-run"
    if coverage_policy not in ("required", "test-only"):
        detail = "coverage policy must be 'required' or 'test-only'"
        return Verdict(passed=False, reasons=[f"BLOCK coverage-policy-invalid: {detail}"],
                       findings=[Finding("coverage-policy-invalid", Severity.BLOCK, detail)],
                       coverage_policy=coverage_policy, integrity_policy=integrity_policy,
                       execution_mode=execution_mode, coverage_status=coverage_status)

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
                       coverage_policy=coverage_policy, integrity_policy=integrity_policy,
                       execution_mode=execution_mode, coverage_status=coverage_status)

    findings: list[Finding] = list(extra_findings or ())
    tests = TestResult()
    existing_baseline = False
    coverage_baseline = None
    baseline_metadata = None
    execution_allowed = True
    bootstrap_coverage = None
    try:
        existing_baseline, coverage_baseline, baseline_metadata = _capture_coverage_baseline(
            root, diff, base_ref, staged)
    except Exception as exc:  # noqa: BLE001 - policy capture failures cannot permit an opt-out
        execution_allowed = False
        findings.append(Finding("coverage-baseline-error", Severity.BLOCK, str(exc)))
    if coverage_policy == "test-only":
        findings.append(Finding("coverage-opt-out", Severity.INFO,
                                "explicit test-only policy: coverage NOT verified"))
        if existing_baseline:
            execution_allowed = False
            findings.append(Finding("coverage-policy-bypass", Severity.BLOCK,
                                    "test-only cannot bypass an existing trusted coverage baseline"))
        if update_baseline:
            execution_allowed = False
            findings.append(Finding("baseline-bootstrap-error", Severity.BLOCK,
                                    "test-only cannot bootstrap a coverage baseline"))
    if update_baseline and base_ref != "HEAD":
        execution_allowed = False
        findings.append(Finding("baseline-bootstrap-error", Severity.BLOCK,
                                "bootstrap is local-only; explicit-base comparisons require a reviewed committed floor"))
    if update_baseline and not run_tests:
        findings.append(Finding("baseline-bootstrap-error", Severity.BLOCK,
                                "--update-baseline requires a successful test run"))

    # Freeze configured policy and its secondary baseline restrictions before detectors.
    configured = suite_base is not None
    suites = None
    commit = ""
    try:
        from .core.suites import configured_mode
        configured = configured or configured_mode(
            root, staged=staged,
            changed_paths=[path for f in diff.files for path in (f.path, f.old_path) if path])
    except Exception as exc:  # noqa: BLE001 - config discovery failures cannot select fallback
        configured = True
        findings.append(Finding("suite-config-error", Severity.BLOCK, str(exc)))
    if configured:
        tests = TestResult(framework="configured", evidence="exit-code")
        try:
            from .core.suites import check_coverage_policy, load_suites
            if not suite_base:
                raise ValueError("configured suites require explicit --suite-base REF")
            commit, reviewed_suites = load_suites(root, suite_base)
            tests.suite_base = commit
            tests.required_suites = [suite["id"] for suite in reviewed_suites]
            if existing_baseline:
                raise ValueError("trusted coverage baseline exists; configured coverage is unsupported")
            check_coverage_policy(root, commit, base_ref=diff.base_commit or base_ref,
                                  staged=staged, update_baseline=update_baseline)
            if coverage_policy != "test-only":
                raise ValueError("configured exit-code suites require explicit coverage_policy='test-only'; "
                                 "required coverage is unsupported")
            if not run_tests:
                raise ValueError("all configured suites are required; --no-tests is unsupported")
            suites = reviewed_suites
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - fail closed
            findings.append(Finding("suite-config-error", Severity.BLOCK, str(exc)))

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

    if configured and suites is not None and execution_allowed:
        try:
            tests = _run_configured_tests(root, suites, commit, staged=staged)
            findings.append(Finding(
                "configured-evidence-scope", Severity.INFO,
                "reviewed commands provide exit-code test-execution evidence only; "
                "coverage and whole-candidate/launcher attestation are unsupported"))
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - fail closed
            findings.append(Finding("suite-config-error", Severity.BLOCK, str(exc)))
    if run_tests and not configured and execution_allowed:
        from .core.detector.coverage_delta import (
            METRIC,
            baseline_path,
            coverage_findings,
            coverage_identity,
            valid_coverage,
            write_baseline,
        )
        from .core.runner import run_tests as _run
        from .core.sandbox import get_sandbox

        try:
            coverage_args = {} if coverage_policy == "required" else {"collect_coverage": False}
            if staged:
                with _index_snapshot(root) as (snapshot, directory):
                    _prepare_staged_dependencies(root, snapshot, diff.languages())
                    python_env = (_staged_python_env(root, snapshot, directory)
                                  if "python" in diff.languages() else None)
                    tests = _run(get_sandbox("local"), snapshot, diff.languages(),
                                 **({"python_env": python_env} if python_env else {}), **coverage_args)
            else:
                tests = _run(get_sandbox("local"), root, diff.languages(), **coverage_args)
            if coverage_policy == "required":
                values = [tests.coverage]
                if "+" in tests.framework:
                    values.append(tests.js_coverage)
                complete = (tests.ran and tests.passed is True and coverage_identity(tests)
                            and all(valid_coverage(value) for value in values))
                if existing_baseline and coverage_baseline is None:
                    findings.append(Finding("coverage-baseline-invalid", Severity.BLOCK,
                                            "existing coverage baseline is malformed or invalid"))
                elif existing_baseline and (baseline_metadata.get("metric") != METRIC
                                             or baseline_metadata.get("languages") != coverage_identity(tests)):
                    findings.append(Finding("coverage-incomparable", Severity.BLOCK,
                                            "trusted baseline metric/languages differ or legacy identity is missing; "
                                            "remeasure on a reviewed tree and explicitly migrate the baseline"))
                else:
                    # Only complete passing instrumentation may suppress missing-baseline
                    # during explicit bootstrap. The temporary floor is not yet trusted.
                    if update_baseline and not existing_baseline and complete:
                        bootstrap_coverage = min(values)
                    findings.extend(coverage_findings(
                        tests, bootstrap_coverage if bootstrap_coverage is not None else coverage_baseline,
                        threshold=coverage_drop_threshold, require_coverage=True))
                if update_baseline:
                    if existing_baseline:
                        raise ValueError("coverage baseline already exists; --update-baseline is bootstrap-only")
                    if not complete:
                        raise ValueError("--update-baseline requires passing tests with all coverage metrics")
        except Exception as exc:  # noqa: BLE001 - execution and baseline errors fail closed
            findings.append(Finding("test-execution-error", Severity.BLOCK, str(exc)))
    if coverage_policy == "test-only":
        # Opt-out evidence must never retain apparently verified measurement numbers.
        tests = replace(tests, coverage=None, js_coverage=None)

    mutation = MutationResult()
    if run_mutation:
        if not execution_allowed:
            findings.append(Finding("mutation-execution-suppressed", Severity.BLOCK,
                                    "mutation cannot run when execution policy suppresses candidate commands"))
        elif staged:
            findings.append(Finding("mutation-staged-unavailable", Severity.BLOCK,
                                    "staged mutation cannot run against the index snapshot"))
        elif not run_tests:
            findings.append(Finding("mutation-tests-required", Severity.BLOCK,
                                    "detector-only checks cannot execute mutation; omit --no-tests"))
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

    from .core.detector.integrity_policy import apply_integrity_policy
    findings = apply_integrity_policy(findings, strict=integrity_policy == "strict")
    if integrity_policy == "advisory":
        findings.append(Finding("integrity-advisory", Severity.INFO,
                                "explicit advisory integrity policy; not a human override or approval"))
    passed, reasons = _decide(findings, tests, require_tests=run_tests)
    if integrity_policy == "advisory":
        reasons.append("advisory integrity policy: high-risk warnings not escalated; no human approval claimed")
    bootstrapped = None
    if passed and update_baseline:
        try:
            identity = coverage_identity(tests)
            write_baseline(root, bootstrap_coverage, staged=staged, identity=identity)
            path = baseline_path(root, staged=staged)
            payload = {"coverage": bootstrap_coverage, "metric": METRIC, "languages": identity}
            bootstrapped = (path, os.stat(path).st_ino, payload)
            coverage_baseline = bootstrap_coverage
        except Exception as exc:  # noqa: BLE001 - bootstrap failures must fail closed
            findings.append(Finding("baseline-bootstrap-error", Severity.BLOCK, str(exc)))
            passed, reasons = _decide(findings, tests, require_tests=run_tests)
    if coverage_policy == "test-only" and run_tests:
        reasons.append("test-only: coverage NOT verified; no baseline comparison claimed")
    if passed and run_tests and coverage_policy == "required" and not configured:
        coverage_status = "verified"
    verdict = Verdict(passed=passed, reasons=reasons, findings=findings, tests=tests,
                      mutation=mutation, coverage_baseline=coverage_baseline, judge=judge_meta,
                      coverage_policy=coverage_policy, integrity_policy=integrity_policy,
                      execution_mode=execution_mode, coverage_status=coverage_status)

    try:  # a passing signed verdict requires a durable log record
        from .log import build_envelope, record
        env = build_envelope(subject=_changeset_sha(diff), verdict=verdict)
        verdict.entry_hash = record(env, db_path)
    except Exception as e:  # noqa: BLE001 - never claim a signed pass without its log
        if bootstrapped is not None:
            # Do not keep a newly bootstrapped baseline after a failed gate log.
            # Only remove our own unchanged file, never a concurrently replaced one.
            path, inode, payload = bootstrapped
            try:
                with open(path, encoding="utf-8") as f:
                    unchanged = (os.fstat(f.fileno()).st_ino == inode
                                 and json.load(f) == payload)
                if unchanged:
                    os.unlink(path)
            except (OSError, ValueError):
                pass
        verdict.passed = False
        verdict.coverage_status = "not-verified" if run_tests else "not-run"
        verdict.entry_hash = ""
        verdict.reasons.append(f"BLOCK log-unavailable: {e}")

    return verdict


def _run_configured_tests(root: str, suites: list[dict], commit: str, *,
                          staged: bool) -> TestResult:
    from .core.sandbox import get_sandbox
    from .core.suites import run_suites

    if not staged:
        return run_suites(get_sandbox("local"), root, suites, commit, source_root=root)
    with _index_snapshot(root) as (snapshot, directory):
        # Validate links, but do not require/copy a root node_modules for arbitrary
        # commands. Their explicit prerequisites must exist in the index.
        _prepare_staged_dependencies(root, snapshot, set())
        env = _staged_python_env(root, snapshot, directory)
        return run_suites(get_sandbox("local"), snapshot, suites, commit,
                          source_root=root, env=env)


@contextmanager
def _index_snapshot(root: str):
    """Materialize the caller's index without changing it; clean only our worktree."""
    from .core.gitdiff import _git

    with tempfile.TemporaryDirectory(prefix="proofofwork-index-") as directory:
        snapshot = os.path.join(directory, "tree")
        _git(root, "worktree", "add", "--detach", "--no-checkout", snapshot, "HEAD")
        try:
            _git(root, "checkout-index", "--all", "--prefix=" + snapshot + os.sep)
            yield snapshot, directory
        finally:
            try:
                _git(root, "worktree", "remove", "--force", snapshot)
            except RuntimeError as remove_error:
                shutil.rmtree(snapshot, ignore_errors=True)
                try:
                    _git(root, "worktree", "remove", "--force", snapshot)
                except RuntimeError:
                    raise remove_error


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
    if passed and tests.framework == "configured":
        reasons.append("all required configured suites passed (exit-code evidence only; "
                       "not full candidate attestation)")
    if passed and not reasons:
        if any(f.rule == "coverage-baseline-missing" for f in findings):
            reasons.append("tests passed; coverage baseline unavailable (coverage not enforced)")
        else:
            reasons.append("no cheat signals; facts check out")
    return passed, reasons
