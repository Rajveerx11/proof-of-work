"""Self-check: chain verifies clean; direct row tamper breaks it."""
import json
import sqlite3

from proofofwork.log import build_envelope, record, verify_chain
from proofofwork.log.envelope import canonical
from proofofwork.types import Finding, Severity, Verdict
from proofofwork.types import TestResult as _TestResult  # alias: avoid pytest collecting it


def _verdict(passed: bool) -> Verdict:
    return Verdict(
        passed=passed,
        findings=[Finding(rule="deleted-test", severity=Severity.BLOCK, message="x")],
        tests=_TestResult(ran=True, passed=passed, coverage=91.5, framework="pytest"),
    )


def test_signed_envelope_includes_mixed_js_coverage():
    verdict = _verdict(True)
    verdict.tests.js_coverage = 83.0
    assert build_envelope("a" * 64, verdict)["predicate"]["js_coverage"] == 83.0


def test_invalid_coverage_is_normalized_in_signed_envelope(tmp_path):
    verdict = _verdict(False)
    verdict.tests.coverage = float("inf")
    verdict.tests.js_coverage = float("nan")
    envelope = build_envelope("a" * 64, verdict)
    decoded = json.loads(canonical(envelope), parse_constant=lambda value: 1 / 0)
    assert decoded["predicate"]["js_coverage"] is None
    assert decoded["predicate"]["coverage"] is None
    db = str(tmp_path / "log.db")
    record(envelope, db)
    assert verify_chain(db)


def test_legacy_nonfinite_signed_entry_still_verifies(tmp_path):
    db = str(tmp_path / "log.db")
    legacy = build_envelope("a" * 64, _verdict(True))
    legacy["predicate"]["coverage"] = float("nan")
    record(legacy, db)
    assert verify_chain(db)


def test_chain_verifies_then_tamper_breaks(tmp_path):
    db = str(tmp_path / "log.db")

    h1 = record(build_envelope("a" * 64, _verdict(True)), db)
    h2 = record(build_envelope("b" * 64, _verdict(False)), db)
    assert h1 != h2
    assert verify_chain(db) is True

    # Tamper: rewrite one row's envelope directly, bypassing record().
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE entries SET envelope_json=? WHERE id=1",
        ('{"_type":"tampered"}',),
    )
    conn.commit()
    conn.close()

    assert verify_chain(db) is False


def test_empty_db_true(tmp_path):
    db = str(tmp_path / "log.db")
    # Create the db + key without inserting rows.
    record(build_envelope("c" * 64, _verdict(True)), db)
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM entries")
    conn.commit()
    conn.close()
    assert verify_chain(db) is True


def test_legacy_policyless_envelope_and_new_policy_both_verify(tmp_path):
    db = str(tmp_path / "log.db")
    legacy = build_envelope("a" * 64, _verdict(True))
    del legacy["predicate"]["integrity_policy"]
    record(legacy, db)
    strict = _verdict(True)
    strict.integrity_policy = "strict-v1"
    record(build_envelope("b" * 64, strict), db)
    assert verify_chain(db)


def test_tampering_signed_integrity_policy_breaks_verification(tmp_path):
    db = str(tmp_path / "log.db")
    strict = _verdict(True)
    strict.integrity_policy = "strict-v1"
    envelope = build_envelope("a" * 64, strict)
    record(envelope, db)
    assert verify_chain(db)
    envelope["predicate"]["integrity_policy"] = "advisory-v1"
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE entries SET envelope_json=?", (canonical(envelope).decode(),))
    assert not verify_chain(db)
