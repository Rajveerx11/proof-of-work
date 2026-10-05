# Required coverage policy (issue #26)

`check` now defaults to `--coverage-policy required`, including Python-only,
JS/TS-only and mixed changes. A successful test exit alone is insufficient:
all executed language families need fresh, nonempty, finite 0–100 line coverage
and a comparable trusted baseline. Missing instrumentation, failed JSON generation,
missing/malformed reports, or a missing/legacy/incomparable baseline block the gate.
`--no-tests` remains detector-only and does not claim coverage verification.
Python's runner-owned pytest hooks refuse collection-only mode from configuration
or PYTEST_ADDOPTS and require a non-skipped test call report before recognizing
execution. Help/discovery-only exit zero without that witness is not a test pass.
The same guard applies to frozen base, missing-instrumentation and test-only runs;
it does not rely on English stdout. This is a standard-configuration boundary,
not authenticated evidence against hostile native plugins/tests.

## Instrumentation and freshness

Install Python instrumentation explicitly in the test interpreter. The project dev
extra pins `coverage==7.13.5`; `uv sync --frozen --extra dev` provisions dogfood CI.
The gate does not install instrumentation on demand. Python uses a new private temp
directory for data, configuration and JSON, ignores candidate coverage config, and
measures project lines (`--source ROOT`, no branch metric). A failed report command
cannot authorize even apparently valid JSON. Temporary outputs are removed; an old
root `.coverage` or `coverage.json` is never read. Includes test code and default
coverage.py exclusions, so this is not a production-code-only metric.

JS requires an already installed Jest or Vitest (and its matching coverage provider).
`npx --no-install` does not provision missing tools. Both runners receive explicit
`json-summary` reporter and private absolute output directory arguments. Root
`coverage/coverage-summary.json` is never used. Summary counts must be nonempty,
consistent with their finite line percentage; unknown tool/report shapes block.
Candidate JS runner configuration still determines which source files are measured.

## Baseline authority and bootstrap

A committed `.proofofwork/baseline.json` contains one conservative percentage:

```json
{"coverage": 90.0, "metric": "project-lines-v1", "languages": ["python"]}
```

JS uses `["js"]`; mixed uses sorted `["js", "python"]`. This is **not** a per-suite
baseline: each mixed suite is checked against the same scalar floor; bootstrap
records the minimum of the complete measurements. The metric/language identity
prevents comparing Python to JS, branches to lines, or unidentified legacy data.
Current adoption ceiling: suite selection follows changed-file languages, while
baseline identity requires exact executed-family equality. A mixed stored baseline
therefore blocks a Python-only or JS-only diff as incomparable, even if that one
suite measures 100%. It never silently drops the trusted other family or measures
around this mismatch. Union suite discovery/configured suites are outside this slice.
The default drop allowance remains 2 percentage points. API callers may tighten it,
not weaken it or use NaN/infinite values.

The base is resolved once to an immutable commit before detectors/tests execute.
Baseline bytes are read from that commit, including default `HEAD` (staged checks
trust committed `HEAD`). Candidate worktree/index/commit baseline edits cannot
weaken this invocation's comparison. There is no caller-supplied percentage/file
substitute. Baseline changes become policy only after a trusted human reviews and
merges them; protect the gate and baseline in repository review policy.

On a reviewed tree without any baseline, an authorized maintainer may run:

```sh
proof-of-work check --update-baseline
```

This is bootstrap-only, requires all suites pass with valid coverage, refuses any
existing local or trusted baseline and any other blocking finding, and removes its
own new file if durable logging fails. Review and explicitly commit the generated
file (`git add -f .proofofwork/baseline.json`, because the directory is ignored).
Before that commit it is not trusted for a subsequent check. Staged bootstrap writes
Git metadata only; review/copy it into the tracked baseline explicitly.

For first-PR adoption when the frozen base has **no** baseline, explicitly opt in:

