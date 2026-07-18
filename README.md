<div align="center">

# 🛡️ Proof-of-Work

**Catch AI coding agents cheating on their work — and prove the work was actually checked.**

[![CI](https://github.com/Rajveerx11/proof-of-work/actions/workflows/ci.yml/badge.svg)](https://github.com/Rajveerx11/proof-of-work/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

*A gate the human wires. It re-checks an agent's "done" against facts, not opinions — then signs the result.*

</div>

---

AI agents write most of the code now. Generating it is easy; **verifying it is the hard
part.** Agents routinely report **"done"** when the job isn't finished — and some actively
cheat to turn the checkmark green:

- 🗑️ delete or `@skip` the tests that would fail
- 🚪 add `sys.exit(0)` to fake a passing run
- 🫥 weaken assertions (`assert True`) or mock away the logic under test
- 📉 ship code that quietly drops coverage

You can't trust an agent to grade its own homework. **Proof-of-Work is the grader that runs
after it** — as a git hook or CI step the agent can't skip — and re-checks the work against
hard facts.

```console
$ proof-of-work check --base origin/main
FAIL
  - BLOCK fake-pass:sys-exit: hard exit added in a test — passes without running
  - tests failed on a clean re-run
  - warn removed-test-fn: test function(s) removed: test_refund
  [block] fake-pass:sys-exit: hard exit added in a test — passes without running (test_pay.py:1)
# exit code 1 → the commit / CI is blocked
```

## Contents

- [Why it's different](#why-its-different)
- [Quickstart](#quickstart)
- [How it works](#how-it-works)
- [What it catches](#what-it-catches)
- [Usage](#usage)
- [The tamper-evident log](#the-tamper-evident-log)
- [Honest limits](#honest-limits)
- [Roadmap](#roadmap)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Why it's different

Existing tools are either academic benchmarks (score models offline) or generic
code-review bots (find bugs, not cheating). Proof-of-Work owns a different lane:
**runtime cheat-detection + a tamper-evident audit trail** — the part the big model labs
won't build against their own agents.

The core rule: **facts get signed; opinions stay advisory.** The verdict is decided by
deterministic checks and a real test run — never by an AI. That's what makes it
reproducible and trustworthy.

## Quickstart

```bash
# run once, no install (requires uv)
uvx proof-of-work check --base origin/main

# or install it
pip install proof-of-work          # once published to PyPI
proof-of-work check --staged       # gate what you're about to commit
```

Wire it as a gate the agent can't skip:

```bash
proof-of-work install-hook         # writes .git/hooks/pre-commit
```

Requires Python 3.11+. The only runtime dependency is `cryptography` (to sign the log).

## How it works

The moment an agent finishes, one engine runs behind whichever surface you wired:

```
      git diff ──▶ deterministic checks ──▶ re-run real tests ──▶ coverage Δ ──▶ verdict ──▶ signed log
                    (the trusted signal)      (facts, not the        (vs stored     pass/fail   (hash chain
                                               agent's word)          baseline)     + reasons)  + Ed25519)
                                                                                       ▲
                                                             LLM judge ────────────────┘  (advisory metadata only)
```

1. **Runs the real tests** in isolation and reads the true result — never the agent's word.
2. **Scans the git diff** for tampering — deleted/weakened tests, fake passes, coverage kills.
3. **Mutation-tests** the change (optional) to catch gutted-but-present tests.
4. Returns a plain **`pass / fail + reasons`** the agent or CI can branch on.
5. **Logs every run** to a tamper-evident record, so you can prove the code was checked.

## What it catches

Facts get signed; opinions stay advisory:

| Signal | Severity | How |
|---|---|---|
| Deleted test file · fake-pass exit (`sys.exit(0)`, `process.exit(0)`) · coverage drop vs baseline · function-under-test mocked away | **block** — fails the verdict | deterministic detector |
| Weakened/removed asserts · added `skip`/`only`/`xfail` · renamed test · surviving mutants | **warn** — surfaced, doesn't fail alone | deterministic detector |
| Real tests fail on a clean re-run | **block** | test runner |
| "Does this diff weaken verification or miss the task?" | metadata only | LLM judge (advisory, BYO key) |

A verdict **fails** if any *block* signal fires or the real tests fail. Python and JS/TS
are supported at v1.

## Usage

Three surfaces, one engine — the **exit code is the contract** (`0` pass, `1` fail):

- **CLI** — `proof-of-work check` (the default; bare `proof-of-work` runs it too).
- **Git hook** — `proof-of-work install-hook` writes a `pre-commit` hook that runs
  `check --staged` and blocks the commit if the work doesn't check out.
- **GitHub Action** — the composite action at `proofofwork/interfaces/`:

  ```yaml
  - uses: Rajveerx11/proof-of-work/proofofwork/interfaces@main   # pin to @v0.1.0 once tagged
    with:
      mutation: "false"   # optional: also run mutation testing (slower)
  ```

**Flags:** `--staged`, `--base <ref>`, `--no-tests`, `--mutation`, `--update-baseline`,
`--json`, `--judge`, `--db <path>`.

**The judge** (`--judge`) is **advisory only** — its output is logged as metadata and never
changes the verdict. Bring your own key: set `ANTHROPIC_API_KEY` and install the extra
(`pip install "proof-of-work[judge]"`); without either, it's silently skipped.

## The tamper-evident log

Every run is appended to a hash-chained SQLite log
(`entry_hash = SHA256(prev_hash || canonical(envelope))`) whose head is signed with an
Ed25519 key. Each entry is an in-toto/DSSE attestation of the changeset and verdict. Verify
it any time:

```bash
proof-of-work verify-log        # recomputes the chain + checks the signed head
```

Verification uses **only the public key** — running `verify-log` never grants signing
authority.

## Honest limits

This tool is a strong filter, not an oracle. Specifically:

- **Tamper-evident, not tamper-proof.** A local key + local file detects edits, but whoever
  holds the key can rewrite the chain. Un-forgeable, cross-repo attestation is a v2 goal
  (see [SECURITY.md](SECURITY.md)).
- **It verifies checks, not correctness.** It signs "these specific checks passed/failed,"
  never "this code is correct."
- **Diff heuristics are a net, not a proof.** The authoritative signals are re-running the
  tests, coverage, and mutation testing; the AST/regex checks are the extra net. Scoped to
  lazy, non-adversarial agents — a determined adversary can evade syntactic checks.

## Roadmap

**✅ v1 (shipped):** deterministic detector · CLI + git hook + GitHub Action · local signed
log · advisory judge · Python + JS/TS.

**🧪 v2 (in progress):** self-improving rule loop — `proof-of-work learn` mines a frozen,
human-labeled cheat corpus, auto-drafts a rule for anything the built-ins miss, and promotes
it only if it catches the cheat with **zero** false positives on the clean corpus (add-only;
rollback is `git revert`).

**🕓 v2+ (deferred):** MCP tool · hosted microVM sandbox · keyless signing → Rekor
transparency log · opt-in federated cheat corpus · LLM rule-drafting · rule GC.

The immediate next step is the **48-hour test**: run the detector on ~20 real agent PRs and
publish the catch count. See [`plan/`](plan/) for the full spec and the design history.

## Development

```bash
git clone https://github.com/Rajveerx11/proof-of-work
cd proof-of-work
uv sync --extra dev      # .venv + project + pytest
uv run pytest -q         # the suite (CI: 3 OS x 3 Python versions)
uvx ruff check .         # lint
```

**Layout** — one package, `proofofwork/`:

```
proofofwork/
├── engine.py          # the one engine every surface calls
├── types.py           # shared contract: Diff, Finding, Verdict, ...
├── core/
│   ├── gitdiff.py     # git plumbing → parsed Diff
│   ├── detector/      # the cheat checks (ALL_CHECKS registry)
│   ├── runner.py      # re-run the real suite through the sandbox
│   └── sandbox/       # isolation seam (local now; Docker/microVM later)
├── log/               # hash-chained, Ed25519-signed tamper-evident log
├── judge/             # advisory LLM judge (never signs)
└── interfaces/        # cli.py · precommit · action.yml
```

## Contributing

Contributions are very welcome — new checks, killed false positives, more languages, docs.
Start with **[CONTRIBUTING.md](CONTRIBUTING.md)**. The golden rule: a check that can fire on
honest code ships with a test proving it doesn't. And yes — this repo gates its own PRs with
Proof-of-Work.

## License

[Apache License 2.0](LICENSE). Open-core: the deterministic detector, CLI, hook, and Action
are free and open forever; hosted team features (dashboard, cross-repo attestation) are the
planned paid tier.
