# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-08-02

### Added

- Versioned corpus of 20 deterministic Python, JavaScript, and TypeScript agent tasks with
  category, difficulty, and protected-verifier metadata.
- Built-in OpenAI Codex CLI and Claude Code CLI adapters alongside the trusted generic argv-list
  adapter.
- Escaped, deterministic static HTML evaluation reports with exact reported cost, usage coverage,
  failure reasons, task/category detail, and equal-window comparisons.
- Linux and Windows adapter, corpus, migration, and HTML security regression tests.
- CI-generated empty-history HTML artifact; no external-agent scores are invented.

### Security

- Documented the evaluation runner as trusted-local process containment, not a sandbox.
- Kept `shell=False`, bounded output, timeouts, minimal child environment, immutable verifier
  protection, and non-persistence of agent output and workspace contents.

### Compatibility

- Legacy SQLite histories migrate additively; unknown usage remains `NULL`, and cost remains
  integer USD micros.
