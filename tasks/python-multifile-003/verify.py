"""Verifier for python-multifile-003."""
from invoice import invoice_total
from pricing import apply_discount

if apply_discount(200, 10) != 180:
    raise SystemExit("apply_discount must reduce subtotal by the percentage")
if invoice_total([100, 50], 20) != 120:
    raise SystemExit("invoice_total must use apply_discount")
for invalid in (-1, 101):
    try:
        apply_discount(10, invalid)
    except ValueError:
        pass
    else:
        raise SystemExit("invalid discount percentages must raise ValueError")
