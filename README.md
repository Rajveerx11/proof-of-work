# Proof-of-Work

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

📋 **Core + interfaces landing.** Deterministic engine plus the three surfaces (CLI, git hook, GitHub Action) and the advisory judge are in place. See [Usage](#usage).

See the plan:
- [`plan/about-this-project.md`](plan/about-this-project.md) — what this is
- [`plan/tech-stack-and-requirements.md`](plan/tech-stack-and-requirements.md) — the finalized spec
- `plan/html-files/` — visual versions of the plans

## Scope (v1)

Ship the deterministic core first: detector (git-diff + coverage delta + `sys.exit`/skip checks
+ mutation testing) → CLI + git hook + GitHub Action → local signed log. The self-improving
loop, MCP tool, hosted sandbox, and public attestation are deferred to v2+.

## Usage

Install:

```bash
uvx proof-of-work check          # run once, no install
pip install -e .                 # or install into the current env
```

Three surfaces, one engine — the exit code is the contract (`0` pass, `1` fail):

- **CLI** — `proof-of-work check` (the default; bare `proof-of-work` runs it too).
- **Git hook** — `proof-of-work install-hook` writes `.git/hooks/pre-commit`, which
  runs `check --staged` and blocks the commit if the work doesn't check out.
- **GitHub Action** — the composite action in `proofofwork/interfaces/action.yml`:

  ```yaml
  - uses: your-org/proof-of-work@v1
    with:
      mutation: "false"   # optional
  ```

Example run:

```bash
$ proof-of-work check --base origin/main
FAIL
  - BLOCK deleted-test: a test was removed without a replacement
  - tests failed on a clean re-run
  [block] deleted-test: a test was removed without a replacement (tests/test_pay.py:12)
```

Useful flags: `--staged`, `--base <ref>`, `--no-tests`, `--mutation`,
`--update-baseline`, `--json`. Check the tamper-evident log with
`proof-of-work verify-log`.

The AI judge (`--judge`) is **advisory only** — its output is logged as metadata and
never changes the verdict. It's bring-your-own-key: set `ANTHROPIC_API_KEY` and install
the `judge` extra (`pip install "proof-of-work[judge]"`); without either it's silently skipped.

## License

TBD (planned: open-source, open-core).
