# Supervised production hardening: #26, #28, #29

This is **unreleased hardening work**, not a production-readiness certification or a release.
At the verification checkpoint, no commits, pushes, tags, PR publication, or issue closure
had been performed. Commit and push were subsequently requested by the operator; this
report preserves the implementation and verification checkpoint, not publication state.

## Management and boundaries

Three implementation agents followed the `security` and `code-structure` skills.
Work was divided into coverage/runner, isolation backend, and Git/test-integrity ownership.
The supervisor integrated shared engine, types, signed evidence, CLI and Action changes.
Independent read-only review found four defects, all repaired with regressions; the
follow-up review found no critical/high issue in its reviewed scope.

The user's initial dirty worktree was snapshotted at
`/tmp/pow-management-baseline-pvzbmvvl` before edits. Existing unrelated files were
left untouched. New changes are additional to that user state, not equivalent to the
whole diff against HEAD. Existing CRLF format is retained.

## Status and acceptance boundaries

| Issue | Implemented | Still required |
|---|---|---|
| #26 | Required coverage default; fresh private Python/JS reports and positive test-execution witnesses; finite/consistent report validation; frozen explicit-base authority; baseline capture before execution; metric/language identity checks; guarded local bootstrap; signed policy/status; explicit test-only cannot bypass a trusted baseline; pinned dev coverage tooling and positive/negative CI fixture | Reviewed baseline adoption/migration in the actual base branch, hosted self-check, real Jest/Vitest provider and staged dependency-layout validation, native Windows validation |
| #29 | Old/new rename/copy classification, unchanged-body discovery-loss detection, unusual Git paths, strict high-risk policy default, explicit advisory labels in JSON/signed evidence, real Git/pytest integration and learning regressions | Full-source assertion/lexical semantics and collection-aware verification; heuristic findings require review rather than accusations; advisory is not authorization or a human override |
| #28 | Experimental digest-pinned Linux Docker backend, no host binds/automatic pulls/env forwarding, bounded metadata-free snapshot, requested privilege/network/resource controls, fail-closed readiness, bounded client capture/disposal, modeled negative tests | Gate/runner integration, trusted image/toolchain and artifact transport, all-stage eval isolation, live containment/resource/cleanup tests, external crash recovery, supported-platform decision |

**Do not close #28 as complete.** No live container ran: WSL Docker integration/daemon
was unavailable. The gate rejects `--sandbox docker` before Git or host execution,
without a local fallback. CLI `eval run --untrusted` and library
`run_task(..., untrusted=True)` reject before execution. Local mode remains trusted-only.
Do not use it for hostile repos or agents.

Policy switches must belong to protected trusted CI, not candidate configuration.
The `v0.2.0` tag and published snapshots do not include these changes.

## Review repairs

1. Explicit non-HEAD base comparisons cannot bootstrap a floor from candidate coverage.
2. Detector-only checks cannot execute mutation; execution-policy suppression covers
   mutation as well as normal suites.
3. Docker snapshot traversal errors raise instead of silently omitting source subtrees.
4. Docker daemon `Warnings` must be present and empty/null; missing, malformed or
   nonempty warnings block preflight.

## Verification

Commands ran in `/mnt/c/proof-of-work` using the frozen dev environment.

| Exact command | Result |
|---|---|
| `uv run --frozen --extra dev python -m pytest -q` | PASS, exit 0: 604 passed, 5 skipped in 168.23s |
| `uv run --frozen --extra dev ruff check proofofwork tests` | PASS, exit 0: All checks passed |
| `uv lock --check` | PASS, exit 0: lock resolves without dependency drift |
| `uv run --frozen --extra dev python -m pytest -q tests/test_manager_integration.py tests/test_production_policies.py tests/test_integrity_integration.py tests/test_configured_suites.py tests/test_action.py tests/test_learn.py` | PASS, exit 0: 173 passed, 2 skipped |
| `uv run --frozen --no-sync python -m pytest -q tests/test_manager_integration.py tests/test_sandbox.py` | Independent follow-up PASS, exit 0: 96 passed |

Earlier integration failures included the missing collector `base_commit` connection,
unwired strict policy, outdated learning assumptions, stale policy expectations during
concurrent edits, and one missing explicit subprocess `check=False`. These were fixed;
the full-suite result above was obtained with stable implementation files.

Docker tests validate requested argv, readiness and client/snapshot failure handling,
not actual containment. Python integration tests execute reviewed temporary fixtures;
JS coverage/report regressions model provider outputs. Action Bash tests validate actual
argument forwarding and injection rejection, not a hosted consumer Action workflow.
The full suite's five skips and platform-specific limits remain unverified behavior.

After verification, restoring the original CRLF format of `uv.lock` and
`tests/test_configured_suites.py` changed no normalized content. Final checks:

- `uv run --frozen --extra dev python -m pytest -q tests/test_configured_suites.py tests/test_manager_integration.py`
  — PASS, exit 0: 97 passed, 2 skipped in 15.98s.
- `uv lock --check` — PASS, exit 0.
- `uv run --frozen --extra dev ruff check proofofwork tests` — PASS, exit 0.
- `git -c core.whitespace=cr-at-eol diff --check -- .github/workflows/self-check.yml README.md proofofwork pyproject.toml uv.lock tests/test_detector.py tests/test_gate_hardening.py tests/test_learn.py tests/test_runner.py`
  — PASS: no whitespace diagnostics.

## Follow-up order

1. Review and commit the coverage-floor migration separately into the trusted base;
   do not bootstrap from a candidate PR merely to make self-check pass.
2. Finish/review the existing #27 configured-suite slice and related #30/#31 coverage
   and JS toolchain requirements.
3. Provision an approved Docker worker/image and finish #28 integration/validation
   before supporting untrusted execution.
4. Run #33 realistic consumer Action workflows, then prepare #35 release evidence.
   Release/version/tag decisions and publication still require explicit authorization.

See [coverage policy](coverage-policy.md), [integrity policy](integrity-policy.md),
[experimental isolation](isolation.md), and [configured suites](configured-suites.md).
