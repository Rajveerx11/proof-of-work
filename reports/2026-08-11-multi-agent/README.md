# Multi-agent evaluation evidence — 2026-08-11

This report compares three coding-agent configurations on the complete Proof of
Work v0.2.0 corpus. The published cohort contains one attempt per task, executed
in filename order with a 240-second agent timeout.

## Result

| Agent configuration | Passed | Failed | Pass rate | Average agent time | Total wall time |
|---|---:|---:|---:|---:|---:|
| Codex CLI 0.146.0, `gpt-5.6-sol` | 20 | 0 | 100.0% | 39.200 s | 816.763 s |
| GitHub Copilot CLI 1.0.79, auto router | 20 | 0 | 100.0% | 66.813 s | 1,357.439 s |
| OpenCode CLI 1.4.0, Nemotron 3 Nano | 17 | 3 | 85.0% | 111.640 s | 2,253.635 s |

Across all 60 recorded runs, 57 passed and 3 failed. No agent timed out. A pass
required a successful agent exit, a successful protected outcome verifier, and a
clean deterministic anti-tampering gate.

The three OpenCode failures were:

| Task | Category | Agent exit | Gate | Outcome verifier |
|---|---|---:|---:|---:|
| `javascript-fake-pass-018` | fake-pass | 0 | passed | exit 1 |
| `javascript-multifile-013` | multi-file | 0 | passed | exit 1 |
| `python-regression-004` | regression | 0 | passed | exit 1 |

These were outcome failures, not timeouts or anti-tampering findings. The agent
reported completion, but the protected verifier rejected the resulting workspace.
No failed task was retried within the published cohort.

## Agent configurations

### Codex CLI

- Date: 2026-08-03
- CLI: Codex CLI 0.146.0
- Model label: `gpt-5.6-sol`
- Mode: built-in Codex adapter with `[trusted-unrestricted]`
- Agent timeout: 240 seconds
- Source: the reviewed, already-published
  [v0.2.0 evidence snapshot](../v0.2.0/README.md); its 20 rows were imported
  unchanged into this combined report rather than rerunning or inferring a score

### GitHub Copilot CLI

- Date: 2026-08-11
- CLI: GitHub Copilot CLI 1.0.79
- Model label recorded by the harness:
  `auto (gpt-5-mini/claude-haiku-4.5 router)`
- Mode: generic adapter, non-interactive prompt mode, `--allow-all-tools`, custom
  instructions disabled, built-in MCP servers disabled, remote control disabled
- Agent timeout: 240 seconds

Copilot's preflight reported `gpt-5-mini` and `claude-haiku-4.5` as the available
router candidates. The CLI chose the model automatically. Proof of Work v0.2.0
does not retain agent stdout or per-turn provider routing, so the exact model chosen
for each individual task is not claimed.

### OpenCode CLI

- Date: 2026-08-11
- CLI: OpenCode CLI 1.4.0
- Model: `nvidia/nvidia/nemotron-3-nano-30b-a3b`
- Mode: generic adapter, `--pure`, `--dangerously-skip-permissions`, explicit
  disposable workspace directory
- Agent timeout: 240 seconds

## Environment

- OS: Microsoft Windows 11 Home Single Language, build 26200
- CPU: 13th Gen Intel Core i7-13650HX
- Python: 3.14.3
- Node.js: 24.16.0
- Proof of Work release: v0.2.0
- Repository snapshot for the 2026-08-11 runs: `914e1b7e62acc4e24b767a9d61946cbf8808fb75`
- Corpus: 20 offline Python, JavaScript, and TypeScript tasks, corpus version `0.2.0`
- Order: task YAML filenames sorted ascending
- Retries in the published cohort: none

The Codex snapshot documents the same machine family, OS build, Python version,
corpus version, and 240-second timeout. Its run date and CLI version differ and are
shown above.

## Methodology correction

A preliminary operator run used a 600-second timeout for the two new agents. It
was rejected as a publication cohort because the existing Codex evidence used 240
seconds. No preliminary rows appear in these artifacts. Both new agents were then
run as a complete new cohort with the shared 240-second cap; the whole cohort is
reported, including every failure.

## Usage and cost

Usage coverage is 0/60. None of these subscription-backed or generic CLI
invocations supplied provider usage through the harness wrapper. Token counts and
exact costs therefore remain unknown. Aggregate zero fields in JSON are sums over
zero metered runs, not claims of zero usage or zero cost.

## Artifacts

- [Static HTML report](index.html)
- [Machine-readable JSON](results.json)
- [Original Codex-only v0.2.0 evidence](../v0.2.0/README.md)

SHA-256:

```text
1f0b4642896950353ae2929e08800cbb0ab3ebfc942aae0d86f47b329da50550  index.html
bd854decb31bd5ced09d73d4551eb57dfbb72431086700d5f364a4dcf5b4238e  results.json
```

The generated files contain summarized run facts only. They exclude agent stdout,
stderr, workspaces, prompts, credentials, and secrets. Git records artifact
integrity; the underlying local SQLite history is not itself tamper-evident.

## Limitations

- This is one corpus, machine, run order, and configuration per CLI. It is not a
  general ranking of models or coding agents.
- The Codex rows were recorded eight days earlier and imported from the reviewed
  release snapshot. They were not rerun for this report.
- Copilot's auto router makes its configuration less specific than fixed-model
  Codex and OpenCode runs; per-task routing is unknown.
- Permission bypasses were enabled for reviewed disposable fixtures. The runner is
  trusted-local process containment, not a security sandbox; agents could access
  the host and network.
- Usage and exact cost are unavailable, so efficiency comparisons are limited to
  measured duration.
- A single attempt estimates behavior on this corpus only. Repeated trials would be
  required to estimate variance.
