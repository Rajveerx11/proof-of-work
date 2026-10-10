"""Safe local fixtures and modeled Docker policy/lifecycle, NOT containment proof."""

import errno
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from proofofwork.core.sandbox import RunOutput, SandboxError, docker, get_sandbox
from proofofwork.core.sandbox.local import LocalSandbox

_IMAGE = "example.invalid/tests@sha256:" + "a" * 64
_SERVER_INFO = {
    "OSType": "linux",
    "CgroupVersion": "2",
    "CgroupDriver": "systemd",
    "SecurityOptions": ["name=seccomp,profile=builtin"],
    "Warnings": [],
}


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr(docker.sys, "platform", "linux")
    monkeypatch.setattr(docker.shutil, "which", lambda _: "/usr/bin/docker")
    calls = []
    responses = {}

    def capture(argv, **kwargs):
        args = argv[5:]
        action = args[0]
        assert argv[1] == "--config" and argv[3:5] == ["--host", docker._SOCKET]
        assert os.path.isdir(argv[2]) and not os.listdir(argv[2])
        assert kwargs["env"] == {"PATH": os.defpath, "HOME": argv[2], "LANG": "C.UTF-8"}
        calls.append((args, kwargs))
        if action in responses:
            response = responses[action]
            return response(args, kwargs) if callable(response) else response
        if action == "info":
            return RunOutput(0, json.dumps(_SERVER_INFO), "")
        if action == "image":
            return RunOutput(0, json.dumps([{"Os": "linux", "Config": {"Volumes": None}}]), "")
        if action == "inspect":
            return RunOutput(0, json.dumps({"Status": "exited", "ExitCode": 0}), "")
        if action == "start":
            return RunOutput(0, "modeled output", "")
        return RunOutput(0, "", "")

    monkeypatch.setattr(docker, "_capture", capture)
    return calls, responses


def test_default_local_behavior_is_unchanged(tmp_path, monkeypatch):
    sandbox = get_sandbox()
    assert isinstance(sandbox, LocalSandbox) and sandbox.name == "local"
    monkeypatch.setenv("POW_LOCAL_FIXTURE", "parent")
    result = sandbox.run(
        [sys.executable, "-c", "import os; print(os.environ['POW_LOCAL_FIXTURE'])"],
        str(tmp_path),
        {"POW_LOCAL_FIXTURE": "override"},
    )
    assert result.code == 0 and result.stdout.strip() == "override"


def test_factory_never_ignores_selection_or_image():
    with pytest.raises(ValueError):
        get_sandbox("unknown")
    with pytest.raises(ValueError):
        get_sandbox("local", image=_IMAGE)


@pytest.mark.parametrize(
    "image", [None, "python:latest", "python@sha256:abc", "--bad@sha256:" + "a" * 64]
)
def test_explicit_digest_required_before_cli(monkeypatch, image):
    monkeypatch.setattr(docker.shutil, "which", lambda _: pytest.fail("must reject first"))
    with pytest.raises(SandboxError):
        get_sandbox("docker", image=image)


def test_missing_native_cli_fails_closed(monkeypatch):
    monkeypatch.setattr(docker.shutil, "which", lambda _: None)
    with pytest.raises(SandboxError, match="native Linux"):
        get_sandbox("docker", image=_IMAGE)


@pytest.mark.parametrize(
    "response",
    [
        RunOutput(1, "", "unavailable"),
        RunOutput(0, "{}", "warning"),
        RunOutput(0, "{}", ""),
        RunOutput(0, "invalid", ""),
    ],
)
def test_preflight_failure_never_falls_back(model, response):
    calls, responses = model
    responses["info"] = response
    with pytest.raises(SandboxError):
        get_sandbox("docker", image=_IMAGE)
    assert [args[0] for args, _ in calls] == ["info"]


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"Warnings": ["No memory limit support"]},
        {"Warnings": [None]},
        {"Warnings": ""},
        {"Warnings": "No memory limit support"},
        {"Warnings": {}},
        {"Warnings": False},
        {"Warnings": 0},
    ],
)
def test_structured_warnings_reject_before_image_or_create(model, fields):
    calls, responses = model
    server = {key: value for key, value in _SERVER_INFO.items() if key != "Warnings"}
    server.update(fields)
    responses["info"] = RunOutput(0, json.dumps(server), "")
    with pytest.raises(SandboxError, match="warnings"):
        get_sandbox("docker", image=_IMAGE)
    assert [args[0] for args, _ in calls] == ["info"]


