# Production hardening status

**Checkpoint: 2026-11-01, reviewed source `3612633`.** This is merged,
**unreleased** hardening, not a production-readiness certification. The published
release remains v0.2.0; installing that tag or PyPI package does not include all
source-tree features described here. Package metadata on `main` also remains
0.2.0. Use the [installation guide](installation.md) to verify the source commit.

## Merged and verified

- Required coverage and strict integrity are the source defaults. Baseline
  existence/value/identity are captured before candidate execution. Missing,
  invalid, stale or incomparable coverage blocks; advisory/test-only modes are
  explicit labeled evidence, not human authorization or a baseline bypass.
- The reviewed committed floor was adopted independently from an already reviewed
  tree. Its [provenance](coverage-baseline-provenance.json) and artifacts remain
  unchanged. Candidate coverage cannot authorize replacing it.
- [PR #49](https://github.com/Rajveerx11/proof-of-work/pull/49) fixed docs/workflow-only
  changes selecting no suites: trusted baseline families augment changed-language
  requirements before execution, without changing the floor or policies.
- Reviewed configured suites run all required commands across packages. This is
  **exit-code test-execution evidence only**, not configured coverage or whole-candidate
  attestation. Explicit suite base and test-only policy are required; an applicable
  trusted coverage baseline still blocks that mode.
- [PR #50](https://github.com/Rajveerx11/proof-of-work/pull/50) hardened the experimental
  Docker API's resource-capability preflight and added bounded live Linux probes.
  Missing/false/malformed resource support fails before image inspection/create.
  Its CodeRabbit startup-timeout finding was reproduced, repaired and verified at
  the exact final commit before merge.

## Verification evidence at this checkpoint

| Scope | Observed result | Boundary |
| --- | --- | --- |
| Full strict local gate | 985 passed, 7 platform-specific skips; required coverage and strict integrity passed | Python 3.13.15, Node 24.21.0; trusted-local execution |
| Final sandbox unit regressions | 95 passed | Modeled policy/lifecycle plus safe local fixtures, not containment proof |
| Live Docker probes | 10 passed on native Linux; the 3 affected failure/disposal cases passed again after the startup-budget repair | One existing pinned Python 3.12 image, Docker 29.1.3; see [isolation evidence](isolation.md#opt-in-live-linux-probes) |
| Hosted validation | [Main CI at the reviewed squash commit passed](https://github.com/Rajveerx11/proof-of-work/actions/runs/41231169761), including Linux/Windows Python 3.11–3.14, real JS coverage, lint and distribution validation | Does not prove arbitrary consumer Action workflows or untrusted execution |
| Final source checks | Locked dependency consistency, pinned Ruff and whitespace checks passed | No separate typecheck is configured |

The full-gate coverage measurement was 92.157% against the unchanged trusted
92.568% floor, within the existing 2-point threshold. The measured/tested source
tree matched the merged squash commit exactly. These are checkpoint observations,
not promises about later revisions or universal platform coverage.

## Remaining production boundaries

| Workstream | Available now | Still required |
| --- | --- | --- |
| [#26 coverage](https://github.com/Rajveerx11/proof-of-work/issues/26) | Required policy, execution witnesses, frozen-base authority, reviewed floor, real provider and hosted matrix regressions | Consumer-specific adoption and remaining acceptance evidence; no issue closure implied |
| [#27 configured suites](https://github.com/Rajveerx11/proof-of-work/issues/27) | Reviewed multi-package argv/cwd/prerequisites, staged snapshots, all required suites and exit statuses | Selective mapping, full configured coverage and toolchain/rollout validation |
| [#28 isolation](https://github.com/Rajveerx11/proof-of-work/issues/28) | Experimental digest-pinned Linux Docker API and bounded live resource/network/cleanup evidence | Gate/runner integration, trusted image/toolchain and artifact transport, all eval stages, external crash recovery and supported-platform decision |
| [#29 integrity](https://github.com/Rajveerx11/proof-of-work/issues/29) | Strict high-risk policy, rename/copy/discovery analysis and executable skip-marker context | Full assertion/lexical semantics and collection-aware verification; findings remain heuristics |
| [#30 baselines](https://github.com/Rajveerx11/proof-of-work/issues/30) | Stored scalar floor with identity; explicit frozen-base measurement records per-language values | Trusted per-suite baseline histories and configured-suite coverage |
| [#31 JS tooling](https://github.com/Rajveerx11/proof-of-work/issues/31) | Installed tooling, matching dependency-input checks and real Jest/Vitest regression fixtures | Broader deterministic/offline-safe package-manager and workspace support |
| [#32 budgets](https://github.com/Rajveerx11/proof-of-work/issues/32) | Bounded experimental Docker workload/capture controls | End-to-end diff, dependency-copy, local output/runtime/resource budgets and stress evidence |
| [#33 consumer Action](https://github.com/Rajveerx11/proof-of-work/issues/33) | Composite argument/trust-boundary tests and self-dogfood | Realistic consumer-repository Action workflows and supported OS evidence |
| [#34 attestation](https://github.com/Rajveerx11/proof-of-work/issues/34) | Local hash chain and signed head | Externally verifiable cross-machine attestation |
| [#35 rollout](https://github.com/Rajveerx11/proof-of-work/issues/35) | Reviewed merged slices and CI evidence | Supported rollout/release acceptance and separately authorized publication |

**Do not close #28 as complete.** `check --sandbox docker` still rejects before Git
or candidate execution, without local fallback. CLI `eval run --untrusted` and
library `run_task(..., untrusted=True)` also reject before execution. Native mode
remains trusted-only; do not run hostile repositories or agents on the host.
The live probes are not malicious-image safety, escape assessment, full-stage
attestation or recovery from controller/daemon crashes. Shared-kernel containment
and an approved production worker/image remain operator decisions.

Maintenance changes in PRs [#45](https://github.com/Rajveerx11/proof-of-work/pull/45),
[#47](https://github.com/Rajveerx11/proof-of-work/pull/47),
[#48](https://github.com/Rajveerx11/proof-of-work/pull/48) and
[#51](https://github.com/Rajveerx11/proof-of-work/pull/51) were not part of this
source checkpoint. Check their current status before claiming their dependency
or updater changes shipped. Do not bypass required review to clear a quota/seat blocker.

## Historical evidence and follow-up

The [earlier integration report](https://github.com/Rajveerx11/proof-of-work/blob/3612633823f777b8ad2cd605a15d477a2cdd7903/docs/production-hardening-status.md)
records the older 604-test WSL checkpoint and its unavailable Docker daemon.
That historical environment is not the evidence for the later native Linux run.
Versioned `reports/`, coverage provenance and v0.2.0 release artifacts are preserved,
not rewritten into current scores or readiness claims.

Next: complete configured/per-suite coverage and JS toolchain requirements; integrate
and validate all isolated execution stages with crash recovery; exercise real consumer
Action workflows; then prepare rollout/release evidence. Protect launcher, policy,
reviewed refs and signing authority outside candidate control throughout.

See [coverage](coverage-policy.md), [integrity](integrity-policy.md),
[configured suites](configured-suites.md), [isolation](isolation.md),
[evaluation](evaluation.md) and [installation](installation.md).
