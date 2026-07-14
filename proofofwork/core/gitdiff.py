"""Parse a git changeset into the frozen Diff/DiffFile contract via git plumbing.

Two calls: `--name-status -z` for authoritative statuses/renames, `--unified=0`
for the added/removed source lines. We merge them keyed by path.
"""
from __future__ import annotations

import os
import subprocess

from ..types import Diff, DiffFile

_TEST_DIR_MARKERS = ("/tests/", "/test/", "/__tests__/")
_LANG = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts",
}


def _language(path: str) -> str:
    _, ext = os.path.splitext(path)
    return _LANG.get(ext.lower(), "")


def _is_test(path: str) -> bool:
    p = path.replace("\\", "/")
    low = p.lower()
    base = low.rsplit("/", 1)[-1]
    if base.startswith("test_") and base.endswith(".py"):
        return True
    if base.endswith("_test.py"):
        return True
    if any(m in "/" + low for m in _TEST_DIR_MARKERS):
        return True
    for suf in (".test.js", ".test.ts", ".test.jsx", ".test.tsx",
                ".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx"):
        if base.endswith(suf):
            return True
    return False


def _git(root: str, *args: str) -> str:
    """Run git plumbing; return stdout, or '' on any failure (graceful degradation)."""
    # ponytail: swallow-and-empty on error; a broken/absent repo yields an empty Diff
    # rather than crashing the gate. Upgrade path: surface the stderr if callers need it.
    try:
        cp = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except (OSError, ValueError):
        return ""
    if cp.returncode != 0:
        return ""
    return cp.stdout


def _parse_name_status(out: str) -> list[tuple[str, str, str]]:
    """-> [(status, path, old_path)]. status normalized to A/M/D/R."""
    toks = out.split("\0")
    i, files = 0, []
    while i < len(toks):
        st = toks[i]
        if not st:
            i += 1
            continue
        code = st[0]
        if code in ("R", "C"):  # rename/copy: two paths follow
            old = toks[i + 1] if i + 1 < len(toks) else ""
            new = toks[i + 2] if i + 2 < len(toks) else ""
            files.append(("R" if code == "R" else "A", new, old))
            i += 3
        else:
            path = toks[i + 1] if i + 1 < len(toks) else ""
            norm = code if code in ("A", "M", "D") else "M"  # T (typechange) -> M
            files.append((norm, path, ""))
            i += 2
    return files


def _parse_unified(out: str) -> dict[str, tuple[list[str], list[str]]]:
    """path -> (added_lines, removed_lines), text stripped of the +/- prefix."""
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    old = new = key = None
    for line in out.split("\n"):
        if line.startswith("diff --git "):
            old = new = key = None
        elif line.startswith("--- "):
            p = line[4:]
            old = None if p == "/dev/null" else p[2:] if p[:2] in ("a/", "b/") else p
        elif line.startswith("+++ "):
            p = line[4:]
            new = None if p == "/dev/null" else p[2:] if p[:2] in ("a/", "b/") else p
            key = new if new is not None else old
            if key is not None:
                added.setdefault(key, [])
                removed.setdefault(key, [])
        elif key is not None and line.startswith("+") and not line.startswith("+++"):
            added[key].append(line[1:])
        elif key is not None and line.startswith("-") and not line.startswith("---"):
            removed[key].append(line[1:])
    return {k: (added.get(k, []), removed.get(k, [])) for k in added.keys() | removed.keys()}


def collect_diff(root: str, base_ref: str = "HEAD", *, staged: bool = False) -> Diff:
    cached = ["--cached"] if staged else []
    status_out = _git(root, "diff", "--name-status", "-z", *cached, base_ref)
    unified_out = _git(root, "diff", "--unified=0", "--no-color", *cached, base_ref)

    lines_by_path = _parse_unified(unified_out)
    files: list[DiffFile] = []
    for status, path, old_path in _parse_name_status(status_out):
        added, removed = lines_by_path.get(path, ([], []))
        files.append(DiffFile(
            path=path, status=status, old_path=old_path,
            added=list(added), removed=list(removed),
            is_test=_is_test(path), language=_language(path),
        ))
    return Diff(files=files, base_ref=base_ref)