@pytest.mark.parametrize("warnings", [None, []])
def test_structured_empty_warnings_allow_preflight(model, warnings):
    calls, responses = model
    responses["info"] = RunOutput(0, json.dumps({**_SERVER_INFO, "Warnings": warnings}), "")
    assert get_sandbox("docker", image=_IMAGE).name == "docker-experimental"
    assert [args[0] for args, _ in calls] == ["info", "image"]


@pytest.mark.parametrize(
    "response",
    [
        RunOutput(1, "", "missing"),
        RunOutput(0, '[{"Os":"linux","Config":{"Volumes":{"/data":{}}}}]', ""),
        RunOutput(0, '[{"Os":"windows","Config":{}}]', ""),
    ],
)
def test_image_must_be_preinstalled_linux_without_volumes(model, response):
    calls, responses = model
    responses["image"] = response
    with pytest.raises(SandboxError):
        get_sandbox("docker", image=_IMAGE)
    assert [args[0] for args, _ in calls] == ["info", "image"]


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux snapshot")
def test_controls_and_environment_are_explicit(model, tmp_path, monkeypatch):
    calls, _ = model
    monkeypatch.setenv("DUMMY_HOST_TOKEN", "must-not-cross-boundary")
    monkeypatch.setenv("DOCKER_HOST", "tcp://example.invalid:2375")
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid")
    (tmp_path / "app.py").write_text("print('fixture')\n")
    result = get_sandbox("docker", image=_IMAGE).run(["python3", "-I", "app.py"], str(tmp_path))
    assert result.code == 0 and result.stdout == "modeled output"
    create = next(args for args, _ in calls if args[0] == "create")
    for flag in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--cpus=1",
        "--memory=512m",
        "--memory-swap=512m",
        "--pids-limit=64",
        "--no-healthcheck",
        "--log-driver=none",
        "--tmpfs=/work:rw,nosuid,nodev,size=128m,mode=1777",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
        "--shm-size=8m",
    ):
        assert flag in create
    assert "--mount" not in create and "--volume" not in create and "--env-file" not in create
    assert create[create.index(_IMAGE) + 1 : create.index(_IMAGE) + 3] == ["-I", "-c"]
    assert not any("DUMMY_HOST_TOKEN" in arg or "HTTP_PROXY" in arg for arg in create)
    start_kwargs = next(kwargs for args, kwargs in calls if args[0] == "start")
    with tarfile.open(fileobj=io.BytesIO(start_kwargs["data"])) as archive:
        assert archive.getnames() == ["app.py"]
        assert archive.extractfile("app.py").read() == b"print('fixture')\n"
    assert [args[0] for args, _ in calls] == [
        "info",
        "image",
        "create",
        "start",
        "inspect",
        "rm",
        "ps",
    ]


@pytest.mark.parametrize("env", [{"TOKEN": "fixture"}, {"PATH": "/tmp"}, [], "", 0])
def test_unknown_environment_rejected_before_snapshot(model, monkeypatch, env):
    sandbox = get_sandbox("docker", image=_IMAGE)
    monkeypatch.setattr(docker, "_snapshot", lambda _: pytest.fail("must reject first"))
    with pytest.raises(SandboxError, match="environment"):
        sandbox.run(["python3"], "/fixture", env=env)


