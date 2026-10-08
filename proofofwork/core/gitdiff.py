"""Parse a git changeset into the frozen Diff/DiffFile contract via git plumbing.

`--name-status -z` supplies authoritative statuses/renames; full-context patches
supply added/removed lines, candidate lexical context, and added-line coordinates.
Copies also use a no-renames patch to inspect the entire new destination, even when
Git's copy patch has no hunks. Cached patches never read dirty working-tree text.
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
    ".mjsx": "js", ".cjsx": "js",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
    ".mtsx": "ts", ".ctsx": "ts",
}
_JS_TEST_SUFFIX = re.compile(r"\.(?:test|spec)\.[cm]?[jt]sx?$")
_PY_NORECURSEDIRS = ("*.egg", ".*", "_darcs", "build", "CVS", "dist",
                     "node_modules", "venv", "{arch}")
_FULL_CONTEXT = "--unified=2147483647"


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
    return bool(_JS_TEST_SUFFIX.search(base))


def _test_discovery(path: str) -> str:
    """Known default collection families, not proof of configured discovery.

    Keep this narrower than _is_test: a helper under tests/ is test-related but
    is not necessarily a collected test. JS __tests__ is a Jest default, not Vitest's.
    Explicit custom includes/excludes and environment-directory detection remain
    outside this path-only heuristic.
    """
    p = path.replace("\\", "/")
    base = p.rsplit("/", 1)[-1]
    language = _language(path)
    if base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py")):
        directories = p.split("/")[:-1]
        if any(fnmatch.fnmatch(part, pattern)
               for part in directories for pattern in _PY_NORECURSEDIRS):
            return ""
        return "python"
    if language in ("js", "ts") and (
        _JS_TEST_SUFFIX.search(base) or "/__tests__/" in "/" + p
    ):
        return "" if "node_modules" in p.split("/")[:-1] else "js"
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
    # Universal newline translation would corrupt CR-containing NUL-delimited paths.
    return cp.stdout.decode("utf-8", errors="surrogateescape")


def _parse_name_status(out: str) -> list[tuple[str, str, str]]:
    """-> [(status, path, old_path)]. status normalized to A/M/D/R/C."""
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
            files.append((code, new, old))
            i += 3
        else:
            path = toks[i + 1] if i + 1 < len(toks) else ""
            norm = code if code in ("A", "M", "D") else "M"  # T (typechange) -> M
            files.append((norm, path, ""))
            i += 2
    return files


def _patch_path(value: str) -> str | None:
    """Decode Git's C-quoted patch paths (name-status paths are already NUL-delimited)."""
    if value == "/dev/null":
        return None
    if value.startswith('"'):
        raw = bytearray()
        i = 1
        escapes = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13}
        while i < len(value) and value[i] != '"':
            if value[i] != "\\":
                raw.extend(value[i].encode("utf-8", errors="surrogateescape"))
                i += 1
                continue
            i += 1
            if i >= len(value):
                raise ValueError("unterminated escape in quoted Git path")
            if value[i] in "01234567":
                end = i + 1
                while end < min(i + 3, len(value)) and value[end] in "01234567":
                    end += 1
                raw.append(int(value[i:end], 8))
                i = end
            else:
                raw.extend(bytes([escapes[value[i]]]) if value[i] in escapes
                           else value[i].encode("utf-8", errors="surrogateescape"))
                i += 1
        value = raw.decode("utf-8", errors="surrogateescape")
    else:
        # Git terminates an unquoted header containing spaces with a tab.
        value = value.split("\t", 1)[0]
    return value[2:] if value[:2] in ("a/", "b/") else value


def _parse_unified(
    out: str, *, context: dict[str, tuple[list[str], list[int]]] | None = None,
) -> dict[str, tuple[list[str], list[str]]]:
    """path -> (added, removed); optionally capture full-context candidate + positions.

    Only full-context Git patches may supply ``context``. Partial corpus patches
    still use the original added/removed-only contract.
    """
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    old = new = key = None
    in_hunk = False
    new_line = 0
    for line in out.split("\n"):
        line = line.removesuffix("\r")
        if line.startswith("diff --git "):
            old = new = key = None
            in_hunk = False
        elif line.startswith("@@ "):
            in_hunk = True
            if context is not None:
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if match is None:
                    raise ValueError("invalid Git hunk coordinates")
                new_line = int(match.group(1))
        elif not in_hunk and line.startswith("--- "):
            old = _patch_path(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            new = _patch_path(line[4:])
            key = new if new is not None else old
            if key is not None:
                added.setdefault(key, [])
                removed.setdefault(key, [])
                if context is not None:
                    context.setdefault(key, ([], []))
        elif in_hunk and key is not None and line.startswith("+"):
            added[key].append(line[1:])
            if context is not None:
                context[key][0].append(line[1:])
                context[key][1].append(new_line)
                new_line += 1
        elif in_hunk and key is not None and line.startswith("-"):
            removed[key].append(line[1:])
        elif in_hunk and key is not None and line.startswith(" ") and context is not None:
            context[key][0].append(line[1:])
            new_line += 1
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
        _FULL_CONTEXT,
        "--no-color",
        "--text",
        "--no-ext-diff",
        "--no-textconv",
        *moves,
        *cached,
        commit,
        "--",
    )

    context: dict[str, tuple[list[str], list[int]]] = {}
    lines_by_path = _parse_unified(unified_out, context=context)
    statuses = _parse_name_status(status_out)
    # A C100 patch contains no source lines. A copy introduces all destination
    # content, not just the edits relative to the still-existing source. Diffing
    # without move detection also respects --cached and Git's raw-text safeguards.
    copy_context: dict[str, tuple[list[str], list[int]]] = {}
    copies = (_parse_unified(_git(
        root, "diff", _FULL_CONTEXT, "--no-color", "--text", "--no-ext-diff",
        "--no-textconv", "--no-renames", *cached, commit, "--",
    ), context=copy_context) if any(status == "C" for status, _, _ in statuses) else {})
    files: list[DiffFile] = []
    for status, path, old_path in statuses:
        added, removed = (copies.get(path, ([], [])) if status == "C" else
                          lines_by_path.get(path, ([], [])))
        files.append(DiffFile(
            path=path, status=status, old_path=old_path,
            added=list(added), removed=list(removed),
            is_test=_is_test(path) or _is_test(old_path),
            language=_language(path) or _language(old_path),
            old_language=_language(old_path),
            candidate_lines=(copy_context if status == "C" else context).get(path, (None, []))[0],
            added_line_numbers=(copy_context if status == "C" else context).get(path, (None, []))[1],
        ))
    return Diff(files=files, base_ref=base_ref)
