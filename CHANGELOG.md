# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

These changes are merged source functionality, not a new tagged/PyPI release.
The source package version remains 0.2.0; verify the installed Git commit.

### Added

- Explicit reviewed configured suites for multiple packages, with required command
  statuses and exit-code evidence; configured coverage remains unsupported.
- Strict required-coverage evidence, immutable trusted-baseline capture and explicit
  frozen-base measurement with dependency-compatibility checks.
- Reviewed coverage-floor provenance, real Jest/Vitest coverage fixtures and locked
  Linux/Windows Python 3.11–3.14 CI, distribution builds and installed-wheel smoke tests.
- Experimental digest-pinned Linux Docker API with bounded snapshots/capture and
  opt-in live probes for host boundaries, resources, failure and cancellation cleanup.

### Changed

- Source defaults to required coverage and strict high-risk integrity policy, with
  effective policies/status recorded in JSON and signed evidence. These defaults
  must be controlled by the trusted operator, not candidate inputs.
- Diff analysis includes rename/copy/discovery and executable skip-marker context;
  syntactic assertions and other heuristics retain documented limitations.

### Fixed

- Trusted coverage-baseline families remain required for docs/workflow-only changes
  and augment, rather than replace, changed-language requirements.
- Docker preflight rejects missing, false or malformed resource-capability support
  before image inspection/container creation.
- Live nonzero-exit probes allow container startup time without weakening the
  deliberately short timeout probe; modeled slow-startup regressions cover both.

### Security

- Docker gate routing and untrusted eval still fail closed: the experimental API and
  bounded live Linux evidence do not establish production untrusted execution.
- No production-readiness certification, externally anchored attestation or new
  release is implied. See [current status](docs/production-hardening-status.md).

## [0.2.0] - 2026-08-03

### Added

- Versioned corpus of 20 deterministic Python, JavaScript, and TypeScript agent tasks with
  category, difficulty, and protected-verifier metadata.
- Built-in OpenAI Codex CLI and Claude Code CLI adapters alongside the trusted generic argv-list
  adapter.
- Escaped, deterministic static HTML evaluation reports with exact reported cost, usage coverage,
  failure reasons, task/category detail, and equal-window comparisons.
- Direct JSON report-file output for reproducible, machine-readable evidence artifacts.
- Optional comparison omission for one-shot corpora whose ordered task halves are not a trend.
- Linux and Windows adapter, corpus, migration, and HTML security regression tests across
  Python 3.11 through 3.14.
- CI-generated empty-history HTML artifact; no external-agent scores are invented.
- Published a complete, comparison-free v0.2.0 Codex corpus run with HTML, JSON, hashes,
  environment disclosure, and a CI regression check over all 20 task records.
- Explicit `--trusted-unrestricted` opt-in for reviewed fixtures when a built-in CLI sandbox
  is incompatible with the host; safe permission controls remain the default.

### Security

- Documented the evaluation runner as trusted-local process containment, not a sandbox.
- Kept `shell=False`, bounded output, timeouts, minimal child environment, immutable verifier
  protection, and non-persistence of agent output and workspace contents.
- Requested clean/safe built-in CLI modes to reduce benchmark contamination from local setup.
- Made the composite GitHub Action install its own pinned source instead of a mismatched PyPI name.

### Compatibility

- Legacy SQLite histories migrate additively; unknown usage remains `NULL`, and cost remains
  integer USD micros.
- Declared Python 3.11 through 3.14 support and complete PyPI project links.
