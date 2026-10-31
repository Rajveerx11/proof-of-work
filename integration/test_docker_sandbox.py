"""Opt-in, reviewed Linux Docker probes; not proof that issue #28 is complete.

POW_DOCKER_TEST_IMAGE must name an already provisioned, credential-free Python
3.12+ image by digest. No pull/build/install/daemon changes or host code execution
of the probe payloads. Every container belongs to this test and is checked by name.
"""

import os
import signal
import threading

import pytest

from proofofwork.core.sandbox import SandboxError, get_sandbox


@pytest.fixture
def live(monkeypatch):
    image = os.environ.get("POW_DOCKER_TEST_IMAGE")
    if not image:
        raise pytest.UsageError(
            "set POW_DOCKER_TEST_IMAGE to a reviewed preinstalled Linux image digest"
        )
    # Explicit invocation must fail when image selection or preflight is unavailable.
    sandbox = get_sandbox("docker", image=image)
    client = sandbox._client
    names = []

    def tracked(*args, **kwargs):
        if args[0] == "create":
            names.append(args[args.index("--name") + 1])
        return client(*args, **kwargs)

    monkeypatch.setattr(sandbox, "_client", tracked)
    try:
        yield sandbox
    finally:
        for name in names:
            # Independently verify absence, not the backend's ps filter/return value.
            state = client("container", "inspect", name)
            if state.code != 1 or f"no such container: {name}" not in state.stderr.lower():
                client("rm", "--force", "--volumes", name)
                pytest.fail(f"backend did not prove disposal of its owned container {name}")


def test_live_host_boundaries_and_kernel_controls(live, tmp_path, monkeypatch):
    canary = tmp_path / "host-only-dummy-canary"
    canary.write_text("reviewed dummy, not a real secret")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "fixture.txt").write_text("snapshot input")
    monkeypatch.setenv("POW_DUMMY_HOST_SECRET", "reviewed dummy")
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid")
    program = f"""
import errno, os, pathlib, resource, socket
assert pathlib.Path('fixture.txt').read_text() == 'snapshot input'
pathlib.Path('fixture.txt').write_text('container-only mutation')
assert not pathlib.Path({str(canary)!r}).exists()
assert 'POW_DUMMY_HOST_SECRET' not in os.environ
assert 'HTTP_PROXY' not in os.environ
assert not pathlib.Path('/var/run/docker.sock').exists()
assert (os.getuid(), os.getgid()) == (65534, 65534)
status = pathlib.Path('/proc/self/status').read_text().splitlines()
fields = dict(line.split(':', 1) for line in status if ':' in line)
assert int(fields['CapEff'].strip(), 16) == 0
assert int(fields['CapBnd'].strip(), 16) == 0
assert fields['NoNewPrivs'].strip() == '1'
assert fields['Seccomp'].strip() == '2'
limits = pathlib.Path('/sys/fs/cgroup')
assert (limits / 'cpu.max').read_text().split() == ['100000', '100000']
assert int((limits / 'memory.max').read_text()) == 512 * 1024**2
assert int((limits / 'memory.swap.max').read_text()) == 0
assert int((limits / 'pids.max').read_text()) == 64
assert resource.getrlimit(resource.RLIMIT_NOFILE) == (256, 256)
assert resource.getrlimit(resource.RLIMIT_FSIZE) == (64 * 1024**2, 64 * 1024**2)
try:
    pathlib.Path('/rootfs-probe').write_text('must fail')
except OSError as exc:
    assert exc.errno == errno.EROFS
else:
    raise AssertionError('root filesystem was writable')
with socket.socket() as connection:
    connection.settimeout(1)
    try:
        connection.connect(('192.0.2.1', 80))
    except OSError as exc:
        assert exc.errno == errno.ENETUNREACH
    else:
        raise AssertionError('unexpected network access')
print('boundaries verified')
"""
    output = live.run(["python3", "-I", "-c", program], str(snapshot), timeout=10)
    assert output.code == 0 and output.stdout.strip() == "boundaries verified", output
    assert canary.read_text() == "reviewed dummy, not a real secret"
    assert sorted(path.name for path in snapshot.iterdir()) == ["fixture.txt"]
    assert (snapshot / "fixture.txt").read_text() == "snapshot input"


