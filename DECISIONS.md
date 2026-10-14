# DECISIONS.md

1. **Indexes.**
   - Unique on `employees.emp_code`: guarantees employee IDs are unique.
   - Unique on `attendance_logs(emp_code, date)`: prevents duplicate punch records on the same day.
   - Compound on `attendance_logs(date, emp_code)`: enables fast date-range filtering and sorted pagination via index scan (`IXSCAN`).
   - Compound on `employees(department, joined_on)`: speeds up department-level lookups and join-date checks.
   - *Rejected:* An index on `late_minutes`. Leaderboard metrics sum minutes dynamically over a date range, so indexing individual unsummed minutes doesn't help MongoDB calculate totals.

2. **Punch-in race.**
   When two identical punch-in requests arrive at the exact same millisecond:
   Both calculate the same IST date. MongoDB's unique index on `(emp_code, date)` acts as the lock. The first request writes the document and returns `201 Created`. The second hits a `DuplicateKeyError`, which our code catches and returns as `409 Conflict`. No duplicate rows or overwritten timestamps can occur.

3. **Ties.**
   If employees tie at the cutoff (e.g. two people tied for 10th place when `limit=10`), both are returned. Our aggregation uses MongoDB's `$rank` with `rank <= limit`. Standard competition ranking keeps tied people at the boundary rather than arbitrarily dropping one.

4. **Headcount.**
   The pipeline starts from the `employees` collection (filtered by join date) and pulls in `attendance_logs` using `$lookup`. If an employee has no logs, the lookup returns an empty list `[]`, but the employee is still counted toward the department's headcount, with `avg_work_hours` set to `null`.

5. **One thing you would change for 100x data.**
   I would pre-calculate daily department totals in a background worker instead of running heavy aggregations (`$lookup`, `$densify`, `$setWindowFields`) on raw logs for every request. Reading a pre-computed summary document is far faster and saves database memory to O(1) time complexity.
