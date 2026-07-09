# Proof-of-Work — Tech Stack & Technical Requirements

> Open-source tool that catches AI coding agents cheating on their work (deleted tests,
> `sys.exit(0)` fake passes, weakened asserts, coverage drops, mocked-away logic).
> This is the finalized engineering spec — decisions, not options — with the `/roast`
> council's corrections applied (marked **[roast]**).

**Status:** finalized · verified July 2026 · reshaped after adversarial review
**Companion docs:** `how-we-build-it` (deep dive) · `closed-source-ideas` (separate track)

---

## 0. The one-liner

A tool that runs the moment an AI agent says **"done."** It re-checks the work against
**facts** — did the tests really pass, were any tests deleted or faked, does the change
actually match the task — and returns a plain `pass / fail + reasons`. Every run is
signed and logged, so you can prove the code was checked.

**The reframe (from the first roast):** we are **not** building an AI that grades another
AI. We are building a **cheat-catcher built on hard facts**, run as a **gate the human
wires** (git hook / CI), that the agent can't skip.

---

## 1. Scope — ship the core, defer the cathedral  **[roast]**

The architecture review was unanimous: build the deterministic core first, ship it, and
let everything else grow once there are users. The core scored 8–9/10; the full spec as
originally written scored 3/10 for a solo v1.

### ✅ Ship in v1 (the ~2-week core)
- Deterministic detector: **git-diff + coverage delta + `sys.exit`/skip checks + mutation testing**
- **CLI + git pre-commit hook + GitHub Action** (3 surfaces)
- **Local hash-chained SQLite log** (prev-hash column + signed head) — ~40 lines
- **Layer 2 LLM judge — advisory only**, bring-your-own-key

### 🕓 Defer to v2+ (each is its own subproject; none has a week-one user)
- The **self-improving loop** (needs a real corpus first — it grows as a byproduct)
- **MCP tool** (add when asked; also the SDK version-cliff surface)
- **Hosted microVM** sandbox (infra + billing + on-call = a company)
- **Rekor / Trillian attestation** (enterprise; local hash-chain is enough for v1)

---

## 2. Finalized tech stack

Bottom line: **Python engine + tree-sitter (+ stdlib `ast`) + Docker-local / microVM-hosted
+ pytest·coverage.py / Vitest·v8 + git subprocess + official `mcp` SDK + uv/PyPI + SQLite.**

| Layer | Decision | Why | Watch out |
|---|---|---|---|
| Engine language | **Python 3.12+** | Native pytest/coverage access, most mature MCP SDK, first-class tree-sitter, fastest solo loop | No single binary — solved by uv/pipx |
| Code parsing | **tree-sitter-language-pack** + stdlib `ast` | One uniform parser across Py + JS/TS (306 grammars, maintained); `ast` for deep Python checks | Syntax tree, not semantic — tamper checks are heuristics; pin versions (API churn) |
| Isolation | **Docker (local)** · **microVM (hosted, v2)** | Container is fine for your own local code; hosted mode running others' code needs a real guest kernel | The one architectural fork → hide behind a `Sandbox` interface. Reject WASM (can't run real test deps) |
| Tests + coverage | **pytest + coverage.py 7.15** · **Vitest 3 + v8** | Machine-readable JSON for both; Vitest v8 matches Istanbul at 3–5× speed | Also detect Jest projects. Coverage-drop needs a stored baseline — parse JSON, never scrape text |
| Git diff | **shell out to `git`** plumbing | Every repo has git; output matches exactly, no build dep | Use `-z` NUL separators, disable color/pager, plumbing commands only |
| MCP server (v2) | **official `mcp` SDK 1.28.1** (FastMCP) | Current stable, production-recommended; one tool via `@mcp.tool()` | **v2 rename ~Jul 27–28 2026** (`FastMCP`→`MCPServer`, stateless) — wall all MCP code behind one module |
| Packaging | **uv + PyPI** (`uvx proof-of-work`) | 10–100× faster than pipx, zero-install runs; PyPI keeps pipx users working | Skip single-binary until offline is demanded. Ship pre-commit hook + composite Action |
| Local storage | **SQLite (stdlib), WAL mode** | Zero-dependency single file, perfect for append-only logs + coverage baselines | Hook + CI write concurrently → enable WAL + `busy_timeout` |

---

## 3. The detector — two layers, only one is trusted

Facts get signed; opinions stay advisory. The AI never decides the verdict, which keeps
the record reproducible and un-poisonable.

