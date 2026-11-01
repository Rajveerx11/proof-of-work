# Installation, upgrades and local synchronization

## Choose the revision, not just the version label

The latest published release is **v0.2.0**. The reviewed `main` branch contains
**unreleased** coverage, integrity, configured-suite and Docker-backend hardening.
Its package metadata still says `0.2.0`; that number alone cannot prove which
source revision is installed. Installing current source does not publish a release.
See the [current status](production-hardening-status.md) and [changelog](../CHANGELOG.md).

For the published release:

```sh
python -m pip install --upgrade proof-of-work-agent
```

The package is `proof-of-work-agent`; its command is `proof-of-work`. Python 3.11+
and Git are required. Runtime dependencies are cryptography and PyYAML. Test tools
and the repository's own test dependencies must be provisioned separately.

## Recommended: reviewed checkout and locked environment

The examples below reproduce the dated reviewed checkpoint
`3612633823f777b8ad2cd605a15d477a2cdd7903`, not whichever commit a remote branch
happens to point to later. For a newer installation, review the new revision and
replace this SHA with its full immutable commit in every step and source requirement.

For a new checkout, pin the source **before installing dependencies**:

```sh
git clone https://github.com/Rajveerx11/proof-of-work.git
cd proof-of-work
git checkout --detach --no-overwrite-ignore 3612633823f777b8ad2cd605a15d477a2cdd7903
git rev-parse HEAD
```

Compare the displayed HEAD with the exact reviewed SHA above. Stop on a mismatch.
Only after that verification, install and run:

```sh
uv sync --locked --extra dev
uv run --frozen proof-of-work --help
uv run --frozen proof-of-work check --help
```

For an existing checkout, first inspect `git status --short --branch`. Continue
only on your intended `main` checkout with no tracked edits or unpublished commits.
Preserve unrelated files; do not use reset, clean, forced checkout or automatic
stashing to make synchronization succeed.

```sh
git fetch origin
git merge --ff-only --no-overwrite-ignore 3612633823f777b8ad2cd605a15d477a2cdd7903
git rev-parse HEAD
```

Verify that HEAD is exactly the selected full SHA **before** running `uv sync`.
If this checkout is already ahead, merge may report "already up to date" without
selecting the older checkpoint: stop on that SHA mismatch rather than installing
an unreviewed revision or forcing a downgrade. After verification:

```sh
uv sync --locked --extra dev
uv run --frozen proof-of-work check --help
```

The explicit merge refuses divergence and refuses to overwrite ignored local
files that would become tracked, including an existing local baseline. Resolve
such collisions deliberately; do not silently replace a coverage floor or local
signing state. `uv sync` intentionally updates the checkout's `.venv`, not other
Python environments. `--locked` rejects an inconsistent manifest/lockfile;
`--frozen` runs against the selected lock without resolving a replacement.

These commands work in POSIX shells and PowerShell when Git and uv are installed.
On Windows, prefer the project's `.venv\Scripts\python.exe` over an unrelated
`python` alias. Git is the source of truth: sync each separate checkout explicitly,
never copy another machine's `.venv`, installed dependencies or `.proofofwork` state.

## Optional: standalone source CLI

A uv tool installation is isolated from the checkout. For this repository's
maintainer/test tools, export the lock from the **verified checkout above** and use
that same full reviewed SHA in the source requirement below. Replace the placeholder
path and ref; choose a new constraints-file location. Do not mix another revision's
constraints with the selected source commit.

```sh
uv export --frozen --extra dev --no-emit-project --no-hashes --output-file "PATH_TO_NEW_CONSTRAINTS.txt"
uv tool install --reinstall --constraints "PATH_TO_NEW_CONSTRAINTS.txt" "proof-of-work-agent[dev] @ git+https://github.com/Rajveerx11/proof-of-work.git@<full-reviewed-commit>"
uv tool list
proof-of-work check --help
```

Repeat with a new reviewed commit to upgrade; a pinned Git installation does not
silently follow a moving branch. The dev extra supplies the pinned pytest/coverage
and lint environment for this project. It does **not** install arbitrary consumer
project dependencies. The Python runner uses the interpreter that launches the
gate: a globally installed command cannot assume it can import another project's
virtualenv. For other repositories, provision the gate and their test dependencies
in the same trusted environment. JS/TS checks also need Node/npm and installed
Jest/Vitest coverage tooling; configured suites need reviewed external executables.

Record the checkout SHA, `uv tool list`, and the installed distribution's
`direct_url.json` Git `commit_id` when verifying a source install. An editable
checkout installation instead records its local directory; use its Git SHA too.
Do not rely solely on the `0.2.0` label or an old command already on PATH. Reopen
PowerShell after a PATH change, or use the executable under `uv tool dir --bin`.

## First check and trust boundary

- Current source defaults to required coverage and strict integrity. A missing,
  invalid or incompatible trusted floor blocks; installation is not baseline approval.
- The checkout carries this project's reviewed committed floor. For another project,
  review its own [baseline/bootstrap or frozen-base measurement](coverage-policy.md).
  Never copy this project's numeric floor into a consumer repository.
- Stored baseline families select suites even for docs/workflow-only changes.
- `--no-tests` is detector-only evidence, not a verified test pass. Test-only and
  advisory policies are explicit trusted-operator decisions, not repair shortcuts.
- Do not reinstall hooks, regenerate keys, overwrite databases, bootstrap baselines
  or run external agents merely as an installation smoke test. `--help` and an
  empty-history report against a new temporary database need no provider credentials.
- Local gate/eval execution is trusted-only. Docker has bounded live Linux evidence,
  but `check --sandbox docker` and `eval run --untrusted` still fail closed. Installing
  the source does not enable production sandboxing. See [isolation](isolation.md).

For development validation, use the locked [contributor commands](../CONTRIBUTING.md).
Historical `reports/`, release notes and `plan/` documents do not define the
currently installed source contract.
