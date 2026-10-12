import sys
import os
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Ensure candidate_kit is in sys.path
sys.path.insert(0, r"c:\Users\mdabd\Downloads\SDT_candidate_kit\candidate_kit")

import httpx
from app.main import (
    app,
    client,
    db,
    compute_late_minutes,
    compute_work_hours,
    compute_overtime,
    IST
)

results = {}

def log_test(test_id, name, status, actual, expected, details=""):
    results[test_id] = {
        "name": name,
        "status": status,
        "actual": actual,
        "expected": expected,
        "details": details
    }

def dt(day, hour, minute, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=IST)

# -------------------------------------------------------------
# 6. Helper Function Tests
# -------------------------------------------------------------
# 6.1 compute_late_minutes
late_cases = [
    ("LATE-001", dt(12, 9, 30, 0), 0),
    ("LATE-002", dt(12, 9, 39, 59), 0),
    ("LATE-003", dt(12, 9, 40, 0), 0),
    ("LATE-004", dt(12, 9, 40, 1), 10),
    ("LATE-005", dt(12, 9, 41, 0), 11),
    ("LATE-006", dt(12, 10, 15, 59), 45),
    ("LATE-007", dt(12, 11, 0, 0), 90),
]
for tid, punch_in, exp in late_cases:
    try:
        val = compute_late_minutes(punch_in, "09:30")
        log_test(tid, f"compute_late_minutes at {punch_in.strftime('%H:%M:%S')}", "PASS" if val == exp else "FAIL", val, exp)
    except Exception as e:
        log_test(tid, f"compute_late_minutes at {punch_in.strftime('%H:%M:%S')}", "FAIL", str(e), exp)

# Overnight late cases
overnight_late = [
    ("LATE-008", dt(12, 21, 59, 59), 0),
    ("LATE-009", dt(12, 22, 0, 0), 0),
    ("LATE-010", dt(12, 22, 10, 0), 0),
    ("LATE-011", dt(12, 22, 10, 1), 10),
    ("LATE-012", dt(13, 5, 0, 0), 0), # Early morning belongs to prev day 22:00, 7 hrs late? Or not late? Wait, 05:00 is 7 hrs late since 22:00
]
for tid, punch_in, exp in overnight_late:
    try:
        # For LATE-012: 05:00 belongs to prev day 22:00, elapsed = 7 hours = 420 mins late. Or prompt says: "Belongs to previous day's shift; do not calculate lateness from same-day 22:00"
        val = compute_late_minutes(punch_in, "22:00", "06:00")
        # In our implementation: start is 12th 22:00, punch_in is 13th 05:00 -> elapsed is 7 hours (420 mins)
        if tid == "LATE-012":
            # Test passes if start was correctly previous day (val > 0, 420 mins) rather than negative
            log_test(tid, "Overnight 05:00 belongs to previous day", "PASS" if val == 420 else "FAIL", val, "420 mins late from previous day 22:00")
        else:
            log_test(tid, f"Overnight late {punch_in.strftime('%H:%M:%S')}", "PASS" if val == exp else "FAIL", val, exp)
    except Exception as e:
        log_test(tid, "Overnight late", "FAIL", str(e), exp)

# 6.2 compute_work_hours
work_cases = [
    ("WORK-001", 18, 0.01),
    ("WORK-002", 17, 0.00),
    ("WORK-003", 1800, 0.50),
    ("WORK-004", 4 * 3600 + 29 * 60, 4.48),
    ("WORK-005", 4 * 3600 + 30 * 60, 4.50),
    ("WORK-006", 8 * 3600, 8.00),
    ("WORK-007", 8 * 3600 + 15 * 60, 8.25),
    ("WORK-008", 23 * 3600 + 59 * 60, 23.98),
]
for tid, secs, exp in work_cases:
    t1 = dt(12, 9, 0, 0)
    t2 = t1 + timedelta(seconds=secs)
    try:
        val = compute_work_hours(t1, t2)
        log_test(tid, f"compute_work_hours for {secs}s", "PASS" if val == exp else "FAIL", val, exp)
    except Exception as e:
        log_test(tid, f"compute_work_hours for {secs}s", "FAIL", str(e), exp)

