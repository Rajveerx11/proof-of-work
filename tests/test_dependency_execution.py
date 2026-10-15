"""Manifest-only changes execute suites without relaxing their coverage policy."""
from __future__ import annotations

import pytest

from proofofwork import engine
from proofofwork.core import gitdiff, runner
from proofofwork.types import Diff, DiffFile
from tests.test_production_policies import git, payload, put, repo, rules


def dependency_repo(root, name, baseline):
    repo(root, baseline)
    git(root, "checkout", "HEAD", "--", "app.py")
    (root / name).write_text("# reviewed dependency input\n")
    git(root, "add", name)
    git(root, "commit", "-qm", "reviewed dependency inputs")
    base = git(root, "rev-parse", "HEAD")
    (root / name).write_text("# candidate dependency input\n")
    return base


@pytest.mark.parametrize("name", ["pyproject.toml", "uv.lock"])
@pytest.mark.parametrize("staged", [False, True])
def test_actual_manifest_only_change_runs_python_against_reviewed_floor(tmp_path, name, staged):
    base = dependency_repo(tmp_path, name, payload(100))
    git(tmp_path, "add", name)
    diff = gitdiff.collect_diff(str(tmp_path), base, staged=staged)
    assert diff.languages() == set()  # No manifest is reclassified as Python source.
    verdict = engine.check(str(tmp_path), base_ref=base, staged=staged)
    assert verdict.passed, verdict.reasons
    assert verdict.tests.ran and verdict.tests.framework == "pytest"
    assert verdict.coverage_status == "verified" and verdict.coverage_baseline == 100
    assert verdict.coverage_baseline_source == base


@pytest.mark.parametrize("operation", ["delete", "rename"])
@pytest.mark.parametrize("name", ["pyproject.toml", "uv.lock"])
def test_removed_or_renamed_manifest_keeps_python_required(tmp_path, name, operation):
    base = dependency_repo(tmp_path, name, payload(100))
    git(tmp_path, "checkout", "HEAD", "--", name)
    if operation == "delete":
        git(tmp_path, "rm", name)
    else:
        git(tmp_path, "mv", name, "archived.txt")
    diff = gitdiff.collect_diff(str(tmp_path), base)
    assert diff.languages() == set()
    verdict = engine.check(str(tmp_path), base_ref=base)
    assert verdict.passed and verdict.tests.ran, verdict.reasons


def test_manifest_change_cannot_lower_reviewed_floor(tmp_path):
    base = dependency_repo(tmp_path, "uv.lock", payload(100))
    put(tmp_path / ".proofofwork/baseline.json", payload(0))
    (tmp_path / "app.py").write_text("value = 1\n\ndef uncovered():\n" +
                                    "".join(f"    value{i} = {i}\n" for i in range(40)))
    verdict = engine.check(str(tmp_path), base_ref=base)
    assert verdict.tests.ran and verdict.tests.passed
    assert not verdict.passed and "coverage-drop" in rules(verdict)
    assert verdict.coverage_baseline == 100


@pytest.mark.parametrize("name", ["pyproject.toml", "uv.lock"])
def test_manifest_only_change_cannot_reuse_candidate_dependencies_for_base(monkeypatch, tmp_path, name):
    base = dependency_repo(tmp_path, name, None)
    monkeypatch.setattr(runner, "run_tests", lambda *a, **k: pytest.fail("must not execute"))
    verdict = engine.check(str(tmp_path), base_ref=base, measure_base_coverage=True)
    assert not verdict.passed and "coverage-base-unavailable" in rules(verdict)
    assert any("dependency inputs differ" in reason for reason in verdict.reasons)


def test_execution_selection_preserves_mixed_sources_and_bounds_manifest_inference():
    mixed = Diff(files=[DiffFile("uv.lock", "M"), DiffFile("app.js", "M", language="js")])
    assert engine._execution_languages(mixed) == {"python", "js"}
    assert mixed.languages() == {"js"}
    unrelated = Diff(files=[DiffFile("config.toml", "M"),
                            DiffFile("nested/pyproject.toml", "M"),
                            DiffFile("nested/uv.lock", "M")])
    assert engine._execution_languages(unrelated) == set()
