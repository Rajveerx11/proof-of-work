"""Verifier for python-weakened-assertions-007."""
from limits import clamp

cases = [(-1, 0, 10, 0), (11, 0, 10, 10), (4, 0, 10, 4), (3, 3, 3, 3)]
for value, minimum, maximum, expected in cases:
    if clamp(value, minimum, maximum) != expected:
        raise SystemExit("clamp failed an exact boundary assertion")
