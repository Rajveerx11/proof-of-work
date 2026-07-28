"""Standard-library verifier for the python-fix-001 benchmark."""
from calculator import add

if add(2, 3) != 5:
    raise SystemExit("add(2, 3) must return 5")
