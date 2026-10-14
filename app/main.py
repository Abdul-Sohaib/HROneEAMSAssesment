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
from enum import Enum
import os
import json
import logging
import calendar
from datetime import datetime, date, timedelta, timezone
from typing import Optional
from bson import json_util

import dns.resolver
try:
    _res = dns.resolver.Resolver()
    _res.nameservers = ['8.8.8.8', '1.1.1.1']
    dns.resolver.default_resolver = _res
except Exception:
    pass

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

logger = logging.getLogger(__name__)

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.employees.create_index("emp_code", unique=True)
        db.employees.create_index([("department", 1), ("joined_on", 1)])
        db.employees.create_index("joined_on")
        db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
        db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
        db.attendance_logs.create_index([("emp_code", 1), ("punch_in", -1)])
    except Exception as e:
        logger.error(f"Failed to create required MongoDB indexes: {e}")
        raise RuntimeError(f"Startup failed: unable to create required database indexes: {e}") from e
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
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    punched_at: Optional[StrictInt] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
        description="Epoch milliseconds",
    )
    status: str = Field(default="PRESENT", pattern=r"^(PRESENT|WFH|ON_DUTY)$")


class PunchOutIn(BaseModel):
    emp_code: str = Field(..., pattern=r"^EMP\d{4,6}$")
    punched_at: Optional[StrictInt] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
        description="Epoch milliseconds",
    )


