"""Validation helpers shared by public Norwegian register adapters."""

from __future__ import annotations


ORGNR_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def valid_orgnr(value: str) -> bool:
    """Return whether *value* is a valid nine-digit organisation number."""
    if len(value) != 9 or not value.isdigit():
        return False
    weighted = sum(
        int(digit) * weight for digit, weight in zip(value[:8], ORGNR_WEIGHTS)
    )
    check_digit = 11 - (weighted % 11)
    if check_digit == 11:
        check_digit = 0
    if check_digit == 10:
        return False
    return check_digit == int(value[-1])
