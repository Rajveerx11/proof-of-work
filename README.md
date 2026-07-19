<div align="center">

# Proof-of-Work

**Catch AI coding agents faking their work, and prove the work was actually checked.**

[![CI](https://github.com/Rajveerx11/proof-of-work/actions/workflows/ci.yml/badge.svg)](https://github.com/Rajveerx11/proof-of-work/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/proof-of-work-agent.svg)](https://pypi.org/project/proof-of-work-agent/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

</div>

---

Agents write most of the code now. Generating it is easy; verifying it is the hard part.
Agents often report **"done"** when the job is not finished, and some game the checks to turn
the light green:

- delete or `@skip` the tests that would fail
- add `sys.exit(0)` to fake a passing run
- weaken assertions (`assert True`) or mock away the code under test
- ship changes that quietly drop coverage

An agent cannot grade its own homework. Proof-of-Work is the grader that runs after it, as a
git hook or CI step the agent cannot skip, and re-checks the work against hard facts.

```console
$ proof-of-work check --base origin/main
FAIL
  - block fake-pass:sys-exit: hard exit added in a test, passes without running (test_pay.py:1)
  - tests failed on a clean re-run
  - warn removed-test-fn: test function removed: test_refund
# exit code 1, the commit or CI is blocked
```

The core rule: **facts get signed, opinions stay advisory.** The verdict comes from
deterministic checks and a real test run, never from an AI. That is what makes it
reproducible.

## Install

```bash
# run once, no install (requires uv)
uvx --from proof-of-work-agent proof-of-work check --base origin/main

# or install
pip install proof-of-work-agent    # PyPI name; CLI command stays proof-of-work
proof-of-work check --staged       # gate what you are about to commit
```

Wire it as a gate the agent cannot skip:

```bash
proof-of-work install-hook         # writes .git/hooks/pre-commit
```

Needs Python 3.11+. The only runtime dependency is `cryptography` (to sign the log).

## How it works

One engine runs behind whichever surface you wire up:

```
git diff ──▶ deterministic checks ──▶ re-run real tests ──▶ coverage delta ──▶ verdict ──▶ signed log
             (the trusted signal)      (facts, not the        (vs baseline)    pass/fail   (hash chain
                                        agent's word)                          + reasons)  + Ed25519)
                                                                                  ▲
                                                        LLM judge ────────────────┘ (advisory metadata only)
```

1. **Re-runs the real tests** in isolation and reads the true result, never the agent's word.
2. **Scans the git diff** for tampering: deleted or weakened tests, fake passes, coverage kills.
3. **Mutation-tests** the change (optional) to catch present-but-gutted tests.
4. Returns a plain **pass/fail with reasons** the agent or CI can branch on.
5. **Logs every run** to a tamper-evident record, so you can prove the code was checked.

## What it catches

| Signal | Severity | How |
|---|---|---|
| Deleted test file, fake-pass exit (`sys.exit(0)`, `process.exit(0)`), coverage drop vs baseline, function-under-test mocked away | **block** (fails the verdict) | deterministic detector |
| Weakened or removed asserts, added `skip`/`only`/`xfail`, renamed test, surviving mutants | **warn** (surfaced, does not fail alone) | deterministic detector |
| Real tests fail on a clean re-run | **block** | test runner |
| "Does this diff weaken verification or miss the task?" | metadata only | LLM judge (advisory, bring your own key) |

A verdict **fails** if any block signal fires or the real tests fail. Python and JS/TS
are supported at v1.

## Usage

Three surfaces, one engine. The **exit code is the contract** (`0` pass, `1` fail):

- **CLI:** `proof-of-work check` (bare `proof-of-work` runs it too).
- **Git hook:** `proof-of-work install-hook` writes a `pre-commit` hook that runs
  `check --staged` and blocks the commit if the work does not check out.
- **GitHub Action:** the composite action at `proofofwork/interfaces/`:

  ```yaml
  - uses: Rajveerx11/proof-of-work/proofofwork/interfaces@main   # pin to a tag once released
    with:
      mutation: "false"   # optional: also run mutation testing (slower)
  ```

Flags: `--staged`, `--base <ref>`, `--no-tests`, `--mutation`, `--update-baseline`,
`--json`, `--judge`, `--db <path>`.

The judge (`--judge`) is advisory only: its output is logged as metadata and never changes
the verdict. Set `ANTHROPIC_API_KEY` and install the extra
(`pip install "proof-of-work-agent[judge]"`); without either, it is skipped.

## The tamper-evident log

Every run is appended to a hash-chained SQLite log
(`entry_hash = SHA256(prev_hash || canonical(envelope))`) whose head is signed with an
Ed25519 key. Each entry is an in-toto/DSSE attestation of the changeset and verdict. Verify
it any time:

```bash
proof-of-work verify-log        # recomputes the chain and checks the signed head
```

Verification uses only the public key, so running `verify-log` never grants signing authority.

## Limits

A strong filter, not an oracle:

- **Tamper-evident, not tamper-proof.** A local key and local file detect edits, but whoever
  holds the key can rewrite the chain. Un-forgeable cross-repo attestation is a v2 goal
  (see [SECURITY.md](SECURITY.md)).
- **It verifies checks, not correctness.** It signs "these checks passed or failed," never
  "this code is correct."
- **Diff heuristics are a net, not a proof.** The authoritative signals are the test re-run,
  coverage, and mutation testing; the AST and regex checks are the extra net. A determined
  adversary can evade the syntactic checks.

## Roadmap

- **v1 (shipped):** deterministic detector, CLI + git hook + GitHub Action, local signed log,
  advisory judge, Python and JS/TS.
- **v2 (in progress):** self-improving rule loop. `proof-of-work learn` mines a frozen,
  human-labeled cheat corpus, drafts a rule for anything the built-ins miss, and promotes it
  only if it catches the cheat with zero false positives on the clean corpus (add-only;
  rollback is `git revert`).
- **v2+ (deferred):** MCP tool, hosted microVM sandbox, keyless signing to a Rekor
  transparency log, opt-in federated cheat corpus.

See [`plan/`](plan/) for the full spec and design history.

## Development

```bash
git clone https://github.com/Rajveerx11/proof-of-work
cd proof-of-work
uv sync --extra dev      # .venv + project + pytest
uv run pytest -q         # the suite (CI: 3 OS x 3 Python versions)
uvx ruff check .         # lint
```

One package, `proofofwork/`:

```
proofofwork/
├── engine.py          # the one engine every surface calls
├── types.py           # shared contract: Diff, Finding, Verdict, ...
├── core/
│   ├── gitdiff.py     # git plumbing to parsed Diff
│   ├── detector/      # the cheat checks (ALL_CHECKS registry)
│   ├── runner.py      # re-run the real suite through the sandbox
│   └── sandbox/       # isolation seam (local now; Docker/microVM later)
├── log/               # hash-chained, Ed25519-signed tamper-evident log
├── judge/             # advisory LLM judge (never signs)
└── interfaces/        # cli.py, precommit, action.yml
```

## Contributing

New checks, killed false positives, more languages, and docs are all welcome. Start with
[CONTRIBUTING.md](CONTRIBUTING.md). The golden rule: a check that can fire on honest code
ships with a test proving it does not. This repo gates its own PRs with Proof-of-Work.

## License

[Apache License 2.0](LICENSE). The deterministic detector, CLI, hook, and Action are open and
free.
