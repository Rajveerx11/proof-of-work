# Experimental Docker backend (#28 bounded slice)

**Limited live Linux evidence; not issue #28 complete or a production sandbox.** This
slice only supplies an explicit backend. The gate's `--sandbox docker` selection
fails closed before Git or candidate execution because runner integration is unavailable.
`eval run --untrusted` and `run_task(..., untrusted=True)` likewise reject before execution.
Default `local` execution remains trusted
and can access host files, environment and network. This backend is unreleased source
functionality; see [installation](installation.md) and [current status](production-hardening-status.md).

## API and prerequisites

```python
from proofofwork.core.sandbox import get_sandbox

sandbox = get_sandbox("docker", image="reviewed/image@sha256:<64-lowercase-hex-digest>")
result = sandbox.run(["python3", "-I", "reviewed_entrypoint.py"], cwd=prepared_snapshot,
                     timeout=60)
```

The digest is a placeholder, not a validated image. Operator explicitly provisions
a credential-free, reviewed Linux image containing Python 3.12+ and all required
runtimes/dependencies **outside the backend**. No build, install, pull or settings
changes occur. Images declaring volumes are rejected. Docker must be a native
Linux CLI using `/var/run/docker.sock`, cgroup v2 and built-in seccomp. Remote
contexts, rootless alternative sockets, Windows `docker.exe` and platform breadth
are deliberately unsupported. Preflight warnings/missing backend/image fail
closed with `SandboxError`; there is no local fallback. Structured Docker info
`Warnings` must be present and either `null` or an empty list; nonempty or malformed
values reject before image inspection or container creation, even with empty stderr.
`MemoryLimit`, `SwapLimit`, `CpuCfsPeriod`, `CpuCfsQuota` and `PidsLimit` must each
be the JSON boolean `true`; false, missing or malformed support flags fail closed
before image inspection. An empty warnings list alone is not resource evidence.

Existing `Sandbox.run(cmd, cwd, env=None, timeout=600) -> RunOutput` stays compatible.
`get_sandbox(kind="local", *, image=None)` retains its default behavior; image is
accepted only with explicit `kind="docker"`. Docker's name is `docker-experimental`.
Docker rejects all nonempty environment overrides and non-dict values. Only empty
`{}`/`None` are accepted; do not pass `os.environ`, host PATH/PYTHONPATH, coverage
configuration or witness paths. Each run is independent; no artifact export or
persistent session is supplied.

## Workspace boundary and threat model

The caller is a trusted supervisor. `cwd` must be a supervisor-prepared, immutable,
metadata-free snapshot of the exact selected source. **Do not pass a repository
checkout or a home directory.** The backend refuses `.git` (file or directory),
`.proofofwork`, common credential directories/config names, `.env*`, symlinks,
hardlinked files and special files. It rejects the whole snapshot rather than
silently removing files and changing test semantics. Empty directories, regular
file contents and executable modes are retained. File descriptors/no-follow opens
avoid following links during export; detected changes reject. Child-directory
open/traversal and file-read errors abort the entire export before container
creation; unreadable subtrees are never silently omitted. Concurrent source
mutation is outside the supported contract.

No host bind mounts are used, even for the workspace. A bounded tar snapshot is
streamed over stdin into `/work` tmpfs and host source is never writable by the
container. Limits: 4096 total entries, 1024-character paths, 32 MiB archive.
This is stricter than workspace-only bind mounting: a read-only bind would still
expose Git configuration/signing keys/ignored files, and a writable bind would
bypass disk quotas. Nothing silently exports or excludes untracked metadata.

Filename checks are **not a secret scanner**. Secrets embedded in ordinary source
or the image would still be exposed. Supervisor must review source/image and must
not give an attacker host-side write access to the prepared snapshot. Trusted
components include PoW/host Python, Docker CLI/daemon, image and shared host kernel.
Kernel/daemon escapes, malicious operators/images, side channels and host parser
DoS are not proven contained. Image ENV/layers must be credential-free; the image
is trusted, and bootstrap startup is not image-secret isolation.

Docker client commands use a private empty config and a minimal host-client
environment (fixed PATH, private HOME, locale). This avoids reading host Docker
credentials/proxy settings, including automatic proxy ENV injection. Container
bootstrap uses isolated image Python (`-I`) and execs the requested argv with only
fixed PATH, HOME=/tmp and TMPDIR=/tmp. No host environment is forwarded.

## Requested controls — bounded live checks, not a containment guarantee

| Boundary/resource | Requested control |
| --- | --- |
| Network | `--network=none`; no enable-network option |
| Identity/privilege | UID/GID 65534, `--cap-drop=ALL`, no-new-privileges, built-in seccomp |
| Root filesystem | read-only; no image volumes, host mounts or Docker socket |
| CPU/memory/swap | 1 CPU, 512 MiB memory and equal memory-swap (no extra swap) |
| Processes/files | 64 PIDs, 256 FDs, 64 MiB file-size limit |
| Writable storage | `/work` 128 MiB, `/tmp` 16 MiB noexec, `/dev/shm` 8 MiB |
| Output/logs | 64 KiB combined host capture; Docker logging disabled; healthcheck disabled |
| Time | 1..600 seconds per workload; 30 seconds per control call |
| Disposal | unique owned name, forced volume removal, exact-name absence check |

