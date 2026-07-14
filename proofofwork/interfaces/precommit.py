"""Git pre-commit hook installer. The hook runs the gate on staged changes and
blocks the commit on a nonzero exit."""
from __future__ import annotations

import os

HOOK_BODY = """#!/bin/sh
# Proof-of-Work pre-commit gate — blocks the commit if the work doesn't check out.
exec proof-of-work check --staged
"""


def install(root: str = ".") -> str:
    """Write .git/hooks/pre-commit; back up any existing hook first. Returns the path."""
    hooks_dir = os.path.join(os.path.abspath(root), ".git", "hooks")
    if not os.path.isdir(hooks_dir):
        raise FileNotFoundError(f"no git hooks dir at {hooks_dir} — is {root} a git repo?")

    path = os.path.join(hooks_dir, "pre-commit")
    if os.path.exists(path):
        os.replace(path, path + ".bak")  # don't clobber an existing hook silently

    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write(HOOK_BODY)
    os.chmod(path, 0o755)
    return path
