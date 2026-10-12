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
from contextlib import asynccontextmanager
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb+srv://sohaibmdabdul999_db_user:WvmjyDdvLUZpkwaX@hroneeamsassesment.olv2huq.mongodb.net/?appName=HROneEAMSAssesment"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.employees.create_index("emp_code", unique=True)
        db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
        db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
        db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    except Exception:
        pass
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


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
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be whitespace-only")
        return v

    @field_validator("department")
    @classmethod
    def validate_department(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("department cannot be whitespace-only")
        return v

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, v: str) -> str:
        try:
            d = datetime.strptime(v, "%Y-%m-%d")
            if d.strftime("%Y-%m-%d") != v:
                raise ValueError("Invalid date format")
        except Exception:
            raise ValueError("Invalid calendar date, must be valid YYYY-MM-DD")
        return v

    @model_validator(mode="after")
    def validate_shifts(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
        description="Epoch milliseconds",
    )
    status: str = Field(default="PRESENT", pattern=r"^(PRESENT|WFH|ON_DUTY)$")


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
        description="Epoch milliseconds",
    )


def to_epoch_ms(val) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return int(val.timestamp() * 1000)
    return None


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health_check():
    try:
        client.admin.command("ping")
        return {
            "status": "ok",
            "database": "connected"
        }
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "database": "disconnected"
            }
        )


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    created_at_ms = int(now_utc.timestamp() * 1000)

    doc = body.model_dump()
    doc["created_at"] = now_utc

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    resp = body.model_dump()
    resp["created_at"] = created_at_ms
    return resp


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    raw_items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))

    items = []
    for item in raw_items:
        item["created_at"] = to_epoch_ms(item.get("created_at"))
        items.append(item)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    if body.punched_at is not None:
        ts_utc = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(microsecond=0)
    else:
        ts_utc = now_utc

    ts_ist = ts_utc.astimezone(IST)
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    start_dt = shift_start_datetime(ts_ist, shift_start, shift_end)
    date_str = start_dt.date().isoformat()

    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": date_str}):
        raise HTTPException(409, "already punched in for this date")

    late_mins = compute_late_minutes(ts_ist, shift_start, shift_end)

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")

    return {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": int(ts_utc.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }


@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    if body.punched_at is not None:
        pout_utc = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc).replace(microsecond=0)
    else:
        pout_utc = now_utc

    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": pout_utc}},
        sort=[("punch_in", -1)],
    )

    if not record:
        raise HTTPException(404, "no punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "already punched out for this record")

    pin_utc = record["punch_in"]
    if pin_utc.tzinfo is None:
        pin_utc = pin_utc.replace(tzinfo=timezone.utc)

    diff_seconds = (pout_utc - pin_utc).total_seconds()
    if diff_seconds <= 0 or diff_seconds > 24 * 3600:
        raise HTTPException(422, "punched_at must be strictly greater than punch_in and within 24 hours")

    pin_ist = pin_utc.astimezone(IST)
    pout_ist = pout_utc.astimezone(IST)

    work_hours = compute_work_hours(pin_ist, pout_ist)
    ot_minutes = compute_overtime(pin_ist, pout_ist, emp["shift_start"], emp["shift_end"])
    half_day = work_hours < 4.50

    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": pout_utc,
                "work_hours": work_hours,
                "overtime_minutes": ot_minutes,
                "half_day": half_day,
            }
        },
        return_document=True,
    )

    if not updated:
        raise HTTPException(409, "already punched out for this record")

    return {
        "emp_code": updated["emp_code"],
        "date": updated["date"],
        "status": updated["status"],
        "punch_in": to_epoch_ms(pin_utc),
        "punch_out": to_epoch_ms(pout_utc),
        "work_hours": work_hours,
        "late_minutes": updated.get("late_minutes", 0),
        "overtime_minutes": ot_minutes,
        "half_day": half_day,
        "history": updated.get("history", []),
    }


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not be greater than date_to")

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

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    docs = list(db.attendance_logs.find(q).sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size))

    items = []
    for d in docs:
        items.append({
            "emp_code": d["emp_code"],
            "date": d["date"],
            "status": d["status"],
            "punch_in": to_epoch_ms(d.get("punch_in")),
            "punch_out": to_epoch_ms(d.get("punch_out")),
            "work_hours": d.get("work_hours"),
            "late_minutes": d.get("late_minutes", 0),
            "overtime_minutes": d.get("overtime_minutes", 0),
            "half_day": d.get("half_day", False),
            "history": d.get("history", []),
        })

    return {"items": items, "total": total, "page": page, "page_size": page_size}


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
