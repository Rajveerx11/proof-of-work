"""Real custom-command execution and fail-closed trusted configuration boundaries."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofofwork import engine
from proofofwork.core import suites
from proofofwork.core.sandbox import RunOutput, get_sandbox
from proofofwork.interfaces.cli import main


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def suite(sid="first", **kwargs):
    return {"id": sid, "cwd": ".", "argv": ["node", "check.cjs"],
            "prerequisites": ["check.cjs"], "timeout": 5, "evidence": "exit-code", **kwargs}


def write_config(root, entries):
    path = root / suites.CONFIG
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"version": 1, "suites": entries}), encoding="utf-8")


def commit(root):
    git(root, "add", "-A")
    git(root, "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "user.name", "fixture")
    (tmp_path / "check.cjs").write_text("console.log('real node suite');\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".proofofwork/log*\n", encoding="utf-8")
    write_config(tmp_path, [suite(), suite("second")])
    commit(tmp_path)
    return tmp_path


@pytest.fixture
def node_repo(repo):
    if not shutil.which("node"):
        pytest.skip("actual Node executable unavailable")
    return repo


@pytest.fixture
def nested_node_repo(node_repo):
    entries = []
    (node_repo / "check.cjs").unlink()  # No root-level fallback for package-relative paths.
    for sid, cwd in (("first", "packages/first"), ("second", "components/nested/second")):
        package = node_repo / cwd
        package.mkdir(parents=True)
        (package / "input.txt").write_text(sid, encoding="utf-8")
        (package / "check.cjs").write_text(
            "const assert = require('node:assert/strict');\n"
            f"assert.equal(require('fs').readFileSync('input.txt', 'utf8'), '{sid}');\n"
            f"console.log('package {sid}');\n", encoding="utf-8")
        entries.append(suite(sid, cwd=cwd, prerequisites=["check.cjs", "input.txt"]))
    write_config(node_repo, entries)
    commit(node_repo)
    return node_repo


def check(root, **kwargs):
    return engine.check(str(root), suite_base=kwargs.pop("suite_base", "HEAD"), **kwargs)


def test_all_suites_run_for_config_only_changes_and_cli(node_repo, capsys):
    base = git(node_repo, "rev-parse", "HEAD")
    # A candidate config cannot change argv, suite IDs, or evidence policy.
    write_config(node_repo, [suite("substitute", argv=["node", "-e", "process.exit(9)"])])
    code = main(["check", "--root", str(node_repo), "--suite-base", base, "--json"])
    verdict = json.loads(capsys.readouterr().out)
    assert code == 0 and verdict["passed"], f"{verdict['reasons']}\n{verdict['tests']['raw']}"
    result = verdict["tests"]
    assert result["suite_base"] == base and result["evidence"] == "exit-code"
    assert result["required_suites"] == result["executed_suites"] == ["first", "second"]
    assert [item["exit_code"] for item in result["suites"]] == [0, 0]
    assert all(item["outcome"] == "passed" for item in result["suites"])
    assert "real node suite" in result["raw"]
    assert "not full candidate attestation" in " ".join(verdict["reasons"])
    assert result["coverage"] is None and verdict["entry_hash"]


@pytest.mark.parametrize("entrypoint", ["engine", "cli"])
@pytest.mark.parametrize("missing_package", [None, "first", "second"])
def test_nested_package_cwds_and_relative_prerequisites(
        nested_node_repo, capsys, entrypoint, missing_package):
    root = nested_node_repo
    base, entries = suites.load_suites(str(root), "HEAD")
    if missing_package:
        entry = next(item for item in entries if item["id"] == missing_package)
        (root / entry["cwd"] / "input.txt").unlink()
    if entrypoint == "cli":
        code = main(["check", "--root", str(root), "--suite-base", base, "--json"])
        verdict = json.loads(capsys.readouterr().out)
        assert code == (1 if missing_package else 0)
        passed, result = verdict["passed"], verdict["tests"]
    else:
        verdict = check(root, suite_base=base)
        passed, result = verdict.passed, asdict(verdict.tests)
    assert passed == (missing_package is None), result["raw"]
    assert result["required_suites"] == ["first", "second"]
    assert result["executed_suites"] == [sid for sid in ["first", "second"]
                                         if sid != missing_package]
    for outcome in result["suites"]:
        if outcome["id"] == missing_package:
            assert outcome["outcome"] == "unavailable" and outcome["exit_code"] is None
            assert "missing prerequisite: input.txt" in outcome["raw"]
        else:
            assert outcome["outcome"] == "passed" and outcome["exit_code"] == 0
            assert f"package {outcome['id']}" in outcome["raw"]


@pytest.mark.parametrize("entrypoint", ["engine", "cli"])
def test_staged_nested_packages_use_index_and_preserve_source(
        nested_node_repo, capsys, entrypoint):
    root = nested_node_repo
    base, entries = suites.load_suites(str(root), "HEAD")
    for entry in entries:
        package = root / entry["cwd"]
        script = package / "check.cjs"
        script.write_text(script.read_text().replace(entry["id"], f"index {entry['id']}"),
                          encoding="utf-8")
        data = package / "input.txt"
        data.write_text(f"index {entry['id']}", encoding="utf-8")
        git(root, "add", entry["cwd"])
        data.write_text(f"worktree {entry['id']}", encoding="utf-8")
    sentinel = root / "untracked.txt"
    sentinel.write_text("preserve source workspace", encoding="utf-8")
    source_before = {name: (root / name).read_bytes()
                     for name in [*git(root, "ls-files").splitlines(), sentinel.name]}
    status_before = git(root, "status", "--porcelain")
    index_path = root / git(root, "rev-parse", "--git-path", "index")
    index_before = index_path.read_bytes()
    trees_before = git(root, "worktree", "list", "--porcelain")
    if entrypoint == "cli":
        assert main(["check", "--root", str(root), "--suite-base", base,
                     "--staged", "--json"]) == 0
        verdict = json.loads(capsys.readouterr().out)
        assert verdict["passed"]
        result = verdict["tests"]
    else:
        verdict = check(root, suite_base=base, staged=True)
        assert verdict.passed
        result = asdict(verdict.tests)
    assert result["required_suites"] == result["executed_suites"] == ["first", "second"]
    assert [item["outcome"] for item in result["suites"]] == ["passed", "passed"]
    assert [item["exit_code"] for item in result["suites"]] == [0, 0]
    assert all(f"package index {sid}" in result["raw"] for sid in ["first", "second"])
    assert all((root / name).read_bytes() == content for name, content in source_before.items())
    assert git(root, "status", "--porcelain") == status_before
    assert index_path.read_bytes() == index_before
    assert git(root, "worktree", "list", "--porcelain") == trees_before
    worktree_verdict = check(root, suite_base=base)
    assert not worktree_verdict.passed
    assert [item["outcome"] for item in worktree_verdict.tests.suites] == ["failed", "failed"]


def test_failure_does_not_skip_other_required_suites(node_repo, capsys):
    write_config(node_repo, [suite(argv=["node", "-e", "process.exit(7)"]), suite("second")])
    base = commit(node_repo)
    code = main(["check", "--root", str(node_repo), "--suite-base", base, "--json"])
    verdict = json.loads(capsys.readouterr().out)
    assert code == 1 and not verdict["passed"]
    assert verdict["tests"]["executed_suites"] == ["first", "second"]
    assert [item["exit_code"] for item in verdict["tests"]["suites"]] == [7, 0]


@pytest.mark.parametrize("overrides,expected", [
    ({"prerequisites": ["absent"]}, "missing prerequisite"),
    ({"argv": ["pow-intentionally-missing-tool-77"]}, "native tool missing"),
    ({"cwd": "absent"}, "suite cwd missing"),
])
def test_missing_requirements_fail_closed_and_retain_ids(node_repo, overrides, expected):
    write_config(node_repo, [suite(**overrides), suite("second")])
    base = commit(node_repo)
    result = check(node_repo, suite_base=base)
    assert not result.passed
    assert result.tests.required_suites == ["first", "second"]
    assert result.tests.executed_suites == ["second"]
    assert result.tests.suites[0]["outcome"] == "unavailable"
    assert result.tests.suites[0]["exit_code"] is None
    assert expected in result.tests.raw


def test_actual_node_timeout_fails_closed(node_repo):
    write_config(node_repo, [suite(argv=["node", "-e",
                                       "console.log('waiting'); setTimeout(() => {}, 10000)"],
                                  timeout=1), suite("second")])
    base = commit(node_repo)
    verdict = check(node_repo, suite_base=base)
    assert not verdict.passed
    assert verdict.tests.suites[0]["outcome"] == "timeout"
    assert verdict.tests.suites[0]["exit_code"] == 124
    assert "waiting" in verdict.tests.raw
    assert verdict.tests.suites[1]["outcome"] == "passed"


def test_explicit_suite_base_required_no_head_fallback(repo):
    verdict = engine.check(str(repo))
    assert not verdict.passed and not verdict.tests.ran
    assert "explicit --suite-base" in verdict.findings[-1].message
    assert not check(repo, suite_base="missing").passed
    assert not check(repo, suite_base="--quiet").passed
    assert not check(repo, run_tests=False).passed


def test_candidate_only_configuration_cannot_substitute(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "rm", suites.CONFIG)
    without_config = commit(repo)
    write_config(repo, [suite()])
    for reference in (without_config, "--quiet"):
        verdict = check(repo, suite_base=reference)
        assert not verdict.passed and not verdict.tests.ran
    assert not engine.check(str(repo)).passed
    # The reviewed earlier commit still supplies both suites, not the candidate's one.
    loaded_commit, entries = suites.load_suites(str(repo), base)
    assert loaded_commit == base and [entry["id"] for entry in entries] == ["first", "second"]


@pytest.mark.parametrize("entries", [
    [], [suite(), suite()], [suite(evidence="coverage")], [suite(evidence=None)],
    [suite(timeout=True)], [suite(timeout=0)], [suite(timeout=601)], [suite(argv=[])],
    [suite(argv=["sh", "-c", "true"])], [suite(argv=["../node", "check.cjs"])],
    [suite(argv=["node", "\0"])], [suite(cwd="../outside")], [suite(cwd="/absolute")],
    [suite(cwd="C:/outside")], [suite(cwd="a\\b")], [suite(cwd="a/.. ")],
    [suite(prerequisites=["../outside"])], [suite(prerequisites="check.cjs")],
    [suite(id="not stable")], [suite(unknown=True)],
])
def test_malformed_trusted_config_blocks(repo, entries):
    write_config(repo, entries)
    base = commit(repo)
    verdict = check(repo, suite_base=base)
    assert not verdict.passed and not verdict.tests.ran
    assert any(item.rule == "suite-config-error" for item in verdict.findings)


@pytest.mark.parametrize("raw", [
    '{"version":1,"version":1,"suites":[]}', '{"version":true,"suites":[]}',
    '{"version":1,"suites":[],"unknown":0}', 'not json', '[]',
])
def test_strict_json_rejects_duplicate_and_unknown_keys(repo, raw):
    (repo / suites.CONFIG).write_text(raw, encoding="utf-8")
    base = commit(repo)
    assert not check(repo, suite_base=base).passed


@pytest.mark.parametrize("content", ['{"coverage":90}', 'malformed', '{"coverage":null}'])
@pytest.mark.parametrize("policy", ["suite-base", "diff-base", "worktree", "staged-head",
                                    "staged-private", "staged-diff-base"])
def test_any_applicable_baseline_blocks_without_parsing(repo, content, policy):
    suite_base = git(repo, "rev-parse", "HEAD")
    path = repo / ".proofofwork/baseline.json"
    path.write_text(content, encoding="utf-8")
    options = {}
    if policy in {"suite-base", "diff-base", "staged-head", "staged-diff-base"}:
        with_baseline = commit(repo)
        if policy == "suite-base":
            suite_base = with_baseline
        if policy in {"diff-base", "staged-diff-base"}:
            options["base_ref"] = with_baseline
        if policy in {"staged-head", "staged-diff-base"}:
            options["staged"] = True
        path.unlink()  # candidate deletion cannot remove trusted policy
        if policy == "staged-diff-base":
            commit(repo)  # HEAD lacks the explicit diff-base's coverage policy
    elif policy == "staged-private":
        path.unlink()
        private = Path(git(repo, "rev-parse", "--git-path", "proofofwork/baseline.json"))
        if not private.is_absolute():
            private = repo / private
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text(content, encoding="utf-8")
        options["staged"] = True
    verdict = check(repo, suite_base=suite_base, **options)
    assert not verdict.passed and not verdict.tests.ran
    assert "coverage" in verdict.findings[-1].message
    assert verdict.tests.coverage is None


def test_update_baseline_is_unsupported_and_never_writes(repo):
    verdict = check(repo, update_baseline=True)
    assert not verdict.passed and not verdict.tests.ran
    assert not (repo / ".proofofwork/baseline.json").exists()


def test_staged_uses_index_not_worktree_and_preserves_index(node_repo):
    base = git(node_repo, "rev-parse", "HEAD")
    (node_repo / "check.cjs").write_text("console.log('index candidate');\n", encoding="utf-8")
    git(node_repo, "add", "check.cjs")
    (node_repo / "check.cjs").write_text("process.exit(8);\n", encoding="utf-8")
    write_config(node_repo, [suite("untrusted", argv=["node", "-e", "process.exit(9)"])])
    git(node_repo, "add", suites.CONFIG)
    index_path = node_repo / git(node_repo, "rev-parse", "--git-path", "index")
    index_before = index_path.read_bytes()
    trees_before = git(node_repo, "worktree", "list", "--porcelain")
    verdict = check(node_repo, suite_base=base, staged=True)
    assert verdict.passed and "index candidate" in verdict.tests.raw
    assert verdict.tests.required_suites == verdict.tests.executed_suites == ["first", "second"]
    assert index_path.read_bytes() == index_before
    assert git(node_repo, "worktree", "list", "--porcelain") == trees_before
    assert (node_repo / "check.cjs").read_text() == "process.exit(8);\n"
    assert not check(node_repo, suite_base=base).passed


def test_staged_failure_cleans_snapshot_and_preserves_index(node_repo):
    (node_repo / "check.cjs").write_text("process.exit(6);\n", encoding="utf-8")
    git(node_repo, "add", "check.cjs")
    index_path = node_repo / git(node_repo, "rev-parse", "--git-path", "index")
    index_before = index_path.read_bytes()
    trees_before = git(node_repo, "worktree", "list", "--porcelain")
    verdict = check(node_repo, staged=True)
    assert not verdict.passed
    assert [item["exit_code"] for item in verdict.tests.suites] == [6, 6]
    assert index_path.read_bytes() == index_before
    assert git(node_repo, "worktree", "list", "--porcelain") == trees_before


def test_cwd_and_prerequisite_links_rejected(node_repo, tmp_path):
    outside = tmp_path / "external"
    outside.mkdir()
    link = node_repo / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("symlink creation unavailable")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True)
    with pytest.raises(ValueError):
        suites.contained(str(node_repo), "linked")
    _, entries = suites.load_suites(str(node_repo), "HEAD")
    entries[0]["prerequisites"] = ["linked"]
    result = suites.run_suites(get_sandbox("local"), str(node_repo), entries, "fixture",
                              source_root=str(node_repo))
    assert not result.passed and result.suites[0]["outcome"] == "unavailable"


def test_candidate_tool_and_batch_wrapper_rejected(node_repo, monkeypatch):
    monkeypatch.setattr(suites.shutil, "which", lambda tool: str(node_repo / "check.cjs"))
    assert not check(node_repo).passed
    monkeypatch.setattr(suites.shutil, "which", lambda tool: "C:/external/runner.cmd")
    assert not check(node_repo).passed


@pytest.mark.parametrize("output,expected", [(RunOutput(None, "", ""), "unknown"),
                                              (KeyboardInterrupt(), "interrupted"),
                                              (OSError("launch failed"), "unavailable")])
def test_unknown_interrupted_or_launch_errors_fail_closed(node_repo, output, expected):
    calls = []

    class Sandbox:
        def run(self, *args, **kwargs):
            calls.append(args)
            if isinstance(output, BaseException):
                raise output
            return output

    base, entries = suites.load_suites(str(node_repo), "HEAD")
    result = suites.run_suites(Sandbox(), str(node_repo), entries, base, source_root=str(node_repo))
    assert not result.passed
    assert [item["outcome"] for item in result.suites] == [expected, expected]
    assert result.required_suites == ["first", "second"]
    assert len(calls) == (1 if isinstance(output, KeyboardInterrupt) else 2)
    if isinstance(output, KeyboardInterrupt):
        assert result.suites[1]["raw"] == "not started: run interrupted"
        assert result.executed_suites == []


def test_reference_resolved_once(repo, monkeypatch):
    original = suites._git
    resolutions = []

    def traced(root, *args):
        if args[0] == "rev-parse":
            resolutions.append(args)
        return original(root, *args)

    monkeypatch.setattr(suites, "_git", traced)
    base, _ = suites.load_suites(str(repo), "HEAD")
    assert base == git(repo, "rev-parse", "HEAD") and len(resolutions) == 1


def test_tool_resolution_passes_absolute_path_to_sandbox(node_repo, monkeypatch):
    actual = Path(shutil.which("node")).resolve()
    # The runner checkout and external Node may be on different Windows drives.
    # Relative tool lookup must start outside both controlled roots on Node's drive.
    monkeypatch.chdir(actual.parent)
    relative = os.path.relpath(actual, Path.cwd())
    assert not Path(relative).is_absolute() and Path(relative).resolve() == actual
    monkeypatch.setattr(suites.shutil, "which", lambda tool: relative)
    calls = []

    class Sandbox:
        def run(self, argv, **kwargs):
            calls.append(argv)
            assert Path(argv[0]).is_absolute() and Path(argv[0]) == actual
            return RunOutput(0, "external native tool", "")

    base, entries = suites.load_suites(str(node_repo), "HEAD")
    result = suites.run_suites(Sandbox(), str(node_repo), entries, base,
                              source_root=str(node_repo))
    assert result.passed and len(calls) == 2


def test_existing_integrity_detector_can_block_zero_exit_evidence(node_repo):
    launcher = node_repo / "sample.test.js"
    launcher.write_text("console.log('test launcher');\n", encoding="utf-8")
    write_config(node_repo, [suite(argv=["node", launcher.name], prerequisites=[launcher.name])])
    base = commit(node_repo)
    launcher.write_text("process.exit(0);\n", encoding="utf-8")
    verdict = check(node_repo, suite_base=base)
    assert verdict.tests.passed and verdict.tests.suites[0]["exit_code"] == 0
    assert not verdict.passed
    assert any(f.rule == "fake-pass:process-exit" and f.file == launcher.name
               for f in verdict.findings)


@pytest.mark.parametrize("operation", ["rename", "delete"])
@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("run_tests", [False, True])
def test_removed_config_never_falls_back_to_legacy(node_repo, capsys, operation,
                                                  staged, run_tests):
    base = git(node_repo, "rev-parse", "HEAD")
    if operation == "rename":
        git(node_repo, "mv", suites.CONFIG, ".proofofwork/renamed.json")
    else:
        git(node_repo, "rm", suites.CONFIG)
    if not staged:
        commit(node_repo)
    status = git(node_repo, "diff", "--name-status", *(["--cached"] if staged else []), base)
    assert (f"R100\t{suites.CONFIG}\t.proofofwork/renamed.json" if operation == "rename"
            else f"D\t{suites.CONFIG}") in status
    index_path = node_repo / git(node_repo, "rev-parse", "--git-path", "index")
    index_before = index_path.read_bytes()
    verdict = engine.check(str(node_repo), base_ref=base, staged=staged, run_tests=run_tests)
    assert not verdict.passed and not verdict.tests.ran
    assert verdict.tests.framework == "configured"
    assert any(f.rule == "suite-config-error" and "explicit --suite-base" in f.message
               for f in verdict.findings)
    args = ["check", "--root", str(node_repo), "--base", base, "--json"]
    if staged:
        args.append("--staged")
    if not run_tests:
        args.append("--no-tests")
    assert main(args) == 1
    cli_verdict = json.loads(capsys.readouterr().out)
    assert cli_verdict["tests"]["framework"] == "configured"
    assert not cli_verdict["tests"]["ran"]
    # A reviewed pre-removal base still supplies every required suite. No-tests
    # remains forbidden even when that explicit base is provided.
    assert main([*args, "--suite-base", base]) == (0 if run_tests else 1)
    trusted = json.loads(capsys.readouterr().out)["tests"]
    assert trusted["required_suites"] == ["first", "second"]
    assert trusted["executed_suites"] == (["first", "second"] if run_tests else [])
    assert index_path.read_bytes() == index_before


def directory_link(link, target):
    if os.name == "nt":
        # Exercise actual junctions even where developer-mode symlinks work.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("location", ["candidate", "source"])
@pytest.mark.parametrize("path_form,invocation_cwd", [
    ("absolute", "runner"), ("absolute", "candidate"),
    ("relative", "runner"),
    ("mixed-case", "runner"), ("mixed-case", "candidate"),
])
def test_real_node_lookup_through_controlled_directory_link_is_rejected(
        node_repo, tmp_path_factory, monkeypatch, location, path_form, invocation_cwd):
    if path_form == "mixed-case" and os.name != "nt":
        pytest.skip("case-insensitive PATH lookup is Windows-specific")
    source = tmp_path_factory.mktemp("source")
    controlled = node_repo if location == "candidate" else source
    external_node = Path(shutil.which("node")).resolve()
    link = controlled / "tools"
    directory_link(link, external_node.parent)
    if invocation_cwd == "candidate":
        monkeypatch.chdir(node_repo)
    path_entry = str(link)
    if path_form == "relative":
        # Anchor to the fixture drive, not the (potentially cross-drive) checkout.
        # Keep cwd outside the controlled tree so fallback PATH remains trusted.
        monkeypatch.chdir(controlled.parent)
        path_entry = os.path.relpath(link, Path.cwd())
        assert not Path(path_entry).is_absolute()
        assert Path(os.path.abspath(path_entry)) == link
    elif path_form == "mixed-case":
        path_entry = path_entry.swapcase()
    original_path = os.environ["PATH"]
    monkeypatch.setenv("PATH", path_entry + os.pathsep + original_path)
    lookup = shutil.which("node")
    assert lookup and Path(os.path.abspath(lookup)).is_relative_to(controlled)
    assert Path(lookup).resolve() == external_node
    (node_repo / "check.cjs").write_text(
        "require('fs').writeFileSync('tool-ran', 'yes');\n", encoding="utf-8")
    base, entries = suites.load_suites(str(node_repo), "HEAD")
    result = suites.run_suites(get_sandbox("local"), str(node_repo), entries, base,
                              source_root=str(source))
    assert not result.passed and not result.ran and result.executed_suites == []
    assert all(item["outcome"] == "unavailable" for item in result.suites)
    assert "candidate-controlled" in result.raw
    assert not (node_repo / "tool-ran").exists()
    # The same real Node outside both trees remains supported.
    monkeypatch.setenv("PATH", original_path)
    result = suites.run_suites(get_sandbox("local"), str(node_repo), entries, base,
                              source_root=str(source))
    assert result.passed and result.executed_suites == ["first", "second"]
    assert (node_repo / "tool-ran").read_text() == "yes"


@pytest.mark.parametrize("field", ["cwd", "prerequisites"])
def test_internal_directory_links_are_rejected_on_python_311(node_repo, field):
    package = node_repo / "package"
    package.mkdir()
    (package / "check.cjs").write_text("console.log('must not run');\n", encoding="utf-8")
    directory_link(node_repo / "alias", package)
    with pytest.raises(ValueError, match="linked suite path"):
        suites.contained(str(node_repo), "alias/check.cjs")
    base, entries = suites.load_suites(str(node_repo), "HEAD")
    if field == "cwd":
        entries[0][field] = "alias"
    else:
        entries[0][field] = ["alias/check.cjs"]
    result = suites.run_suites(get_sandbox("local"), str(node_repo), entries, base,
                              source_root=str(node_repo))
    assert not result.passed and result.executed_suites == ["second"]
    assert result.suites[0]["outcome"] == "unavailable"
    assert "linked suite path" in result.suites[0]["raw"]


def test_lstat_reparse_attributes_reject_links_without_path_is_junction(repo, monkeypatch):
    # Portable regression for the 3.11 stdlib interface: no is_junction call,
    # and a directory reparse point need not advertise a symlink mode bit.
    alias = repo / "alias"
    alias.mkdir()
    original = Path.lstat

    def lstat(path):
        if path == alias:
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path)

    def unsupported(*args):
        raise AssertionError("Python 3.11 has no Path.is_junction")

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "is_junction", unsupported, raising=False)
    with pytest.raises(ValueError, match="linked suite path"):
        suites.contained(str(repo), "alias")


def test_external_lookup_link_to_candidate_tool_is_still_rejected(
        node_repo, tmp_path_factory, monkeypatch):
    external = tmp_path_factory.mktemp("external-lookup")
    directory_link(external / "tools", node_repo)
    monkeypatch.setattr(suites.shutil, "which", lambda tool: str(external / "tools/check.cjs"))
    base, entries = suites.load_suites(str(node_repo), "HEAD")
    result = suites.run_suites(get_sandbox("local"), str(node_repo), entries, base,
                              source_root=str(node_repo))
    assert not result.passed and not result.ran
    assert "candidate-controlled" in result.raw
