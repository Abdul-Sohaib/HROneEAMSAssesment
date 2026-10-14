# DECISIONS.md

1. **Indexes.**
   We created unique indexes on `employees.emp_code` and `attendance_logs(emp_code, date)` to enforce natural identity and prevent double punches. We added compound indexes `(department, joined_on)` on employees and `(date, emp_code)` on logs to guarantee `IXSCAN` coverage across range filters, joins, and deterministic sorting. We rejected an index on `late_minutes` because leaderboard queries aggregate sums dynamically over date ranges, which cannot utilize a raw unaggregated field index.

2. **Punch-in race.**
   Both requests compute the same IST attendance date. One request wins the atomic write lock at the storage level. The second request hits a duplicate key collision on the unique `(emp_code, date)` index. The code catches `DuplicateKeyError` and returns `409 Conflict`, ensuring zero duplicate records or overwritten timestamps.

3. **Ties.**
   The leaderboard uses MongoDB's window operator `$rank` followed by `{"$match": {"rank": {"$lte": limit}}}`. If the 10th and 11th employees share equal late minutes, both receive rank 10 and both are returned, honoring standard competition ranking rather than truncating arbitrarily.

4. **Headcount.**
   The pipeline anchors on `employees` (filtered by `joined_on <= month_end`) and left-joins `attendance_logs` via `$lookup`. Employees with no logs produce an empty array but remain grouped by department, ensuring 100% accurate headcount and setting `avg_work_hours: null`.

5. **One thing you would change for 100x data.**
   I would materialize daily department rollups using MongoDB Change Streams or background workers, turning heavy multi-stage aggregations (`$lookup`, `$densify`, `$setWindowFields`) into instant $O(1)$ indexed reads.