@pytest.mark.parametrize(
    "cmd,timeout",
    [
        ([], 1),
        (["python3", "\0"], 1),
        ([1], 1),
        (["python3"], 0),
        (["python3"], 601),
        (["python3"], True),
    ],
)
def test_invalid_request_rejects_before_execution(model, monkeypatch, cmd, timeout):
    sandbox = get_sandbox("docker", image=_IMAGE)
    monkeypatch.setattr(docker, "_snapshot", lambda _: pytest.fail("must reject first"))
    with pytest.raises(SandboxError):
        sandbox.run(cmd, "/fixture", timeout=timeout)


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux snapshot")
@pytest.mark.parametrize(
    "name", [".git", ".env.policy", ".ssh/config", "nested/.proofofwork/log.key"]
)
def test_snapshot_rejects_metadata_instead_of_omitting(model, tmp_path, name):
    calls, _ = model
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("reviewed dummy fixture")
    with pytest.raises(SandboxError, match="forbidden"):
        get_sandbox("docker", image=_IMAGE).run(["python3"], str(tmp_path))
    assert [args[0] for args, _ in calls] == ["info", "image"]


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux snapshot traversal")
@pytest.mark.parametrize("phase", ["child-open", "child-scan", "file-open"])
@pytest.mark.parametrize("error_code", [errno.EACCES, errno.ENOENT, errno.EIO])
def test_snapshot_io_error_rejects_before_create(model, tmp_path, monkeypatch, phase, error_code):
    calls, _ = model
    child = tmp_path / "child"
    child.mkdir()
    (child / "policy.py").write_text("raise AssertionError('must not be omitted')\n")
    child_inode = child.stat().st_ino
    original_open = os.open
    original_scandir = os.scandir
    failure = OSError(error_code, "modeled snapshot IO failure")
    injected = []

    def open_file(path, flags, *args, **kwargs):
        target = "child" if phase == "child-open" else "policy.py"
        if phase != "child-scan" and path == target and kwargs.get("dir_fd") is not None:
            injected.append(phase)
            raise failure
        return original_open(path, flags, *args, **kwargs)

    def scan_directory(path):
        if phase == "child-scan" and isinstance(path, int) and os.fstat(path).st_ino == child_inode:
            injected.append(phase)
            raise failure
        return original_scandir(path)

    monkeypatch.setattr(docker.os, "open", open_file)
    monkeypatch.setattr(docker.os, "scandir", scan_directory)
    with pytest.raises(SandboxError, match="cannot read safe snapshot") as error:
        get_sandbox("docker", image=_IMAGE).run(["python3"], str(tmp_path))
    assert error.value.__cause__ is failure
    assert injected == [phase]
    assert [args[0] for args, _ in calls] == ["info", "image"]


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux snapshot")
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "root-symlink"])
def test_snapshot_refuses_links_and_special_files(tmp_path, kind):
    root = tmp_path / "snapshot"
    root.mkdir()
    own_fixture = tmp_path / "fixture"
    own_fixture.write_text("reviewed dummy")
    if kind == "symlink":
        (root / "link").symlink_to(own_fixture)
    elif kind == "hardlink":
        os.link(own_fixture, root / "link")
    elif kind == "fifo":
        os.mkfifo(root / "pipe")
    else:
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        root = tmp_path / "alias"
    with pytest.raises((SandboxError, OSError)):
        docker._snapshot(str(root))


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux snapshot")
def test_snapshot_size_and_entry_limits(tmp_path, monkeypatch):
    (tmp_path / "fixture").write_bytes(b"x" * 32)
    monkeypatch.setattr(docker, "_ARCHIVE_LIMIT", 16)
    with pytest.raises(SandboxError, match="size limit"):
        docker._snapshot(str(tmp_path))
    monkeypatch.setattr(docker, "_ARCHIVE_LIMIT", 1024 * 1024)
    monkeypatch.setattr(docker, "_FILE_LIMIT", 0)
    with pytest.raises(SandboxError, match="entry limit"):
        docker._snapshot(str(tmp_path))


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux lifecycle")
@pytest.mark.parametrize("phase", ["create", "start", "inspect", "rm", "ps"])
def test_daemon_failures_never_skip_disposal_or_pass(model, monkeypatch, phase):
    calls, responses = model
    monkeypatch.setattr(docker, "_snapshot", lambda _: b"fixture")
    responses[phase] = RunOutput(1, "", "modeled failure")
    if phase == "rm":
        responses["ps"] = RunOutput(0, "still-present", "")
    with pytest.raises(SandboxError):
        get_sandbox("docker", image=_IMAGE).run(["python3"], "/fixture")
    assert [args[0] for args, _ in calls][-2:] == ["rm", "ps"]


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux lifecycle")
def test_timeout_disposes_and_returns_timeout(model, monkeypatch):
    calls, responses = model
    monkeypatch.setattr(docker, "_snapshot", lambda _: b"fixture")
    responses["start"] = RunOutput(124, "", "timeout", True)
    result = get_sandbox("docker", image=_IMAGE).run(["python3"], "/fixture", timeout=1)
    assert result.timed_out and result.code == 124
    assert [args[0] for args, _ in calls][-2:] == ["rm", "ps"]


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux lifecycle")
def test_late_create_remains_unconfirmed_despite_empty_ps(model, monkeypatch):
    _, responses = model
    monkeypatch.setattr(docker, "_snapshot", lambda _: b"fixture")
    responses["create"] = RunOutput(124, "", "timeout", True)
    with pytest.raises(SandboxError, match="disposal unconfirmed"):
        get_sandbox("docker", image=_IMAGE).run(["python3"], "/fixture")


