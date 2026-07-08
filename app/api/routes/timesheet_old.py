"""Timesheet Calendar module — Holidays, Leave, Permissions and the calendar
aggregation endpoint used for working-hour management.

Minimum required working hours = 7 hours/day, INCLUDING permission hours and
breaks/buffer time already logged through Work Hours. Time entries can be
made from anywhere in the app (Work Hours page, Milestone Configuration,
etc.) — this module simply reads/aggregates the same WorkHours rows, so any
entry logged elsewhere automatically shows up here.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func
from typing import Optional
from datetime import datetime, date as date_cls, timedelta
from pydantic import BaseModel
from calendar import monthrange
from app.db.database import get_db
from app.models.models import Holiday, LeaveRequest, Permission, WorkHours, User
from app.core.deps import get_current_user

router = APIRouter(prefix="/timesheet", tags=["Timesheet Calendar"])

MIN_DAILY_HOURS = 7.0


def _parse_date(v: str) -> date_cls:
    return datetime.fromisoformat(v).date()


# ── Holidays ───────────────────────────────────────────────────────────────
class HolidayCreate(BaseModel):
    date: str
    name: str
    project_id: Optional[int] = None

@router.get("/holidays")
def list_holidays(project_id: Optional[int] = None, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    q = db.query(Holiday)
    if project_id is not None:
        q = q.filter((Holiday.project_id == project_id) | (Holiday.project_id.is_(None)))
    rows = q.order_by(Holiday.date).all()
    return [{"id": h.id, "date": h.date, "name": h.name, "project_id": h.project_id} for h in rows]

@router.post("/holidays")
def create_holiday(payload: HolidayCreate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    h = Holiday(date=_parse_date(payload.date), name=payload.name, project_id=payload.project_id)
    db.add(h); db.commit(); db.refresh(h)
    return {"id": h.id, "date": h.date, "name": h.name, "project_id": h.project_id}

@router.delete("/holidays/{holiday_id}")
def delete_holiday(holiday_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    h = db.query(Holiday).filter_by(id=holiday_id).first()
    if not h: raise HTTPException(404, "Holiday not found")
    db.delete(h); db.commit()
    return {"status": "deleted"}


# ── Leave requests ─────────────────────────────────────────────────────────
class LeaveCreate(BaseModel):
    user_id: Optional[int] = None
    date_from: str
    date_to: str
    leave_type: str = "Casual"
    reason: Optional[str] = None

class LeaveUpdate(BaseModel):
    status: Optional[str] = None
    leave_type: Optional[str] = None
    reason: Optional[str] = None

@router.get("/leaves")
def list_leaves(user_id: Optional[int] = None, status: Optional[str] = None,
                 db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    q = db.query(LeaveRequest)
    if user_id: q = q.filter_by(user_id=user_id)
    if status: q = q.filter_by(status=status)
    rows = q.order_by(LeaveRequest.date_from.desc()).all()
    out = []
    for l in rows:
        u = db.query(User).filter_by(id=l.user_id).first()
        out.append({"id": l.id, "user_id": l.user_id, "user_name": u.name if u else "—",
                    "date_from": l.date_from, "date_to": l.date_to, "leave_type": l.leave_type,
                    "status": l.status, "reason": l.reason})
    return out

@router.post("/leaves")
def create_leave(payload: LeaveCreate, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    l = LeaveRequest(user_id=payload.user_id or current_user.id,
                     date_from=_parse_date(payload.date_from), date_to=_parse_date(payload.date_to),
                     leave_type=payload.leave_type, reason=payload.reason, status="Pending")
    db.add(l); db.commit(); db.refresh(l)
    return {"id": l.id, "status": l.status}

@router.patch("/leaves/{leave_id}")
def update_leave(leave_id: int, payload: LeaveUpdate, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    l = db.query(LeaveRequest).filter_by(id=leave_id).first()
    if not l: raise HTTPException(404, "Leave request not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(l, k, v)
    db.commit()
    return {"status": "ok"}

@router.delete("/leaves/{leave_id}")
def delete_leave(leave_id: int, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    l = db.query(LeaveRequest).filter_by(id=leave_id).first()
    if not l: raise HTTPException(404, "Leave request not found")
    db.delete(l); db.commit()
    return {"status": "deleted"}


# ── Permissions (short leave, counted toward the 7hr/day minimum) ───────────
class PermissionCreate(BaseModel):
    user_id: Optional[int] = None
    date: str
    hours: float
    reason: Optional[str] = None

class PermissionUpdate(BaseModel):
    status: Optional[str] = None
    hours: Optional[float] = None
    reason: Optional[str] = None

@router.get("/permissions")
def list_permissions(user_id: Optional[int] = None, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    q = db.query(Permission)
    if user_id: q = q.filter_by(user_id=user_id)
    rows = q.order_by(Permission.date.desc()).all()
    out = []
    for p in rows:
        u = db.query(User).filter_by(id=p.user_id).first()
        out.append({"id": p.id, "user_id": p.user_id, "user_name": u.name if u else "—",
                    "date": p.date, "hours": p.hours, "reason": p.reason, "status": p.status})
    return out

@router.post("/permissions")
def create_permission(payload: PermissionCreate, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    p = Permission(user_id=payload.user_id or current_user.id, date=_parse_date(payload.date),
                   hours=payload.hours, reason=payload.reason, status="Pending")
    db.add(p); db.commit(); db.refresh(p)
    return {"id": p.id, "status": p.status}

@router.patch("/permissions/{permission_id}")
def update_permission(permission_id: int, payload: PermissionUpdate, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    p = db.query(Permission).filter_by(id=permission_id).first()
    if not p: raise HTTPException(404, "Permission not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    return {"status": "ok"}

@router.delete("/permissions/{permission_id}")
def delete_permission(permission_id: int, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    p = db.query(Permission).filter_by(id=permission_id).first()
    if not p: raise HTTPException(404, "Permission not found")
    db.delete(p); db.commit()
    return {"status": "deleted"}


# ── Calendar aggregation ──────────────────────────────────────────────────
@router.get("/calendar")
def get_calendar(
    month: str,                       # "YYYY-MM"
    user_id: Optional[int] = None,
    project_id: Optional[int] = None,
    sort: Optional[str] = None,       # "asc" | "desc" — sort days by total hours
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        year, mon = (int(x) for x in month.split("-"))
    except Exception:
        raise HTTPException(400, "month must be in YYYY-MM format")

    days_in_month = monthrange(year, mon)[1]
    start = date_cls(year, mon, 1)
    end = date_cls(year, mon, days_in_month)

    # Work hours logged anywhere in the app (Work Hours page, Milestone
    # Configuration, etc.) all land in the same WorkHours table — so this
    # automatically reflects entries made from any module.
    wq = db.query(WorkHours).filter(WorkHours.date >= start, WorkHours.date <= end)
    if user_id: wq = wq.filter(WorkHours.user_id == user_id)
    if project_id: wq = wq.filter(WorkHours.project_id == project_id)
    work_rows = wq.all()

    hours_by_day = {}
    for w in work_rows:
        d = w.date.isoformat() if hasattr(w.date, "isoformat") else str(w.date)
        hours_by_day[d] = hours_by_day.get(d, 0) + (w.hours_spent or 0)

    holidays = db.query(Holiday).filter(Holiday.date >= start, Holiday.date <= end).all()
    holiday_dates = {h.date.isoformat(): h.name for h in holidays}

    # Only count Approved leave/permission requests toward the day's hours —
    # this page's own UI lets an admin Approve/Reject each request (amber
    # Pending / emerald Approved / rose Rejected), so a still-Pending or
    # Rejected request must not silently make a day look like it met the
    # minimum, or make hours disappear once a request is rejected.
    leave_q = db.query(LeaveRequest).filter(LeaveRequest.date_from <= end, LeaveRequest.date_to >= start,
                                             LeaveRequest.status == "Approved")
    if user_id: leave_q = leave_q.filter(LeaveRequest.user_id == user_id)
    leaves = leave_q.all()
    leave_dates = {}
    for l in leaves:
        cur = max(l.date_from, start)
        last = min(l.date_to, end)
        while cur <= last:
            leave_dates[cur.isoformat()] = l.leave_type
            cur += timedelta(days=1)

    perm_q = db.query(Permission).filter(Permission.date >= start, Permission.date <= end,
                                          Permission.status == "Approved")
    if user_id: perm_q = perm_q.filter(Permission.user_id == user_id)
    perms = perm_q.all()
    permission_hours_by_day = {}
    for p in perms:
        d = p.date.isoformat()
        permission_hours_by_day[d] = permission_hours_by_day.get(d, 0) + (p.hours or 0)

    days = []
    for day_num in range(1, days_in_month + 1):
        d = date_cls(year, mon, day_num)
        key = d.isoformat()
        worked = round(hours_by_day.get(key, 0), 2)
        perm_hours = round(permission_hours_by_day.get(key, 0), 2)
        total = round(worked + perm_hours, 2)
        is_holiday = key in holiday_dates
        is_leave = key in leave_dates
        days.append({
            "date": key,
            "weekday": d.strftime("%A"),
            "worked_hours": worked,
            "permission_hours": perm_hours,
            "total_hours": total,
            "is_holiday": is_holiday,
            "holiday_name": holiday_dates.get(key),
            "is_leave": is_leave,
            "leave_type": leave_dates.get(key),
            "min_required_hours": MIN_DAILY_HOURS,
            "meets_minimum": (is_holiday or is_leave or total >= MIN_DAILY_HOURS),
            "shortfall": 0 if (is_holiday or is_leave or total >= MIN_DAILY_HOURS) else round(MIN_DAILY_HOURS - total, 2),
        })

    if sort in ("asc", "desc"):
        days.sort(key=lambda x: x["total_hours"], reverse=(sort == "desc"))

    return {
        "month": month,
        "min_daily_hours": MIN_DAILY_HOURS,
        "days": days,
        "summary": {
            "total_hours": round(sum(d["total_hours"] for d in days), 2),
            "holidays": len(holiday_dates),
            "leave_days": len(leave_dates),
            "shortfall_days": sum(1 for d in days if d["shortfall"] > 0),
        }
    }
