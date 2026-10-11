
from datetime import datetime, timedelta, timezone

from app.main import (
    compute_late_minutes,
    compute_work_hours,
    compute_overtime,
)

IST = timezone(timedelta(hours=5, minutes=30))


def dt(day, hour, minute, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=IST)


# Late arrival and grace-period boundaries
assert compute_late_minutes(dt(12, 9, 40), "09:30") == 0
assert compute_late_minutes(dt(12, 9, 40, 1), "09:30") == 10
assert compute_late_minutes(dt(12, 10, 15, 59), "09:30") == 45

# Half-up rounding: 18 seconds is exactly 0.005 hours.
assert compute_work_hours(dt(12, 9, 0), dt(12, 9, 0, 18)) == 0.01

# Overnight shift: 22:00 to 06:00, next calendar day.
assert compute_overtime(
    dt(12, 22, 0),
    dt(13, 6, 30),
    "22:00",
    "06:00",
) == 30

# Overtime threshold
assert compute_overtime(
    dt(12, 9, 30),
    dt(12, 18, 59),
    "09:30",
    "18:30",
) == 0

assert compute_overtime(
    dt(12, 9, 30),
    dt(12, 19, 0),
    "09:30",
    "18:30",
) == 30

print("All helper tests passed.")
