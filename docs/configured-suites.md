# Configured suites (bounded first slice)

The source-tree CLI/API can execute reviewed custom commands for multiple packages,
including configuration-only and unsupported-language changes. Every configured
suite is required on every check. There is no changed-path mapping or autodiscovery.
Repositories without suite configuration retain the existing pytest/Jest/Vitest runner.
This is not a validated tagged release or full production rollout.

## Reviewed configuration

Commit and review `.proofofwork/suites.json` in the trusted base. For example:

```json
{
  "version": 1,
  "suites": [
    {
      "id": "api-tests",
      "cwd": "packages/api",
      "argv": ["python", "-m", "pytest", "-q"],
      "prerequisites": ["tests"],
      "timeout": 120,
      "evidence": "exit-code"
    },
    {
      "id": "web-contracts",
      "cwd": "packages/web",
      "argv": ["node", "scripts/check-contracts.mjs"],
      "prerequisites": ["scripts/check-contracts.mjs"],
      "timeout": 60,
      "evidence": "exit-code"
    }
  ]
}
```

The JSON schema is strict: exactly `version` and a nonempty `suites` list; every suite
has exactly the six shown fields. Duplicate keys/IDs and unknown fields are rejected.
IDs match `[A-Za-z0-9][A-Za-z0-9_.-]*` and should remain stable across reviewed changes.
`timeout` is an integer from 1 to 600 seconds. Only explicit `evidence: "exit-code"`
is supported. Prerequisites are existing paths relative to the suite's cwd; they do
not install anything or attest file contents.

`cwd` is relative to the candidate repository (or staged index snapshot); `.` is
allowed. Prerequisites and cwd cannot traverse upward, use absolute/drive paths, or
follow symlinks or Windows reparse points (including internal junctions). `argv` is
a nonempty array of strings, invoked with no shell. Its first item must name an installed external tool on the gate's PATH, not a
path, known shell, or Windows `.cmd`/`.bat` wrapper. The resolved executable is passed
as an absolute path. Both its normalized lookup path and resolved target must be
outside the candidate and source worktree; a candidate PATH junction to an external
tool is not accepted. Use a
native executable such as `node`/`node.exe`, not a Windows `npm.cmd` wrapper.
Arguments themselves are reviewed command inputs, not a semantic confinement policy:
interpreters and programs may access files, launch subprocesses or perform network I/O.

## Explicit trusted base

```bash
proof-of-work check --root . --base origin/main --suite-base origin/main --coverage-policy test-only --json
proof-of-work check --root . --staged --suite-base origin/main --coverage-policy test-only --json
```

Fetch the trusted ref through your CI's existing trusted setup, then supply it
explicitly. `--suite-base REF` resolves once to an immutable commit; all configuration
reads use that commit, never the candidate configuration. There is **no implicit HEAD
or candidate fallback**. `HEAD` is permitted only when explicitly supplied and reviewed
by the caller. Merely having candidate/index configuration does not make it trusted;
checks noticing configuration without `--suite-base` block. Candidate modifications,
deletions, renames away, or additions cannot substitute commands from the chosen
trusted commit or switch the check back to the legacy runner.
To approve a config change, review it and explicitly select the new trusted commit.
The API equivalent is `engine.check(..., suite_base=reviewed_ref, coverage_policy="test-only")`.
Configured exit-code commands cannot satisfy required coverage; the trusted operator must
explicitly select test-only evidence. Any applicable existing baseline still blocks.

For CI, run the source revision under review with trusted installed dependencies and
both explicit refs as above; the existing tagged Action, hooks, MCP and agent adapters
do not gain a suite-base input in this slice. Do not treat an older tag as containing
this feature.

Staged commands run against an isolated copy of the index. The user index and working
tree are not changed by snapshot creation. Installed ignored `node_modules` are **not**
copied in configured mode; prerequisites must exist in the index or commands must use
trusted external tooling. Inherited worktree Python import paths are filtered by the
existing staged Python bootstrap. Neither dependencies nor local execution are an
adversarial security sandbox.

## Verdict evidence and limits

`tests` contains `framework: "configured"`, the immutable `suite_base`,
`evidence: "exit-code"`, ordered `required_suites`, `executed_suites`, and `suites`
records (`id`, `outcome`, `exit_code`, output tail `raw`). Executed IDs identify commands
whose sandbox call returned; interrupted/failed launches may have no confirmed executed
ID. Outcomes include `passed`, `failed`, `unavailable`, `timeout`, `interrupted`, and
`unknown`. Missing prerequisites/tools, malformed config, exceptions, timeout,
interruption, unknown status, or any nonzero required exit blocks. Other required
suites are still attempted after a suite failure, except interruption cancels further
launches and records the remaining IDs as interrupted/not started. `--no-tests` cannot
pass configured mode.

There is **no configured coverage parser or baseline bootstrap**. A trusted baseline
at the suite base, the existing diff/staged policy base, or the applicable local
worktree/Git-metadata baseline path blocks, even when malformed or deleted by the
candidate. `--update-baseline` always blocks. Coverage is never fabricated from exit
status. Choosing a different suite base cannot opt out of the existing coverage policy.

The verdict explicitly describes **test-execution evidence, not whole-candidate or
launcher attestation**. Existing deterministic tampering detectors still run and can
block a zero-exit suite, but their supported checks do not prove all candidate scripts,
launchers, dependencies, assertions or toolchains genuine. Reviewed argv alone cannot
establish that a candidate launcher exercised real tests. Full candidate integrity,
per-suite coverage, selective mapping, full Windows/PowerShell project/toolchain
validation, adapter inputs, and release validation remain follow-ups (#26/#27/#35).
