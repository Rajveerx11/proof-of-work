"""Self-check: chain verifies clean; direct row tamper breaks it."""
import sqlite3

from proofofwork.log import build_envelope, record, verify_chain
from proofofwork.types import Finding, Severity, Verdict
from proofofwork.types import TestResult as _TestResult  # alias: avoid pytest collecting it


def _verdict(passed: bool) -> Verdict:
    return Verdict(
        passed=passed,
        findings=[Finding(rule="deleted-test", severity=Severity.BLOCK, message="x")],
        tests=_TestResult(ran=True, passed=passed, coverage=91.5, framework="pytest"),
    )


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
