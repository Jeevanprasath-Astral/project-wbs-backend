from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
from app.db.database import get_db
from app.models.models import TaskAssignment, User, ProjectMember, Notification, Project, CustomTask, WorkHours
from app.core.deps import get_current_user
from app.core.permissions import is_elevated
from app.services.audit_service import log_action
from app.services.notification_service import create_notification
from app.services.email_service import email_task_assigned

router = APIRouter(prefix="/projects/{project_id}/assignments", tags=["Assignments"])

class AssignmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    assigned_to: int
    team: Optional[str] = None
    # Both null = "General" task, not tied to the Milestone Configuration
    # hierarchy. milestone_num set + custom_task_id null = milestone-level
    # (or "General" task under that milestone). Both set = a specific Task.
    milestone_num: Optional[int] = None
    custom_task_id: Optional[int] = None
    priority: str = "Medium"
    status: Optional[str] = "Not Started"
    due_date: Optional[datetime] = None
    # Planned date & time, entered at assignment time (testing feedback:
    # "the planned date and time can be entered during assignment"). The
    # assignee later logs Actual time against this same assignment through
    # the Working Hours module (assignment_id link), which is what feeds
    # the Working Hours calculation.
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    remarks: Optional[str] = None
    category: Optional[str] = None

class AssignmentUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[datetime] = None
    planned_start: Optional[datetime] = None
    planned_end: Optional[datetime] = None
    remarks: Optional[str] = None
    description: Optional[str] = None
    milestone_num: Optional[int] = None
    custom_task_id: Optional[int] = None
    # Actual start/end — manual fallback for General tasks (no
    # milestone_num/custom_task_id) when no Working Hours entries have been
    # logged against this assignment yet. Once a Working Hours entry exists
    # (assignment_id == this assignment), that becomes the source of truth
    # instead. See _assignment_actual_hours below.
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    category: Optional[str] = None

def _assignment_actual_hours(a: TaskAssignment, db: Session) -> float:
    """Actual Hours logged against this assignment so far.

    The Working Hours module ("Log hours") is the canonical place this gets
    entered for BOTH Milestone-tied and General tasks — General tasks log
    against this assignment via the "Linked task assignment (General Task
    Time Management)" picker (WorkHours.assignment_id == a.id), exactly
    mirroring how Milestone-tied tasks log against custom_milestone_id/
    custom_task_id/etc. So WorkHours rows are always the first source of
    truth here, the same rows the Working Hours page's own "By person" /
    Milestone vs General split is built from — this keeps the two screens
    from disagreeing about the same assignment's hours.

    The manual actual_start/actual_end fields on the Assignments card are a
    fallback ONLY for General tasks that have no Working Hours entries
    logged yet at all (e.g. someone records the start/end time directly on
    the card before ever opening Working Hours). The moment a real
    WorkHours entry exists for this assignment, it takes over — so logging
    (or editing/deleting) hours in Working Hours is immediately reflected
    here too, instead of being silently shadowed by stale start/end values.
    """
    rows = db.query(WorkHours.hours_spent, WorkHours.buffer_hours).filter_by(assignment_id=a.id).all()
    if rows:
        return round(sum(max((h or 0.0) - (b or 0.0), 0.0) for h, b in rows), 2)
    is_general = not a.milestone_num and not a.custom_task_id
    if is_general and a.actual_start and a.actual_end:
        return round(max((a.actual_end - a.actual_start).total_seconds() / 3600.0, 0.0), 2)
    return 0.0