class RegularizeRequest(BaseModel):
    status: Optional[str] = Field(
        default=None,
        pattern=r"^(PRESENT|ABSENT|LEAVE|WFH|ON_DUTY)$",
    )
    punch_in: Optional[StrictInt] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
    )
    punch_out: Optional[StrictInt] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000,
    )
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason cannot be whitespace-only")
        return v

    @field_validator("regularized_by")
    @classmethod
    def validate_regularized_by(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("regularized_by cannot be whitespace-only")
        return v


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


def serialize_history_entry(entry: dict) -> dict:
    at_val = entry.get("at")
    at_ms = to_epoch_ms(at_val)
    changes = {}
    for k, v in entry.get("changes", {}).items():
        if k in ("punch_in", "punch_out"):
            changes[k] = {
                "from": to_epoch_ms(v.get("from")),
                "to": to_epoch_ms(v.get("to")),
            }
        else:
            changes[k] = {
                "from": v.get("from"),
                "to": v.get("to"),
            }
    return {
        "at": at_ms,
        "by": entry.get("by"),
        "reason": entry.get("reason"),
        "changes": changes,
    }


def serialize_attendance(d: dict) -> dict:
    return {
        "emp_code": d["emp_code"],
        "date": d["date"],
        "status": d["status"],
        "punch_in": to_epoch_ms(d.get("punch_in")),
        "punch_out": to_epoch_ms(d.get("punch_out")),
        "work_hours": d.get("work_hours"),
        "late_minutes": d.get("late_minutes", 0),
        "overtime_minutes": d.get("overtime_minutes", 0),
        "half_day": d.get("half_day", False),
        "history": [serialize_history_entry(h) for h in d.get("history", [])],
    }


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
    items = [serialize_attendance(d) for d in docs]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}", status_code=200)
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeRequest,
):
    try:
        d_obj = datetime.strptime(date, "%Y-%m-%d")
        if d_obj.strftime("%Y-%m-%d") != date:
            raise ValueError()
    except Exception:
        raise HTTPException(422, "Invalid date format, must be YYYY-MM-DD")

    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "attendance record not found")

    old_status = record["status"]
    old_pin = record.get("punch_in")
    old_pout = record.get("punch_out")
    old_work_hours = record.get("work_hours")
    old_late_minutes = record.get("late_minutes", 0)
    old_overtime_minutes = record.get("overtime_minutes", 0)
    old_half_day = record.get("half_day", False)

    if old_pin and old_pin.tzinfo is None:
        old_pin = old_pin.replace(tzinfo=timezone.utc)
    if old_pout and old_pout.tzinfo is None:
        old_pout = old_pout.replace(tzinfo=timezone.utc)

    new_status = body.status if body.status is not None else old_status

    if body.status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "cannot supply punch_in or punch_out when setting status to ABSENT or LEAVE")
        new_pin = None
        new_pout = None
    elif new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "cannot supply punch_in or punch_out for an ABSENT or LEAVE record")
        new_pin = None
        new_pout = None
    else:
        if body.punch_in is not None:
            new_pin = datetime.fromtimestamp(body.punch_in / 1000, tz=timezone.utc).replace(microsecond=0)
        else:
            new_pin = old_pin

        if body.punch_out is not None:
            new_pout = datetime.fromtimestamp(body.punch_out / 1000, tz=timezone.utc).replace(microsecond=0)
        else:
            new_pout = old_pout

        if new_pin is None:
            raise HTTPException(422, "presence status requires a punch_in")

        pin_ist = new_pin.astimezone(IST)
        start_dt = shift_start_datetime(pin_ist, emp["shift_start"], emp["shift_end"])
        if start_dt.date().isoformat() != date:
            raise HTTPException(422, "punch_in does not belong to the record's attendance date in IST")

        if new_pout is not None:
            diff_sec = (new_pout - new_pin).total_seconds()
            if diff_sec <= 0 or diff_sec > 24 * 3600:
                raise HTTPException(422, "punch_out must be strictly after punch_in and within 24 hours")

    if new_status in ("ABSENT", "LEAVE"):
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        pin_ist = new_pin.astimezone(IST)
        new_late_minutes = compute_late_minutes(pin_ist, emp["shift_start"], emp["shift_end"])
        if new_pout is not None:
            pout_ist = new_pout.astimezone(IST)
            new_work_hours = compute_work_hours(pin_ist, pout_ist)
            new_overtime_minutes = compute_overtime(pin_ist, pout_ist, emp["shift_start"], emp["shift_end"])
            new_half_day = new_work_hours < 4.50
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False

    changes_db = {}
    changes_api = {}

    if old_status != new_status:
        changes_db["status"] = {"from": old_status, "to": new_status}
        changes_api["status"] = {"from": old_status, "to": new_status}

    old_pin_ms = to_epoch_ms(old_pin)
    new_pin_ms = to_epoch_ms(new_pin)
    if old_pin_ms != new_pin_ms:
        changes_db["punch_in"] = {"from": old_pin, "to": new_pin}
        changes_api["punch_in"] = {"from": old_pin_ms, "to": new_pin_ms}

    old_pout_ms = to_epoch_ms(old_pout)
    new_pout_ms = to_epoch_ms(new_pout)
    if old_pout_ms != new_pout_ms:
        changes_db["punch_out"] = {"from": old_pout, "to": new_pout}
        changes_api["punch_out"] = {"from": old_pout_ms, "to": new_pout_ms}

    if old_work_hours != new_work_hours:
        changes_db["work_hours"] = {"from": old_work_hours, "to": new_work_hours}
        changes_api["work_hours"] = {"from": old_work_hours, "to": new_work_hours}

    if old_late_minutes != new_late_minutes:
        changes_db["late_minutes"] = {"from": old_late_minutes, "to": new_late_minutes}
        changes_api["late_minutes"] = {"from": old_late_minutes, "to": new_late_minutes}

    if old_overtime_minutes != new_overtime_minutes:
        changes_db["overtime_minutes"] = {"from": old_overtime_minutes, "to": new_overtime_minutes}
        changes_api["overtime_minutes"] = {"from": old_overtime_minutes, "to": new_overtime_minutes}

    if old_half_day != new_half_day:
        changes_db["half_day"] = {"from": old_half_day, "to": new_half_day}
        changes_api["half_day"] = {"from": old_half_day, "to": new_half_day}

    if not changes_api:
        raise HTTPException(422, "request makes no actual changes")

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    history_entry_db = {
        "at": now_utc,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes_db,
    }

    current_history_len = len(record.get("history", []))

    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "history": {"$size": current_history_len},
        },
        {
            "$set": {
                "status": new_status,
                "punch_in": new_pin,
                "punch_out": new_pout,
                "work_hours": new_work_hours,
                "late_minutes": new_late_minutes,
                "overtime_minutes": new_overtime_minutes,
                "half_day": new_half_day,
            },
            "$push": {"history": history_entry_db},
        },
        return_document=True,
    )

    if not updated:
        raise HTTPException(409, "concurrent modification conflict; please retry regularization")

    return serialize_attendance(updated)


def compute_working_days(joined_on_str: str, month_str: str) -> int:
    year, month_num = map(int, month_str.split("-"))
    _, last_day = calendar.monthrange(year, month_num)
    d_start = date(year, month_num, 1)
    d_end = date(year, month_num, last_day)
    d_joined = datetime.strptime(joined_on_str, "%Y-%m-%d").date()

    if d_joined > d_end:
        return 0

    start_counting = max(d_start, d_joined)
    count = 0
    curr = start_counting
    while curr <= d_end:
        if curr.weekday() < 5:  # Monday to Friday
            count += 1
        curr += timedelta(days=1)
    return count


