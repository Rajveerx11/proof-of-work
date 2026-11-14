"""Read-only collection and Git failure regressions for harness integrations."""
import os
import subprocess

import pytest

from proofofwork.core import gitdiff


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    gitdiff._git(str(root), "init", "-q")
    gitdiff._git(str(root), "config", "user.email", "test@example.invalid")
    gitdiff._git(str(root), "config", "user.name", "test")
    (root / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored/\n.proofofwork/log*\n", encoding="utf-8")
    gitdiff._git(str(root), "add", "-A")
    gitdiff._git(str(root), "commit", "-qm", "fixture")
    return root


def metadata(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in (root / ".git").rglob("*") if p.is_file()}


def test_untracked_is_opt_in_and_preserves_git_metadata(repo):
    (repo / "test_new.py").write_bytes(b"import sys\nsys." b"exit(0)\n")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "test_hidden.py").write_text("assert False\n")
    (repo / "empty.txt").touch()
    before = metadata(repo)
    assert gitdiff.collect_diff(str(repo)).files == []
    diff = gitdiff.collect_diff(str(repo), include_untracked=True)
    assert {f.path for f in diff.files} == {"empty.txt", "test_new.py"}
    added = next(f for f in diff.files if f.path == "test_new.py")
    assert added.status == "A" and added.is_test and added.language == "python"
    assert added.added == added.candidate_lines == ["import sys", ("sys." "exit(0)")]
    assert added.added_line_numbers == [1, 2]
    assert metadata(repo) == before


@pytest.mark.parametrize("name", ["space name.py", "-option.py", "unicodé.py",
                                  "bracket[name].py" if os.name == "nt" else "tab\tname.py"])
def test_untracked_paths_and_raw_bytes(repo, name):
    (repo / name).write_bytes(b"first\r\nlast\xff")
    file, = gitdiff.collect_diff(str(repo), include_untracked=True).files
    assert file.path == name
    assert file.added == ["first\r", "last\udcff"]
    assert file.added_line_numbers == [1, 2]


@pytest.mark.parametrize("replacement", ["value = 1\n", "value = 2\n"])
def test_untracked_replacement_of_staged_deletion(repo, replacement):
    gitdiff._git(str(repo), "rm", "--cached", "tracked.py")
    (repo / "tracked.py").write_bytes(replacement.encode())
    before = metadata(repo)
    diff = gitdiff.collect_diff(str(repo), include_untracked=True)
    if replacement == "value = 1\n":
        assert diff.files == []
    else:
        file, = diff.files
        assert (file.status, file.added, file.removed) == ("M", ["value = 2"], ["value = 1"])
    assert metadata(repo) == before


def test_untracked_rejects_staged_mode(repo):
    with pytest.raises(ValueError, match="cannot combine"):
        gitdiff.collect_diff(str(repo), staged=True, include_untracked=True)
    assert gitdiff.collect_diff(str(repo), staged=True).files == []


def test_untracked_symlink_reads_link_not_target(repo, tmp_path):
    external = tmp_path / "outside.py"
    external.write_text("must not be read\n")
    (repo / "link.py").symlink_to(external)
    file, = gitdiff.collect_diff(str(repo), include_untracked=True).files
    assert file.added == [os.readlink(repo / "link.py")]
    assert "must not be read" not in file.added


@pytest.mark.parametrize("failure", ["nonzero", "missing", "timeout"])
def test_git_command_failures_raise_instead_of_empty_diff(monkeypatch, repo, failure):
    def fail(*args, **kwargs):
        assert kwargs["timeout"] == 60
        if failure == "missing":
            raise FileNotFoundError("git missing")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("git", 60)
        return subprocess.CompletedProcess(args[0], 128, b"", b"fatal: broken repository")

    monkeypatch.setattr(gitdiff.subprocess, "run", fail)
    with pytest.raises(gitdiff.GitError):
        gitdiff.collect_diff(str(repo))


def test_worktree_failure_explains_read_only_git(monkeypatch, repo):
    monkeypatch.setattr(gitdiff.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 128, b"", b"Read-only file system"))
    with pytest.raises(gitdiff.GitError, match="read-only .git is unsupported"):
        gitdiff._git(str(repo), "worktree", "add", "--detach", "/unused", "HEAD")


def test_untracked_collection_with_nonwritable_git(repo):
    (repo / "new.py").write_text("answer = 42\n")
    paths = [repo / ".git", *(repo / ".git").rglob("*")]
    modes = {path: path.stat().st_mode for path in paths}
    before = metadata(repo)
    try:
        for path in paths:
            path.chmod(modes[path] & ~0o222)
        file, = gitdiff.collect_diff(str(repo), include_untracked=True).files
        assert file.path == "new.py"
        assert metadata(repo) == before
    finally:
        for path, mode in modes.items():
            path.chmod(mode)
