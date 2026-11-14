# Agent integration and check JSON schema

This contract ships in the unreleased 0.3.0 source. It does not change v0.2.0.

## Trust inputs and read-only Git

An agent-facing caller must pass a reviewed **full commit SHA** to `--base`, never
`HEAD`. Ordinary `--base HEAD` preserves trusted-local behavior: it may read the
working-tree `.proofofwork/baseline.json`. An explicit SHA reads the baseline from
that commit, even when the candidate lowers, raises or deletes its copy. Freeze
the reviewed SHA outside candidate control before invoking the gate.

`--include-untracked` includes files returned by `git ls-files --others
--exclude-standard -z`. It is opt-in. Ignored files remain excluded. New files
receive the same language/test classification and detector passes as tracked files.
Collection reads raw bytes without filters, textconv, staging, or Git metadata
writes. Symlinks contribute their link text, not target contents. Unsupported file
types and unreadable inputs fail closed. `--staged` cannot combine with this flag:
staged checks continue to analyse only the index.

Stop candidate writers while checking. This flag does not freeze the whole
working tree or validate candidate-controlled ignore rules. The operator must
protect ignore policy if ignored files are relevant to verification.

For an externally isolated candidate with no committed coverage baseline:

```sh
proof-of-work check --root /candidate --base "$REVIEWED_BASE_SHA" \
  --include-untracked --suites-file /operator/suites.json \
  --coverage-policy test-only --db /operator-output/gate.db --json
```

The suites file uses the [configured-suite schema](configured-suites.md). It
overrides repository suite configuration and `--suite-base`. All required suites
still run. Missing/malformed external files fail closed, with no candidate fallback.
This mode reports exit-code evidence only and cannot bypass a committed coverage
floor. For required coverage, provision and commit a reviewed floor ahead of time,
omit `--suites-file`, and retain the default required coverage policy.

Base measurement (`--measure-base-coverage`) and staged execution still create
Git worktrees. They require writable Git metadata. Failure, including read-only
`.git`, produces a specific reason and exit 3. No local fallback or archive path
is provided. Unstaged checks with a pre-existing floor, and external exit-code
suites without a floor, do not need a worktree. Each Git command times out after
60 seconds. This is not an end-to-end gate deadline.

Neura must provide confinement, disable network access, and protect the installed
gate, suite definitions, base SHA, tool PATH, dependencies and signing/log state.
Use an operator-owned `--db` path. Candidate tests remain executable untrusted code.
argv validation is not a sandbox. The local backend is still labelled
`trusted-local`. That label does not attest Neura's external sandbox. Do not pass
`--judge` in an offline integration. No new networking is introduced here.

## Exit status

| Code | Meaning |
| --- | --- |
| 0 | A passing verdict was produced. Inspect evidence scope: `--no-tests` is detector-only. |
| 1 | The gate produced a failing verdict, such as a detector finding, test failure or rejected coverage policy. |
| 3 | The check could not complete: Git failure/timeout, unusable arguments, suite input error from either source, missing snapshot dependencies, failed Git snapshot setup, log failure or uncaught internal exception. |

For caught tool errors, JSON retains `passed: false` and adds a nonempty
`tool_error`. Existing findings and reasons are retained where available. Argument
parser errors write usage/error text to stderr without a JSON object and return 3.
`--help` remains exit 0. Other subcommands keep their existing exit contracts.
Treat every nonzero exit, missing/malformed JSON, signal termination or external
timeout as failure. A failure may contain partial evidence, not a usable pass.

## JSON schema version 1

`check --json` emits one object. All fields below are present. Consumers must
ignore unknown fields so additive updates remain compatible. Existing field
names and types are unchanged. `schema_version` versions this JSON contract.
`tool_version` identifies the installed distribution, not the candidate.

| Field | JSON type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | `1` |
| `tool_version` | string | Installed `proof-of-work-agent` version. `0.0.0+unknown` for an uninstalled source tree. |
| `base_commit` | string | Full resolved commit object ID used by the collector. Empty if collection did not complete. |
| `tool_error` | string | Empty for a completed verdict. Otherwise incomplete-check reason (exit 3). |
| `passed` | boolean | Gate decision. False on tool errors. |
| `reasons` | array of strings | Human-readable decision reasons. |
| `findings` | array of finding objects | Deterministic detector/policy results. |
| `tests` | test-result object | Execution and coverage evidence described below. |
| `mutation` | object | `ran` boolean, `survived` and `killed` integer or null, `tool` string. |
| `coverage_baseline` | number or null | Selected coverage floor. |
| `coverage_baseline_source` | string | Commit, `measured:<commit>`, `trusted-local`, or empty. |
| `coverage_baseline_measurements` | object | Language-family keys mapped to measured percentages. |
| `judge` | object or null | Optional advisory metadata. Does not decide `passed`. |
| `entry_hash` | string | Recorded log hash, or empty when unavailable. |
| `coverage_policy` | string | `required` or `test-only`. |
| `integrity_policy` | string | `strict` or `advisory`. |
| `execution_mode` | string | `trusted-local`, `detector-only` or `unavailable`. |
| `coverage_status` | string | `verified`, `not-verified` or `not-run`. |

A finding object has string fields `rule`, `message`, `file`, `evidence` and
`severity` (`block`, `warn`, `info`), plus integer `line` (0 when unavailable).

The test-result object has:

| Field | JSON type | Meaning |
| --- | --- | --- |
| `ran` | boolean | Whether a suite executed. |
| `passed` | boolean or null | Suite result, or unknown. |
| `coverage`, `js_coverage` | number or null | Percentage evidence when available. |
| `framework`, `raw` | string | Runner identity and bounded diagnostic output. |
| `suite_base` | string | Resolved reviewed suite commit, or empty for external suites/default runners. |
| `evidence` | string | `exit-code` for configured suites, otherwise empty. |
| `required_suites`, `executed_suites` | array of strings | Configured suite IDs. |
| `suites` | array of objects | Each has `id`, `outcome`, `raw` strings and `exit_code` integer or null. |

Configured outcomes include `passed`, `failed`, `timeout`, `unknown`, `unavailable`
and `interrupted`. Empty strings, nulls and empty arrays are intentional absence
values. They do not establish successful verification.

## Per-turn cost

Collection runs full-context diffing with `--find-copies-harder`, sometimes followed
by another full diff for copy destinations. Large unchanged sources increase copy
search cost. Large changed files increase memory and output. Untracked file reads
are also unbounded. Base measurement runs another suite and scans dependency inputs.
Staged runs may copy `node_modules`. Configured mode executes every suite each time.

Provision reviewed baselines once per immutable base and dependency state instead
of measuring them every turn. Measure diff/runner time on representative large
repositories before making this a per-turn gate. A follow-up can add bounded copy
detection and a total diff byte/time budget. Budget exhaustion must return failure,
never silently truncate analysis. Reducing context needs detector regression tests
because lexical checks use unchanged surrounding code. No performance claim or
large-repository benchmark is made by this change.
