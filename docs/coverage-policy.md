# Required coverage policy — issue #26

The engine, source-tree CLI and composite Action use `coverage_policy='required'|'test-only'`,
default `required`, and record coverage policy/status, integrity policy and execution mode
in JSON and the signed predicate. CLI flags are `--coverage-policy` and `--integrity-policy`;
Action inputs are `coverage-policy` and `integrity-policy`. Strict integrity is enabled
by default. Protect these selections in trusted CI configuration, not candidate inputs.
These changes are unreleased. Configured-suite completion (#27) and package-manager
provisioning (#31) stay separate; Docker gate routing is unavailable.

## Contracts and backward compatibility

- `coverage_findings(tests, baseline, *, threshold=2.0, require_coverage=False)` retains
  legacy scalar behavior by default, including informational missing instrumentation.
  The production engine explicitly passes `require_coverage=True`. Required mode
  blocks missing/invalid coverage, unknown measurement identity, invalid/missing
  baselines and drops in either mixed measurement. Percentages must be finite 0–100;
  required thresholds must also be finite and bounded. Tests must independently pass.
- `run_tests(..., python_env=None, collect_coverage=True)` measures by default. Explicit
  `collect_coverage=False` runs plain pytest and supplies Vitest
  `--coverage.enabled=false` / Jest `--coverage=false`, overriding configured coverage.
  Raw evidence says coverage **NOT verified**. This is not permission to opt out of a
  trusted baseline; the engine blocks that opt-out before launching suites.
- `coverage_identity(tests)` returns sorted `['python']`, `['js']` or `['js','python']`
  for supported pytest/Jest/Vitest measurements. Unknown/configured commands return
  no comparable identity. `METRIC` is `project-lines-v1`.
- `read_baseline_metadata(...)` exposes metric/language identity alongside the scalar.
  `read_baseline(...)` retains legacy scalar reading. `write_baseline(..., identity=None)`
  retains legacy scalar writing; explicitly supply `identity=coverage_identity(tests)`
  to write `metric`/`languages`. Creation remains exclusive: even malformed existing
  files cannot be silently replaced.

A new reviewed baseline looks like:

```json
{"coverage":90,"metric":"project-lines-v1","languages":["python"]}
```

Mixed suites keep separate Python/JS measurements, not an average. The scalar floor
must be compared with each required family; bootstrap may record the minimum only
when all required measurements are present/valid and all other checks pass. This is
not independent per-suite history or changed-package suite selection.

## Invocation freshness and status

Python uses a private temporary directory for data, report, pytest execution witness
and coverage configuration. The runner sets its own `COVERAGE_FILE`, clears optional
startup measurement variables, supplies an explicit empty rcfile and measures root
project lines (`--source ROOT`), not candidate-configured branches/omissions. The
metric includes test code and coverage.py default exclusions. Old root `.coverage`
and `coverage.json` are never read. Missing instrumentation, failed/timed-out JSON
production or malformed/empty/inconsistent/non-finite reports yield no coverage.

Runner-owned pytest hooks require an actual non-skipped test call and reject
collection-only configuration. Zero-exit help/collection/empty runs are not passes.
JS uses installed tooling via `npx --no-install`, explicit `json-summary` reporters
and fresh private absolute coverage/execution paths, never root `coverage/` artifacts.
Positive completed-test counts, `success=true`, zero failed tests/suites and a
successful non-timed-out command are all needed for a JS pass. A configured Jest
`testFailureExitCode:0` cannot excuse failure JSON. Failed command output cannot supply
an accepted coverage measurement. Unsupported reporter shapes fail closed.

This prevents accidental/stale artifact reuse, not forged evidence from hostile native
code. Dependencies, tests, plugins, configuration, PATH and launcher remain trusted;
they can access the host, inspect argv/environment or forge reports. JS configuration
still selects covered source. There is no hostile-code isolation or tool attestation.

## Baseline authority and migration

The engine captures baseline **existence, value and metric/language metadata before
any detectors or suites execute**. Later edits/deletions cannot change that invocation's
comparison or enable opt-out. `Diff.base_commit` must contain the immutable commit
already resolved by the Git collector; the engine never re-resolves a branch after
execution. Empty commit values are for synthetic/non-Git Diff callers only, which
retain local reads for compatibility. A real collector leaving that field empty is
an integration defect; real-Git regressions deliberately expose it.

| Invocation | Trusted source captured before execution |
|---|---|
| Ordinary default `HEAD` | Local `.proofofwork/baseline.json`, trusted-local mode |
| Explicit non-HEAD base | Baseline blob from `diff.base_commit`; candidate files ignored |
| Staged | Baseline blob from `diff.base_commit`; staged/worktree edits ignored |
| Staged, pinned commit has no baseline | Local Git metadata `proofofwork/baseline.json` |

Trusted-local mode preserves legitimate local bootstrap: on a reviewed tree with no
baseline, `--update-baseline` may propose an identity-bearing local floor. It is not
PR/hostile-code attestation: a user controlling the local baseline can change the floor
between invocations. Explicit-base checks instead require the reviewed committed
floor. Deleting a committed baseline in ordinary HEAD mode cannot enable test-only;
required mode must restore/review the missing local file rather than silently ignore
that committed existence. Staged metadata fallback is read directly, without looking
up a potentially moved HEAD again.

Existing malformed baselines block. Existing legacy scalar-only, wrong-metric or
wrong-language baselines block as `coverage-incomparable`, with reviewed remeasurement
and migration required. The engine does not automatically relabel old scalar numbers.
Required mode calls the strict detector after checking baseline identity.

Bootstrap is rejected when `--base` selects an explicit non-HEAD comparison; missing
reviewed floors cannot be certified using candidate measurements. Local bootstrap
suppresses missing-baseline only when tests actually execute and
pass, the measurement identity is supported, and **all** required metrics are finite
valid percentages. Mixed bootstrap records the minimum, never an average. No existing
baseline or other blocker may be present. Writes are exclusive and include metric and
languages. If logging fails, rollback removes only the unchanged identity-bearing file
created by this invocation, not a replaced file or one with changed metadata. Review
and commit the proposal separately; never trust a candidate's first proposed floor in
an explicit-base comparison.

## Evidence modes

- `required`: the default, with `coverage_status='verified'` only for a passing gate
  with complete passing measurement and a comparable trusted/explicitly bootstrapped
  floor. Failed gates use `not-verified`.
- `test-only`: must be explicitly requested through a trusted launcher and cannot
  bypass any applicable existing trusted baseline, even malformed/legacy metadata.
  Bootstrap is unsupported. The runner receives `collect_coverage=False`; opt-out is
  informational and explicit in reasons, JSON and the signed predicate. Measurements
  are cleared and status is `not-verified`, even when every test passes.
- `run_tests=False`: remains explicitly detector-only, with status `not-run`, no test
  pass or numeric coverage claim, and no baseline bootstrap. It is not a fully verified
  pass. Existing configured suites still reject `--no-tests` because their required
  commands cannot be skipped.

Configured exit-only commands require explicit `test-only`; they cannot satisfy
required coverage. All existing configured-suite secondary-base and local-baseline
restrictions are retained and captured before detectors, not weakened by a second
`suite_base`.

`Verdict.as_dict()` and the signed predicate include `coverage_policy`,
`integrity_policy`, `execution_mode` and `coverage_status`. Execution is `trusted-local`
for suite requests or `detector-only` without tests. Detector-only mode cannot launch
mutation, and rejected execution policies suppress mutation as well as suites.
Requested Docker gate execution is blocked before Git or candidate commands, without
local fallback. Canonical encoding of existing signed entries is unchanged.

Do not copy candidate dependencies to a frozen base with changed dependency inputs.
The manager also owns staged/frozen dependency copying: preserve validated relative
internal `.bin` symlinks when relocating installed JS tooling, reject escaping/absolute
links and junctions, and verify with real provider fixtures. This slice does not edit
that engine path or attempt full package-manager support.

## Tooling, CI and negative proof

The dev extra pins `coverage==7.13.5`, upstream [coverage.py](https://github.com/coveragepy/coveragepy).
The lock records PyPI distribution hashes; unrelated locked versions are preserved.
Install with `uv sync --frozen --extra dev`. The runner does not install coverage.

Self-check runs a real Python fixture with a fixed reviewed 100% floor: a covered
positive tree passes comparison, then added uncovered source still passes tests but
blocks coverage. Candidate `.coveragerc` cannot change the metric. It does not read or
trust a candidate-proposed baseline and does not mutate the repository baseline.
This fixture proves the service contract separately from real-Git engine regressions.

```bash
uv run --frozen --extra dev python -m pytest -q tests/test_runner.py tests/test_coverage_policy.py tests/test_gate_hardening.py tests/test_production_policies.py tests/test_log.py
uv run --frozen --extra dev python -m pytest -q tests/test_coverage_policy.py -k actual_baseline_comparison
```

Self-check uses explicit frozen-base measurement when the immutable base has no
reviewed baseline, under the dependency compatibility restrictions below. Candidate
bootstrap output cannot authorize this comparison. Modeled report regressions remain
in the fast suite; a separate CI job validates installed Jest/Vitest providers.

## Explicit frozen-base measurement

`--measure-base-coverage` (Action input `measure-base-coverage: true`) can measure
an immutable comparison commit when no applicable trusted baseline exists. The
engine runs that base before candidate detectors/tests, requires actual passing
execution and complete valid coverage, then compares candidate coverage to that
floor. Fresh mixed suites compare each language against its own base measurement,
so low JS coverage cannot hide a Python regression (or vice versa). JSON and signed
evidence record the per-language `coverage_baseline_measurements` map and
`coverage_baseline_source: measured:<commit>`; no baseline
file is created. Existing invalid, legacy or incomparable baselines still block;
this option never replaces them. Default checks retain their existing behavior.

The option cannot combine with staged checks, bootstrap, detector-only, test-only
or configured exit-code suites. Its native execution is trusted-local, not hostile
code isolation. The base uses the launcher's installed Python dependencies and
copied installed JS dependencies only when recognized dependency inputs match the
frozen tree byte-for-byte, including uncommitted changes and nested manifests.
Python reuse supports static root `pyproject.toml` plus a matching `uv.lock`, or
fixtures without declared dependency manifests. Supported metadata includes PEP621
project/optional dependencies, PEP735 groups and registry build requirements. Group
includes are validated recursively; local helpers and missing/cyclic/ambiguous
includes fail closed. Lock sources must be HTTPS registries or the project's own
root editable package; referenced lock artifacts must use HTTPS. Other editable,
directory, path or local artifact entries require independent base provisioning.
Requirements files, nested manifests, standalone `uv.toml`, custom manager settings,
in-tree build backends and dynamic dependency declarations are unsupported in this
optional mode. Changed inputs fail closed: provision the reviewed base independently
and review a committed baseline before comparing that candidate.
This check does not attest installed packages against their lockfile; launchers
must provision their trusted environment from that lock with artifact verification.

JS reuse additionally requires a matching root lockfile and rejects workspaces,
nested packages and local-file dependency references. Copied internal relative `.bin` symlinks retain their targets;
absolute links, external links and junctions are refused. Python imports prioritize
the frozen tree and exclude candidate editable paths. Base tests that fail, do not
execute, or cannot produce valid comparable coverage block the gate. Owned temporary
worktrees are removed on success or failure without pruning unrelated worktrees.

Dogfood opts into this explicit missing-baseline mode. It still fails closed on
changed dependency inputs; a candidate baseline or test-only opt-out must never be
used to turn that failure green. The separate `real-js-coverage` CI job installs
Jest 30.5.2 and Vitest/coverage-v8 5.0.4 in disposable fixture repositories and tests
actual coverage/report flags, empty/skipped/failing runs, frozen-base execution,
and dependency mismatch rejection. Run it locally with Node 24/npm and:

```bash
uv run --frozen --extra dev python -m pytest -q integration/test_js_coverage.py
```

The allowed tool tables for Python reuse are pytest, Ruff, coverage, Black, isort,
mypy, pyright, and static Hatch wheel-package selection. Other custom tool layouts
require independent base provisioning; no pip directive parser or alternative
manager inference is attempted. This bounded static-input check does not attest
arbitrary build code or installed artifacts. Default stored-baseline checks are
unchanged.

Changes to the root `pyproject.toml` or `uv.lock` require the Python suite even
when no Python source file changed. Removal and renaming retain that requirement,
and mixed Python/JS changes still require both suites. This execution selection
does not classify manifests as Python source for detectors or authorize dependency
reuse: changed dependency inputs still require an independently reviewed committed
coverage floor instead of measuring the base with candidate dependencies. Nested
manifests and other dependency managers are not inferred by this root-only rule.

## Reviewing this repository's committed floor

The adopted [baseline](../.proofofwork/baseline.json) is exactly
`92.56814921090387%`: 6,452 covered lines out of 6,970 statements, measured from
reviewed main commit `993580776f53b9bdc21fd34fd75c953264ae386e`. The independent run
passed 927 tests with 7 platform-specific skips. Its unmodified
[provenance record](coverage-baseline-provenance.json),
[coverage report](../reports/coverage-baseline/coverage.json),
[execution witness](../reports/coverage-baseline/executed.json), and
[test output](../reports/coverage-baseline/command-3.stdout.txt) record the source
and evidence used for review. Artifact hashes in the provenance retain their
original filenames; the binary coverage database and empty command logs are not
committed. Absolute paths identify the original independent measurement checkout.

Dependency updates must compare against a floor measured independently from an
already reviewed `main` commit. Provision that exact commit in a separate clean
checkout with its own Python 3.12 environment using `uv sync --frozen --extra dev`.
Do not reuse the dependency update's environment, editable installation, or Python
path. Run the source commit's real Python coverage runner, require passing test
execution and a valid project-line coverage report, and retain its report and
execution witness.

Review the measurement's source commit and tree, lockfile digest, installed Python,
pytest and coverage versions, executed commands, test results, line totals and
report digest before adopting the floor. The provenance record is review evidence;
the engine enforces the baseline's `coverage`, `metric` and `languages` fields, not
environment attestation. An instrumentation upgrade needs its own comparability
review; a lower candidate measurement cannot authorize replacing the floor.

The adoption change must pass the existing required-coverage self-check against
its immutable base. When that base has no committed floor, explicit base measurement
provides the comparison; candidate baseline bytes never authorize the adoption
change's own result. After adoption merges, dependency updates read the reviewed
floor from their immutable base and execute the required Python suite. Keep
dependency-mismatch rejection, strict integrity checks and coverage thresholds
unchanged throughout this sequence.
