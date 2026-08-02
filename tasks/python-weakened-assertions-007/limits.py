"""Numeric limits."""


def clamp(value: int, minimum: int, maximum: int) -> int:
    return min(value, minimum)
