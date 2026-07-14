"""In-toto Statement envelope, DSSE-ready shape. Deterministic JSON for hashing/signing."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from proofofwork import __version__
from proofofwork.types import Severity, Verdict


def canonical(obj) -> bytes:
    """Deterministic JSON: sorted keys, no whitespace, ASCII-only. Same bytes every time."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def build_envelope(subject: str, verdict: Verdict) -> dict:
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": "changeset", "digest": {"sha256": subject}}],
        "predicateType": "https://proof-of-work.dev/verdict/v1",
        "predicate": {
            "verdict": "pass" if verdict.passed else "fail",
            "cheats_caught": [
                f.rule for f in verdict.findings
                if f.severity in (Severity.BLOCK, Severity.WARN)
            ],
            "tool_version": __version__,
            "ruleset_version": "v1",
            "tests_passed": verdict.tests.passed,
            "coverage": verdict.tests.coverage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
