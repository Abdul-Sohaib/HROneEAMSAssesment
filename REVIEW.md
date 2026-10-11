# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` (`app/main.py:64`) | (1) Assumed punch-in was on the same calendar day as `shift_start`, failing overnight shifts (R1).<br>(2) Retained microsecond noise.<br>(3) Failed strict 10-minute grace period boundary checks (`> 10:00`). | Overnight punch-in at 05:00 for a 22:00–06:00 shift produced negative/wrong shift start. `09:40:01` on a `09:30` shift was handled inconsistently. | Truncated microseconds to seconds, normalized to IST, resolved overnight shift start date via `shift_start_datetime`, and applied integer floor division `elapsed_seconds // 60` only when `elapsed_seconds > 600`. |
| 2 | `compute_work_hours` (`app/main.py:76`) | (1) Used Python's built-in `round(..., 2)` (banker's round-half-to-even) instead of R4-mandated **half-up** rounding.<br>(2) Failed to truncate microseconds prior to division. | Boundary test `(09:00:00 to 09:00:18)`: 18 seconds is exactly 0.005 hours; Python `round()` rounded down to `0.0`, whereas R4 requires `0.01`. | Truncated timestamps to whole seconds, converted elapsed seconds to `Decimal`, and quantized to `0.01` using `ROUND_HALF_UP`. |
| 3 | `compute_overtime` (`app/main.py:90`) | (1) Ignored R3 threshold requiring $\ge 30$ whole minutes (returned all positive minutes).<br>(2) Signature `(punch_out, shift_end, date_str)` lacked `shift_start`, making overnight shift end date rollover impossible.<br>(3) Positional argument mismatch caused `AttributeError: 'datetime.datetime' object has no attribute 'split'`. | (1) 29 minutes late punch-out (`18:59` for `18:30` shift) gave 29 instead of 0.<br>(2) Overnight shift `22:00` to `06:00` did not advance `shift_end` to next calendar day.<br>(3) Calling with `punch_in` crashed `test_helpers.py:26`. | Updated signature to `(punch_in, punch_out, shift_start, shift_end)`, dynamically resolved overnight shift end date in IST, and returned `whole_minutes` only when `whole_minutes >= 30` (else `0`). |

## Reviewed but Not Identified as Defective

- **Fixed IST Timezone definition (`IST = timezone(timedelta(hours=5, minutes=30))`):** Verified correct; standard library fixed offset satisfies R1 (UTC+05:30) without external timezone libraries.
- **Second Truncation (`.replace(microsecond=0)`):** Retaining timezone info while dropping microseconds correctly implements R1's requirement before derived calculations.