### Layer 1 — deterministic signatures  ·  **THE VERDICT (signed & logged)**
Engine = git-diff + coverage delta + Semgrep + mutation testing.
- **Test-integrity diff** — tests deleted, renamed, or `@skip`/`xfail` added vs baseline.
- **Fake-pass** — `sys.exit(0)`, patched test runner, coverage disabled, function-under-test mocked away.
- **Coverage delta** — tests still "pass" but cover much less code than the baseline.
- **Mutation testing (mutmut / Stryker)** **[roast]** — the real fix for semantically
  gutted-but-present asserts that syntax checks miss: introduce a bug, confirm a test
  actually fails. The strongest single signal.
- **Assert-weakening (heuristic)** — `assert True`, removed assertions. Syntactic only; a helper, not a proof.

### Layer 2 — LLM judge  ·  **ADVISORY ONLY (never signed)**
Engine = DeepEval-style scorer.
- Reads the diff, asks "does this weaken verification / miss the task?"
- Used only to **triage** suspicious cases and to **propose** rules (v2 loop).
- Its opinion is logged as **metadata**, never as the verdict. Bring-your-own-key.

---

## 4. The self-improving loop  ·  v2, not v1  **[roast]**

> The council was unanimous: build the core first. The loop below is the plan for **after**
> the core has users, and the corpus has grown as a byproduct of real runs. Name it
> honestly — it's a **monotonically-growing net for known lazy-agent cheat patterns**,
> **not** a measurable "catch-rate" (you can't measure cheats you never see).

Blueprint (Self-Harness, 2026): **mine failures → propose a fix → gate it hard → only then promote.**
Golden rule: the loop can only ever **ADD** detection power; coverage never silently shrinks.

1. **Capture** — every run logs verdict, rules fired, confirmed-clean runs, human overrides,
   and cheats caught later. Each signal is tagged by trust level.
2. **Propose** — a batch job clusters confirmed cheats no rule catches; the LLM **drafts** a
   candidate Semgrep rule. The AI only drafts — zero authority to ship.
3. **Safety gate** — tested against two frozen, signed corpora. Promote only if:
   - catches ≥1 NEW cheat, **AND**
   - per-rule false-positive rate ≤1% (Wilson lower bound), **AND**
   - aggregate FP stays under budget.
   - **[roast]** Dropped the original "0% regression on known cheats" clause — it was
     **vacuous**: additive Semgrep rules cannot *un*-catch a positive, so it never rejects
     anything. The real gate is false-positive based.
4. **Version & deploy (with GC)** — id + semver + provenance hash, signed git commit,
   shadow-run for N runs before it changes a real verdict. Rollback = `git revert`.
   - **[roast]** "Add-only" makes FPs and runtime grow forever → **rule garbage collection**:
     a new rule that *subsumes* an old one retires it automatically, but only when the frozen
     corpus confirms zero coverage is lost. Any change that would actually *reduce* coverage
     still needs a human two-key.
5. **Measure — honestly** — track precision + FP rate on the frozen corpora and coverage of
   known cheat classes per iteration.
   - **[roast]** You **cannot** measure true catch-rate solo (misses are unlabeled). Claim
     "grows the net over known patterns, keeps FPs bounded" — not recall on unknowns.
   - Ground truth must be **human/synthetic-anchored, never LLM-labeled**, or the judge leaks
     into the signed verdict.

**Anti-poisoning:** the loop may only add power; it can't auto-disable a check. The frozen
signed regression corpus means even a poisoned input stream can't promote a rule that lets a
known cheat through. Only deterministic "caught-later" and oracle signals create ground truth.

---

## 5. Tamper-evident logging  ·  one format, two tiers

Every run emits the same envelope — an **in-toto attestation in a DSSE wrapper** (the 2026
standard shared by Sigstore, SLSA, cosign):

```
DSSE ⟶ in-toto Statement {
  subject:   sha256(changeset),
  predicate: { verdict, cheats_caught:[rule_ids],
               tool_version, ruleset_version,
               corpus_version, timestamp } }
```

### Tier 1 — Local & free (v1 default)
Hash-chained append-only SQLite: each row links to the previous via
`entry_hash = SHA256(prev_hash || canonical(envelope))`. Fold hashes into a Merkle Mountain
Range → one small signed "head". Sign the head with minisign/cosign.
- **Honest limit:** local key + local file is tamper-*evident*, not tamper-*proof*. Mitigate
  by periodically posting just the signed head to an external anchor (gist / CI artifact).

### Tier 2 — Hosted & un-forgeable (v2, paid)
cosign keyless signing (OIDC, short-lived cert) → push each attestation to **Rekor v2** (GA,
tile-backed) for a public, third-party-auditable, cross-repo inclusion proof. Self-host option:
**Trillian Tessera**.
- **[roast]** Crypto proves **integrity, not correctness** — never let cosign/Rekor imply the
  verdict is *right*, only that it wasn't *altered*.

> Avoid: Amazon QLDB (shut down Jul 2025) and Trillian v1 (maintenance mode).

---

## 6. Technical requirements

### Functional
- **F-1** Run the project's real tests in isolation and read the true result — never trust the agent's word.
- **F-2** Detect core cheats deterministically: deleted/skipped tests, weakened asserts, fake pass, coverage drop, mocked-away logic.
- **F-3** Run mutation testing to catch semantically gutted-but-present tests.
- **F-4** Return a structured `pass / fail + reasons` the agent (or CI) can branch on.
- **F-5** Ship as CLI, git hook, GitHub Action (MCP tool later) — one engine behind all surfaces.
- **F-6** Support Python + JS/TS at v1; add languages via new grammars + rules.
- **F-7** Write every finding to the tamper-evident log.
- **F-8** *(v2)* Self-improve each run through the gated loop.

### Non-functional
- **N-1** Deterministic verdict — same input, same signed output, re-runnable by anyone.
- **N-2** Human-wired gate — the agent can't skip it or reinterpret the result.
- **N-3** Fast — deterministic scan in seconds; learning is an off-hot-path batch job.
- **N-4** Reversible — every rule change is a signed commit; rollback is one command.
- **N-5** Coverage never silently shrinks; reducing it needs a human two-key.
- **N-6** Zero-friction install — `uvx proof-of-work`, no account, offline for the local tier.
- **N-7** Isolation swappable — Docker/microVM behind one `Sandbox` interface.
- **N-8** MCP churn contained — all SDK calls behind one module.

---

## 7. Repo structure

```
proof-of-work/
├── core/              # the one engine everything shares
│   ├── runner.py      # run tests in isolation, read result
│   ├── detector/      # Layer 1: git-diff + coverage + Semgrep + mutation
│   ├── oracle.py      # frozen held-out randomized tests
│   └── sandbox/       # Sandbox interface + Docker (v1) / microVM (v2) drivers
├── rules/             # versioned, signed Semgrep rules
├── learn/             # (v2) mine → propose → gate → promote (batch)
│   └── corpora/       # frozen regression + clean sets (signed)
├── log/               # DSSE envelopes, hash chain, Merkle head
├── judge/             # Layer 2 LLM judge (advisory, BYO-key)
├── interfaces/        # cli.py · precommit · action.yml · mcp_server.py (v2, walled off)
└── eval/              # metrics harness (precision/FP + known-class coverage)
```

The `interfaces/` layer is thin — it just calls `core/`. `mcp_server.py` is the single file
walled off from the coming MCP v2 rename.

---

## 8. Hardest risks & the answer

| Risk | How the design handles it |
|---|---|
| Correctness can't be fully verified ("verification horizon") | Never sign "this is correct" — only "these specific checks passed/failed." A strong filter, not an oracle. |
| AST checks are heuristic; a determined agent evades them | Authoritative signal is re-running tests + coverage + **mutation testing**; AST patterns are the extra net. Scope the claim to lazy, non-adversarial agents. |
| Local log re-forgeable with disk + key access | Anchor the signed head externally; Tier 2 removes the local key (keyless). |
| False-positive fatigue → humans disable the tool | Wilson-bounded per-rule FP + aggregate FP budget + **rule GC**. |
| Poisoning the learning signal | Frozen signed corpus + add-only authority + label provenance tiers. |
| MCP SDK v2 breaking change (~late Jul 2026) | All MCP code behind `interfaces/mcp_server.py` — a one-file migration. |

---

## 9. Roast verdict (architecture review)

**RESHAPE → SCOPE DOWN · confidence high.** Scores: Contrarian 3 · Builder 3 · Logician 5 ·
Researcher 8 · Expansionist 8 (avg 5.4).

- **Stack choices validated** (Researcher 8) and **the moat is real** (Expansionist 8) — but
  as originally specified it's a cathedral a solo dev won't ship (Contrarian 3, Builder 3).
- **Biggest upside:** the moat isn't the detector — it's the **corpus of how agents cheat** +
  attestation as a standard. The local-only spec forfeits it; the 10x version needs an
  **opt-in federated corpus** (users share caught cheats, everyone's net grows). That's later.
- **Biggest risk:** scope. Built as written, you spend a month on plumbing and never ship the
  thing people wanted.

### The 48-hour test (do this before building anything else)
Build ONLY the deterministic detector — git-diff for deleted/weakened tests + coverage delta
+ `sys.exit`/skip grep + one `mutmut` pass. Run it on ~20 real agent PRs. **Publish the catch
count.** Don't write a line of the loop or the crypto until that number proves people care.

---

*Grounded in two engineering research passes + a 5-agent /roast review. All 2026 versions
verified at time of writing; re-check the MCP SDK before starting (v2 imminent).*
