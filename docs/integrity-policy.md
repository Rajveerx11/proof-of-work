# Test-integrity policy (#29)

This policy turns review signals into a deterministic gate. A finding is not an
accusation of cheating, nor proof of configured test collection or intent.

## API and manager integration

```python
from proofofwork.core.detector.integrity_policy import apply_integrity_policy

# Apply once to all detector findings, before calculating the verdict/bootstrap.
findings = apply_integrity_policy(findings)               # strict=True by default
findings = apply_integrity_policy(findings, strict=False) # explicit advisory compatibility
```

The API accepts an iterable of `Finding` and returns a new `list[Finding]`. It does
not mutate its input, calculate a verdict, read repository/environment policy,
execute commands, log evidence, or authorize overrides. `strict` must be an actual
`bool`; values such as `"false"`, `0`, or `None` raise `TypeError` rather than silently
selecting a mode. There is no override/approval argument or candidate-controlled
allowlist.

Only high-risk **WARN** findings are promoted to **BLOCK** in strict mode:

| Rule | Signal |
| --- | --- |
| `deleted-test` | Test-related deletion (existing detector already emits BLOCK) |
| `test-path-removed` | Rename loses a known default collection family |
| `removed-test-fn` | Test-like definition removed from changed-line fragments |
| `added-skip` | Recognized framework skip/focus syntax added |
| `removed-assert` | Existing assertion detector reports net removal |
| `weak-assert` | Existing assertion detector reports weakened/tautological checks |
| `weakened-assert` | Compatibility name for weakened-assert findings |
| `integrity-analysis-incomplete` | Patch encoding cannot be analyzed reliably |

Other WARN/INFO findings retain their severity. Existing BLOCKs stay BLOCK even
in advisory mode; for example, deleted test-related files remain blocking under
the existing detector behavior. A preserved `renamed-test` WARN is not promoted.

The source-tree engine, CLI and composite Action now default to strict enforcement.
Use `--integrity-policy strict|advisory` (CLI) or `integrity-policy` (Action input).
The effective selection is recorded in JSON and signed evidence. These changes are
unreleased. Trusted CI must own mode selection and protect it from candidate changes.
Advisory means warnings are not escalated; it is not a recorded override, approval authorization,
or proof that a reviewer approved a particular change. Candidate repository files,
PR-provided approval text, and inherited unvalidated environment flags must not
silently disable the gate. Validate Action/CLI mode input at the trusted boundary.

## Git and collection semantics

- Git name/status output is NUL-delimited and decoded losslessly with
  UTF-8/surrogateescape. Truncated/unmerged statuses fail closed. No shell command
  interpolation is used. Base references are resolved with `--end-of-options`;
  filesystem paths never become command options.
- Renames/copies are explicitly requested, including unchanged-body moves with
  no patch hunks. Both source and destination classify a file as test-related.
  A rename from a known default test path into a helper or excluded directory
  emits `test-path-removed`, even with identical contents. An ordinary compatible
  move emits only `renamed-test` WARN.
- Copies retain status `C` and `old_path`; they leave the source intact. Their
  complete destination is inspected as additions using a no-renames diff, never
  as removal of source tests. Staged copies read only index content, not dirty
  working-tree text.
- C-quoted/octal, Unicode, tab, newline, CR, quote and literal-backslash paths are
  handled without splitting on whitespace. Only `/` separates Git directories;
  a backslash in a Git path is a filename character. Patch source lines beginning
  with `+++` or `---` are not confused with file headers inside a hunk.
- Python defaults require a literal `.py` suffix and platform-aware `test_*.py` /
  `*_test.py` matching. Known default `norecursedirs` exclusions are modeled, not
  arbitrary configured excludes. Windows normcase has modeled regressions and the hosted suite runs on Windows
  across Python 3.11–3.14. That matrix is not exhaustive evidence for all native
  filename restrictions, custom collection or consumer toolchains.
- JS/TS `.test`/`.spec` names and Jest's `__tests__` convention are modeled outside
  `node_modules`. `__tests__` is not a universal Vitest default. Compatible
  Python/JS moves preserve their family; cross-family renames require review,
  even if a deliberate rewrite might preserve behavior.

## Heuristic limits and types handoff

`DiffFile` preserves source/destination paths, copy status, and optional complete
candidate source lines with 1-based Git LF coordinates for additions. Real Git
checks populate that context from full-context raw-text patches. Staged checks
use the index, never dirty working-tree text. Exact copies inspect complete new
destination contents. Stored/hand-built partial patches retain fragment analysis.

Skip checks tokenize the complete candidate and require a marker token on an
added line, preventing disjoint docstring hunks from hiding an executable skip or
flagging a documentation example. Python encoding/BOM handling is preserved;
unreadable source emits `integrity-analysis-incomplete`. Function-removal checks
still compare changed-line fragments, so omitted original lexical context remains
a limitation. JS masking distinguishes statement blocks from expression values,
regex literals from division, and contextual `for...of` from identifiers/property
names, and recognizes statement boundaries for jumps, debugger and static module
declarations. Added-marker provenance uses executable tokens, so comment-only
changes within an existing multiline skip do not become new findings. The scanner
preserves line terminators and ignores unrelated `.`/`?.` receivers
across whitespace/comments. Templates remain opaque; aliases, shadowed bindings,
computed calls, unsupported syntax and configured collection remain unresolved.
These are heuristics, not complete parsers or proof of cheating.

Full-context patch collection can cost more for large changed files; performance
optimization must preserve index provenance and complete lexical context.

The assertion detector is also outside ownership: this policy promotes its
existing `weak-assert`/`removed-assert` findings; it does not make those detectors
executable-context-aware. Manager review is needed for comment/string replacements
and removed docstring examples before claiming full semantic-integrity coverage.
Signed subject binding, test execution/cache freshness, coverage and approval
records remain separate manager-owned responsibilities. Test/policy success here
is not an end-to-end signed-gate attestation.

## Integrated regression coverage

The expanded marker detector recognizes the learning corpus's module-level `pytestmark`
skip. Learning tests now expect `already caught` and inject a genuinely missed custom
fixture to retain promotion/idempotence coverage without weakening detection.
`tests/test_integrity_integration.py` exercises real Git and pytest, strict/advisory
verdicts, preserved and discovery-losing renames, and durably signed policy labels.
These regressions do not establish full-context assertion semantics or hostile-code safety.
For the current source/release distinction and validation evidence, see
[installation](installation.md) and [hardening status](production-hardening-status.md).