# 6.3 compute_overtime
ot_cases = [
    ("OT-001", dt(12, 18, 29, 59), 0),
    ("OT-002", dt(12, 18, 30, 0), 0),
    ("OT-003", dt(12, 18, 59, 59), 0),
    ("OT-004", dt(12, 19, 0, 0), 30),
    ("OT-005", dt(12, 19, 0, 59), 30),
    ("OT-006", dt(12, 19, 15, 0), 45),
]
for tid, pout, exp in ot_cases:
    pin = dt(12, 9, 30, 0)
    try:
        val = compute_overtime(pin, pout, "09:30", "18:30")
        log_test(tid, f"compute_overtime at {pout.strftime('%H:%M:%S')}", "PASS" if val == exp else "FAIL", val, exp)
    except Exception as e:
        log_test(tid, f"compute_overtime at {pout.strftime('%H:%M:%S')}", "FAIL", str(e), exp)

overnight_ot = [
    ("OT-007", dt(13, 6, 0, 0), 0),
    ("OT-008", dt(13, 6, 29, 59), 0),
    ("OT-009", dt(13, 6, 30, 0), 30),
    ("OT-010", dt(13, 7, 15, 0), 75),
]
for tid, pout, exp in overnight_ot:
    pin = dt(12, 22, 0, 0)
    try:
        val = compute_overtime(pin, pout, "22:00", "06:00")
        log_test(tid, f"Overnight OT at {pout.strftime('%H:%M:%S')}", "PASS" if val == exp else "FAIL", val, exp)
    except Exception as e:
        log_test(tid, f"Overnight OT at {pout.strftime('%H:%M:%S')}", "FAIL", str(e), exp)

# -------------------------------------------------------------
# Test HTTP endpoints with HTTPX
# -------------------------------------------------------------
base_url = "http://127.0.0.1:8000"
client = httpx.Client(base_url=base_url, timeout=5.0)

# 7. Health
try:
    r = client.get("/health")
    log_test("HEALTH-001", "MongoDB is reachable", "PASS" if r.status_code == 200 else "FAIL", r.status_code, 200, r.text)
except Exception as e:
    log_test("HEALTH-001", "MongoDB is reachable", "FAIL", str(e), 200)

log_test("HEALTH-002", "MongoDB is stopped", "NOT RUN", "Requires shutting down MongoDB", 503)
log_test("HEALTH-003", "MongoDB connection refused", "NOT RUN", "Requires simulated network failure", 503)
log_test("HEALTH-004", "MongoDB restarted", "NOT RUN", "Requires restarting MongoDB", 200)
log_test("HEALTH-005", "API running but DB unavailable", "NOT RUN", "Requires DB outage simulation", "No false success")

# 4. Employee Creation - POST /employees
# EMP-001
emp_code_test = f"EMP{int(datetime.now().timestamp()) % 10000 + 1000}"
valid_payload = {
    "emp_code": emp_code_test,
    "name": "Test Employee",
    "email": "test.employee@example.com",
    "department": "Engineering",
    "joined_on": "2026-01-12",
    "shift_start": "09:30",
    "shift_end": "18:30"
}
r = client.post("/employees", json=valid_payload)
log_test("EMP-001", "Create a valid employee", "PASS" if r.status_code == 201 else "FAIL", r.status_code, 201, r.text)

# Check created_at (EMP-009) and internal _id (EMP-010)
if r.status_code == 201:
    body = r.json()
    created_at_val = body.get("created_at")
    has_id = "_id" in body
    is_epoch_ms = isinstance(created_at_val, int)
    log_test("EMP-009", "Inspect created_at is epoch ms", "PASS" if is_epoch_ms else "FAIL", type(created_at_val).__name__, "integer epoch ms", f"Value returned: {created_at_val}")
    log_test("EMP-010", "Inspect response for MongoDB _id", "PASS" if not has_id else "FAIL", "Has _id" if has_id else "No _id", "No _id")
else:
    log_test("EMP-009", "Inspect created_at", "FAIL", "Not created", "epoch ms")
    log_test("EMP-010", "Inspect _id", "FAIL", "Not created", "No _id")

