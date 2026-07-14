"""Deterministic cheat-signal checks. Each module exposes check(diff, root) -> list[Finding].

ALL_CHECKS holds ONLY these four. coverage_delta is owned elsewhere and invoked by the
engine separately — do not add it here.
"""
from __future__ import annotations

from . import asserts, fake_pass, mocks, tests_integrity

ALL_CHECKS = [
    tests_integrity.check,
    fake_pass.check,
    asserts.check,
    mocks.check,
]
