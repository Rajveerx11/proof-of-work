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
  arbitrary configured excludes. Windows normcase is tested in simulation; native
  Windows filename restrictions/collection are not verified locally.
- JS/TS `.test`/`.spec` names and Jest's `__tests__` convention are modeled outside
  `node_modules`. `__tests__` is not a universal Vitest default. Compatible
  Python/JS moves preserve their family; cross-family renames require review,
  even if a deliberate rewrite might preserve behavior.

## Heuristic limits and types handoff

The current `DiffFile` has `old_path`, `status`, `added`, `removed`, `is_test` and
`language`. No new fields, dynamic attributes or type edits were made. Runtime `C`
is supported by its existing string status; the manager should update the type's
status/old-path documentation to include copies when that file is owned.

Function-removal and skip checks inspect **changed-line fragments**, not complete
before/after files. Python comments/complete string examples are masked using
stdlib tokenization; declared encoding/BOM handling is possible only when that
context is present in the fragment. Missing encoding produces an explicit
`integrity-analysis-incomplete` signal. Complete tokens before incomplete fragment
syntax are retained because hunks need not be complete programs. Arbitrary omitted
string/docstring/decorator context and tokens split across separate hunks cannot
be reconstructed reliably. JS masking excludes ordinary comments, strings and
simple regex contexts, respects LF/CR/U+2028/U+2029, and excludes unrelated `.` /
`?.` receivers across whitespace/comments. Templates remain opaque; ambiguous
regex/division contexts, aliases, shadowed names, computed calls and dynamic
collection are not resolved. These checks are not full parsers.

For full-context accuracy the manager must extend the shared contract with
optional original/candidate source lines and changed-line coordinates (plus source
language if desired), populate them from a full-context **Git** patch for the
correct base/index snapshot, then migrate the lexical consumers. Do not read a
dirty worktree as a substitute for staged context. That type integration is
outside this worker's ownership and has not been implemented.

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
