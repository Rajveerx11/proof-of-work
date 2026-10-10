"""Commit-pinned custom commands: reviewed exit-code evidence, not candidate attestation."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from pathlib import Path, PurePosixPath

from ..types import TestResult
from .gitdiff import _git
from .sandbox import Sandbox

CONFIG = ".proofofwork/suites.json"
_SHELLS = {"sh", "bash", "zsh", "dash", "cmd", "powershell", "pwsh", "fish", "csh", "ksh"}


def relative_path(value: object, *, dot: bool = False) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\0" in value or PurePosixPath(value).is_absolute()
            or any(ord(char) < 32 or char in '<>"|?*' for char in value)
            or (value != "." and any(not part or part.endswith((".", " "))
                                     for part in value.split("/")))
            or (value == "." and not dot)):
        raise ValueError(f"invalid contained relative path: {value!r}")
    return value


def contained(root: str, value: str) -> Path:
    base = Path(root).absolute()
    path = base / value
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"path escapes candidate root: {value}")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        # Python 3.11 has no Path.is_junction(). Reject all Windows reparse
        # points, including junctions whose targets stay inside the candidate.
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError(f"linked suite path unsupported: {value}")
        if part == base:
            break
    return path


def configured_mode(root: str, *, staged: bool, changed_paths: list[str]) -> bool:
    """Configuration presence opts in, but never supplies the trusted commands."""
    if CONFIG in changed_paths or (not staged and os.path.lexists(Path(root) / CONFIG)):
        return True
    if staged:
        return bool(_git(root, "ls-files", "--stage", "--", CONFIG).strip()
                    or _git(root, "ls-tree", "HEAD", "--", CONFIG).strip())
    return False


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate configuration key: {key}")
        result[key] = value
    return result


def load_suites(root: str, reference: str) -> tuple[str, list[dict]]:
    # Resolve exactly once, then read only objects addressed by that immutable commit.
    commit = _git(root, "rev-parse", "--verify", "--end-of-options",
                  f"{reference}^{{commit}}").strip()
    entry = _git(root, "ls-tree", commit, "--", CONFIG).strip()
    if not entry.startswith(("100644 blob ", "100755 blob ")):
        raise ValueError("trusted suite configuration missing or not a regular file")
    config = json.loads(_git(root, "show", f"{commit}:{CONFIG}"), object_pairs_hook=_unique)
    if (not isinstance(config, dict) or set(config) != {"version", "suites"}
            or type(config["version"]) is not int or config["version"] != 1
            or not isinstance(config["suites"], list) or not config["suites"]):
        raise ValueError("expected version 1 configuration with nonempty suites")
    ids = set()
    for suite in config["suites"]:
        if not isinstance(suite, dict) or set(suite) != {
            "id", "cwd", "argv", "prerequisites", "timeout", "evidence"
        }:
            raise ValueError("invalid suite fields")
        sid = suite["id"]
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", sid):
            raise ValueError("invalid stable suite id")
        if sid in ids:
            raise ValueError(f"duplicate suite id: {sid}")
        ids.add(sid)
        relative_path(suite["cwd"], dot=True)
        argv = suite["argv"]
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)):
            raise ValueError(f"{sid}: argv must be a nonempty string array")
        if ("/" in argv[0] or "\\" in argv[0] or ":" in argv[0]
                or Path(argv[0]).stem.lower() in _SHELLS):
            raise ValueError(f"{sid}: executable must be an external tool name, not a shell/path")
        if not isinstance(suite["prerequisites"], list):
            raise TypeError(f"{sid}: prerequisites must be relative paths")
        for prerequisite in suite["prerequisites"]:
            relative_path(prerequisite)
        if type(suite["timeout"]) is not int or not 1 <= suite["timeout"] <= 600:
            raise ValueError(f"{sid}: timeout must be 1..600 seconds")
        if suite["evidence"] != "exit-code":
            raise ValueError(f"{sid}: only explicit exit-code evidence is supported")
    return commit, config["suites"]


def check_coverage_policy(root: str, commit: str, *, base_ref: str, staged: bool,
                          update_baseline: bool) -> None:
    """Do not let a second suite base bypass the existing trusted coverage policy."""
    from .detector.coverage_delta import baseline_path

    if update_baseline:
        raise ValueError("configured exit-code suites do not support --update-baseline")
    refs = {commit, _git(root, "rev-parse", "--verify", "--end-of-options",
                         f"{base_ref}^{{commit}}").strip()}
    if staged:
        refs.add(_git(root, "rev-parse", "--verify", "HEAD^{commit}").strip())
    for ref in refs:
        if _git(root, "ls-tree", ref, "--", ".proofofwork/baseline.json").strip():
            raise ValueError("trusted coverage baseline exists; configured coverage is unsupported")
    if (staged or base_ref == "HEAD") and os.path.lexists(baseline_path(root, staged=staged)):
        raise ValueError("coverage baseline exists; configured coverage is unsupported")


def run_suites(sandbox: Sandbox, root: str, suites: list[dict], commit: str, *,
               source_root: str, env: dict[str, str] | None = None) -> TestResult:
    result = TestResult(framework="configured", suite_base=commit, evidence="exit-code",
                        required_suites=[suite["id"] for suite in suites])
    for index, suite in enumerate(suites):
        outcome = {"id": suite["id"], "outcome": "unavailable", "exit_code": None, "raw": ""}
        try:
            cwd = contained(root, suite["cwd"])
            if not cwd.is_dir():
                raise ValueError("suite cwd missing")
            for prerequisite in suite["prerequisites"]:
                if not contained(str(cwd), prerequisite).exists():
                    raise ValueError(f"missing prerequisite: {prerequisite}")
            tool = shutil.which(suite["argv"][0])
            # Check the lookup location before following links: a candidate PATH
            # directory may be a junction to an otherwise external native tool.
            # abspath normalizes relative paths and '..' without dereferencing;
            # native Path comparisons handle Windows case and distinct drives.
            lookup_path = Path(os.path.abspath(tool)) if tool else None
            roots = [Path(os.path.abspath(base)) for base in (root, source_root)]
            if (lookup_path is None or lookup_path.suffix.lower() in {".bat", ".cmd"}
                    or any(lookup_path.is_relative_to(base) for base in roots)):
                raise ValueError("required native tool missing, batch wrapper, or candidate-controlled")
            tool_path = lookup_path.resolve()
            if (tool_path.suffix.lower() in {".bat", ".cmd"}
                    or any(tool_path.is_relative_to(base.resolve()) for base in roots)):
                raise ValueError("required native tool missing, batch wrapper, or candidate-controlled")
            output = sandbox.run([str(tool_path), *suite["argv"][1:]], cwd=str(cwd), env=env,
                                 timeout=suite["timeout"])
            result.executed_suites.append(suite["id"])
            known = type(output.code) is int
            outcome.update(exit_code=output.code if known else None,
                           outcome=("timeout" if output.timed_out else
                                    "unknown" if not known else
                                    "passed" if output.code == 0 else "failed"),
                           raw=(str(output.stdout) + str(output.stderr))[-2000:])
        except KeyboardInterrupt:
            outcome.update(outcome="interrupted", raw="suite execution interrupted")
            result.suites.append(outcome)
            result.suites.extend({"id": pending["id"], "outcome": "interrupted",
                                  "exit_code": None, "raw": "not started: run interrupted"}
                                 for pending in suites[index + 1:])
            break
        except Exception as exc:  # noqa: BLE001 - retain all required suite outcomes, fail closed
            outcome["raw"] = f"{type(exc).__name__}: {exc}"
        result.suites.append(outcome)
    result.ran = bool(result.executed_suites)
    result.passed = bool(result.suites) and all(item["outcome"] == "passed" for item in result.suites)
    result.raw = "\n".join(f"{item['id']}: {item['outcome']} {item['raw']}"
                           for item in result.suites)
    return result
