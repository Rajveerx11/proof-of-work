"""Experimental Linux Docker backend; containment has not been live-validated."""

from __future__ import annotations

import io
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager

from . import RunOutput, SandboxError

_ARCHIVE_LIMIT = 32 * 1024 * 1024
_OUTPUT_LIMIT = 64 * 1024
_FILE_LIMIT = 4096
_SOCKET = "unix:///var/run/docker.sock"
_CONTAINER_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp"}
_FORBIDDEN = {
    ".git",
    ".proofofwork",
    ".ssh",
    ".aws",
    ".azure",
    ".gnupg",
    ".docker",
    ".gitconfig",
    ".npmrc",
    ".netrc",
    ".pypirc",
}
_BOOTSTRAP = """
import os, sys, tarfile
with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as archive:
    archive.extractall('/work', filter='data')
os.chdir('/work')
os.execvpe(sys.argv[1], sys.argv[1:], {
    'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp', 'TMPDIR': '/tmp',
})
"""


@contextmanager
def _cancellation():
    """Record signals, not async exceptions; only explicit checks interrupt clients."""
    if threading.current_thread() is not threading.main_thread():
        raise SandboxError("experimental Docker execution requires the main thread")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    pending = None

    def record(signum, _frame):
        nonlocal pending
        if pending is None:
            pending = signum

    def check():
        if pending is not None:
            raise SystemExit(128 + pending)

    try:
        for sig in previous:
            signal.signal(sig, record)
        yield check
    finally:
        failure = sys.exc_info()[1]
        if pending is not None and isinstance(failure, SandboxError):
            print(str(failure), file=sys.stderr, flush=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        check()


def _capture(
    argv: list[str],
    *,
    env: dict[str, str],
    data: bytes = b"",
    timeout: int = 30,
    check_cancel: Callable[[], None] | None = None,
) -> RunOutput:
    """Bound stdin disk and stdout/stderr memory, and always reap the owned CLI client."""
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        if check_cancel is not None:
            check_cancel()
        # Children must not be interrupted by repeated terminal-group cancellation
        # during rm/ps. The parent restores its mask and records every first signal.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        process = None
        try:
            process = subprocess.Popen(
                argv, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
            )
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            output = {"stdout": bytearray(), "stderr": bytearray()}
            deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    if check_cancel is not None:
                        check_cancel()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output[key.data].extend(chunk)
                            if sum(map(len, output.values())) > _OUTPUT_LIMIT:
                                raise SandboxError("Docker output limit exceeded")
                if check_cancel is not None:
                    check_cancel()
                # EOF is not process completion: keep cancellation/time checks live.
                while process.poll() is None:
                    if check_cancel is not None:
                        check_cancel()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    time.sleep(min(remaining, 0.1))
                code = process.returncode
            return RunOutput(
                code,
                output["stdout"].decode("utf-8", "replace"),
                output["stderr"].decode("utf-8", "replace"),
            )
        except subprocess.TimeoutExpired:
            return RunOutput(124, "", "Docker execution time limit exceeded", True)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
                process.stderr.close()
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def _snapshot(cwd: str) -> bytes:
    """Accept only an immutable supervisor-prepared snapshot, never silently filter it."""
    if any(
        part in _FORBIDDEN or part.startswith(".env")
        for part in os.path.realpath(cwd).split(os.sep)
    ):
        raise SandboxError("snapshot root is a forbidden metadata/credential path")

    def traversal_error(error: OSError) -> None:
        raise error

    destination = io.BytesIO()
    root = os.open(cwd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    count = 0
    try:
        with tarfile.open(fileobj=destination, mode="w") as archive:
            for directory, dirs, files, descriptor in os.fwalk(
                ".", dir_fd=root, follow_symlinks=False, onerror=traversal_error
            ):
                for name in sorted(dirs + files):
                    count += 1
                    if count > _FILE_LIMIT:
                        raise SandboxError("snapshot entry limit exceeded")
                    if name in _FORBIDDEN or name.startswith(".env"):
                        raise SandboxError("snapshot contains forbidden metadata/credential path")
                    path = os.path.join(directory, name).removeprefix("./")
                    if len(path) > 1024:
                        raise SandboxError("snapshot path limit exceeded")
                    metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if stat.S_ISDIR(metadata.st_mode):
                        entry = tarfile.TarInfo(path)
                        entry.type = tarfile.DIRTYPE
                        entry.mode = 0o755
                        archive.addfile(entry)
                        continue
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise SandboxError("snapshot links and special files are forbidden")
                    if metadata.st_size + destination.tell() + 2048 > _ARCHIVE_LIMIT:
                        raise SandboxError("snapshot size limit exceeded")
                    fd = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
                    )
                    with os.fdopen(fd, "rb") as source:
                        opened = os.fstat(source.fileno())
                        if (opened.st_ino, opened.st_dev, opened.st_mode, opened.st_nlink) != (
                            metadata.st_ino,
                            metadata.st_dev,
                            metadata.st_mode,
                            1,
                        ):
                            raise SandboxError("snapshot changed during export")
                        data = source.read(metadata.st_size + 1)
                        after = os.fstat(source.fileno())
                        if (
                            len(data) != metadata.st_size
                            or after.st_mtime_ns != metadata.st_mtime_ns
                            or after.st_size != metadata.st_size
                        ):
                            raise SandboxError("snapshot changed during export")
                    entry = tarfile.TarInfo(path)
                    entry.size = len(data)
                    entry.mode = 0o755 if metadata.st_mode & 0o111 else 0o644
                    entry.uid = entry.gid = 65534
                    archive.addfile(entry, io.BytesIO(data))
        if destination.tell() > _ARCHIVE_LIMIT:
            raise SandboxError("snapshot size limit exceeded")
        return destination.getvalue()
    finally:
        os.close(root)


class DockerSandbox:
    name = "docker-experimental"

    def __init__(self, image: str | None):
        if not image or not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image):
            raise SandboxError("Docker requires an explicit NAME@sha256:DIGEST image")
        self.binary = shutil.which("docker")
        if sys.platform != "linux" or self.binary is None:
            raise SandboxError("experimental Docker requires a native Linux CLI/daemon")
        self.image = image
        try:
            result = self._client("info", "--format", "{{json .}}")
            if result.code or result.stderr:
                raise SandboxError("Docker daemon unavailable or resource preflight warned")
            server = json.loads(result.stdout)
            warnings = server.get("Warnings")
            if "Warnings" not in server or (
                warnings is not None and (not isinstance(warnings, list) or warnings)
            ):
                raise SandboxError("Docker structured warnings are nonempty, missing or malformed")
            if (
                server.get("OSType") != "linux"
                or server.get("CgroupVersion") != "2"
                or server.get("CgroupDriver") not in {"systemd", "cgroupfs"}
                or "name=seccomp,profile=builtin" not in server.get("SecurityOptions", [])
            ):
                raise SandboxError("Docker requires Linux cgroup v2 and built-in seccomp")
            result = self._client("image", "inspect", image)
            if result.code or result.stderr:
                raise SandboxError("preinstalled pinned Docker image unavailable")
            info = json.loads(result.stdout)[0]
            if info.get("Os") != "linux" or info["Config"].get("Volumes"):
                raise SandboxError("image must be Linux without declared volumes")
        except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise SandboxError("Docker preflight failed") from exc

    def _client(self, *args: str, **kwargs) -> RunOutput:
        # Empty per-call config prevents Docker's automatic proxy/credential injection.
        with tempfile.TemporaryDirectory(prefix="pow-docker-config-") as config:
            env = {"PATH": os.defpath, "HOME": config, "LANG": "C.UTF-8"}
            return _capture(
                [self.binary, "--config", config, "--host", _SOCKET, *args], env=env, **kwargs
            )

    def run(
        self, cmd: list[str], cwd: str, env: dict | None = None, timeout: int = 600
    ) -> RunOutput:
        if env is not None and (not isinstance(env, dict) or env):
            raise SandboxError("Docker rejects all environment overrides; never forward host env")
        if (
            not isinstance(cmd, list)
            or not cmd
            or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in cmd)
            or type(timeout) is not int
            or not 1 <= timeout <= 600
        ):
            raise SandboxError("invalid Docker command or timeout (1..600 seconds)")
        with _cancellation() as check_cancel:
            try:
                data = _snapshot(cwd)
            except OSError as exc:
                raise SandboxError("cannot read safe snapshot") from exc
            check_cancel()
            name = "proofofwork-" + uuid.uuid4().hex
            print(f"experimental Docker recovery name: {name}", file=sys.stderr, flush=True)
            confirmed = False
            try:
                created = self._client(
                    "create",
                    "--name",
                    name,
                    "--pull=never",
                    "--interactive",
                    "--network=none",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--user=65534:65534",
                    "--cpus=1",
                    "--memory=512m",
                    "--memory-swap=512m",
                    "--pids-limit=64",
                    "--ulimit=nofile=256:256",
                    "--ulimit=fsize=67108864:67108864",
                    "--tmpfs=/work:rw,nosuid,nodev,size=128m,mode=1777",
                    "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                    "--shm-size=8m",
                    "--log-driver=none",
                    "--no-healthcheck",
                    "--entrypoint=python3",
                    *[f"--env={key}={value}" for key, value in _CONTAINER_ENV.items()],
                    self.image,
                    "-I",
                    "-c",
                    _BOOTSTRAP,
                    *cmd,
                    check_cancel=check_cancel,
                )
                confirmed = created.code == 0 and not created.timed_out
                check_cancel()
                if created.code or created.stderr:
                    raise SandboxError("Docker create failed or warned")
                output = self._client(
                    "start",
                    "--attach",
                    "--interactive",
                    name,
                    data=data,
                    timeout=timeout,
                    check_cancel=check_cancel,
                )
                check_cancel()
                if output.timed_out:
                    return output
                state = self._client(
                    "inspect", "--format", "{{json .State}}", name, check_cancel=check_cancel
                )
                check_cancel()
                try:
                    status = json.loads(state.stdout)
                    code = status["ExitCode"]
                    if (
                        state.code
                        or status.get("Status") != "exited"
                        or type(code) is not int
                        or output.code not in {0, code}
                    ):
                        raise SandboxError("container completion unconfirmed or contradictory")
                except (ValueError, KeyError, TypeError) as exc:
                    raise SandboxError("container status unavailable") from exc
                return RunOutput(code, output.stdout, output.stderr)
            except OSError as exc:
                raise SandboxError("Docker execution failed") from exc
            finally:
                # No cancellation checks here: first/repeat signals defer until disposal ends.
                try:
                    self._client("rm", "--force", "--volumes", name)
                    remaining = self._client(
                        "ps", "--all", "--filter", f"name=^{name}$", "--format", "{{.ID}}"
                    )
                    if not confirmed or remaining.code or remaining.stdout.strip():
                        raise SandboxError(
                            "unknown create outcome or container absence unconfirmed"
                        )
                except (OSError, SandboxError) as exc:
                    raise SandboxError(f"Docker disposal unconfirmed for {name}") from exc