def build_employee_monthly_pipeline(emp_code: str, month: str) -> tuple[str, list[dict]]:
    year, month_num = map(int, month.split("-"))
    _, last_day = calendar.monthrange(year, month_num)
    start_date = f"{month}-01"
    end_date = f"{month}-{last_day:02d}"

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": start_date, "$lte": end_date},
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {
                                        "$in": [
                                            {"$isoDayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                                            [1, 2, 3, 4, 5],
                                        ]
                                    },
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                            0.0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]
    return "attendance_logs", pipeline


@app.get("/analytics/employees/{emp_code}/monthly", status_code=200)
def get_employee_monthly_analytics(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "unknown employee")

    working_days = compute_working_days(emp["joined_on"], month)
    coll_name, pipeline = build_employee_monthly_pipeline(emp_code, month)
    docs = list(db[coll_name].aggregate(pipeline))

    if docs:
        d = docs[0]
        present_days = float(d.get("present_days", 0.0))
        leave_days = int(d.get("leave_days", 0))
        late_count = int(d.get("late_count", 0))
        total_late_minutes = int(d.get("total_late_minutes", 0))
        total_overtime_minutes = int(d.get("total_overtime_minutes", 0))
    else:
        present_days = 0.0
        leave_days = 0
        late_count = 0
        total_late_minutes = 0
        total_overtime_minutes = 0

    if working_days == 0:
        attendance_pct = None
    else:
        pct = (Decimal(str(present_days)) * Decimal("100")) / Decimal(str(working_days))
        attendance_pct = float(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct,
    }




def build_dept_summary_pipeline(month: str, department: Optional[str] = None) -> tuple[str, list[dict]]:
    year, month_num = map(int, month.split("-"))
    _, last_day = calendar.monthrange(year, month_num)
    start_date = f"{month}-01"
    end_date = f"{month}-{last_day:02d}"

    match_stage = {"joined_on": {"$lte": end_date}}
    if department:
        match_stage["department"] = department

    pipeline = [
        {"$match": match_stage},
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "emp_codes": {"$push": "$emp_code"},
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"codes": "$emp_codes"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$in": ["$emp_code", "$$codes"]},
                                    {"$gte": ["$date", start_date]},
                                    {"$lte": ["$date", end_date]},
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {
                                                    "$in": [
                                                        {"$isoDayOfWeek": {"$dateFromString": {"dateString": "$date"}}},
                                                        [1, 2, 3, 4, 5],
                                                    ]
                                                },
                                            ]
                                        },
                                        {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                                        0.0,
                                    ]
                                }
                            },
                            "avg_work_hours": {
                                "$avg": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]},
                                            ]
                                        },
                                        "$work_hours",
                                        "$$REMOVE",
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}
                            },
                            "total_late_minutes": {
                                "$sum": {"$ifNull": ["$late_minutes", 0]}
                            },
                            "leave_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                            },
                            "on_duty_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                            },
                        }
                    },
                ],
                "as": "metrics",
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "metrics": {"$arrayElemAt": ["$metrics", 0]},
            }
        },
        {"$sort": {"department": 1}},
    ]
    return "employees", pipeline


@app.get("/analytics/departments/summary", status_code=200)
def get_department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    coll_name, pipeline = build_dept_summary_pipeline(month, department)
    docs = list(db[coll_name].aggregate(pipeline))

    items = []
    for d in docs:
        m = d.get("metrics") or {}
        raw_avg = m.get("avg_work_hours")
        if raw_avg is not None:
            avg_work_hours = float(Decimal(str(raw_avg)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        else:
            avg_work_hours = None

        items.append({
            "department": d["department"],
            "headcount": d["headcount"],
            "present_days": float(m.get("present_days", 0.0)),
            "avg_work_hours": avg_work_hours,
            "late_count": int(m.get("late_count", 0)),
            "total_late_minutes": int(m.get("total_late_minutes", 0)),
            "leave_count": int(m.get("leave_count", 0)),
            "on_duty_count": int(m.get("on_duty_count", 0)),
        })

    return {
        "month": month,
        "items": items,
    }


def build_late_leaderboard_pipeline(month: str, limit: int = 10, department: Optional[str] = None):
    year, m = map(int, month.split("-"))
    last_day = calendar.monthrange(year, m)[1]
    start_date = f"{month}-01"
    end_date = f"{month}-{last_day:02d}"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})
    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                }
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
    ])
    return "attendance_logs", pipeline


@app.get("/analytics/leaderboard/late", status_code=200)
def get_late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    coll_name, pipeline = build_late_leaderboard_pipeline(month, limit, department)
    docs = list(db[coll_name].aggregate(pipeline))

    return {
        "month": month,
        "items": docs,
    }


