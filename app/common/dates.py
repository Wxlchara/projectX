"""Calendar arithmetic shared by applicability and document checks."""

from calendar import monthrange
from datetime import date


def add_calendar_months(value: date, months: int) -> date:
    offset = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(offset, 12)
    if not 1 <= year <= 9999:
        raise ValueError("Calendar date outside the supported range")
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))
