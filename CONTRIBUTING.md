# Contributing to Proof-of-Work

Thanks for helping catch cheating agents. The project runs on one principle —
**facts over opinions, and the smallest change that works.** A contribution that adds a
real detection signal, kills a false positive, or makes the gate easier to adopt is
exactly what we want.

- [Ways to contribute](#ways-to-contribute)
- [Development setup](#development-setup)
- [The golden rule: no false positives without a test](#the-golden-rule-no-false-positives-without-a-test)
- [Adding a detector check](#adding-a-detector-check)
- [What stays out of the signed verdict](#what-stays-out-of-the-signed-verdict)
- [Pull requests](#pull-requests)
- [Reporting security issues](#reporting-security-issues)
- [License](#license)

## Ways to contribute

- **New detector checks** — catch a cheat class we miss.
- **Kill false positives** — a check that fires on honest code erodes trust and gets the
  whole tool disabled. Fixing that is as valuable as a new check.
- **The cheat corpus** — sanitized, real examples of agents gaming tests make every
  check sharper. (This corpus is the project's long-term moat.)
- **Language support** — today Python + JS/TS; add rules for more.
- **Docs, bug reports, adoption glue** — hooks, CI recipes, clearer errors.

Not sure where to start? Open an issue describing the cheat you want to catch, and we'll
figure out the smallest check that catches it.

## Development setup

```bash
git clone https://github.com/Rajveerx11/proof-of-work
cd proof-of-work
uv sync --extra dev      # create .venv and install the project + pytest
uv run pytest -q         # run the suite (CI runs it on 3 OS x 3 Python versions)
uvx ruff check .         # lint
```

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## The golden rule: no false positives without a test

Proof-of-Work is a **gate**. A check that blocks honest work gets the tool turned off — so
every non-trivial check ships with tests for **both**:

- a **positive** case — the cheat is caught, and
- a **negative** case — a legitimate look-alike is **not** flagged.

See `tests/test_detector.py` for the pattern: `sys.exit(1)` (a real failure) must not be
flagged as a fake pass, `db.skip(5)` must not read as a test skip, and so on.

## Adding a detector check

Checks are small, pure functions. To add one:

1. Create `proofofwork/core/detector/<your_check>.py`:

   ```python
   from ...types import Diff, Finding, Severity

   def check(diff: Diff, root: str) -> list[Finding]:
       findings = []
       for f in diff.files:
           # inspect f.added / f.removed (line text), f.is_test, f.language, f.status
           ...
       return findings
   ```

2. Register it in `ALL_CHECKS` in `proofofwork/core/detector/__init__.py`.
3. Emit `Finding(rule, severity, message, file=..., line=...)`. Choose severity carefully:
   - **`BLOCK`** — a hard cheat signal that fails the verdict on its own. Use only when
     you are confident it cannot fire on honest code.
   - **`WARN`** — suspicious; surfaced but does not fail alone. The right default for a
     heuristic.
4. Work from `DiffFile.added` / `.removed` line text — that's the information the check
   receives. Prefer the stdlib `ast` for deeper Python analysis.
5. Add the positive **and** negative tests (see the golden rule above).

Checks must **never raise** — the engine isolates exceptions, but a broken check should
degrade gracefully rather than crash the gate.

## What stays out of the signed verdict

The LLM judge (`proofofwork/judge/`) is **advisory only**. It may annotate a run, never
decide it. Keep its output as metadata — the verdict must stay deterministic and
reproducible from facts alone. PRs that let the judge influence `passed` will be declined.

## Pull requests

- Keep diffs small and focused — one concern per PR.
- **CI must pass**: tests on Linux/macOS/Windows x Python 3.11–3.13, plus `ruff`.
- **The tool gates its own PRs.** `proof-of-work check` runs on your diff via
  [`.github/workflows/self-check.yml`](.github/workflows/self-check.yml) — if you touch
  tests, expect it to notice.
- An automated reviewer comments on PRs; address its findings or reply with why not.
- Write commit messages in the imperative mood ("add coverage-delta check", not "added").

## Reporting security issues

Please do **not** open public issues for vulnerabilities — see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the project's
[Apache License 2.0](LICENSE).
