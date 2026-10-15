"""Parse a git changeset into the frozen Diff/DiffFile contract via git plumbing.

`--name-status -z` supplies authoritative statuses/rename/copy paths; `--unified=0`
supplies changed lines. Copies also use a no-renames patch for the entire destination.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess

from ..types import Diff, DiffFile

_TEST_DIR_MARKERS = ("/tests/", "/test/", "/__tests__/")
_LANG = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
    ".mjsx": "js", ".cjsx": "js", ".mtsx": "ts", ".ctsx": "ts",
}
_JS_TEST_SUFFIX = re.compile(r"\.(?:test|spec)\.[cm]?[jt]s(?:x)?$")
_PY_NORECURSEDIRS = ("*.egg", ".*", "_darcs", "build", "CVS", "dist",
                     "node_modules", "venv", "{arch}")


def _language(path: str) -> str:
    _, ext = os.path.splitext(path)
    return _LANG.get(ext.lower(), "")


def _is_test(path: str) -> bool:
    # Git uses '/' separators; a backslash is a literal filename character.
    low = path.lower()
    base = low.rsplit("/", 1)[-1]
    if base.startswith("test_") and base.endswith(".py"):
        return True
    if base.endswith("_test.py"):
        return True
    if any(m in "/" + low for m in _TEST_DIR_MARKERS):
        return True
    return bool(_JS_TEST_SUFFIX.search(base))


def _test_discovery(path: str) -> str:
    """Known default collection family, not proof of custom collection/configuration."""
    base = path.rsplit("/", 1)[-1]
    directories = path.split("/")[:-1]
    # pytest has a literal .py guard followed by platform-aware filename matching.
    if base.endswith(".py") and any(fnmatch.fnmatch(base, pattern)
                                    for pattern in ("test_*.py", "*_test.py")):
        return "" if any(fnmatch.fnmatch(part, pattern) for part in directories
                         for pattern in _PY_NORECURSEDIRS) else "python"
    if _language(path) in ("js", "ts") and (
        _JS_TEST_SUFFIX.search(base) or "__tests__" in directories
    ):
        # __tests__ is a Jest default, not a Vitest default.
        return "" if "node_modules" in directories else "js"
    return ""


def _git(root: str, *args: str) -> str:
    """Run git plumbing; raise on errors so an incomplete diff cannot pass."""
    try:
        cp = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-c", "diff.suppressBlankEmpty=false", *args],
            cwd=root, capture_output=True, check=False,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"git unavailable: {exc}") from exc
    if cp.returncode != 0:
        detail = cp.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    # No universal newline conversion: CRs may belong to NUL-delimited paths.
    return cp.stdout.decode("utf-8", errors="surrogateescape")


def _parse_name_status(out: str) -> list[tuple[str, str, str]]:
    """-> [(status, path, old_path)]; retain C and reject incomplete/unmerged input."""
    if out and not out.endswith("\0"):
        raise ValueError("unterminated Git name-status output")
    toks = out.split("\0")[:-1]
    i, files = 0, []
    while i < len(toks):
        st = toks[i]
        if not re.fullmatch(r"[ADMT]|[RC](?:\d{1,3})?", st):
            raise ValueError("unsupported or unmerged Git status")
        code = st[0]
        count = 2 if code in ("R", "C") else 1
        paths = toks[i + 1:i + count + 1]
        if len(paths) != count or not all(paths):
            raise ValueError("incomplete Git name-status paths")
        if count == 2:
            files.append((code, paths[1], paths[0]))
        else:
            files.append(("M" if code == "T" else code, paths[0], ""))
        i += count + 1
    return files


def _patch_path(value: str) -> str | None:
    """Decode Git's C-quoted patch path; name-status paths are already raw/NUL-delimited."""
    if value == "/dev/null":
        return None
    if value.startswith('"'):
        raw = bytearray()
        i = 1
        escapes = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}
        while i < len(value) and value[i] != '"':
            if value[i] != "\\":
                raw.extend(value[i].encode("utf-8", "surrogateescape"))
                i += 1
                continue
            i += 1
            if i >= len(value):
                raise ValueError("unterminated quoted Git path escape")
            if value[i] in "01234567":
                end = i + 1
                while end < min(i + 3, len(value)) and value[end] in "01234567":
                    end += 1
                raw.append(int(value[i:end], 8))
                i = end
            else:
                raw.extend(bytes([escapes[value[i]]]) if value[i] in escapes else
                           value[i].encode("utf-8", "surrogateescape"))
                i += 1
        if i == len(value):
            raise ValueError("unterminated quoted Git path")
        value = raw.decode("utf-8", "surrogateescape")
    else:
        value = value.split("\t", 1)[0]  # Git appends a tab to unquoted spaced headers.
    return value[2:] if value[:2] in ("a/", "b/") else value


