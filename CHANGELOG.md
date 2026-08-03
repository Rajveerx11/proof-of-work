# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
