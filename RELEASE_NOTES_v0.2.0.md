# Proof of Work v0.2.0 — draft release notes

v0.2.0 turns the original single-task harness into a tested local agent-evaluation platform.

Highlights:

- 20 deterministic, offline Python and JavaScript/TypeScript tasks across nine categories.
- OpenAI Codex CLI, Claude Code CLI, and generic trusted argv-list adapters.
- Portable static HTML reports with agent/model labels, corpus version, task/category outcomes,
  pass rate, failure reasons, wall time, exact provider-reported cost, usage coverage, and
  comparisons against previous runs.
- Additive legacy SQLite migration and explicit unknown-versus-zero usage handling.
- Linux and Windows CI coverage plus an empty-history HTML report artifact.

Security boundary: evaluation commands and fixtures are trusted local inputs. Process containment
is not a sandbox; use a container or microVM for untrusted code.

No benchmark scores or agent comparisons are bundled because no credentialed external-agent run
is part of release preparation. No PyPI publication, GitHub release, or merge is performed by this
change.
