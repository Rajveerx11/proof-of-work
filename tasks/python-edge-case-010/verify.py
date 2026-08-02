"""Verifier for python-edge-case-010."""
from stats import median

values = [9, 1, 5, 3]
if median(values) != 4 or values != [9, 1, 5, 3]:
    raise SystemExit("even median or input preservation failed")
if median([-3, 10, 2]) != 2:
    raise SystemExit("odd median failed")
try:
    median([])
except ValueError:
    pass
else:
    raise SystemExit("empty input must raise ValueError")
