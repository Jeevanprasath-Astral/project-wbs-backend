from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import Optional
from datetime import datetime, date, timedelta
from pydantic import BaseModel
from app.db.database import get_db
from app.models.models import (WorkHours, User, Project, TaskAssignment, Team,
                                CustomMilestone, CustomTask, CustomSubtask, Activity)
from app.core.deps import get_current_user
from app.core.permissions import is_elevated

router = APIRouter(prefix="/work-hours", tags=["Work Hours"])

class WorkHoursCreate(BaseModel):
    project_id: Optional[int] = None  # None = hours logged against a General Task
    # Who this entry is logged for. Defaults to the caller (current_user).
    # Only Admin/Functional Consultant may set this to someone else — e.g.
    # backfilling/editing a team member's timesheet directly from the
    # Timesheet Calendar. Anyone else passing a different id is silently
    # restricted to themselves (see log_hours below).
    user_id: Optional[int] = None
    assignment_id: Optional[int] = None
    task_name: Optional[str] = None
    # Granular level linkage — log time directly against a Milestone, Task,
    # Subtask or Activity. When one of these is set, task_name is resolved
    # automatically from the entity's name if not supplied.
    level: Optional[str] = None              # Milestone | Task | Subtask | Activity
    custom_milestone_id: Optional[int] = None
    custom_task_id: Optional[int] = None
    custom_subtask_id: Optional[int] = None
    activity_id: Optional[int] = None
    date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None
    hours_spent: float = 0.0
    assigned_hours: float = 0.0
    buffer_hours: float = 0.0
    buffer_category: Optional[str] = None
    is_billable: Optional[bool] = True
    work_type: Optional[str] = None   # Billable | Non-Billable | No Work | Training | R&D
    notes: Optional[str] = None

class WorkHoursUpdate(BaseModel):
    end_time: Optional[str] = None
    hours_spent: Optional[float] = None
    buffer_hours: Optional[float] = None
    buffer_category: Optional[str] = None
    is_billable: Optional[bool] = None
    work_type: Optional[str] = None   # Billable | Non-Billable | No Work | Training | R&D
    notes: Optional[str] = None

def _actual_hours(w: WorkHours):
    """Actual Working Hours = Total Time Taken (hours_spent) - Buffer Time"""
    return round(max((w.hours_spent or 0) - (w.buffer_hours or 0), 0), 2)

def _resolve_level_name(w: WorkHours, db: Session):
    """Resolve a human-readable name for whichever level this entry targets."""
    if w.activity_id:
        a = db.query(Activity).filter_by(id=w.activity_id).first()
        return a.name if a else None
    if w.custom_subtask_id:
        s = db.query(CustomSubtask).filter_by(id=w.custom_subtask_id).first()
        return s.name if s else None
    if w.custom_task_id:
        t = db.query(CustomTask).filter_by(id=w.custom_task_id).first()
        return t.name if t else None
    if w.custom_milestone_id:
        m = db.query(CustomMilestone).filter_by(id=w.custom_milestone_id).first()
        return m.name if m else None
    return None


def _build(w: WorkHours, db: Session):
    user = db.query(User).filter_by(id=w.user_id).first()
    project = db.query(Project).filter_by(id=w.project_id).first()
    assignment = db.query(TaskAssignment).filter_by(id=w.assignment_id).first() if w.assignment_id else None
    return {
        "id": w.id,
        "user_id": w.user_id,
        "user_name": user.name if user else "—",
        "user_role": user.role if user else "—",
        "team_id": user.team_id if user else None,
        "team_name": user.team.name if user and user.team else None,
        "project_id": w.project_id,
        "project_name": project.name if project else "📋 General Task",
        "assignment_id": w.assignment_id,
        "milestone_num": assignment.milestone_num if assignment else None,
        "level": w.level,
        "custom_milestone_id": w.custom_milestone_id,
        "custom_task_id": w.custom_task_id,
        "custom_subtask_id": w.custom_subtask_id,
        "activity_id": w.activity_id,
        "task_name": w.task_name or _resolve_level_name(w, db),
        "date": str(w.date),
        "start_time": w.start_time.isoformat() if w.start_time else None,
        "end_time": w.end_time.isoformat() if w.end_time else None,
        "hours_spent": w.hours_spent,
        "assigned_hours": w.assigned_hours,
        "buffer_hours": w.buffer_hours or 0,
        "buffer_category": w.buffer_category,
        "is_billable": w.is_billable if w.is_billable is not None else True,
        "work_type": w.work_type or ("Billable" if (w.is_billable if w.is_billable is not None else True) else "Non-Billable"),
        "actual_working_hours": _actual_hours(w),
        "notes": w.notes,
        "created_at": w.created_at,
    }

