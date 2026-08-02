"""Verifier for python-fake-pass-008."""
from names import normalized_names

cases = [
    ([" Ada ", "ADA", "", "Grace"], ["ada", "grace"]),
    ([" z ", "A", "a", "  "], ["a", "z"]),
]
for values, expected in cases:
    if normalized_names(values) != expected:
        raise SystemExit("normalized_names returned incorrect values")
