"""Score an agent changeset with the deterministic Proof-of-Work gate."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from .. import engine
from ..types import Finding, Severity, Verdict


def snapshot_workspace(source: Path, target: Path) -> None:
    """Capture regular agent output without following links or copying Git metadata."""
    _reject_unsafe_entries(source)
    shutil.copytree(
        source,
        target,
        symlinks=False,
        ignore=shutil.ignore_patterns(".git"),
    )


def gate_failure(message: str) -> Verdict:
    detail = f"could not score agent changes: {message}"
    return Verdict(
        passed=False,
        reasons=[f"BLOCK eval:gate-error: {detail}"],
        findings=[
            Finding(
                rule="eval:gate-error",
                severity=Severity.BLOCK,
                message=detail,
            )
        ],
    )


def score_gate(
    fixture: Path,
    agent_workspace: Path,
    gate_workspace: Path,
    *,
    instruction: str,
    db_path: Path,
    protected_paths: tuple[str, ...] = (),
) -> Verdict:
    """Compare agent output with a separately committed fixture baseline.

    The agent never works inside the baseline repository. This prevents an ordinary
    agent commit or index edit from hiding its changes from the gate.
    """
    try:
        _prepare_baseline(fixture, gate_workspace, instruction)
        _replace_worktree(gate_workspace, agent_workspace)
        # Intent-to-add makes new, untracked files visible to ``git diff HEAD``.
        _git(gate_workspace, "add", "--intent-to-add", "--all", "--force", "--", ".")
        protected_findings = _protected_file_findings(
            fixture,
            gate_workspace,
            protected_paths,
        )
        return engine.check(
            root=str(gate_workspace),
            base_ref="HEAD",
            run_tests=False,
            db_path=str(db_path),
            extra_findings=protected_findings,
        )
    except Exception as exc:  # noqa: BLE001 - a broken gate must fail the eval closed
        return gate_failure(str(exc))


def _prepare_baseline(fixture: Path, gate_workspace: Path, instruction: str) -> None:
    shutil.copytree(
        fixture,
        gate_workspace,
        symlinks=False,
        ignore=shutil.ignore_patterns(".git"),
    )
    (gate_workspace / "TASK.md").write_text(instruction + "\n", encoding="utf-8")
    _git(gate_workspace, "init", "--quiet")
    _git(gate_workspace, "add", "--all", "--force")
    _git(
        gate_workspace,
        "-c",
        "user.name=Proof-of-Work",
        "-c",
        "user.email=eval@proof-of-work.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "eval fixture baseline",
    )


def _replace_worktree(gate_workspace: Path, agent_workspace: Path) -> None:
    _reject_unsafe_entries(agent_workspace)

    for child in gate_workspace.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()

    for child in agent_workspace.iterdir():
        if child.name == ".git":
            continue
        target = gate_workspace / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(
                child,
                target,
                symlinks=True,
                ignore=shutil.ignore_patterns(".git"),
            )
        else:
            shutil.copy2(child, target)


def _reject_unsafe_entries(workspace: Path) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    for root, directories, files in os.walk(workspace, followlinks=False):
        for name in [*directories, *files]:
            path = Path(root) / name
            metadata = path.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if path.is_symlink() or (reparse_flag and attributes & reparse_flag):
                relative = path.relative_to(workspace)
                raise ValueError(f"agent workspace contains a symlink or reparse point: {relative}")
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                relative = path.relative_to(workspace)
                raise ValueError(f"agent workspace contains a special file: {relative}")


def _protected_file_findings(
    fixture: Path,
    gate_workspace: Path,
    protected_paths: tuple[str, ...],
) -> list[Finding]:
    findings: list[Finding] = []
    for protected in protected_paths:
        baseline = fixture / protected
        candidate = gate_workspace / protected
        try:
            changed = (
                not candidate.is_file()
                or baseline.read_bytes() != candidate.read_bytes()
                or stat.S_IMODE(baseline.stat().st_mode)
                != stat.S_IMODE(candidate.stat().st_mode)
            )
        except OSError:
            changed = True
        if changed:
            findings.append(
                Finding(
                    rule="eval:protected-path-changed",
                    severity=Severity.BLOCK,
                    message=f"agent changed protected benchmark path: {protected}",
                    file=protected,
                )
            )
    return findings


def _git(root: Path, *args: str) -> None:
    environment = {"PATH": os.environ.get("PATH", "")}
    if os.name == "nt":
        for key in ("SYSTEMROOT", "COMSPEC", "WINDIR", "PATHEXT"):
            if key in os.environ:
                environment[key] = os.environ[key]
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        shell=False,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
