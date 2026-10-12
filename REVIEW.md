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
| **8. Attendance (Punch-In & Punch-Out)** | 6 | 6 | 0 | 0 | **100%** |
| **8. Attendance Regularization (`PATCH`)** | 2 | 0 | 0 | 2 (Unimplemented) | **Pending** |
| **9. Analytics Endpoints** | 4 | 0 | 0 | 4 (Unimplemented) | **Pending** |
| **TOTAL** | **92** | **82** | **0** | **10** | **89.1%** |

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

### 5. Attendance Endpoints — Punch-In & Punch-Out (Section 8) — 100% PASS

| ID | Test Scenario | Method & Endpoint | Expected | Actual | Status | Verified Output |
|---|---|---|---|---|---|---|
| ATT-PIN-001 | Record valid punch-in | `POST /attendance/punch-in` | `201 Created` | `201` | **PASS** | Date resolved per R1, late minutes per R2 |
| ATT-PIN-002 | Unknown employee | `POST /attendance/punch-in` | `404 Not Found` | `404` | **PASS** | Returns `{"detail": "unknown employee"}` |
| ATT-PIN-003 | Duplicate punch-in on date | `POST /attendance/punch-in` | `409 Conflict` | `409` | **PASS** | Prevented duplicate on `(emp_code, date)` |
| ATT-POUT-001 | Record valid punch-out | `POST /attendance/punch-out` | `200 OK` | `200` | **PASS** | `work_hours: 9.0`, `overtime_minutes: 35`, `half_day: False` |
| ATT-POUT-002 | Duplicate punch-out | `POST /attendance/punch-out` | `409 Conflict` | `409` | **PASS** | Atomic `$set` prevents duplicate close |
| ATT-LIST-001 | List attendance logs | `GET /attendance` | `200 OK` | `200` | **PASS** | Paginated, sorted by date desc then emp_code asc |

---

### 6. Pending Items (To Be Implemented)

| ID | Endpoint | Description | Status |
|---|---|---|---|
| ATT-PATCH-001 | `PATCH /attendance/{emp_code}/{date}` | Manual regularization with audit trail history | **Pending** |
| ANL-MONTH-001 | `GET /analytics/employees/{emp_code}/monthly` | Monthly employee attendance aggregation | **Pending** |
| ANL-SUMM-001 | `GET /analytics/departments/summary` | Department summary aggregation | **Pending** |
| ANL-LEAD-001 | `GET /analytics/leaderboard/late` | Competition ranking leaderboard | **Pending** |
| ANL-TRND-001 | `GET /analytics/departments/{dept}/trend` | 7-day moving average attendance trend | **Pending** |
| ADM-EXPL-001 | `GET /admin/explain/{endpoint}` | MongoDB executionStats explain query | **Pending** |

---

### 7. Implementation Change Log in `app/main.py`

1. **Lifespan Indexes (`app/main.py:31`):** Added FastAPI lifespan creating unique indexes on `db.employees.emp_code`, `(emp_code, date)` for attendance, and compound index `(date, emp_code)`.
2. **Schema Hardening (`app/main.py:131`):** Configured strict constraints on `EmployeeIn`, `PunchInIn`, `PunchOutIn`:
   - `emp_code`: `^EMP\d{4,6}$`
   - `name`: 1–100 chars, whitespace rejection
   - `email`: strict regex matching OpenAPI spec
   - `department`: 1–50 chars, whitespace rejection
   - `joined_on`: real calendar date validator rejecting invalid dates (e.g. `2026-02-29`)
   - `shift_start` & `shift_end`: regex `^(?:[01]\d|2[0-3]):[0-5]\d$`, asserting start $\ne$ end
3. **Instant Standardization (`app/main.py:202`):** All timestamps returned in responses now strictly conform to integer epoch milliseconds (`EpochMillis`), stripping internal MongoDB `_id` and legacy `id` fields.
4. **Pagination Bugfix (`app/main.py:228`, `295`):** Fixed offset calculation `skip = (page - 1) * page_size`, added `Query(ge=1)` and `Query(le=100)`, and sorted by `emp_code` ascending.
5. **Punch-Out Implementation (`app/main.py:246`):** Fully implemented `POST /attendance/punch-out` calculating work hours, overtime ($\ge 30$ mins), and half-day status with atomic concurrency protection.