def build_department_trend_pipeline(department: str, from_date: str, to_date: str):
    d_from = date.fromisoformat(from_date)
    d_to = date.fromisoformat(to_date)
    from_dt = datetime(d_from.year, d_from.month, d_from.day)
    bounds_end = datetime(d_to.year, d_to.month, d_to.day) + timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "date": {"$gte": from_date, "$lte": to_date},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
        {"$match": {"emp.department": department}},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1.0]},
                            0.0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {"$dateFromString": {"dateString": "$_id"}},
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [from_dt, bounds_end],
                },
            }
        },
        {
            "$project": {
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
                "present_count": {"$ifNull": ["$present_count", 0.0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "is_working_day": {
                    "$in": [
                        {"$isoDayOfWeek": "$date"},
                        [1, 2, 3, 4, 5],
                    ]
                },
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"day_str": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$day_str"]},
                                ]
                            }
                        }
                    },
                    {"$count": "count"},
                ],
                "as": "hc",
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.count", 0]}, 0]},
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                {"$eq": ["$is_working_day", True]},
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$cond": [
                                {"$eq": ["$present_count", 0.0]},
                                0.0,
                                {
                                    "$round": [
                                        {"$divide": ["$present_count", "$headcount"]},
                                        4,
                                    ]
                                },
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, "current"]
                        }
                    }
                }
            }
        },
        {
            "$addFields": {
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_avg_7d", None]},
                        None,
                        {"$round": ["$moving_avg_7d", 4]}
                    ]
                }
            }
        },
        {"$sort": {"date": 1}},
    ]
    return "attendance_logs", pipeline


@app.get("/analytics/departments/{department}/trend", status_code=200)
def get_department_trend(
    department: str,
    from_: str = Query(..., alias="from", pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
    to: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"),
):
    dept_exists = db.employees.count_documents({"department": department}) > 0
    if not dept_exists:
        raise HTTPException(404, "unknown department")

    try:
        d_from = date.fromisoformat(from_)
        d_to = date.fromisoformat(to)
    except ValueError:
        raise HTTPException(422, "Invalid date format")

    if d_to < d_from:
        raise HTTPException(422, "'to' date must be >= 'from' date")

    if (d_to - d_from).days + 1 > 92:
        raise HTTPException(422, "date range exceeds 92 days")

    coll_name, pipeline = build_department_trend_pipeline(department, from_, to)
    docs = list(db[coll_name].aggregate(pipeline))

    return {
        "department": department,
        "items": docs,
    }


class ExplainEndpointEnum(str, Enum):
    attendance_list = "attendance_list"
    employee_monthly = "employee_monthly"
    department_summary = "department_summary"
    late_leaderboard = "late_leaderboard"
    department_trend = "department_trend"


@app.get("/admin/explain/{endpoint}", status_code=200)
def explain_endpoint(
    endpoint: ExplainEndpointEnum,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if endpoint == ExplainEndpointEnum.attendance_list:
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

        skip = (page - 1) * page_size
        exp = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": skip,
                "limit": page_size,
            },
            verbosity="executionStats",
        )
        collection = "attendance_logs"

    elif endpoint == ExplainEndpointEnum.employee_monthly:
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required for employee_monthly")

        collection, pipeline = build_employee_monthly_pipeline(emp_code, month)
        exp = db.command(
            "explain",
            {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    elif endpoint == ExplainEndpointEnum.department_summary:
        if not month:
            raise HTTPException(422, "month is required for department_summary")

        collection, pipeline = build_dept_summary_pipeline(month, department)
        exp = db.command(
            "explain",
            {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    elif endpoint == ExplainEndpointEnum.late_leaderboard:
        if not month:
            raise HTTPException(422, "month is required for late_leaderboard")

        collection, pipeline = build_late_leaderboard_pipeline(month, limit or 10, department)
        exp = db.command(
            "explain",
            {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    elif endpoint == ExplainEndpointEnum.department_trend:
        if not department or not from_ or not to:
            raise HTTPException(422, "department, from, and to are required for department_trend")

        try:
            d_from = date.fromisoformat(from_)
            d_to = date.fromisoformat(to)
        except ValueError:
            raise HTTPException(422, "Invalid date format")

        if d_to < d_from:
            raise HTTPException(422, "'to' date must be >= 'from' date")
        if (d_to - d_from).days + 1 > 92:
            raise HTTPException(422, "date range exceeds 92 days")

        collection, pipeline = build_department_trend_pipeline(department, from_, to)
        exp = db.command(
            "explain",
            {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    else:
        raise HTTPException(422, f"unsupported endpoint: {endpoint}")

    return {
        "endpoint": endpoint.value,
        "collection": collection,
        "explain": json.loads(json_util.dumps(exp)),
    }