def _build_assignment(a: TaskAssignment, db: Session,
                      user_map=None, task_map=None, wh_map=None):
    """
    Build the assignment dict.
    Pass pre-fetched user_map/task_map/wh_map for bulk list endpoints
    to avoid N+1 queries.  Single-record endpoints can leave them as None
    and the function falls back to individual DB lookups.
    """
    if user_map is not None:
        assignee = user_map.get(a.assigned_to)
        assigner = user_map.get(a.assigned_by)
    else:
        assignee = db.query(User).filter_by(id=a.assigned_to).first()
        assigner = db.query(User).filter_by(id=a.assigned_by).first()

    if task_map is not None:
        task = task_map.get(a.custom_task_id) if a.custom_task_id else None
    else:
        task = db.query(CustomTask).filter_by(id=a.custom_task_id).first() if a.custom_task_id else None

    if wh_map is not None:
        wh_rows = wh_map.get(a.id, [])
    else:
        wh_rows = db.query(WorkHours).filter_by(assignment_id=a.id).all()

    hours_via_working_hours = any(w.level != "Assignment" for w in wh_rows)
    # Re-use the existing helper but pass pre-loaded rows to avoid another query
    actual_hours = round(sum(max((h or 0.0) - (b or 0.0), 0.0) for h, b in
                             [(w.hours_spent, w.buffer_hours) for w in wh_rows]), 2) if wh_rows else (
        round(max((a.actual_end - a.actual_start).total_seconds() / 3600.0, 0.0), 2)
        if (not a.milestone_num and not a.custom_task_id and a.actual_start and a.actual_end) else 0.0
    )

    return {
        "id":            a.id,
        "title":         a.title,
        "description":   a.description,
        "assigned_to":   a.assigned_to,
        "assigned_to_name": assignee.name if assignee else "—",
        "assigned_to_role": assignee.role if assignee else "—",
        "assigned_by":   a.assigned_by,
        "assigned_by_name": assigner.name if assigner else "—",
        "team":          a.team,
        "milestone_num": a.milestone_num,
        "custom_task_id": a.custom_task_id,
        "task_name":     task.name if task else ("General" if a.custom_task_id is None and a.milestone_num is None else None),
        "priority":      a.priority,
        "status":        a.status,
        "due_date":      a.due_date,
        "planned_start": a.planned_start,
        "planned_end":   a.planned_end,
        "actual_start":  a.actual_start,
        "actual_end":    a.actual_end,
        "actual_hours":  actual_hours,
        "hours_via_working_hours": hours_via_working_hours,
        "completed_at":  a.completed_at,
        "created_at":    a.created_at,
        "remarks":       a.remarks,
        "project_id":    a.project_id,
        "category":      a.category,
    }


def _bulk_prefetch(assignments, db):
    """
    Pre-fetch users, tasks, and work-hours for a list of assignments
    in 3 queries instead of 3*N queries.
    """
    user_ids = {a.assigned_to for a in assignments} | {a.assigned_by for a in assignments if a.assigned_by}
    task_ids = {a.custom_task_id for a in assignments if a.custom_task_id}
    a_ids    = [a.id for a in assignments]

    user_map = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    task_map = {t.id: t for t in db.query(CustomTask).filter(CustomTask.id.in_(task_ids)).all()} if task_ids else {}

    from collections import defaultdict
    wh_map: dict = defaultdict(list)
    for w in db.query(WorkHours).filter(WorkHours.assignment_id.in_(a_ids)).all():
        wh_map[w.assignment_id].append(w)

    return user_map, task_map, wh_map