@pytest.mark.parametrize("resource", ["pids", "memory", "disk", "cpu"])
def test_live_resource_exhaustion(live, tmp_path, resource):
    programs = {
        "pids": """
import errno, subprocess
children = []
try:
    for _ in range(80):
        try:
            children.append(subprocess.Popen(['/bin/sleep', '30']))
        except OSError as exc:
            assert exc.errno == errno.EAGAIN and len(children) < 64
            print('pids denied', flush=True)
            break
    else:
        raise AssertionError('PID limit not enforced')
finally:
    for child in children:
        child.kill()
        child.wait()
""",
        "memory": """
# Intentionally exceed the 512 MiB cgroup; the controller must survive/dispose.
chunks = []
for _ in range(768):
    chunks.append(bytearray(1024**2))
raise AssertionError('memory limit not enforced')
""",
        "disk": """
import errno, pathlib
for directory, blocks in [('/work', 140), ('/tmp', 20), ('/dev/shm', 12)]:
    try:
        for index in range(blocks):
            # Separate files stay below RLIMIT_FSIZE; hit each tmpfs quota instead.
            pathlib.Path(directory, str(index)).write_bytes(b'x' * 1024**2)
    except OSError as exc:
        assert exc.errno == errno.ENOSPC
    else:
        raise AssertionError('tmpfs quota not enforced: ' + directory)
print('disk denied')
""",
        "cpu": """
import os, pathlib, subprocess, sys
stats = pathlib.Path('/sys/fs/cgroup/cpu.stat')
def throttled():
    return int(dict(line.split() for line in stats.read_text().splitlines())['nr_throttled'])
before = throttled()
worker = 'import time; end = time.monotonic() + 2\\nwhile time.monotonic() < end: pass'
children = []
try:
    for _ in range(2):
        children.append(subprocess.Popen([sys.executable, '-I', '-c', worker]))
    for child in children:
        assert child.wait() == 0
finally:
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait()
if len(os.sched_getaffinity(0)) > 1:
    assert throttled() > before
print('cpu quota verified')
""",
    }
    output = live.run(["python3", "-I", "-c", programs[resource]], str(tmp_path), timeout=10)
    assert not output.timed_out, output
    if resource == "memory":
        assert output.code == 137, output
    else:
        assert output.code == 0 and resource in output.stdout, output


@pytest.mark.parametrize("failure", ["timeout", "output", "exit"])
def test_live_failure_disposal(live, tmp_path, failure):
    program = {
        "timeout": "import time; time.sleep(30)",
        "output": "print('x' * 131072, flush=True)",
        "exit": "raise SystemExit(7)",
    }[failure]
    if failure == "output":
        with pytest.raises(SandboxError, match="output limit"):
            live.run(["python3", "-I", "-c", program], str(tmp_path), timeout=10)
    else:
        output = live.run(["python3", "-I", "-c", program], str(tmp_path), timeout=1)
        assert output.code == (124 if failure == "timeout" else 7), output
        assert output.timed_out == (failure == "timeout")


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_live_cancellation_disposal(live, tmp_path, monkeypatch, signum):
    client = live._client
    timer = None

    def cancel_on_attach(*args, **kwargs):
        nonlocal timer
        if args[0] == "start":
            timer = threading.Timer(1, os.kill, args=(os.getpid(), signum))
            timer.start()
        return client(*args, **kwargs)

    monkeypatch.setattr(live, "_client", cancel_on_attach)
    try:
        with pytest.raises(SystemExit) as error:
            live.run(
                ["python3", "-I", "-c", "import time; time.sleep(30)"], str(tmp_path), timeout=10
            )
        assert error.value.code == 128 + signum
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
