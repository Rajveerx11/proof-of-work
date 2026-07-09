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

📋 **Planning complete.** Next: scaffold the 48-hour deterministic detector (the core).

See the plan:
- [`plan/about-this-project.md`](plan/about-this-project.md) — what this is
- [`plan/tech-stack-and-requirements.md`](plan/tech-stack-and-requirements.md) — the finalized spec
- `plan/html-files/` — visual versions of the plans

## Scope (v1)

Ship the deterministic core first: detector (git-diff + coverage delta + `sys.exit`/skip checks
+ mutation testing) → CLI + git hook + GitHub Action → local signed log. The self-improving
loop, MCP tool, hosted sandbox, and public attestation are deferred to v2+.

## License

TBD (planned: open-source, open-core).