@router.get("")
def list_assignments(
    project_id: int,
    team: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    q = db.query(TaskAssignment).filter_by(project_id=project_id)
    # Admin/FC Lead/TC Lead see everything; everyone else only their own.
    if not is_elevated(current_user):
        q = q.filter_by(assigned_to=current_user.id)
    if team:
        q = q.filter_by(team=team)
    if status:
        q = q.filter_by(status=status)
    assignments = q.order_by(TaskAssignment.created_at.desc()).all()
    if not assignments:
        return []
    user_map, task_map, wh_map = _bulk_prefetch(assignments, db)
    return [_build_assignment(a, db, user_map, task_map, wh_map) for a in assignments]

@router.get("/my")
def my_assignments(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    assignments = db.query(TaskAssignment).filter_by(
        project_id=project_id, assigned_to=current_user.id
    ).order_by(TaskAssignment.created_at.desc()).all()
    if not assignments:
        return []
    user_map, task_map, wh_map = _bulk_prefetch(assignments, db)
    return [_build_assignment(a, db, user_map, task_map, wh_map) for a in assignments]

@router.post("")
def create_assignment(
    project_id: int,
    payload: AssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    _assign_roles = {"Associate", "Functional Consultant", "Technical Team"}
    if not is_elevated(current_user) and current_user.role not in _assign_roles:
        raise HTTPException(403, "Only Admin, Project Manager, FC Lead, TC Lead, or Associate can assign tasks")

    assignee = db.query(User).filter_by(id=payload.assigned_to).first()
    if not assignee:
        raise HTTPException(404, "Assigned user not found")

    a = TaskAssignment(
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        assigned_to=payload.assigned_to,
        assigned_by=current_user.id,
        team=payload.team or assignee.role,
        milestone_num=payload.milestone_num,
        custom_task_id=payload.custom_task_id,
        priority=payload.priority,
        status=payload.status or "Not Started",
        due_date=payload.due_date,
        planned_start=payload.planned_start,
        planned_end=payload.planned_end,
        remarks=payload.remarks,
        category=payload.category,
    )
    db.add(a)
    db.flush()

    # Notify assignee — and actually send the email (template already existed
    # but was never dispatched because send_now/subject/body were omitted).
    project = db.query(Project).filter_by(id=project_id).first()
    project_name = project.name if project else "—"
    email_subject = f"[{project_name}] Task assigned to you — {payload.title}"
    email_body = f"""
    <p>Hi {assignee.name},</p>
    <p>A new task has been assigned to you in <strong>{project_name}</strong>:</p>
    <p><strong>Task:</strong> {payload.title}</p>
    {f'<p><strong>Due Date:</strong> {payload.due_date}</p>' if payload.due_date else ''}
    <p>Please log in to review and start working on this task.</p>
    <p>Regards,<br>Project WBS System</p>
    """
    create_notification(
        db, project_id, "assignment",
        f"Task '{payload.title}' assigned to {assignee.name} by {current_user.name}",
        user_id=payload.assigned_to,
        email_to=assignee.email,
        send_now=True,
        email_subject=email_subject,
        email_body=email_body,
    )
    log_action(db, actor=current_user.name, action="assign_task",
               description=f"Assigned task '{payload.title}' to {assignee.name}",
               project_id=project_id, entity_type="assignment",
               entity_id=a.id, user_id=current_user.id)
    db.commit()
    db.refresh(a)
    return _build_assignment(a, db)

@router.patch("/{assignment_id}")
def update_assignment(
    project_id: int,
    assignment_id: int,
    payload: AssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    a = db.query(TaskAssignment).filter_by(id=assignment_id, project_id=project_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")

    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(a, k, v)

    if payload.status == "Completed" and not a.completed_at:
        a.completed_at = datetime.utcnow()
        create_notification(db, project_id, "completed",
                            f"Task '{a.title}' marked completed by {current_user.name}")
        # Email the person who created/assigned this task
        _assigner = db.query(User).filter_by(id=a.assigned_by).first()
        if _assigner and _assigner.email:
            _project = db.query(Project).filter_by(id=project_id).first()
            _project_name = _project.name if _project else "—"
            _assignee = db.query(User).filter_by(id=a.assigned_to).first()
            _completed_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
            _email_subject = f"[{_project_name}] Task Completed — {a.title}"
            _email_body = f"""
            <p>Hi {_assigner.name},</p>
            <p>The following task has been marked as <strong style="color:green">Completed</strong>:</p>
            <p><strong>Project:</strong> {_project_name}</p>
            <p><strong>Task:</strong> {a.title}</p>
            <p><strong>Assigned To:</strong> {_assignee.name if _assignee else '—'}</p>
            <p><strong>Completed By:</strong> {current_user.name}</p>
            <p><strong>Completed At:</strong> {_completed_str} UTC</p>
            <p>Regards,<br>Project WBS System</p>
            """
            create_notification(
                db, project_id, "completed",
                f"Task '{a.title}' completed — notified {_assigner.name}",
                user_id=_assigner.id,
                email_to=_assigner.email,
                send_now=True,
                email_subject=_email_subject,
                email_body=_email_body,
            )

    # Bug fix: manually entering Actual start/end on a General task's card
    # (the only way to log time for a General task that has no Milestone/
    # Task to log granular Working Hours against) only ever wrote to this
    # TaskAssignment row. _assignment_actual_hours() preferred a real
    # WorkHours row when one existed, but as long as none did, this value
    # was a dead end -- invisible on the Working Hours page, the Global
    # Hub's Work Hours Tracking page, and the "By person" breakdowns there,
    # even though it's real logged time for the same person on the same
    # project. Mirror it into an actual WorkHours row (tagged level=
    # "Assignment" so repeat edits update that same row instead of
    # duplicating it) so it shows up identically everywhere, exactly like
    # an entry logged via the Working Hours page's own "Log hours" form.
    is_general = not a.milestone_num and not a.custom_task_id
    if is_general and a.actual_start and a.actual_end and \
            (payload.actual_start is not None or payload.actual_end is not None):
        existing = db.query(WorkHours).filter_by(assignment_id=a.id).all()
        synced = next((w for w in existing if w.level == "Assignment"), None)
        # A genuinely-logged WorkHours entry (anything NOT our own auto-synced
        # row) is already the source of truth per _assignment_actual_hours --
        # that function sums every WorkHours row for this assignment, so
        # adding/keeping our own row alongside a real one double-counts the
        # hours (this is exactly the "5h instead of 4h" bug). Only sync the
        # manual editor's value when no real entry exists yet; otherwise drop
        # any stale synced row so the real entry alone decides the total.
        real_exists = any(w.level != "Assignment" for w in existing)
        if real_exists:
            if synced:
                db.delete(synced)
        else:
            hours = round(max((a.actual_end - a.actual_start).total_seconds() / 3600.0, 0.0), 2)
            if synced:
                synced.date = a.actual_end.date()
                synced.start_time = a.actual_start
                synced.end_time = a.actual_end
                synced.hours_spent = hours
                synced.assigned_hours = hours
            else:
                db.add(WorkHours(
                    user_id=a.assigned_to, project_id=a.project_id, assignment_id=a.id,
                    task_name=a.title, level="Assignment", date=a.actual_end.date(),
                    start_time=a.actual_start, end_time=a.actual_end,
                    hours_spent=hours, assigned_hours=hours, buffer_hours=0.0,
                ))

    log_action(db, actor=current_user.name, action="update_assignment",
               description=f"Updated assignment '{a.title}'",
               project_id=project_id, entity_type="assignment",
               entity_id=a.id, user_id=current_user.id)
    db.commit()
    db.refresh(a)
    return _build_assignment(a, db)

@router.delete("/{assignment_id}")
def delete_assignment(
    project_id: int,
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not is_elevated(current_user):
        raise HTTPException(403, "Only Admin, FC Lead or TC Lead can delete assignments")
    a = db.query(TaskAssignment).filter_by(id=assignment_id, project_id=project_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")
    # Any WorkHours rows logged against this assignment (via the Working
    # Hours page's "Log hours" picker, or auto-synced from the card's manual
    # actual start/end editor) hold a FK to this row with no cascade -- left
    # in place, deleting the assignment would fail with a FK violation.
    db.query(WorkHours).filter_by(assignment_id=a.id).delete()
    db.delete(a)
    log_action(db, actor=current_user.name, action="delete_assignment",
               description=f"Deleted assignment '{a.title}'",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    return {"status": "deleted"}

@router.get("/summary")
def assignment_summary(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    all_a = db.query(TaskAssignment).filter_by(project_id=project_id).all()
    return {
        "total":       len(all_a),
        "not_started": sum(1 for a in all_a if a.status == "Not Started"),
        "in_progress": sum(1 for a in all_a if a.status == "In Progress"),
        "completed":   sum(1 for a in all_a if a.status == "Completed"),
        # "On Hold" is a real, selectable status (see AssignmentUpdate /
        # the status dropdown) but was never counted here -- Total included
        # On Hold assignments while every other bucket silently excluded
        # them, so the cards never summed to Total once a task was put on
        # hold. Tracking it explicitly keeps the breakdown reconciling.
        "on_hold":     sum(1 for a in all_a if a.status == "On Hold"),
        "overdue":     sum(1 for a in all_a if a.due_date and a.due_date < datetime.utcnow() and a.status != "Completed"),
        "by_team": {
            "Functional Consultant": sum(1 for a in all_a if a.team == "Functional Consultant"),
            "Technical Team":        sum(1 for a in all_a if a.team == "Technical Team"),
        }
    }