@router.get("")
def list_work_hours(
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    team: Optional[str] = None,
    team_id: Optional[int] = None,
    milestone_num: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    view: str = "daily",  # daily | weekly | monthly
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    q = db.query(WorkHours)
    if not is_elevated(current_user) and current_user.role != "Functional Consultant":
        q = q.filter(WorkHours.user_id == current_user.id)
    if project_id: q = q.filter(WorkHours.project_id == project_id)
    if user_id:    q = q.filter(WorkHours.user_id == user_id)
    if date_from:  q = q.filter(WorkHours.date >= date_from)
    if date_to:    q = q.filter(WorkHours.date <= date_to)
    if team:
        user_ids = [u.id for u in db.query(User).filter(User.role == team).all()]
        q = q.filter(WorkHours.user_id.in_(user_ids))
    if team_id:
        user_ids = [u.id for u in db.query(User).filter(User.team_id == team_id).all()]
        q = q.filter(WorkHours.user_id.in_(user_ids))
    records = q.order_by(WorkHours.date.desc()).all()
    if milestone_num:
        assignment_ids = {a.id for a in db.query(TaskAssignment).filter(TaskAssignment.milestone_num == milestone_num).all()}
        records = [w for w in records if w.assignment_id in assignment_ids]
    return [_build(w, db) for w in records]

@router.get("/summary")
def work_hours_summary(
    project_id: Optional[int] = None,
    user_id: Optional[int] = None,
    team: Optional[str] = None,
    team_id: Optional[int] = None,
    milestone_num: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    period: str = "daily",  # daily | weekly | monthly — granularity of the time_series
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    now = datetime.utcnow()
    d_from = date_from or (now - timedelta(days=30)).strftime('%Y-%m-%d')
    d_to   = date_to   or now.strftime('%Y-%m-%d')

    q = db.query(WorkHours).filter(
        WorkHours.date >= d_from,
        WorkHours.date <= d_to
    )
    if project_id: q = q.filter(WorkHours.project_id == project_id)
    if user_id:    q = q.filter(WorkHours.user_id == user_id)
    if team:
        user_ids = [u.id for u in db.query(User).filter(User.role == team).all()]
        q = q.filter(WorkHours.user_id.in_(user_ids))
    if team_id:
        user_ids = [u.id for u in db.query(User).filter(User.team_id == team_id).all()]
        q = q.filter(WorkHours.user_id.in_(user_ids))

    records = q.all()

    if milestone_num:
        assignment_ids = {a.id for a in db.query(TaskAssignment).filter(TaskAssignment.milestone_num == milestone_num).all()}
        records = [w for w in records if w.assignment_id in assignment_ids]

    # By employee
    by_employee = {}
    for w in records:
        u = db.query(User).filter_by(id=w.user_id).first()
        name = u.name if u else str(w.user_id)
        role = u.role if u else "—"
        if name not in by_employee:
            by_employee[name] = {"name": name, "role": role, "total_hours": 0, "assigned_hours": 0, "buffer_hours": 0, "actual_working_hours": 0, "records": 0}
        by_employee[name]["total_hours"] += w.hours_spent or 0
        by_employee[name]["assigned_hours"] += w.assigned_hours or 0
        by_employee[name]["buffer_hours"] += w.buffer_hours or 0
        by_employee[name]["actual_working_hours"] += _actual_hours(w)
        by_employee[name]["records"] += 1

    # By project
    by_project = {}
    for w in records:
        p = db.query(Project).filter_by(id=w.project_id).first() if w.project_id else None
        name = p.name if p else "📋 General Task"
        if name not in by_project:
            by_project[name] = {"name": name, "total_hours": 0, "actual_working_hours": 0, "records": 0}
        by_project[name]["total_hours"] += w.hours_spent or 0
        by_project[name]["actual_working_hours"] += _actual_hours(w)
        by_project[name]["records"] += 1

    # By task
    by_task = {}
    for w in records:
        t = w.task_name or "Unknown"
        if t not in by_task:
            by_task[t] = {"name": t, "total_hours": 0, "actual_working_hours": 0}
        by_task[t]["total_hours"] += w.hours_spent or 0
        by_task[t]["actual_working_hours"] += _actual_hours(w)

    # By team (Team Hub teams)
    by_team = {}
    for w in records:
        u = db.query(User).filter_by(id=w.user_id).first()
        tname = u.team.name if u and u.team else "Unassigned"
        if tname not in by_team:
            by_team[tname] = {"name": tname, "total_hours": 0, "actual_working_hours": 0, "buffer_hours": 0, "records": 0}
        by_team[tname]["total_hours"] += w.hours_spent or 0
        by_team[tname]["actual_working_hours"] += _actual_hours(w)
        by_team[tname]["buffer_hours"] += w.buffer_hours or 0
        by_team[tname]["records"] += 1

    # Daily / Weekly / Monthly time analysis
    buckets = {}
    for w in records:
        d = w.date
        if period == "weekly":
            key = (d - timedelta(days=d.weekday())).strftime('%Y-%m-%d')  # Monday of that week
        elif period == "monthly":
            key = d.strftime('%Y-%m')
        else:
            key = d.strftime('%Y-%m-%d')
        if key not in buckets:
            buckets[key] = {"period": key, "total_hours": 0, "actual_working_hours": 0, "buffer_hours": 0, "records": 0}
        buckets[key]["total_hours"] += w.hours_spent or 0
        buckets[key]["actual_working_hours"] += _actual_hours(w)
        buckets[key]["buffer_hours"] += w.buffer_hours or 0
        buckets[key]["records"] += 1
    time_series = sorted(buckets.values(), key=lambda x: x["period"])
    for b in time_series:
        b["total_hours"] = round(b["total_hours"], 2)
        b["actual_working_hours"] = round(b["actual_working_hours"], 2)
        b["buffer_hours"] = round(b["buffer_hours"], 2)

    total_hours = sum(w.hours_spent or 0 for w in records)
    total_assigned = sum(w.assigned_hours or 0 for w in records)
    total_buffer = sum(w.buffer_hours or 0 for w in records)
    total_actual = sum(_actual_hours(w) for w in records)

    return {
        "total_hours":          round(total_hours, 2),
        "total_assigned":       round(total_assigned, 2),
        "total_buffer_hours":   round(total_buffer, 2),
        "total_actual_working_hours": round(total_actual, 2),
        "utilization":          round((total_hours / total_assigned * 100), 1) if total_assigned else 0,
        "total_records":        len(records),
        "by_employee":          list(by_employee.values()),
        "by_project":           list(by_project.values()),
        "by_team":              list(by_team.values()),
        "by_task":              sorted(by_task.values(), key=lambda x: x["total_hours"], reverse=True)[:10],
        "time_series":          time_series,
        "period":                period,
        "chart_data":           [{"name": e["name"], "hours": round(e["total_hours"],1), "assigned": round(e["assigned_hours"],1)} for e in by_employee.values()],
    }

@router.post("")
def log_hours(
    payload: WorkHoursCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    start = datetime.fromisoformat(payload.start_time) if payload.start_time else None
    end   = datetime.fromisoformat(payload.end_time)   if payload.end_time   else None
    hours = payload.hours_spent
    if start and end and not hours:
        hours = round((end - start).total_seconds() / 3600, 2)

    # If this work-hours entry is tied to a task assignment, stamp the
    # assignment's actual_start (first time logged) so Working Hours &
    # Buffer Time tracking can compute Total Duration end-to-end.
    if payload.assignment_id:
        assignment = db.query(TaskAssignment).filter_by(id=payload.assignment_id).first()
        if assignment:
            if payload.planned_start and not assignment.planned_start:
                assignment.planned_start = datetime.fromisoformat(payload.planned_start)
            if payload.planned_end and not assignment.planned_end:
                assignment.planned_end = datetime.fromisoformat(payload.planned_end)
            if not assignment.actual_start:
                assignment.actual_start = start or datetime.utcnow()

    task_name = payload.task_name
    now = datetime.utcnow()

    # Derive the authoritative project_id from whichever entity this entry is
    # actually linked to (granular Milestone Config level, or an assignment),
    # rather than trusting the caller-supplied project_id blindly. This is
    # the root fix for Working Hours interlink issues: a mismatched/omitted
    # project_id on the payload used to make hours silently disappear from
    # (or pollute) the wrong project's Working Hours / Actual Hours rollups.
    derived_project_id = None

    # Roll up: stamp actual_start + bump "Not Started" -> "In Progress" on
    # whichever Milestone/Task/Subtask/Activity this entry is logged against,
    # and auto-resolve task_name from the entity if not explicitly given.
    if payload.activity_id:
        a = db.query(Activity).filter_by(id=payload.activity_id).first()
        if a:
            task_name = task_name or a.name
            derived_project_id = a.project_id
            if not a.actual_start: a.actual_start = now
            if a.status == "Not Started": a.status = "In Progress"
    if payload.custom_subtask_id:
        s = db.query(CustomSubtask).filter_by(id=payload.custom_subtask_id).first()
        if s:
            task_name = task_name or s.name
            derived_project_id = derived_project_id or s.project_id
            if not s.actual_start: s.actual_start = now
            if s.status == "Not Started": s.status = "In Progress"
    if payload.custom_task_id:
        t = db.query(CustomTask).filter_by(id=payload.custom_task_id).first()
        if t:
            task_name = task_name or t.name
            derived_project_id = derived_project_id or t.project_id
            if not t.actual_start: t.actual_start = now
            if t.status == "Not Started": t.status = "In Progress"
    if payload.custom_milestone_id:
        m = db.query(CustomMilestone).filter_by(id=payload.custom_milestone_id).first()
        if m:
            task_name = task_name or m.name
            derived_project_id = derived_project_id or m.project_id
    if derived_project_id is None and payload.assignment_id:
        # General Task Time Management: hours logged against a Task
        # Assignment (no Milestone Config link) — trust the assignment's
        # own project_id (None for a fully-General, non-project task).
        asg = db.query(TaskAssignment).filter_by(id=payload.assignment_id).first()
        if asg:
            derived_project_id = asg.project_id

    final_project_id = derived_project_id if derived_project_id is not None else payload.project_id

    # Resolve who this entry is attributed to. Only Admin/Functional
    # Consultant may log on behalf of someone else (e.g. backfilling a missed
    # day from the Timesheet Calendar); everyone else is pinned to themself
    # regardless of what's in the payload.
    target_user_id = current_user.id
    if payload.user_id and payload.user_id != current_user.id:
        if is_elevated(current_user) or current_user.role == "Functional Consultant":
            target_user_id = payload.user_id
        else:
            raise HTTPException(403, "You can only log hours for yourself")

    w = WorkHours(
        user_id=target_user_id,
        project_id=final_project_id,
        assignment_id=payload.assignment_id,
        task_name=task_name or "Untitled",
        level=payload.level,
        custom_milestone_id=payload.custom_milestone_id,
        custom_task_id=payload.custom_task_id,
        custom_subtask_id=payload.custom_subtask_id,
        activity_id=payload.activity_id,
        date=date.fromisoformat(payload.date),
        start_time=start, end_time=end,
        hours_spent=hours,
        assigned_hours=payload.assigned_hours,
        buffer_hours=payload.buffer_hours,
        buffer_category=payload.buffer_category,
        is_billable=(payload.work_type == 'Billable') if payload.work_type else (payload.is_billable if payload.is_billable is not None else True),
        work_type=payload.work_type or ('Billable' if (payload.is_billable if payload.is_billable is not None else True) else 'Non-Billable'),
        notes=payload.notes,
    )
    db.add(w); db.commit(); db.refresh(w)
    return _build(w, db)

@router.patch("/{wh_id}")
def update_hours(
    wh_id: int,
    payload: WorkHoursUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    w = db.query(WorkHours).filter_by(id=wh_id).first()
    if not w: raise HTTPException(404, "Record not found")
    if payload.end_time:
        w.end_time = datetime.fromisoformat(payload.end_time)
        if w.start_time:
            w.hours_spent = round((w.end_time - w.start_time).total_seconds() / 3600, 2)
        # mark the linked assignment as completed once final hours are logged
        if w.assignment_id:
            assignment = db.query(TaskAssignment).filter_by(id=w.assignment_id).first()
            if assignment and not assignment.completed_at:
                assignment.completed_at = w.end_time
    if payload.hours_spent is not None:    w.hours_spent = payload.hours_spent
    if payload.buffer_hours is not None:   w.buffer_hours = payload.buffer_hours
    if payload.buffer_category is not None: w.buffer_category = payload.buffer_category
    if payload.work_type is not None:
        w.work_type = payload.work_type
        w.is_billable = (payload.work_type == 'Billable')
    elif payload.is_billable is not None:
        w.is_billable = payload.is_billable
        w.work_type = 'Billable' if payload.is_billable else 'Non-Billable'
    if payload.notes: w.notes = payload.notes
    db.commit(); db.refresh(w)
    return _build(w, db)

@router.delete("/{wh_id}")
def delete_hours(wh_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    w = db.query(WorkHours).filter_by(id=wh_id).first()
    if not w: raise HTTPException(404, "Record not found")
    db.delete(w); db.commit()
    return {"status": "deleted"}
