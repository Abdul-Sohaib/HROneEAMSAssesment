"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
from decimal import ROUND_HALF_UP
from decimal import Decimal
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb+srv://sohaibmdabdul999_db_user:WvmjyDdvLUZpkwaX@hroneeamsassesment.olv2huq.mongodb.net/?appName=HROneEAMSAssesment"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def truncate_to_seconds(value: datetime) -> datetime:
# Remove fractional seconds while preserving the datetime's timezone.
    return value.replace(microsecond=0)


def to_ist(value: datetime) -> datetime:
# Convert an aware datetime to IST.
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must include timezone information")

    return value.astimezone(IST)


def shift_start_datetime(punch_in: datetime, shift_start: str, shift_end: str) -> datetime:
    # Find the shift start associated with this punch-in.
    punch_in = to_ist(truncate_to_seconds(punch_in))
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    start = punch_in.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)

    # A shift ending at or before its start time runs overnight.
    overnight = (end_hour, end_minute)<=(start_hour, start_minute)
    if overnight and (punch_in.hour, punch_in.minute)<(end_hour, end_minute):
        start -= timedelta(days=1)

    return start


def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: str = "18:30") -> int:
    # Return late minutes, applying the strict 10-minute grace period.
    punch_in = to_ist(truncate_to_seconds(punch_in))
    start = shift_start_datetime(punch_in, shift_start, shift_end)

    elapsed_seconds = int((punch_in - start).total_seconds())

    if elapsed_seconds <= 10 * 60:
        return 0

    return elapsed_seconds // 60


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    # Calculate elapsed hours rounded to two decimals, half-up.
    punch_in = truncate_to_seconds(punch_in)
    punch_out = truncate_to_seconds(punch_out)

    seconds = (punch_out - punch_in).total_seconds()
    if seconds < 0:
        raise ValueError("punch_out must not be earlier than punch_in")

    hours = Decimal(str(seconds)) / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_in: datetime, punch_out: datetime, shift_start: str, shift_end: str) -> int:
    # Return overtime only when at least 30 whole minutes are reached.
    punch_in = to_ist(truncate_to_seconds(punch_in))
    punch_out = to_ist(truncate_to_seconds(punch_out))

    start_dt = shift_start_datetime(punch_in, shift_start, shift_end)
    shift_date = start_dt.date()

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))
    overnight = (end_hour, end_minute) <= (start_hour, start_minute)

    # For an overnight shift, its end falls on the following date.
    end_date = shift_date + timedelta(days=1) if overnight else shift_date

    shift_end_dt = datetime(end_date.year, end_date.month, end_date.day, end_hour, end_minute, tzinfo=IST)

    elapsed_seconds = int((punch_out - shift_end_dt).total_seconds())
    whole_minutes = elapsed_seconds // 60

    return whole_minutes if whole_minutes >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = page * page_size
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at else datetime.now()
    d = ts.date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
