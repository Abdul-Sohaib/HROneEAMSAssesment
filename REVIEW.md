# REVIEW.md

## Starter Code Defects Summary

List every defect found in the starter `app/main.py` (helpers and endpoints):

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` (`app/main.py:64`) | (1) Assumed punch-in was on the same calendar day as `shift_start`, failing overnight shifts (R1).<br>(2) Retained microsecond noise.<br>(3) Failed strict 10-minute grace period boundary checks (`> 10:00`). | Overnight punch-in at 05:00 for a 22:00–06:00 shift produced negative/wrong shift start. `09:40:01` on a `09:30` shift was handled inconsistently. | Truncated microseconds to seconds, normalized to IST, resolved overnight shift start date via `shift_start_datetime`, and applied integer floor division `elapsed_seconds // 60` only when `elapsed_seconds > 600`. |
| 2 | `compute_work_hours` (`app/main.py:76`) | (1) Used Python's built-in `round(..., 2)` (banker's round-half-to-even) instead of R4-mandated **half-up** rounding.<br>(2) Failed to truncate microseconds prior to division. | Boundary test `(09:00:00 to 09:00:18)`: 18 seconds is exactly 0.005 hours; Python `round()` rounded down to `0.0`, whereas R4 requires `0.01`. | Truncated timestamps to whole seconds, converted elapsed seconds to `Decimal`, and quantized to `0.01` using `ROUND_HALF_UP`. |
| 3 | `compute_overtime` (`app/main.py:90`) | (1) Ignored R3 threshold requiring $\ge 30$ whole minutes (returned all positive minutes).<br>(2) Signature `(punch_out, shift_end, date_str)` lacked `shift_start`, making overnight shift end date rollover impossible.<br>(3) Positional argument mismatch caused `AttributeError: 'datetime.datetime' object has no attribute 'split'`. | (1) 29 minutes late punch-out (`18:59` for `18:30` shift) gave 29 instead of 0.<br>(2) Overnight shift `22:00` to `06:00` did not advance `shift_end` to next calendar day.<br>(3) Calling with `punch_in` crashed `test_helpers.py:26`. | Updated signature to `(punch_in, punch_out, shift_start, shift_end)`, dynamically resolved overnight shift end date in IST, and returned `whole_minutes` only when `whole_minutes >= 30` (else `0`). |
| 4 | `create_employee` (`app/main.py:153`) | (1) Lacked input validation on `EmployeeIn` (accepted invalid `emp_code` formats like `emp1234`, whitespace-only strings, non-existent dates like `2026-02-29`, identical shift start/end).<br>(2) Returned `created_at` as ISO string/datetime instead of integer epoch milliseconds.<br>(3) Relied solely on `find_one` for duplicate check without handling database unique constraint errors for concurrent requests. | Passing invalid payloads (e.g. `emp_code="EMP123"`, `shift_start=="09:30"==shift_end`) returned `201 Created` instead of `422`. Concurrent duplicates risked unhandled errors. | Added strict Pydantic regex/length constraints, calendar date parsing, model validator ensuring `shift_start != shift_end`, converted `created_at` to integer epoch ms in response, and created a MongoDB unique index on `emp_code` catching `DuplicateKeyError` to return `409`. |
| 5 | `list_employees` (`app/main.py:165`) | (1) **Off-by-one pagination bug:** Computed `skip = page * page_size`, which caused `page=1` to skip 20 records and return page 2 instead of page 1.<br>(2) Unbounded pagination parameters allowed `page=0` and `page_size=101` with `200 OK` instead of `422`.<br>(3) Calculated `total = db.employees.count_documents({})`, which ignored the active `department` filter.<br>(4) Query results lacked deterministic sorting. | `GET /employees?page=1` did not return the first employee; `GET /employees?department=X` returned total of the entire company; `page_size=101` and `page=0` did not return `422`. | Changed offset formula to `skip = (page - 1) * page_size`, added `Query(ge=1)` and `Query(le=100)`, passed query filter `q` to `count_documents(q)`, applied `.sort("emp_code", 1)` directly in MongoDB, and serialized `created_at` to integer epoch ms. |
| 6 | `regularize_attendance` (`app/main.py:495`) | (1) Unimplemented endpoint in starter code (`# TODO - the rest of the contract`).<br>(2) Missing validation to clear punch times on `ABSENT`/`LEAVE` or reject supplied times.<br>(3) Missing presence `punch_in` enforcement and R1 IST attendance date verification.<br>(4) Risk of lost audit history during concurrent corrections. | Calling `PATCH /attendance/{emp_code}/{date}` returned `404 Not Found`. Concurrent updates could overwrite audit history or corrupt derived calculations. | Implemented `regularize_attendance` in [app/main.py](file:///c:/Users/mdabd/Downloads/SDT_candidate_kit/candidate_kit/app/main.py#L495-L655) adhering strictly to OpenAPI 3.0: validated natural key existence (404), rejected no-op updates (422), cleared punch times on ABSENT/LEAVE, enforced R1 IST attendance date boundary, recalculated derived fields (late, work hours, overtime, half day), recorded field-level deltas in `history` with epoch ms, and implemented optimistic locking via `{"history": {"$size": N}}` to eliminate race conditions. |
| 7 | `get_employee_monthly_analytics` (`app/main.py:666`) | (1) Unimplemented endpoint in starter code (`# TODO - the rest of the contract`).<br>(2) Missing MongoDB aggregation pipeline for monthly attendance metrics.<br>(3) Missing R7 working days computation (mid-month joiners, Mon–Fri calendar) and R8 half-up percentage calculation.<br>(4) Missing handling of zero logs and future joiners (null percentage). | Calling `GET /analytics/employees/{emp_code}/monthly` returned `404 Not Found`. Inefficient looping in Python risked slow execution and COLLSCAN on large collections. | Implemented `build_employee_monthly_pipeline` and `get_employee_monthly_analytics` in [app/main.py](file:///c:/Users/mdabd/Downloads/SDT_candidate_kit/candidate_kit/app/main.py#L666-L780) backed by an index-supported MongoDB aggregation pipeline (`IXSCAN` on `[("emp_code", 1), ("date", 1)]`), computed `working_days` per R7, filtered weekend presence from `present_days`, and applied strict R8 half-up percentage quantization. |
| 8 | `get_department_summary` (`app/main.py:785`) | (1) Unimplemented endpoint in starter code (`# TODO - the rest of the contract`).<br>(2) Missing two-collection MongoDB aggregation pipeline across `employees` and `attendance_logs`.<br>(3) Missing R9 headcount retention for employees with zero logs.<br>(4) Missing omission of departments with 0 headcount for the month.<br>(5) Missing R8 half-up rounding of `avg_work_hours` across presence records with work hours.<br>(6) Missing alphabetical sorting by department. | Calling `GET /analytics/departments/summary` returned `404 Not Found`. Loop-based joins in Python would be vulnerable to race conditions, poor performance, and COLLSCAN. | Implemented `build_dept_summary_pipeline` and `get_department_summary` in [app/main.py](file:///c:/Users/mdabd/Downloads/SDT_candidate_kit/candidate_kit/app/main.py#L785-L875) backed by a MongoDB `$lookup` aggregation pipeline utilizing `[("department", 1), ("joined_on", 1)]` and `[("emp_code", 1), ("date", 1)]` indexes (`IXSCAN`), accurately retained R9 headcount, formatted all required department metrics, and sorted alphabetically by department. |
| 9 | `get_late_leaderboard` (`app/main.py:925`) | (1) Unimplemented endpoint in starter code (`# TODO - the rest of the contract`).<br>(2) Missing MongoDB aggregation pipeline for ranking employees by late minutes.<br>(3) Missing standard competition ranking (1, 2, 2, 4) with ties at cutoff retaining all tied employees.<br>(4) Missing exclusion of employees with 0 late minutes and exclusion of logs with no corresponding employee.<br>(5) Missing department-restricted ranking. | Calling `GET /analytics/leaderboard/late` returned `404 Not Found`. Client requests with ties at cutoff or 0 late minutes would produce incorrect ranks or invalid counts. | Implemented `build_late_leaderboard_pipeline` and `get_late_leaderboard` in [app/main.py](file:///c:/Users/mdabd/Downloads/SDT_candidate_kit/candidate_kit/app/main.py#L925-L995) backed by an index-supported MongoDB aggregation pipeline (`IXSCAN` on `[("date", -1), ("emp_code", 1)]`), computed standard competition ranking (1, 2, 2, 4), retained all tied employees at cutoff (`rank <= limit`), strictly excluded zero-late employees, and supported optional department filtering. |

---

## Reviewed but Not Identified as Defective

- **Fixed IST Timezone definition (`IST = timezone(timedelta(hours=5, minutes=30))`):** Verified correct; standard library fixed offset satisfies R1 (UTC+05:30) without external timezone libraries.
- **Second Truncation (`.replace(microsecond=0)`):** Retaining timezone info while dropping microseconds correctly implements R1's requirement before derived calculations.
- **Database Connection Pooling:** Reusing single `MongoClient` instance created at module startup is thread-safe and optimal for FastAPI worker threads.

---

## Verified Test Documentation & Execution Audit

### 1. Test Execution Summary

| Category | Total Cases | PASS | FAIL | BLOCKED / PENDING | Pass Rate |
|---|---|---|---|---|---|
| **6. Helper Functions (LATE, WORK, OT)** | 30 | 30 | 0 | 0 | **100%** |
| **7. API Health (`/health`)** | 5 | 1 | 0 | 4 (Fault injection) | **100% (Active)** |
| **4. Employee Creation (`POST /employees`)** | 35 | 35 | 0 | 0 | **100%** |
| **5. Employee Listing (`GET /employees`)** | 10 | 10 | 0 | 0 | **100%** |
| **8. Attendance (Punch-In, Out, Regularize)** | 17 | 17 | 0 | 0 | **100%** |
| **9. Monthly Employee Analytics (`/monthly`)** | 6 | 6 | 0 | 0 | **100%** |
| **10. Department Summary (`/departments/summary`)** | 6 | 6 | 0 | 0 | **100%** |
| **11. Late Leaderboard (`/leaderboard/late`)** | 8 | 8 | 0 | 0 | **100%** |
| **12. Other Analytics & Explain Endpoints** | 2 | 0 | 0 | 2 (Unimplemented) | **Pending** |
| **TOTAL** | **119** | **113** | **0** | **6** | **95.0%** |

---

### 2. Helper Function Tests (Section 6) — 100% PASS

#### 2.1 `compute_late_minutes()` (Shift: `09:30` / Overnight: `22:00`–`06:00`)

| ID | Test Scenario | Punch-in Time | Expected Result | Actual Result | Status |
|---|---|---|---|---|---|
| LATE-001 | On-time start | `09:30:00` | `0` | `0` | **PASS** |
| LATE-002 | Inside grace period | `09:39:59` | `0` | `0` | **PASS** |
| LATE-003 | Exact grace period boundary | `09:40:00` | `0` | `0` | **PASS** |
| LATE-004 | 1 second past grace period (R2) | `09:40:01` | `10` | `10` | **PASS** |
| LATE-005 | 1 minute past grace period | `09:41:00` | `11` | `11` | **PASS** |
| LATE-006 | Mid-shift late arrival | `10:15:59` | `45` | `45` | **PASS** |
| LATE-007 | 1.5 hours late | `11:00:00` | `90` | `90` | **PASS** |
| LATE-008 | Overnight early arrival | `21:59:59` | `0` | `0` | **PASS** |
| LATE-009 | Overnight on-time start | `22:00:00` | `0` | `0` | **PASS** |
| LATE-010 | Overnight exact grace boundary | `22:10:00` | `0` | `0` | **PASS** |
| LATE-011 | Overnight 1s past grace | `22:10:01` | `10` | `10` | **PASS** |
| LATE-012 | Overnight late next morning (`05:00`) | `05:00:00` (next day) | Belongs to prev day (420 mins) | `420` | **PASS** |

#### 2.2 `compute_work_hours()` (Half-up Rounding R4)

| ID | Test Scenario | Elapsed Duration | Expected Result | Actual Result | Status |
|---|---|---|---|---|---|
| WORK-001 | 18 seconds (0.005h half-up) | 18s | `0.01` | `0.01` | **PASS** |
| WORK-002 | 17 seconds (0.0047h half-down) | 17s | `0.00` | `0.00` | **PASS** |
| WORK-003 | Exactly 30 minutes | 1800s | `0.50` | `0.50` | **PASS** |
| WORK-004 | 4 hours 29 minutes | 16140s | `4.48` | `4.48` | **PASS** |
| WORK-005 | 4 hours 30 minutes (half-day cutoff) | 16200s | `4.50` | `4.50` | **PASS** |
| WORK-006 | Full standard shift | 28800s (8h) | `8.00` | `8.00` | **PASS** |
| WORK-007 | 8 hours 15 minutes | 29700s | `8.25` | `8.25` | **PASS** |
| WORK-008 | Full 24-hour boundary | 86340s (23h 59m) | `23.98` | `23.98` | **PASS** |

#### 2.3 `compute_overtime()` (30-Minute Threshold R3)

| ID | Shift End | Punch-out Time | Expected Overtime | Actual Overtime | Status |
|---|---|---|---|---|---|
| OT-001 | `18:30` | `18:29:59` | `0` | `0` | **PASS** |
| OT-002 | `18:30` | `18:30:00` | `0` | `0` | **PASS** |
| OT-003 | `18:30` | `18:59:59` (29m late) | `0` | `0` | **PASS** |
| OT-004 | `18:30` | `19:00:00` (exact 30m) | `30` | `30` | **PASS** |
| OT-005 | `18:30` | `19:00:59` (30m 59s) | `30` | `30` | **PASS** |
| OT-006 | `18:30` | `19:15:00` (45m) | `45` | `45` | **PASS** |
| OT-007 | `06:00` (overnight) | `06:00:00` (following day) | `0` | `0` | **PASS** |
| OT-008 | `06:00` (overnight) | `06:29:59` (following day) | `0` | `0` | **PASS** |
| OT-009 | `06:00` (overnight) | `06:30:00` (following day) | `30` | `30` | **PASS** |
| OT-010 | `06:00` (overnight) | `07:15:00` (following day) | `75` | `75` | **PASS** |

---

### 3. Employee Creation — `POST /employees` (Section 4) — 100% PASS

| ID | Test Scenario | Input Tested | Expected | Actual | Status | Note |
|---|---|---|---|---|---|---|
| EMP-001 | Valid creation | Baseline JSON payload | `201 Created` | `201` | **PASS** | Unique employee created |
| EMP-002 | Default shift values | Omit `shift_start`/`shift_end` | `201 Created` | `201` | **PASS** | Applied `09:30` & `18:30` |
| EMP-004 | Overnight shift | `22:00`–`06:00` | `201 Created` | `201` | **PASS** | Permitted by model |
| EMP-009 | `created_at` instant format | Inspect response | Integer epoch ms | `int` epoch ms | **PASS** | Returns e.g. `1783312500000` |
| EMP-010 | MongoDB `_id` security | Inspect response | No `_id` in response | No `_id` | **PASS** | Internal `_id` hidden |
| EMP-011 | Missing `emp_code` | Omit `emp_code` | `422` | `422` | **PASS** | Required field |
| EMP-012 | Missing `name` | Omit `name` | `422` | `422` | **PASS** | Required field |
| EMP-013 | Missing `email` | Omit `email` | `422` | `422` | **PASS** | Required field |
| EMP-014 | Missing `department` | Omit `department` | `422` | `422` | **PASS** | Required field |
| EMP-015 | Missing `joined_on` | Omit `joined_on` | `422` | `422` | **PASS** | Required field |
| EMP-017 | Empty body `{}` | `{}` | `422` | `422` | **PASS** | Rejected |
| EMP-019 | Valid format `EMP1001` | New valid code | `201` | `201` | **PASS** | Accepted |
| EMP-022 | Code too few digits | `EMP123` | `422` | `422` | **PASS** | Enforced by `^EMP\d{4,6}$` |
| EMP-023 | Code too many digits | `EMP1234567` | `422` | `422` | **PASS** | Enforced by `^EMP\d{4,6}$` |
| EMP-024 | Lowercase prefix | `emp1234` | `422` | `422` | **PASS** | Enforced by `^EMP\d{4,6}$` |
| EMP-025 | Incorrect prefix | `ABC1234` | `422` | `422` | **PASS** | Enforced by `^EMP\d{4,6}$` |
| EMP-026 | Non-numeric suffix | `EMP12AB` | `422` | `422` | **PASS** | Enforced by `^EMP\d{4,6}$` |
| EMP-027 | Leading whitespace | `" EMP1234"` | `422` | `422` | **PASS** | Regex rejects whitespace |
| EMP-028 | Trailing whitespace | `"EMP1234 "` | `422` | `422` | **PASS** | Regex rejects whitespace |
| EMP-029 | Empty string code | `""` | `422` | `422` | **PASS** | Regex rejects empty |
| EMP-031 | Duplicate code | Existing `emp_code` | `409 Conflict` | `409` | **PASS** | Handled via unique index |
| EMP-035 | Name > 100 chars | 101 characters | `422` | `422` | **PASS** | `max_length=100` |
| EMP-036 | Empty name | `""` | `422` | `422` | **PASS** | `min_length=1` + whitespace check |
| EMP-037 | Whitespace name | `"   "` | `422` | `422` | **PASS** | `validate_name` rejected |
| EMP-043 | Invalid email | `"not-an-email"` | `422` | `422` | **PASS** | Strict email regex |
| EMP-054 | Whitespace department | `"   "` | `422` | `422` | **PASS** | `validate_department` rejected |
| EMP-058 | Non-leap Feb 29 | `"2026-02-29"` | `422` | `422` | **PASS** | Real date parser validator |
| EMP-059 | Invalid month | `"2026-13-01"` | `422` | `422` | **PASS** | Real date parser validator |
| EMP-061 | Non-ISO format | `"12-01-2026"` | `422` | `422` | **PASS** | Strict `YYYY-MM-DD` match |
| EMP-062 | Slash separator | `"2026/01/12"` | `422` | `422` | **PASS** | Strict `YYYY-MM-DD` match |
| EMP-068 | Equal shifts | `09:30` and `09:30` | `422` | `422` | **PASS** | Model validator rejected |
| EMP-071 | Single digit hour | `"9:30"` | `422` | `422` | **PASS** | Strict `^(?:[01]\d|2[0-3]):[0-5]\d$` |
| EMP-078 | Concurrent duplicate | Duplicate attempt | `409 Conflict` | `409` | **PASS** | Unique index on `emp_code` |

---

### 4. Employee Listing — `GET /employees` (Section 5) — 100% PASS

| ID | Test Scenario | Query Parameters | Expected Result | Actual Result | Status |
|---|---|---|---|---|---|
| EML-001 | No query parameters | None | `200 OK`, page 1, page size 20 | `200 OK` (page=1, size=20) | **PASS** |
| EML-002 | Page 1, page size 10 | `?page=1&page_size=10` | First 10 matching employees | `200 OK` (10 items) | **PASS** |
| EML-003 | Page 2, page size 10 | `?page=2&page_size=10` | Next 10, without duplicates | `200 OK` (10 items, 0 overlap) | **PASS** |
| EML-004 | Page size 100 (upper bound) | `?page_size=100` | `200 OK` (accepted) | `200 OK` (size=100) | **PASS** |
| EML-005 | Page size 101 | `?page_size=101` | `422 Unprocessable Entity` | `422` | **PASS** |
| EML-006 | Page 0 | `?page=0` | `422 Unprocessable Entity` | `422` | **PASS** |
| EML-007 | Department with no employees | `?department=NonExistent` | Empty list `[]`, `total: 0` | `200 OK` (`items: []`, `total: 0`) | **PASS** |
| EML-008 | Department filter matching employees | `?department=Engineering` | Only matching employees; accurate total | `200 OK` (all Engineering, `total: 24`) | **PASS** |
| EML-009 | Page beyond the last page | `?page=9999&page_size=20` | Empty list `[]` | `200 OK` (`items: []`) | **PASS** |
| EML-010 | Multiple employees sorting | `?page=1&page_size=100` | Consistent `emp_code` ascending sorting | `200 OK` (alphabetically sorted) | **PASS** |

---

### 5. Attendance Endpoints (Section 8) — 100% PASS

#### 5.1 Punch-In & Punch-Out
| ID | Test Scenario | Method & Endpoint | Expected | Actual | Status | Verified Output |
|---|---|---|---|---|---|---|
| ATT-PIN-001 | Record valid punch-in | `POST /attendance/punch-in` | `201 Created` | `201` | **PASS** | Date resolved per R1, late minutes per R2 |
| ATT-PIN-002 | Unknown employee | `POST /attendance/punch-in` | `404 Not Found` | `404` | **PASS** | Returns `{"detail": "unknown employee"}` |
| ATT-PIN-003 | Duplicate punch-in on date | `POST /attendance/punch-in` | `409 Conflict` | `409` | **PASS** | Prevented duplicate on `(emp_code, date)` |
| ATT-POUT-001 | Record valid punch-out | `POST /attendance/punch-out` | `200 OK` | `200` | **PASS** | `work_hours: 9.0`, `overtime_minutes: 35`, `half_day: False` |
| ATT-POUT-002 | Duplicate punch-out | `POST /attendance/punch-out` | `409 Conflict` | `409` | **PASS** | Atomic `$set` prevents duplicate close |
| ATT-LIST-001 | List attendance logs | `GET /attendance` | `200 OK` | `200` | **PASS** | Paginated, sorted by date desc then emp_code asc |

#### 5.2 Attendance Regularization — `PATCH /attendance/{emp_code}/{date}`
| ID | Test Scenario | Request Payload / Input | Expected | Actual | Status | Audit / Behavior Verified |
|---|---|---|---|---|---|---|
| ATT-REG-001 | Correct punch-in time | `punch_in: 09:45:00 IST`, reason, regularized_by | `200 OK` | `200 OK` | **PASS** | Recalculated `late_minutes: 15`; history entry appended with `from`/`to` |
| ATT-REG-002 | Correct punch-out time | `punch_out: 19:30:00 IST`, reason, regularized_by | `200 OK` | `200 OK` | **PASS** | Recalculated `overtime_minutes: 60`, `work_hours: 9.75` |
| ATT-REG-003 | Send existing values unchanged | Unmodified timestamps & status | `422 Unprocessable Entity` | `422` | **PASS** | Rejects no-op modification |
| ATT-REG-004 | Punch-out before punch-in | `punch_out < punch_in` | `422 Unprocessable Entity` | `422` | **PASS** | Temporal order constraint enforced |
| ATT-REG-005 | Punch-in on different IST date | `punch_in` in next day | `422 Unprocessable Entity` | `422` | **PASS** | Enforces R1 attendance date boundary |
| ATT-REG-006 | Change status to LEAVE | `status: "LEAVE"`, reason, regularized_by | `200 OK` | `200 OK` | **PASS** | Clears `punch_in`, `punch_out`, and derived fields to `null`/0 |
| ATT-REG-007 | Presence status without punch-in | `status: "PRESENT"` without punch times on LEAVE | `422 Unprocessable Entity` | `422` | **PASS** | Presence requires punch-in |
| ATT-REG-008 | Unknown employee / missing record | Invalid `emp_code` or date | `404 Not Found` | `404` | **PASS** | Both missing cases return `404` |
| ATT-REG-009 | Concurrent corrections safety | 2 parallel threads modifying record | `200` & `200` / `409` | `[200, 200]` | **PASS** | Optimistic lock on history size ensures no lost audit history |
| ATT-REG-010 | Combine ABSENT/LEAVE with punch times | Supply `punch_in` with `status: "ABSENT"` | `422 Unprocessable Entity` | `422` | **PASS** | Rejects illegal presence times with absence status |
| ATT-REG-011 | Punch-out > 24 hours after punch-in | `punch_out = punch_in + 25h` | `422 Unprocessable Entity` | `422` | **PASS** | Enforces 24-hour upper boundary limit |

---

### 6. Monthly Employee Analytics — `GET /analytics/employees/{emp_code}/monthly` — 100% PASS

| ID | Test Scenario | Request & Parameters | Expected Result | Actual Result | Status | Verified Metrics |
|---|---|---|---|---|---|---|
| ANL-MON-001 | Normal month with mixed records & weekend | `emp_norm`, `month=2026-07` | `200 OK`, Mon full + Tue half + Sat (0 present) = 1.5 present days | `200 OK` | **PASS** | `working_days: 23`, `present_days: 1.5`, `leave_days: 1`, `late_count: 2`, `total_late_minutes: 35`, `total_overtime_minutes: 45`, `attendance_pct: 6.52` |
| ANL-MON-002 | Mid-month joiner working days & rounding | `emp_mid` (joined 2026-07-13), `month=2026-07` | `200 OK`, 15 working days, 1.0 present day | `200 OK` | **PASS** | `working_days: 15`, `present_days: 1.0`, `attendance_pct: 6.67` (half-up rounded) |
| ANL-MON-003 | Employee with zero logs | `emp_nologs`, `month=2026-07` | `200 OK`, 23 working days, 0.0 present, 0.0% | `200 OK` | **PASS** | `working_days: 23`, `present_days: 0.0`, all counts 0, `attendance_pct: 0.0` |
| ANL-MON-004 | Future joiner (joined 2026-08-01) | `emp_future`, `month=2026-07` | `200 OK`, 0 working days, `attendance_pct: null` | `200 OK` | **PASS** | `working_days: 0`, `present_days: 0.0`, `attendance_pct: null` |
| ANL-MON-005 | Invalid or missing month parameter | `month=2026-13`, `2026/07`, omitted | `422 Unprocessable Entity` | `422` (all 3 cases) | **PASS** | Strict regex `^\d{4}-(0[1-9]\|1[0-2])$` enforced |
| ANL-MON-006 | Unknown employee lookup | `EMP999999`, `month=2026-07` | `404 Not Found` | `404` | **PASS** | Returns `{"detail": "unknown employee"}` |

---

### 7. Department Summary — `GET /analytics/departments/summary` — 100% PASS

| ID | Test Scenario | Request & Parameters | Expected Result | Actual Result | Status | Verified Metrics |
|---|---|---|---|---|---|---|
| ANL-DEP-001 | Department filter with diverse records & future joiner | `month=2026-07`, `department=DeptA` | `200 OK`, headcount excludes future joiners; records aggregated | `200 OK` | **PASS** | `headcount: 2`, `present_days: 2.5`, `avg_work_hours: 7.86`, `late_count: 2`, `total_late_minutes: 35`, `leave_count: 1`, `on_duty_count: 1` |
| ANL-DEP-002 | Department with zero logs | `month=2026-07`, `department=DeptB` | `200 OK`, retains headcount, `avg_work_hours: null` | `200 OK` | **PASS** | `headcount: 2`, `present_days: 0.0`, `avg_work_hours: null`, `late_count: 0`, `leave_count: 0`, `on_duty_count: 0` |
| ANL-DEP-003 | Department with 0 headcount in month | `month=2026-07`, `department=DeptFuture` | `200 OK`, omitted from output | `200 OK` (`items: []`) | **PASS** | Department with 0 headcount for the month is cleanly omitted |
| ANL-DEP-004 | Non-existent department filter | `month=2026-07`, `department=NonExistent` | `200 OK`, empty list `[]` | `200 OK` (`items: []`) | **PASS** | Returns `{"month": "2026-07", "items": []}` |
| ANL-DEP-005 | All departments summary sorted ascending | `month=2026-07` | `200 OK`, sorted alphabetically by department | `200 OK` | **PASS** | Sorted by `department` ascending; includes zero-log depts; excludes 0 headcount |
| ANL-DEP-006 | Invalid or missing month parameter | `month=2026-13`, `2026/07`, omitted | `422 Unprocessable Entity` | `422` (all 3 cases) | **PASS** | Strict regex validation enforced |

---
 
### 8. Late Leaderboard — `GET /analytics/leaderboard/late` — 100% PASS

| ID | Test Scenario | Request & Parameters | Expected Result | Actual Result | Status | Verified Metrics |
|---|---|---|---|---|---|---|
| ANL-LEAD-001 | Standard competition ranking with ties (1, 2, 2, 4) & sorting | `month=2026-11`, `department=DeptA`, `limit=10` | `200 OK`, ties share rank, next rank skipped; deterministic tie-breaker | `200 OK` | **PASS** | Ranks: 1 (200m, count: 2), 2 (150m, count: 1), 2 (150m, count: 2), 4 (100m, count: 1); sorted `late_mins` desc, `emp_code` asc |
| ANL-LEAD-002 | Tie at cutoff boundary with `limit=2` | `month=2026-11`, `department=DeptA`, `limit=2` | `200 OK`, returns all items where `rank <= limit` (3 items returned) | `200 OK` (3 items) | **PASS** | `rank <= 2` returns ranks `[1, 2, 2]`; preserves tied employees at cutoff |
| ANL-LEAD-003 | Top rank cutoff with `limit=1` | `month=2026-11`, `department=DeptA`, `limit=1` | `200 OK`, only rank 1 item | `200 OK` (1 item) | **PASS** | Returns single rank 1 employee |
| ANL-LEAD-004 | Cutoff skipping subsequent ranks with `limit=3` | `month=2026-11`, `department=DeptA`, `limit=3` | `200 OK`, returns ranks `[1, 2, 2]`; rank 4 skipped | `200 OK` (3 items) | **PASS** | Skips rank 4 because $4 > 3$ |
| ANL-LEAD-005 | Overall company-wide leaderboard | `month=2026-11`, `limit=10` | `200 OK`, ranks span across all departments | `200 OK` | **PASS** | Overall rank 1 correctly awarded to highest overall late minutes across company |
| ANL-LEAD-006 | Zero late records / empty month | `month=2025-01` | `200 OK`, `{"month": "2025-01", "items": []}` | `200 OK` (`items: []`) | **PASS** | Returns empty items list; 0-late employees completely excluded |
| ANL-LEAD-007 | Query parameter validation (month, limit) | `month=2026-13`, `limit=0`, `limit=51`, `limit=abc` | `422 Unprocessable Entity` (all cases) | `422` (all cases) | **PASS** | Strict regex for `month` and range `[1, 50]` for `limit` enforced |
| ANL-LEAD-008 | Database index verification (IXSCAN vs COLLSCAN) | `month=2026-11`, `department=DeptA` | Aggregation uses index `date_-1_emp_code_1` (`IXSCAN`) | `IXSCAN` present, `COLLSCAN` absent | **PASS** | MongoDB query planner confirms indexed execution with zero collection scans |

---

### 9. Pending Items (Remaining Endpoints)

| ID | Endpoint | Description | Status |
|---|---|---|---|
| ANL-TRND-001 | `GET /analytics/departments/{dept}/trend` | 7-day moving average attendance trend with `$densify` | **Pending Step 4** |
| ADM-EXPL-001 | `GET /admin/explain/{endpoint}` | MongoDB executionStats explain query | **Pending Step 5** |

---

### 10. Implementation Change Log in `app/main.py`

1. **Lifespan Indexes (`app/main.py:31`):** Added FastAPI lifespan creating unique indexes on `db.employees.emp_code`, `(emp_code, date)` for attendance, compound index `(date, emp_code)`, and index `[("department", 1), ("joined_on", 1)]` on `employees`.
2. **DNS Resilience (`app/main.py:18`):** Configured public DNS resolver fallback (`8.8.8.8`, `1.1.1.1`) to resolve SRV records on restrictive networks.
3. **Schema Hardening (`app/main.py:131`):** Configured strict constraints on `EmployeeIn`, `PunchInIn`, `PunchOutIn`, `RegularizeRequest`.
4. **Instant Standardization (`app/main.py:202`):** All timestamps returned in responses now strictly conform to integer epoch milliseconds (`EpochMillis`), stripping internal MongoDB `_id` and legacy `id` fields.
5. **Pagination Bugfix (`app/main.py:228`, `295`):** Fixed offset calculation `skip = (page - 1) * page_size`, added `Query(ge=1)` and `Query(le=100)`, and sorted by `emp_code` ascending.
6. **Punch-Out Implementation (`app/main.py:246`):** Fully implemented `POST /attendance/punch-out` calculating work hours, overtime ($\ge 30$ mins), and half-day status with atomic concurrency protection.
7. **Attendance Regularization (`app/main.py:488`):** Implemented `PATCH /attendance/{emp_code}/{date}` with automatic derived field recalculation, strict state validation, audit history tracking with epoch millisecond timestamps, and optimistic locking concurrency safety.
8. **Monthly Employee Analytics (`app/main.py:666`):** Implemented `GET /analytics/employees/{emp_code}/monthly` backed by MongoDB aggregation pipeline utilizing `[("emp_code", 1), ("date", 1)]` index (`IXSCAN`), accurate weekday working day derivation per R7, half-up percentage calculation per R8, and handling zero logs and mid-month joiners.
9. **Department Summary Analytics (`app/main.py:785`):** Implemented `GET /analytics/departments/summary` backed by two-collection aggregation (`employees` -> `$lookup` `attendance_logs`) utilizing `[("department", 1), ("joined_on", 1)]` and `[("emp_code", 1), ("date", 1)]` indexes (`IXSCAN`), accurate R9 headcount retention for zero-log employees, omission of 0-headcount departments, R8 half-up rounding of `avg_work_hours`, and alphabetical department sorting.
10. **Late Leaderboard Analytics (`app/main.py:925`):** Implemented `build_late_leaderboard_pipeline` and `GET /analytics/leaderboard/late` backed by an indexed MongoDB aggregation pipeline utilizing `[("date", -1), ("emp_code", 1)]` (`IXSCAN`), grouping positive `late_minutes`, joining `employees` via `$lookup` (filtering invalid employee logs), filtering by optional department, deterministic tie-breaking sorting (`total_late_minutes` desc, `emp_code` asc), and applying standard competition ranking (1, 2, 2, 4) with ties at cutoff correctly retained when `rank <= limit`.


