# Proof-of-Work

[![CI](https://github.com/Rajveerx11/proof-of-work/actions/workflows/ci.yml/badge.svg)](https://github.com/Rajveerx11/proof-of-work/actions/workflows/ci.yml)

**Catch AI coding agents cheating on their work — and prove the work was actually checked.**

AI agents write most of the code now. Generating it is easy; *verifying* it is the hard part.
Agents routinely report **"done"** when the job isn't finished — and some actively cheat to
make the checkmark go green (delete failing tests, `sys.exit(0)` to fake a pass, weaken
assertions, mock away the logic under test, quietly drop coverage).

Proof-of-Work runs the moment an agent finishes — as a gate the human wires (git hook / CI the
agent can't skip) — and re-checks the work against **facts, not opinions**:

1. **Runs the real tests** in isolation and reads the true result.
2. **Scans the git diff** for tampering (deleted/weakened tests, fake passes, coverage drops).
3. **Mutation-tests** the change to catch gutted-but-present tests.
4. Returns a plain `pass / fail + reasons`.
5. **Logs every run** to a tamper-evident record.

An optional AI judge adds hints but is **advisory only** — the verdict rests on facts, which
makes it reproducible and trustworthy.

## Status

✅ **v1 shipped.** The deterministic detector, all three surfaces (CLI, git hook, GitHub
Action), the advisory judge, and the tamper-evident signed log are built, tested, and on
`main`. CI runs the suite across **Linux/macOS/Windows × Python 3.11–3.13**, and the tool
[gates its own pull requests](.github/workflows/self-check.yml). Next: the 48-hour test —
run it on ~20 real agent PRs and publish the catch count.

Plan docs (how we got here):
- [`plan/about-this-project.md`](plan/about-this-project.md) — what this is
- [`plan/tech-stack-and-requirements.md`](plan/tech-stack-and-requirements.md) — the finalized spec
- `plan/html-files/` — visual versions of the plans

## Scope (v1)

Ship the deterministic core first: detector (git-diff + coverage delta + `sys.exit`/skip checks
+ mutation testing) → CLI + git hook + GitHub Action → local signed log. The self-improving
loop, MCP tool, hosted sandbox, and public attestation are deferred to v2+.

## Install

```bash
uvx proof-of-work check          # run once, no install
pip install proof-of-work        # or install into the current env (once published)
```

Requires Python 3.11+. The only runtime dependency is `cryptography` (for signing the log).

## Usage

Three surfaces, one engine — the exit code is the contract (`0` pass, `1` fail):

- **CLI** — `proof-of-work check` (the default; bare `proof-of-work` runs it too).
- **Git hook** — `proof-of-work install-hook` writes `.git/hooks/pre-commit`, which
  runs `check --staged` and blocks the commit if the work doesn't check out.
- **GitHub Action** — the composite action at `proofofwork/interfaces/`:

  ```yaml
  - uses: Rajveerx11/proof-of-work/proofofwork/interfaces@main   # pin to @v0.1.0 once tagged
    with:
      mutation: "false"   # optional
  ```

Example run:

```bash
$ proof-of-work check --base origin/main
FAIL
  - BLOCK fake-pass:sys-exit: hard exit added in a test — passes without running
  - tests failed on a clean re-run
  [block] fake-pass:sys-exit: hard exit added in a test — passes without running (test_pay.py:1)
```

Useful flags: `--staged`, `--base <ref>`, `--no-tests`, `--mutation`,
`--update-baseline`, `--json`. Check the tamper-evident log with
`proof-of-work verify-log` (recomputes the hash chain and verifies the signed head).

The AI judge (`--judge`) is **advisory only** — its output is logged as metadata and
never changes the verdict. It's bring-your-own-key: set `ANTHROPIC_API_KEY` and install
the `judge` extra (`pip install "proof-of-work[judge]"`); without either it's silently skipped.

## How it decides

Facts get signed; opinions stay advisory.

| Signal | Severity | Source |
|---|---|---|
| Deleted test file · fake-pass exit · function-under-test mocked away · coverage drop | **block** (fails) | deterministic detector |
| Weakened/removed assert · added skip/only · renamed test · mutation survivors | **warn** (surfaced) | deterministic detector |
| "Does this weaken verification / miss the task?" | metadata only | LLM judge (advisory) |

A verdict fails if any **block** signal fires or the real tests fail on a clean re-run.
Every run is written to a hash-chained SQLite log signed with an Ed25519 key; verification
uses only the public key, so `verify-log` never holds signing authority.

## Development

```bash
git clone https://github.com/Rajveerx11/proof-of-work
cd proof-of-work
uv sync --extra dev      # create .venv and install project + pytest
uv run pytest -q         # run the suite (also runs in CI across 3 OS × 3 Python)
uvx ruff check .         # lint
```

Layout: `proofofwork/` holds one package — `core/` (engine, detector, runner, sandbox),
`log/` (signed hash chain), `judge/` (advisory), and `interfaces/` (CLI, pre-commit,
Action). CI/CD lives in `.github/workflows/` (`ci`, `self-check`, `release`).

## License

Apache-2.0 (declared in `pyproject.toml`); open-core model planned. A `LICENSE` file will
be added on first release.
