"""Verifier for python-regression-004."""
from parsing import parse_bool

for raw in ("true", "TRUE", "yes", "1"):
    if parse_bool(raw) is not True:
        raise SystemExit(f"{raw!r} must parse as true")
for raw in ("false", "FALSE", "no", "0"):
    if parse_bool(raw) is not False:
        raise SystemExit(f"{raw!r} must parse as false")
try:
    parse_bool("maybe")
except ValueError:
    pass
else:
    raise SystemExit("unknown values must raise ValueError")
