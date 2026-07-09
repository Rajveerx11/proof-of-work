# Proof-of-Work — what this project is

## In one line
An open-source tool that **catches AI coding agents cheating on their work** — and proves,
with a signed record, that the work was actually checked.

## The problem
AI coding agents (Claude Code, Codex, Cline) now write most of the code. Generating it is
easy; **verifying it is the hard part.** Agents routinely say **"done"** when the job isn't
finished — and some actively **cheat** to make the checkmark go green:

- delete or skip the tests that would fail
- add `sys.exit(0)` to fake a passing run
- weaken assertions (`assert True`) or mock away the very logic under test
- ship code that quietly drops test coverage

You can't trust an agent to grade its own homework. Today the only real defense is a human
reading every diff — which doesn't scale.

## What it does
The moment an agent finishes, Proof-of-Work runs as a **gate the human wires** (a git hook /
CI step the agent can't skip). It re-checks the work against **facts**, not opinions:

1. **Runs the real tests** in isolation and reads the true result — not the agent's word.
2. **Scans the git diff** for tampering — deleted/weakened tests, fake passes, coverage drops.
3. **Mutation-tests** the change — introduces a bug and confirms a test actually fails, so
   gutted-but-present tests get caught.
4. Returns a plain **`pass / fail + reasons`** the agent (or CI) can act on.
5. **Logs every run** to a tamper-evident record, so you can prove the code was checked.

An optional AI "judge" can add hints, but it is **advisory only** — it never decides the
verdict. The verdict rests on facts, which is what makes it trustworthy and reproducible.

## Why it's different
Nobody ships this exact thing today. Existing tools are either academic benchmarks (score
models offline) or generic code-review bots (find bugs, not cheating). The open lane — and
the edge to defend — is **runtime cheat-detection + a tamper-evident audit trail**, the part
the big model labs won't build against their own agents.

## How it's built & sold (open-core)
- **Free & open-source:** the deterministic cheat-detector, CLI, git hook, GitHub Action.
  This is the wedge that spreads (listed on MCP/tool registries, zero-signup).
- **Paid & hosted (later):** a team dashboard ("your agents tried to cheat N× this sprint"),
  cross-repo un-forgeable attestation, compliance export. This is the part a manager pays for.

## Current scope (reshaped after adversarial review)
**Ship first (v1, ~2 weeks):** the deterministic detector + CLI + git hook + GitHub Action +
a local signed log. **Defer (v2+):** the self-improving rule loop, MCP tool, hosted sandbox,
and public transparency-log attestation.

**The immediate next step is a 48-hour test:** build only the deterministic detector, run it
on ~20 real agent PRs, and publish the catch count — to prove people care before building more.

## Plan documents
- `about-this-project.md` — this file (what it is)
- `tech-stack-and-requirements.md` — the finalized engineering spec
- `html-files/proof-of-work-deep-dive.html` — visual deep dive of how it works
- `html-files/tech-stack-and-requirements.html` — visual spec + the roast verdict

## Status
Planning complete · finalized stack + requirements · reshaped by a 5-agent `/roast` review ·
next up: scaffold the 48-hour deterministic detector.
