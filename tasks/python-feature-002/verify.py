"""Verifier for python-feature-002."""
from text_utils import slugify

cases = {
    "Hello, World!": "hello-world",
    "  Already--Spaced  ": "already-spaced",
    "Python_3.11": "python-3-11",
    "***": "",
}
for raw, expected in cases.items():
    actual = slugify(raw)
    if actual != expected:
        raise SystemExit(f"slugify({raw!r}) returned {actual!r}, expected {expected!r}")