Main-thread-only runs record the first SIGTERM/SIGINT without asynchronous handler
exceptions. Explicit checks cancel the attached CLI client and unwind into
removal. During disposal there are no cancellation checks: first cancellation is
deferred, repeats cannot interrupt owned cleanup, and cancellation propagates
only afterward (143 for SIGTERM, 130 for SIGINT). No successful return follows
cancellation. The owned CLI process is killed/reaped on cancellation, timeout or
output excess. No `--rm` race with daemon exit-status inspection is used.

SIGKILL, controller/host crashes and unreachable daemon cannot be repaired by
Python cleanup. The unique recovery name is printed before create. An external
supervisor must persist it, quarantine the worker on unconfirmed disposal or
unknown create outcome, reconcile pending requests, remove that owned name and
verify absence before reuse. A timed-out/interrupted create is unconfirmed even
if an immediate `ps` is empty: a pending daemon create may complete later. This
slice supplies **no external reaper** and never prunes unrelated containers.

A zero container exit code is not proof of honest tests. Candidate pytest/startup
modules, conftest/plugins, witnesses and reports can be forged. The generic
backend does not validate a trusted test entrypoint, assertion execution or
coverage integrity. Those are separate manager-owned integration requirements.

## Required integration before untrusted execution

The engine, runners, CLI, Action and eval lifecycle are not routed through this
backend. A complete integration must preserve the following boundaries rather
than simply accepting a Docker selector.

1. Explicitly select `get_sandbox(kind="docker", image=...)`, label execution mode,
   and block backend/setup/disposal errors without local fallback.
2. Build a reviewed exact-source snapshot without executing host Git hooks,
   fsmonitor, filters or candidate configuration; reject unsafe snapshots rather
   than silently dropping policy files. Host-side Git/parsing remain outside this
   backend and must be scoped consistently before any Git calls.
3. Do not reuse current runner's host `sys.executable`, environment, temporary
   coverage/witness paths or configured-suite tool paths. Use trusted image
   entrypoints with isolated startup and explicit execution checks. No artifacts
   return to the host through this API.
4. Fail required coverage when no comparable trustworthy measurement is available;
   never reduce an existing trusted coverage baseline to informational output.
5. Explicitly reject untrusted eval until agent, gate, verifier, mutation and any
   other candidate execution stage are isolated. **Eval is not safe now.**
6. Keep this backend experimental pending independent review, an approved production
   worker/image, broader adversarial validation and crash recovery ownership. The
   bounded live probes below do not establish every trust boundary. Decide whether
   shared-kernel containment is acceptable.

## Evidence and documentation

`tests/test_sandbox.py` uses mocked Docker responses, safe temporary-file fixtures
and small reviewed local client-capture checks. It covers flags, environment
rejection, metadata/link refusal, failures, bounded capture, timeout and first /
repeated POSIX cancellation including disposal. These tests are **not evidence of
container containment**. No hostile programs, host-secret probes or network
exploitation are run on the developer host.

### Opt-in live Linux probes

`integration/test_docker_sandbox.py` runs only with explicit image opt-in. It uses
reviewed literal payloads **inside Docker**, a dummy host-file/environment canary,
and only its own uniquely named containers. It is outside default test collection;
explicit invocation without an image raises `pytest.UsageError`. Unavailable
daemon/image or unsupported controls also fail, never skip. No pulls, builds,
installs, daemon startup or settings changes occur. Provision/review the
credential-free Python 3.12+ image with `/bin/sleep` separately, then run:

```sh
POW_DOCKER_TEST_IMAGE='reviewed/image@sha256:<64-lowercase-hex-digest>' \
  uv run --frozen pytest -q integration/test_docker_sandbox.py
```

Native Linux evidence: **10 passed** with Docker 29.1.3, Linux
7.0.0-42-generic, cgroup v2/systemd, built-in seccomp and the already provisioned
`python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`
(Python 3.12 slim). The run checked:

- dummy host file, host environment and Docker socket absent; snapshot unchanged;
- UID/GID 65534, zero effective/bounding capabilities, seccomp filter and
  no-new-privileges; root-filesystem writes denied;
- TCP to documentation address `192.0.2.1` rejected with `ENETUNREACH` (not merely
  an unreachable external server timing out);
- cgroup CPU quota `100000/100000`, memory `536870912`, swap `0`, PIDs `64`, and
  the configured FD/file-size rlimits visible inside the container;
- PID exhaustion denied, 512 MiB memory exhaustion killed the workload (137),
  `/work`, `/tmp` and `/dev/shm` quotas returned `ENOSPC`, and two CPU workers
  increased the kernel throttling counter on the multi-CPU host;
- nonzero exit, output excess, timeout, SIGINT and SIGTERM disposed of each owned
  container; independent `container inspect` confirmed absence after each test.

These are bounded probes on one host/image, not an escape assessment or production
approval. They do not establish malicious-image safety, trusted test/coverage
attestation, agent/verifier isolation, cross-platform support, external reaping,
or recovery from SIGKILL, controller/daemon crashes and unknown create outcomes.
The earlier WSL environment could not reach its daemon; that historical blocker
is not the evidence for this native Linux run. No live CI workflow is enabled:
operators must explicitly supply an approved preinstalled image.

Official flag/config references checked:
- [Docker create](https://docs.docker.com/reference/cli/docker/container/create/)
- [Resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)
- [Client proxy injection](https://docs.docker.com/engine/cli/proxy/)
- [Docker CLI configuration](https://docs.docker.com/reference/cli/docker/)
