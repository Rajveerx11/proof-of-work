"""Real Git -> engine -> real pytest -> signed-log integrity-policy gates.

These cover recognized patch syntax, not complete semantic integrity or intent.
Skip masking can exclude complete comments/strings, but fragments omit arbitrary
before/after docstring, decorator and encoding context. The assertion detector
still counts raw text: assertion replacements by comments/strings and removal of
docstring examples are NOT proven safe here. Full-source lexical integration is
manager-owned. Detector-only PASS never claims that candidate tests executed.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess

import pytest

from proofofwork import engine
from proofofwork.core import runner, sandbox
from proofofwork.core.gitdiff import collect_diff
from proofofwork.log import verify_chain
from proofofwork.types import Severity


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("Git required for end-to-end fixtures")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    (root / "tests").mkdir()
    (root / "tests/test_original.py").write_bytes(
        b"def test_original():\n    value = 42\n    assert value == 42\n")
    # An unchanged retained test witnesses actual execution even after a skip/move.
    (root / "tests/test_retained.py").write_bytes(
        b"def test_retained():\n    assert 7 == 7\n")
    _git(root, "add", "tests")
    _git(root, "commit", "-qm", "integration base")
    return root


def _change(root, case):
    path = root / "tests/test_original.py"
    source = path.read_bytes()
    if case == "added-skip":
        path.write_bytes(b"import pytest\n@pytest.mark.skip(reason='integration fixture')\n" + source)
    elif case == "removed-assert":
        path.write_bytes(source.replace(b"assert value == 42", b"pass"))
    elif case == "test-path-removed":
        _git(root, "mv", "tests/test_original.py", "helper.py")
        moved = collect_diff(str(root)).files[0]
        assert moved.status == "R" and moved.old_path == "tests/test_original.py"
        assert moved.added == moved.removed == []  # actual unchanged-body R100
    else:
        raise AssertionError(f"unknown fixture: {case}")


def _signed(root, verdict, policy):
    """Verify the actual recorded row, not a freshly rebuilt unsigned envelope."""
    db = root.parent / "gate.db"
    assert len(verdict.entry_hash) == 64, verdict.as_dict()
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT entry_hash, envelope_json FROM entries ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None and row[0] == verdict.entry_hash
    assert verify_chain(str(db)) is True
    payload = json.loads(json.dumps(verdict.as_dict()))
    envelope = json.loads(row[1])
    assert payload["integrity_policy"] == policy
    assert envelope["predicate"]["integrity_policy"] == policy
    assert envelope["predicate"]["verdict"] == ("pass" if verdict.passed else "fail")
    assert envelope["predicate"]["tests_passed"] is verdict.tests.passed
    assert len(envelope["subject"][0]["digest"]["sha256"]) == 64
    return envelope


def _check(root, **kwargs):
    return engine.check(str(root), coverage_policy="test-only",
                        db_path=str(root.parent / "gate.db"), **kwargs)


@pytest.mark.parametrize("policy", [None, "strict"], ids=["default-strict", "explicit-strict"])
@pytest.mark.parametrize("case", ["added-skip", "removed-assert", "test-path-removed"])
def test_strict_blocks_even_when_real_pytest_passes(repo, case, policy):
    _change(repo, case)
    kwargs = {} if policy is None else {"integrity_policy": policy}
    verdict = _check(repo, **kwargs)
    assert verdict.tests.ran and verdict.tests.passed is True, verdict.as_dict()
    assert verdict.passed is False
    assert any(f.rule == case and f.severity == Severity.BLOCK for f in verdict.findings)
    envelope = _signed(repo, verdict, "strict")
    assert case in envelope["predicate"]["cheats_caught"]


@pytest.mark.parametrize("case", ["added-skip", "removed-assert", "test-path-removed"])
def test_explicit_advisory_is_labeled_in_json_and_signed_predicate(repo, case):
    _change(repo, case)
    verdict = _check(repo, integrity_policy="advisory")
    assert verdict.tests.ran and verdict.tests.passed is True, verdict.as_dict()
    assert verdict.passed is True, verdict.as_dict()
    assert any(f.rule == case and f.severity == Severity.WARN for f in verdict.findings)
    envelope = _signed(repo, verdict, "advisory")
    assert case in envelope["predicate"]["cheats_caught"]


def test_preserved_rename_warns_in_strict_detector_only_without_claiming_tests(repo, monkeypatch):
    _git(repo, "mv", "tests/test_original.py", "tests/test_moved.py")
    moved = collect_diff(str(repo)).files[0]
    assert moved.status == "R" and moved.added == moved.removed == []

    def forbidden(*args, **kwargs):
        pytest.fail("detector-only check attempted host test execution")

    monkeypatch.setattr(runner, "run_tests", forbidden)
    monkeypatch.setattr(sandbox, "get_sandbox", forbidden)
    verdict = _check(repo, run_tests=False, integrity_policy="strict")
    assert verdict.passed is True, verdict.as_dict()
    assert any(f.rule == "renamed-test" and f.severity == Severity.WARN for f in verdict.findings)
    assert not any(f.rule == "test-path-removed" for f in verdict.findings)
    assert verdict.tests.ran is False and verdict.tests.passed is None
    assert verdict.execution_mode == "detector-only" and verdict.coverage_status == "not-run"
    assert any("detector-only" in reason and "no test pass claimed" in reason
               for reason in verdict.reasons)
    envelope = _signed(repo, verdict, "strict")
    assert envelope["predicate"]["execution_mode"] == "detector-only"
    assert envelope["predicate"]["coverage_status"] == "not-run"
    assert envelope["predicate"]["tests_passed"] is None


def test_advisory_cannot_downgrade_existing_deleted_test_block(repo):
    _git(repo, "rm", "tests/test_original.py")
    verdict = _check(repo, integrity_policy="advisory")
    assert verdict.passed is False
    assert any(f.rule == "deleted-test" and f.severity == Severity.BLOCK for f in verdict.findings)
    assert "deleted-test" in _signed(repo, verdict, "advisory")["predicate"]["cheats_caught"]


@pytest.mark.parametrize("policy", ["", "STRICT", "permissive", None, False, 0])
def test_invalid_policy_blocks_before_host_execution(repo, monkeypatch, policy):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid integrity policy reached host execution")

    monkeypatch.setattr(runner, "run_tests", forbidden)
    monkeypatch.setattr(sandbox, "get_sandbox", forbidden)
    verdict = _check(repo, integrity_policy=policy)
    assert verdict.passed is False
    assert verdict.tests.ran is False
    assert any(f.rule == "integrity-policy-invalid" and f.severity == Severity.BLOCK
               for f in verdict.findings)


@pytest.mark.parametrize("example", [
    b"# pytest.skip('documentation only')\n",
    b"example = \"pytest.mark.skip(reason='documentation only')\"\n",
])
def test_complete_comment_string_skip_examples_do_not_claim_executable_skips(repo, example):
    path = repo / "tests/test_original.py"
    path.write_bytes(path.read_bytes() + example)
    verdict = _check(repo, run_tests=False, integrity_policy="strict")
    assert verdict.passed is True, verdict.as_dict()
    assert not any(f.rule == "added-skip" for f in verdict.findings)
    _signed(repo, verdict, "strict")


def test_advisory_policy_label_is_signature_bound(repo):
    _change(repo, "added-skip")
    verdict = _check(repo, run_tests=False, integrity_policy="advisory")
    assert verdict.passed is True, verdict.as_dict()
    envelope = _signed(repo, verdict, "advisory")
    envelope["predicate"]["integrity_policy"] = "strict"
    with sqlite3.connect(repo.parent / "gate.db") as conn:
        conn.execute("UPDATE entries SET envelope_json=?", (json.dumps(envelope),))
    assert verify_chain(str(repo.parent / "gate.db")) is False