```sh
proof-of-work check --base BASE_COMMIT --measure-base-coverage --json
```

The gate measures BASE_COMMIT in an owned detached worktree using this invocation's
instrumentation, then compares fresh candidate measurements. Base Python imports
prefer its own tree/src and remove candidate editable/PYTHONPATH mappings. Ignored,
installed JS packages are copied using the existing snapshot checks. Base suite
failure, absent/empty/invalid measurement or different identity blocks; there is no
fallback around an existing malformed or incomparable baseline. This mode does
not write a baseline and cannot combine with staged or baseline-file bootstrap.
Measurements and source SHA are reported (`coverage_baseline_source=measured:SHA`)
and the validated scalar baseline is included in new signed predicates. Existing
signed envelopes/chains retain their historical bytes and still verify.
Dogfood opts into this mode explicitly and runs a real CLI fixture demonstrating
both a covered positive candidate and an uncovered negative candidate.

## Migration and trusted test-only projects

Old scalar-only baseline files are now incomparable. Do **not** just add identity to
old numbers: the previous Python metric could include external modules or configured
branches. On an independently reviewed trusted tree, measure the new metric, review
the proposed floor against the old protection, and commit the migration separately.
Automatic fallback/replacement is intentionally unavailable. Consumers with no
baseline must bootstrap explicitly; otherwise a formerly passing exit-only run now
fails with coverage diagnostics. No candidate may authorize its own migration.

For a trusted project intentionally using tests without coverage, its trusted launcher
may choose `--coverage-policy test-only` (Action input `coverage-policy: test-only`).
It is an **explicit opt-out**, not fully verified coverage. Python runs plain pytest
without probing/invoking coverage; Vitest/Jest omit all coverage arguments, so a
missing provider does not prevent ordinary execution. Tests and other detectors
still must pass. Human reasons, findings, JSON, and signed predicate record the opt-out
and `coverage_status=test-only`; it cannot bootstrap a coverage baseline. Do not let
an untrusted candidate choose this policy. Status `bootstrap` similarly identifies
initialization, not a completed trusted-baseline comparison.

## Reviewed tooling and limits

Coverage.py 7.13.5 provenance: PyPI project `coverage`, upstream
<https://github.com/coveragepy/coveragepy>, documentation
<https://coverage.readthedocs.io/en/7.13.5>. The downloaded pure-Python wheel's SHA256
was `34b02417cf070e173989b3db962f7ed56d2f644307b2cf9d5a0f258e13084a61`.
Scoped source review inspected console entry points, `__main__.py`, `jsonreport.py`,
and `control.process_startup`; the wheel's `a1_coverage.pth` activates optional
startup measurement from COVERAGE_PROCESS_START/CONFIG. The runner supplies empty
values for both and a private COVERAGE_FILE. No startup commands were executed from
review output. Platform wheel hashes are recorded by uv. Tests exercise actual
JSON production and failure paths. This is a scoped dependency review, not an audit
of all native extension/source code. The existing exact Ruff pin is unchanged;
regenerating the lock repairs its older 0.16.0 entry to declared 0.16.8.

Freshness prevents accidental/stale report reuse, **not malicious native execution**.
Tests run as local subprocesses, can inspect argv/environment/filesystem, and could
forge reports, change source selection or reach other files. Dependencies, executable
code/config, launcher, baseline and workflow require trusted review. Python module
shadowing and JS configuration are not authenticated instrumentation. Git metadata
and the local signing/log boundary are not a hostile-code sandbox. Per-suite baselines,
configured suites, OS isolation and broad offline execution are separate work. This
change does not establish full Neura verification or general candidate safety.
The unchanged Action mutation input is interpolated directly in shell source; do
not map untrusted candidate values into that inherited input. Hardening it is deferred.
JS command/provider failure regressions here use explicitly labeled subprocess
models, not installed real Jest/Vitest providers; Python/CLI regressions execute real
tools. Hosted checks and independent review must be reported separately.