def _parse_unified(out: str) -> dict[str, tuple[list[str], list[str]]]:
    """path -> (added_lines, removed_lines), text stripped of the +/- prefix."""
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    old = new = key = None
    in_hunk = False
    for line in out.split("\n"):
        if line.startswith("diff --git "):
            old = new = key = None
            in_hunk = False
        elif line.startswith("@@ "):
            in_hunk = True
        elif not in_hunk and line.startswith("--- "):
            old = _patch_path(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            new = _patch_path(line[4:])
            key = new if new is not None else old
            if key is not None:
                added.setdefault(key, [])
                removed.setdefault(key, [])
        elif in_hunk and key is not None and line.startswith("+"):
            added[key].append(line[1:])
        elif in_hunk and key is not None and line.startswith("-"):
            removed[key].append(line[1:])
    return {k: (added.get(k, []), removed.get(k, [])) for k in added.keys() | removed.keys()}


def parse_patch(text: str) -> Diff:
    """Parse a stored unified-diff patch into the Diff contract (for the learning corpora).

    Reuses _parse_unified for the added/removed lines and derives A/M/D status from the
    ---/+++ dev/null markers. Renames are not reconstructed — corpus patches use plain
    add/modify/delete, which is all the loop needs.
    """
    lines_by_path = _parse_unified(text)
    status: dict[str, str] = {}
    old = new = None
    in_hunk = False
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            old = new = None
            in_hunk = False
        elif line.startswith("@@ "):
            in_hunk = True
        elif not in_hunk and line.startswith("--- "):
            old = _patch_path(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            new = _patch_path(line[4:])
            key = new if new is not None else old
            if key is not None:
                status[key] = "A" if old is None else "D" if new is None else "M"
    files: list[DiffFile] = []
    for path, (added, removed) in sorted(lines_by_path.items()):
        files.append(DiffFile(
            path=path, status=status.get(path, "M"),
            added=list(added), removed=list(removed),
            is_test=_is_test(path), language=_language(path),
        ))
    return Diff(files=files)


def collect_diff(root: str, base_ref: str = "HEAD", *, staged: bool = False) -> Diff:
    # Resolve first: an option-looking ref (e.g. --quiet) must not change git diff's
    # behavior or turn a real changeset into an empty passing diff.
    commit = _git(root, "rev-parse", "--verify", "--end-of-options",
                  f"{base_ref}^{{commit}}").strip()
    cached = ["--cached"] if staged else []
    moves = ["--find-renames", "--find-copies", "--find-copies-harder"]
    status_out = _git(root, "diff", "--name-status", "-z", *moves, *cached, commit, "--")
    # Force raw textual diffing. A changeset-controlled .gitattributes file must not
    # suppress detector input with ``-diff`` or invoke a textconv/external driver.
    unified_out = _git(
        root,
        "diff",
        "--unified=0",
        "--no-color",
        "--text",
        "--no-ext-diff",
        "--no-textconv",
        *moves,
        *cached,
        commit,
        "--",
    )

    lines_by_path = _parse_unified(unified_out)
    statuses = _parse_name_status(status_out)
    # Copy patches may have no hunks. Inspect the entire NEW destination as additions,
    # not removals from the still-existing source. --cached never reads dirty text.
    copies = (_parse_unified(_git(
        root, "diff", "--unified=0", "--no-color", "--text", "--no-ext-diff",
        "--no-textconv", "--no-renames", *cached, commit, "--",
    )) if any(status == "C" for status, _, _ in statuses) else {})
    files: list[DiffFile] = []
    for status, path, old_path in statuses:
        added, removed = (copies.get(path, ([], [])) if status == "C" else
                          lines_by_path.get(path, ([], [])))
        files.append(DiffFile(
            path=path, status=status, old_path=old_path,
            added=list(added), removed=list(removed),
            is_test=_is_test(path) or _is_test(old_path),
            language=_language(path) or _language(old_path),
        ))
    return Diff(files=files, base_ref=base_ref, base_commit=commit)