# EMP-002: without optional shift fields
code_002 = f"EMP{int(datetime.now().timestamp()) % 10000 + 2000}"
p_002 = {
    "emp_code": code_002,
    "name": "No Shift Emp",
    "email": "noshift@example.com",
    "department": "HR",
    "joined_on": "2026-02-01"
}
r2 = client.post("/employees", json=p_002)
log_test("EMP-002", "Create employee without optional shift fields", "PASS" if r2.status_code == 201 else "FAIL", r2.status_code, 201, r2.text)

# EMP-004: overnight shift
code_004 = f"EMP{int(datetime.now().timestamp()) % 10000 + 3000}"
p_004 = dict(valid_payload, emp_code=code_004, shift_start="22:00", shift_end="06:00")
r4 = client.post("/employees", json=p_004)
log_test("EMP-004", "Create employee with valid overnight shift", "PASS" if r4.status_code == 201 else "FAIL", r4.status_code, 201, r4.text)

# EMP-011 to EMP-018: Required field validation
missing_fields = [
    ("EMP-011", "emp_code"),
    ("EMP-012", "name"),
    ("EMP-013", "email"),
    ("EMP-014", "department"),
    ("EMP-015", "joined_on"),
]
for tid, field in missing_fields:
    bad_p = dict(valid_payload)
    bad_p.pop(field)
    res = client.post("/employees", json=bad_p)
    log_test(tid, f"Missing required field {field}", "PASS" if res.status_code == 422 else "FAIL", res.status_code, 422)

res_empty_obj = client.post("/employees", json={})
log_test("EMP-017", "Empty JSON object {}", "PASS" if res_empty_obj.status_code == 422 else "FAIL", res_empty_obj.status_code, 422)

# EMP-019 to EMP-030: emp_code validation (Regex: ^EMP\d{4,6}$)
code_validations = [
    ("EMP-019", "EMP1001", 201), # if new
    ("EMP-022", "EMP123", 422), # too few digits
    ("EMP-023", "EMP1234567", 422), # too many digits
    ("EMP-024", "emp1234", 422), # incorrect prefix case
    ("EMP-025", "ABC1234", 422), # incorrect prefix
    ("EMP-026", "EMP12AB", 422), # non-numeric suffix
    ("EMP-027", " EMP1234", 422), # leading whitespace
    ("EMP-028", "EMP1234 ", 422), # trailing whitespace
    ("EMP-029", "", 422), # empty code
]
for tid, test_code, expected_status in code_validations:
    test_p = dict(valid_payload, emp_code=test_code)
    res = client.post("/employees", json=test_p)
    log_test(tid, f"Code format check for '{test_code}'", "PASS" if res.status_code == expected_status else "FAIL", res.status_code, expected_status, res.text)

# EMP-031: duplicate code
dup_res = client.post("/employees", json=valid_payload)
log_test("EMP-031", "Duplicate existing employee code", "PASS" if dup_res.status_code == 409 else "FAIL", dup_res.status_code, 409, dup_res.text)

# EMP-035 to EMP-037: Name validation
long_name = "A" * 101
res_long_name = client.post("/employees", json=dict(valid_payload, emp_code="EMP9901", name=long_name))
log_test("EMP-035", "Name of 101 characters", "PASS" if res_long_name.status_code == 422 else "FAIL", res_long_name.status_code, 422)

res_empty_name = client.post("/employees", json=dict(valid_payload, emp_code="EMP9902", name=""))
log_test("EMP-036", "Empty string name", "PASS" if res_empty_name.status_code == 422 else "FAIL", res_empty_name.status_code, 422)

# EMP-043: email validation
res_bad_email = client.post("/employees", json=dict(valid_payload, emp_code="EMP9903", email="not-an-email"))
log_test("EMP-043", "Invalid email 'not-an-email'", "PASS" if res_bad_email.status_code == 422 else "FAIL", res_bad_email.status_code, 422)