@pytest.mark.skipif(sys.platform != "linux", reason="real POSIX signals, modeled Docker")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("phase", ["create", "start", "inspect", "rm", "ps"])
def test_first_cancellation_preserved_and_disposal_completed(model, monkeypatch, signum, phase):
    calls, responses = model
    monkeypatch.setattr(docker, "_snapshot", lambda _: b"fixture")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def canceled(args, kwargs):
        os.kill(os.getpid(), signum)
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGTERM)
        if phase == "inspect":
            return RunOutput(0, '{"Status":"exited","ExitCode":0}', "")
        return RunOutput(0, "", "")

    responses[phase] = canceled
    with pytest.raises(SystemExit) as error:
        get_sandbox("docker", image=_IMAGE).run(["python3"], "/fixture")
    assert error.value.code == 128 + signum
    assert [args[0] for args, _ in calls][-2:] == ["rm", "ps"]
    assert previous == {sig: signal.getsignal(sig) for sig in previous}


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux main-thread ownership")
def test_worker_thread_rejects_before_snapshot(model, monkeypatch):
    sandbox = get_sandbox("docker", image=_IMAGE)
    monkeypatch.setattr(docker, "_snapshot", lambda _: pytest.fail("must not read snapshot"))
    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        pytest.raises(SandboxError, match="main thread"),
    ):
        executor.submit(sandbox.run, ["python3"], "/fixture").result()


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux snapshot")
def test_metadata_directory_itself_cannot_be_snapshot_root(tmp_path):
    root = tmp_path / ".git"
    root.mkdir()
    (root / "config").write_text("reviewed dummy")
    with pytest.raises(SandboxError, match="snapshot root"):
        docker._snapshot(str(root))


@pytest.mark.skipif(sys.platform != "linux", reason="experimental Linux lifecycle")
def test_failed_command_returns_real_exit_code_after_disposal(model, monkeypatch):
    calls, responses = model
    monkeypatch.setattr(docker, "_snapshot", lambda _: b"fixture")
    responses["start"] = RunOutput(7, "modeled failure", "")
    responses["inspect"] = RunOutput(0, '{"Status":"exited","ExitCode":7}', "")
    result = get_sandbox("docker", image=_IMAGE).run(["python3"], "/fixture")
    assert result.code == 7 and not result.timed_out
    assert [args[0] for args, _ in calls][-2:] == ["rm", "ps"]


@pytest.mark.skipif(sys.platform != "linux", reason="safe Linux client cancellation checks")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_capture_reaps_client_on_real_cancellation(monkeypatch, signum):
    processes = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(docker.subprocess, "Popen", spawn)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    program = f"import os,signal,time; os.kill(os.getppid(),{int(signum)}); time.sleep(2)"
    with pytest.raises(SystemExit) as error, docker._cancellation() as check:
        docker._capture(
            [sys.executable, "-c", program], env={"PATH": os.defpath}, check_cancel=check, timeout=3
        )
    assert error.value.code == 128 + signum
    assert len(processes) == 1 and processes[0].returncode == -signal.SIGKILL
    assert processes[0].stdout.closed and processes[0].stderr.closed
    assert previous == {sig: signal.getsignal(sig) for sig in previous}


@pytest.mark.skipif(sys.platform != "linux", reason="safe Linux client capture checks")
def test_capture_bounds_output_and_time_without_container():
    env = {"PATH": os.defpath}
    output = docker._capture([sys.executable, "-c", "print('fixture')"], env=env)
    assert output.code == 0 and output.stdout.strip() == "fixture"
    with pytest.raises(SandboxError, match="output limit"):
        docker._capture([sys.executable, "-c", "print('x' * 65537)"], env=env)
    timeout = docker._capture(
        [sys.executable, "-c", "import time; time.sleep(2)"], env=env, timeout=1
    )
    assert timeout.code == 124 and timeout.timed_out
