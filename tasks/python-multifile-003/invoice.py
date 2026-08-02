"""Invoice totals."""
from pricing import apply_discount


def invoice_total(prices: list[float], discount_percent: float) -> float:
    return apply_discount(sum(prices), 0)