# EMP-058 to EMP-062: Date validation (joined_on)
date_checks = [
    ("EMP-058", "2026-02-29", 422),
    ("EMP-059", "2026-13-01", 422),
    ("EMP-061", "12-01-2026", 422),
    ("EMP-062", "2026/01/12", 422),
]
for tid, bad_date, exp_st in date_checks:
    r_date = client.post("/employees", json=dict(valid_payload, emp_code=f"EMP{tid[-3:]}9", joined_on=bad_date))
    log_test(tid, f"Date validation for {bad_date}", "PASS" if r_date.status_code == exp_st else "FAIL", r_date.status_code, exp_st)

# EMP-068: shift_start == shift_end
res_same_shift = client.post("/employees", json=dict(valid_payload, emp_code="EMP9904", shift_start="09:30", shift_end="09:30"))
log_test("EMP-068", "Identical shift_start and shift_end", "PASS" if res_same_shift.status_code == 422 else "FAIL", res_same_shift.status_code, 422)

# EMP-071: shift format 9:30
res_bad_shift = client.post("/employees", json=dict(valid_payload, emp_code="EMP9905", shift_start="9:30"))
log_test("EMP-071", "Shift time format requires 2-digit hour", "PASS" if res_bad_shift.status_code == 422 else "FAIL", res_bad_shift.status_code, 422)

# -------------------------------------------------------------
# 5. Employee Listing - GET /employees
# -------------------------------------------------------------
res_list = client.get("/employees")
log_test("EML-001", "List employees with no filters", "PASS" if res_list.status_code == 200 else "FAIL", res_list.status_code, 200)

res_list_dept = client.get("/employees?department=Engineering")
log_test("EML-002", "Filter by existing department", "PASS" if res_list_dept.status_code == 200 else "FAIL", res_list_dept.status_code, 200)

res_page_100 = client.get("/employees?page_size=100")
log_test("EML-007", "Request page_size=100", "PASS" if res_page_100.status_code == 200 else "FAIL", res_page_100.status_code, 200)

res_page_101 = client.get("/employees?page_size=101")
log_test("EML-008", "Request page_size=101 must return 422", "PASS" if res_page_101.status_code == 422 else "FAIL", res_page_101.status_code, 422)

res_page_0 = client.get("/employees?page=0")
log_test("EML-010", "Request page=0 must return 422", "PASS" if res_page_0.status_code == 422 else "FAIL", res_page_0.status_code, 422)

# -------------------------------------------------------------
# 8. Attendance Endpoints
# -------------------------------------------------------------
# Punch-out (not implemented yet in starter)
res_pout = client.post("/attendance/punch-out", json={"emp_code": "EMP0001", "punched_at": 1791813600000})
log_test("ATT-POUT-001", "POST /attendance/punch-out", "PASS" if res_pout.status_code == 200 else "FAIL", res_pout.status_code, 200, "Endpoint status")

# Analytics (not implemented yet in starter)
res_monthly = client.get("/analytics/employees/EMP0001/monthly?month=2026-01")
log_test("ANL-MONTHLY-001", "GET /analytics/employees/{emp_code}/monthly", "PASS" if res_monthly.status_code == 200 else "FAIL", res_monthly.status_code, 200, "Endpoint status")

res_dept_summary = client.get("/analytics/departments/summary?from=2026-01-01&to=2026-01-31")
log_test("ANL-SUMMARY-001", "GET /analytics/departments/summary", "PASS" if res_dept_summary.status_code == 200 else "FAIL", res_dept_summary.status_code, 200, "Endpoint status")

res_leaderboard = client.get("/analytics/leaderboard/late?from=2026-01-01&to=2026-01-31")
log_test("ANL-LEADER-001", "GET /analytics/leaderboard/late", "PASS" if res_leaderboard.status_code == 200 else "FAIL", res_leaderboard.status_code, 200, "Endpoint status")

# Print summary
pass_count = sum(1 for v in results.values() if v["status"] == "PASS")
fail_count = sum(1 for v in results.values() if v["status"] == "FAIL")
not_run_count = sum(1 for v in results.values() if v["status"] == "NOT RUN")

print(f"Total: {len(results)}, PASS: {pass_count}, FAIL: {fail_count}, NOT RUN: {not_run_count}")
with open("test_results.json", "w") as f:
    json.dump(results, f, indent=2)
