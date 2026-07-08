from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import Optional, List
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.db.database import get_db
from app.models.models import (TaskAssignment, ProjectMilestone, Project,
                                User, ProjectMember, SubtaskStatus, Task,
                                Milestone, CustomTask, CustomSubtask, Activity,
                                CustomMilestone)
from app.core.deps import get_current_user
from app.services.audit_service import log_action
from app.services.notification_service import create_notification

router = APIRouter(prefix="/global", tags=["Global Modules"])

# "General Task" label shown wherever a TaskAssignment has no project_id —
# req: tasks can be assigned to a person without linking to a specific project.
GENERAL_TASK_LABEL = "📋 General Task"


def _build_global_assignment(a: TaskAssignment, db: Session, project_map=None, user_map=None):
    """Build the API-shaped dict for one assignment row.

    Perf: list endpoints pass in pre-fetched `project_map`/`user_map` (id -> obj)
    so we avoid 2-3 extra SELECTs per row (N+1). Single-row call sites (create/
    update) omit the maps and fall back to a direct lookup since there's only
    one row to build.
    """
    if project_map is not None:
        project = project_map.get(a.project_id) if a.project_id else None
    else:
        project = db.query(Project).filter_by(id=a.project_id).first() if a.project_id else None

    if user_map is not None:
        assignee = user_map.get(a.assigned_to)
        assigner = user_map.get(a.assigned_by)
    else:
        assignee = db.query(User).filter_by(id=a.assigned_to).first()
        assigner = db.query(User).filter_by(id=a.assigned_by).first()

    now = datetime.utcnow()
    is_overdue = (a.due_date and a.due_date < now and a.status != "Completed")
    days_left = (a.due_date - now).days if a.due_date else None
    task = db.query(CustomTask).filter_by(id=a.custom_task_id).first() if a.custom_task_id else None
    return {
        "id": a.id,
        "project_id": a.project_id,
        "project_name": project.name if project else GENERAL_TASK_LABEL,
        "project_client": project.client if project else "—",
        "title": a.title,
        "description": a.description,
        "assigned_to": a.assigned_to,
        "assigned_to_name": assignee.name if assignee else "—",
        "assigned_to_role": assignee.role if assignee else "—",
        "assigned_by_name": assigner.name if assigner else "—",
        "team": a.team,
        "milestone_num": a.milestone_num,
        "custom_task_id": a.custom_task_id,
        "task_name": task.name if task else None,
        "priority": a.priority,
        "status": a.status,
        "due_date": a.due_date,
        "days_left": days_left,
        "is_overdue": is_overdue,
        "completed_at": a.completed_at,
        "created_at": a.created_at,
        "remarks": a.remarks,
    }


# ── Global Task Assignments ───────────────────────────────────────────────────
@router.get("/assignments")
def global_assignments(
    project_id: Optional[int] = None,
    team: Optional[str] = None,
    assigned_to: Optional[int] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    # Date-range filter (created_at), mirroring /global/workload so the
    # Global Dashboard's date pickers can narrow this endpoint too —
    # optional/no default so existing callers (e.g. Task Assignments page)
    # that omit these params still see the full unfiltered list.
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    q = db.query(TaskAssignment)

    # Non-admins only see their own
    if current_user.role not in ("Admin", "Functional Consultant"):
        q = q.filter_by(assigned_to=current_user.id)

    if project_id:
        q = q.filter(TaskAssignment.project_id == project_id)
    if team:
        q = q.filter(TaskAssignment.team == team)
    if assigned_to:
        q = q.filter(TaskAssignment.assigned_to == assigned_to)
    if status:
        q = q.filter(TaskAssignment.status == status)
    if priority:
        q = q.filter(TaskAssignment.priority == priority)
    if date_from:
        q = q.filter(TaskAssignment.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        q = q.filter(TaskAssignment.created_at <= datetime.fromisoformat(date_to) + timedelta(days=1))

    assignments = q.order_by(TaskAssignment.created_at.desc()).all()

    # Perf: batch-fetch all referenced projects/users in 2 queries instead of
    # up to 3 queries per row (N+1) — matters once there are dozens+ of tasks.
    project_ids = {a.project_id for a in assignments if a.project_id}
    user_ids = {a.assigned_to for a in assignments} | {a.assigned_by for a in assignments if a.assigned_by}
    project_map = {p.id: p for p in db.query(Project).filter(Project.id.in_(project_ids)).all()} if project_ids else {}
    user_map = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    return [_build_global_assignment(a, db, project_map, user_map) for a in assignments]


@router.get("/assignments/summary")
def global_assignment_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    now = datetime.utcnow()
    all_a = db.query(TaskAssignment).all()
    return {
        "total":       len(all_a),
        "not_started": sum(1 for a in all_a if a.status == "Not Started"),
        "in_progress": sum(1 for a in all_a if a.status == "In Progress"),
        "completed":   sum(1 for a in all_a if a.status == "Completed"),
        # See matching fix in assignments.py assignment_summary() — "On Hold"
        # is a real status option that was never counted in any bucket here.
        "on_hold":     sum(1 for a in all_a if a.status == "On Hold"),
        "overdue":     sum(1 for a in all_a if a.due_date and a.due_date < now and a.status != "Completed"),
        "by_priority": {
            "High":   sum(1 for a in all_a if a.priority == "High"),
            "Medium": sum(1 for a in all_a if a.priority == "Medium"),
            "Low":    sum(1 for a in all_a if a.priority == "Low"),
        },
        "by_team": {
            "Functional Consultant": sum(1 for a in all_a if a.team == "Functional Consultant"),
            "Technical Team":        sum(1 for a in all_a if a.team == "Technical Team"),
        }
    }


class GlobalAssignmentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    assigned_to: int
    # None / omitted => "General Task" — assigned to a person without being
    # linked to any specific project.
    project_id: Optional[int] = None
    team: Optional[str] = None
    milestone_num: Optional[int] = None
    # Mirrors the per-project Assign Task modal's Task dropdown: a specific
    # CustomTask under the selected milestone. None = milestone-level (or
    # General if milestone_num is also None).
    custom_task_id: Optional[int] = None
    priority: str = "Medium"
    status: Optional[str] = "Not Started"
    due_date: Optional[datetime] = None
    remarks: Optional[str] = None


@router.post("/assignments")
def create_global_assignment(
    payload: GlobalAssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a task assignment from the Global Task Assignments hub.
    Supports General Tasks (no project_id) in addition to project-linked tasks."""
    if current_user.role not in ("Admin", "Functional Consultant"):
        raise HTTPException(403, "Only Admin or Project Manager can assign tasks")

    assignee = db.query(User).filter_by(id=payload.assigned_to).first()
    if not assignee:
        raise HTTPException(404, "Assigned user not found")

    project = None
    if payload.project_id:
        project = db.query(Project).filter_by(id=payload.project_id).first()
        if not project:
            raise HTTPException(404, "Project not found")

    a = TaskAssignment(
        project_id=payload.project_id,
        title=payload.title,
        description=payload.description,
        assigned_to=payload.assigned_to,
        assigned_by=current_user.id,
        team=payload.team or assignee.role,
        milestone_num=payload.milestone_num if payload.project_id else None,
        custom_task_id=payload.custom_task_id if payload.project_id else None,
        priority=payload.priority,
        status=payload.status or "Not Started",
        due_date=payload.due_date,
        remarks=payload.remarks,
    )
    db.add(a)
    db.flush()

    project_name = project.name if project else "General Task"
    email_subject = f"[{project_name}] Task assigned to you — {payload.title}"
    email_body = f"""
    <p>Hi {assignee.name},</p>
    <p>A new task has been assigned to you{f' in <strong>{project_name}</strong>' if project else ''}:</p>
    <p><strong>Task:</strong> {payload.title}</p>
    {f'<p><strong>Due Date:</strong> {payload.due_date}</p>' if payload.due_date else ''}
    <p>Please log in to review and start working on this task.</p>
    <p>Regards,<br>Project WBS System</p>
    """
    create_notification(
        db, payload.project_id, "assignment",
        f"Task '{payload.title}' assigned to {assignee.name} by {current_user.name}",
        user_id=payload.assigned_to,
        email_to=assignee.email,
        send_now=True,
        email_subject=email_subject,
        email_body=email_body,
    )
    log_action(db, actor=current_user.name, action="assign_task",
               description=f"Assigned task '{payload.title}' to {assignee.name}"
                           + ("" if payload.project_id else " (General Task)"),
               project_id=payload.project_id, entity_type="assignment",
               entity_id=a.id, user_id=current_user.id)
    db.commit()
    db.refresh(a)
    return _build_global_assignment(a, db)


class GlobalAssignmentUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to: Optional[int] = None
    team: Optional[str] = None
    milestone_num: Optional[int] = None
    custom_task_id: Optional[int] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    due_date: Optional[datetime] = None
    remarks: Optional[str] = None


@router.patch("/assignments/{assignment_id}")
def update_global_assignment(
    assignment_id: int,
    payload: GlobalAssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Project-agnostic update — works for both project-linked tasks and
    General Tasks, since it looks the assignment up by id alone."""
    a = db.query(TaskAssignment).filter_by(id=assignment_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")

    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(a, k, v)

    if payload.status == "Completed" and not a.completed_at:
        a.completed_at = datetime.utcnow()
        create_notification(db, a.project_id, "completed",
                             f"Task '{a.title}' marked completed by {current_user.name}")

    log_action(db, actor=current_user.name, action="update_assignment",
               description=f"Updated assignment '{a.title}'",
               project_id=a.project_id, entity_type="assignment",
               entity_id=a.id, user_id=current_user.id)
    db.commit()
    db.refresh(a)
    return _build_global_assignment(a, db)


@router.delete("/assignments/{assignment_id}")
def delete_global_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role != "Admin":
        raise HTTPException(403, "Only Admin can delete assignments")
    a = db.query(TaskAssignment).filter_by(id=assignment_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")
    db.delete(a)
    log_action(db, actor=current_user.name, action="delete_assignment",
               description=f"Deleted assignment '{a.title}'",
               project_id=a.project_id, user_id=current_user.id)
    db.commit()
    return {"status": "deleted"}


# ── Global Upcoming Deadlines ─────────────────────────────────────────────────
@router.get("/deadlines")
def global_deadlines(
    project_id: Optional[int] = None,
    team: Optional[str] = None,
    assigned_to: Optional[int] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 7,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)
    result = []

    # ── Run all 5 row queries up front, then batch-fetch every referenced
    # Project / User / CustomMilestone / CustomTask / CustomSubtask in one
    # query each — replaces what used to be 2-4 SELECTs per row (N+1) ───────
    ms_query = db.query(ProjectMilestone).filter(
        ProjectMilestone.planned_end != None,
        ProjectMilestone.planned_end >= now,
        ProjectMilestone.planned_end <= cutoff,
        ProjectMilestone.status != "Completed"
    )
    if project_id:
        ms_query = ms_query.filter(ProjectMilestone.project_id == project_id)
    ms_rows = ms_query.all()

    ta_query = db.query(TaskAssignment).filter(
        TaskAssignment.due_date != None,
        TaskAssignment.due_date >= now,
        TaskAssignment.due_date <= cutoff,
        TaskAssignment.status != "Completed"
    )
    if project_id:
        ta_query = ta_query.filter(TaskAssignment.project_id == project_id)
    if team:
        ta_query = ta_query.filter(TaskAssignment.team == team)
    if assigned_to:
        ta_query = ta_query.filter(TaskAssignment.assigned_to == assigned_to)
    if priority:
        ta_query = ta_query.filter(TaskAssignment.priority == priority)
    if status:
        ta_query = ta_query.filter(TaskAssignment.status == status)
    ta_rows = ta_query.all()

    def _entity_rows(model, status_filter_field):
        q = db.query(model).filter(
            model.planned_end != None,
            model.planned_end >= now,
            model.planned_end <= cutoff,
        )
        if status_filter_field is not None:
            q = q.filter(getattr(model, "status") != "Completed")
        if project_id:
            q = q.filter(model.project_id == project_id)
        return q.all()

    task_rows = _entity_rows(CustomTask, True)
    subtask_rows = _entity_rows(CustomSubtask, True)
    activity_rows = _entity_rows(Activity, True)
    # 6th row-set: ad-hoc per-project CustomMilestones (Milestone Configuration)
    # — previously omitted, so a milestone-level deadline set there never
    # showed up here even though the model has planned_end/status.
    cmile_rows = _entity_rows(CustomMilestone, True)

    # Batch-fetch Projects referenced by any of the 6 row sets
    project_ids = {r.project_id for r in (*ms_rows, *ta_rows, *task_rows, *subtask_rows, *activity_rows, *cmile_rows) if r.project_id}
    project_map = {p.id: p for p in db.query(Project).filter(Project.id.in_(project_ids)).all()} if project_ids else {}

    # Batch-fetch Users referenced by task assignments
    user_ids = {a.assigned_to for a in ta_rows if a.assigned_to}
    user_map = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    # Batch-fetch CustomMilestones (referenced directly by task_rows and
    # cmile_rows themselves, and indirectly by subtask/activity rows via
    # their CustomTask)
    cm_ids = {t.milestone_id for t in task_rows if t.milestone_id} | {cm.id for cm in cmile_rows}
    cm_map = {cm.id: cm for cm in db.query(CustomMilestone).filter(CustomMilestone.id.in_(cm_ids)).all()} if cm_ids else {}
    # cmile_rows are already-loaded CustomMilestone instances — fold them in
    # directly so they don't need a second fetch.
    cm_map.update({cm.id: cm for cm in cmile_rows})

    # Batch-fetch CustomTasks referenced by subtask_rows, plus the ones needed
    # to resolve activity_rows' milestone (via CustomSubtask.task_id)
    ct_ids = {s.task_id for s in subtask_rows if s.task_id}
    # CustomSubtasks referenced by activity_rows
    cs_ids = {act.subtask_id for act in activity_rows if act.subtask_id}
    cs_map = {cs.id: cs for cs in db.query(CustomSubtask).filter(CustomSubtask.id.in_(cs_ids)).all()} if cs_ids else {}
    ct_ids |= {cs.task_id for cs in cs_map.values() if cs.task_id}
    ct_map = {ct.id: ct for ct in db.query(CustomTask).filter(CustomTask.id.in_(ct_ids)).all()} if ct_ids else {}
    # Extend cm_map with milestones referenced by those CustomTasks
    extra_cm_ids = {ct.milestone_id for ct in ct_map.values() if ct.milestone_id} - cm_map.keys()
    if extra_cm_ids:
        cm_map.update({cm.id: cm for cm in db.query(CustomMilestone).filter(CustomMilestone.id.in_(extra_cm_ids)).all()})

    for pm in ms_rows:
        project = project_map.get(pm.project_id)
        days_remaining = (pm.planned_end - now).days
        result.append({
            "type": "milestone",
            "project_id": pm.project_id,
            "project_name": project.name if project else "—",
            "project_client": project.client if project else "—",
            "milestone_num": pm.num,
            "milestone_name": pm.name,
            "task_name": "—",
            "assigned_to": pm.assignee or "—",
            "team": "—",
            "due_date": pm.planned_end,
            "days_remaining": days_remaining,
            "priority": "High" if days_remaining <= 2 else "Medium" if days_remaining <= 5 else "Low",
            "status": pm.status,
            "progress": pm.progress,
        })

    for cm in cmile_rows:
        project = project_map.get(cm.project_id)
        days_remaining = (cm.planned_end - now).days
        result.append({
            "type": "milestone_config",
            "project_id": cm.project_id,
            "project_name": project.name if project else "—",
            "project_client": project.client if project else "—",
            "milestone_num": cm.num,
            "milestone_name": cm.name,
            "task_name": "—",
            "assigned_to": cm.assignee or "—",
            "team": "—",
            "due_date": cm.planned_end,
            "days_remaining": days_remaining,
            "priority": "High" if days_remaining <= 2 else "Medium" if days_remaining <= 5 else "Low",
            "status": cm.status,
            "progress": 100 if cm.status == "Completed" else 50 if cm.status == "In Progress" else 0,
        })

    for a in ta_rows:
        project = project_map.get(a.project_id)
        assignee = user_map.get(a.assigned_to)
        days_remaining = (a.due_date - now).days
        result.append({
            "type": "task",
            "project_id": a.project_id,
            "project_name": project.name if project else GENERAL_TASK_LABEL,
            "project_client": project.client if project else "—",
            "milestone_num": a.milestone_num,
            "milestone_name": f"M{str(a.milestone_num).zfill(2)}" if a.milestone_num else "—",
            "task_name": a.title,
            "assigned_to": assignee.name if assignee else "—",
            "team": a.team or "—",
            "due_date": a.due_date,
            "days_remaining": days_remaining,
            "priority": a.priority,
            "status": a.status,
            "progress": 100 if a.status == "Completed" else 50 if a.status == "In Progress" else 0,
        })

    for t in task_rows:
        project = project_map.get(t.project_id)
        cm = cm_map.get(t.milestone_id)
        days_remaining = (t.planned_end - now).days
        result.append({
            "type": "task_config",
            "project_id": t.project_id,
            "project_name": project.name if project else "—",
            "project_client": project.client if project else "—",
            "milestone_num": cm.num if cm else None,
            "milestone_name": cm.name if cm else "—",
            "task_name": t.name,
            "assigned_to": t.assignee or "—",
            "team": "—",
            "due_date": t.planned_end,
            "days_remaining": days_remaining,
            "priority": "High" if days_remaining <= 2 else "Medium" if days_remaining <= 5 else "Low",
            "status": t.status,
            "progress": 100 if t.status == "Completed" else 50 if t.status == "In Progress" else 0,
        })

    for s in subtask_rows:
        project = project_map.get(s.project_id)
        ct = ct_map.get(s.task_id)
        cm = cm_map.get(ct.milestone_id) if ct else None
        days_remaining = (s.planned_end - now).days
        result.append({
            "type": "subtask_config",
            "project_id": s.project_id,
            "project_name": project.name if project else "—",
            "project_client": project.client if project else "—",
            "milestone_num": cm.num if cm else None,
            "milestone_name": cm.name if cm else "—",
            "task_name": f"{ct.name if ct else '—'} → {s.name}",
            "assigned_to": s.assignee or "—",
            "team": "—",
            "due_date": s.planned_end,
            "days_remaining": days_remaining,
            "priority": "High" if days_remaining <= 2 else "Medium" if days_remaining <= 5 else "Low",
            "status": s.status,
            "progress": 100 if s.status == "Completed" else 50 if s.status == "In Progress" else 0,
        })

    for act in activity_rows:
        project = project_map.get(act.project_id)
        s = cs_map.get(act.subtask_id)
        ct = ct_map.get(s.task_id) if s else None
        cm = cm_map.get(ct.milestone_id) if ct else None
        days_remaining = (act.planned_end - now).days
        result.append({
            "type": "activity",
            "project_id": act.project_id,
            "project_name": project.name if project else "—",
            "project_client": project.client if project else "—",
            "milestone_num": cm.num if cm else None,
            "milestone_name": cm.name if cm else "—",
            "task_name": f"{s.name if s else '—'} → {act.name}",
            "assigned_to": act.assignee or "—",
            "team": "—",
            "due_date": act.planned_end,
            "days_remaining": days_remaining,
            "priority": "High" if days_remaining <= 2 else "Medium" if days_remaining <= 5 else "Low",
            "status": act.status,
            "progress": 100 if act.status == "Completed" else 50 if act.status == "In Progress" else 0,
        })

    # Sort by days remaining
    result.sort(key=lambda x: x["days_remaining"])
    return result


# ── Global Team Workload ──────────────────────────────────────────────────────
@router.get("/workload")
def global_workload(
    team: Optional[str] = None,
    project_id: Optional[int] = None,
    employee_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    now = datetime.utcnow()
    d_from = datetime.fromisoformat(date_from) if date_from else now - timedelta(days=7)
    d_to   = datetime.fromisoformat(date_to)   if date_to   else now

    # Get all users
    users_q = db.query(User).filter(User.is_active == True)
    if team:
        users_q = users_q.filter(User.role == team)
    if employee_id:
        users_q = users_q.filter(User.id == employee_id)
    users = users_q.all()

    result = []
    chart_data = []

    for u in users:
        # Get assignments for this user
        a_query = db.query(TaskAssignment).filter(
            TaskAssignment.assigned_to == u.id,
            TaskAssignment.created_at >= d_from,
            TaskAssignment.created_at <= d_to,
        )
        if project_id:
            a_query = a_query.filter(TaskAssignment.project_id == project_id)

        assignments = a_query.all()
        total     = len(assignments)
        completed = sum(1 for a in assignments if a.status == "Completed")
        in_prog   = sum(1 for a in assignments if a.status == "In Progress")
        pending   = sum(1 for a in assignments if a.status == "Not Started")
        # "On Hold" is a real status option (see matching fix in
        # assignments.py/global assignment_summary) — without its own bucket
        # here, an On-Hold assignment still counts toward `total`/`assigned`
        # but vanishes from every breakdown bucket and the workload chart.
        on_hold   = sum(1 for a in assignments if a.status == "On Hold")
        overdue   = sum(1 for a in assignments if a.due_date and a.due_date < now and a.status != "Completed")
        pct       = round((completed / total * 100), 1) if total else 0

        if total > 0:  # Only include users with assignments
            result.append({
                "user_id":    u.id,
                "name":       u.name,
                "email":      u.email,
                "role":       u.role,
                "team":       u.role,
                "total":      total,
                "completed":  completed,
                "in_progress": in_prog,
                "pending":    pending,
                "on_hold":    on_hold,
                "overdue":    overdue,
                "completion_pct": pct,
            })

            chart_data.append({
                "name":       u.name,
                "assigned":   total,
                "completed":  completed,
                "in_progress": in_prog,
                "pending":    pending,
                "on_hold":    on_hold,
                "overdue":    overdue,
            })

    # Summary totals
    summary = {
        "total_assigned":  sum(r["total"]       for r in result),
        "total_completed": sum(r["completed"]   for r in result),
        "total_in_progress": sum(r["in_progress"] for r in result),
        "total_pending":   sum(r["pending"]     for r in result),
        "total_on_hold":   sum(r["on_hold"]     for r in result),
        "total_overdue":   sum(r["overdue"]     for r in result),
        "avg_completion":  round(sum(r["completion_pct"] for r in result) / len(result), 1) if result else 0,
    }

    return {
        "employees": result,
        "chart_data": chart_data,
        "summary": summary,
        "period": {"from": d_from.isoformat(), "to": d_to.isoformat()},
    }


# ── Helper: all projects list ─────────────────────────────────────────────────
@router.get("/projects-list")
def global_projects_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    projects = db.query(Project).order_by(Project.name).all()
    return [{"id": p.id, "name": p.name, "client": p.client, "status": p.status} for p in projects]


# ── Helper: all users list ────────────────────────────────────────────────────
@router.get("/users-list")
def global_users_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    users = db.query(User).filter(User.is_active == True).all()
    return [{"id": u.id, "name": u.name, "role": u.role, "email": u.email} for u in users]


# ── Dashboard Project Status (req 7a) ─────────────────────────────────────────
# Per-project Milestone Completion % and Task Completion %, for the
# "Project Status" section / per-project progress graph on the dashboard.
@router.get("/project-status")
def global_project_status(
    # Mirrors the Global Dashboard's filter bar so this section narrows down
    # with the rest of the page instead of always showing every project.
    project_id: Optional[int] = None,
    team: Optional[str] = None,
    employee_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    projects_q = db.query(Project).order_by(Project.name)
    if project_id:
        projects_q = projects_q.filter(Project.id == project_id)
    projects = projects_q.all()

    # CustomMilestone/CustomTask store the assignee as a free-text name
    # (consistent with the Deadlines fix), not a user_id FK — so an
    # employee/team filter is applied by matching that name against the
    # selected user (or every user in the selected team/role).
    assignee_names = None
    if employee_id:
        u = db.query(User).filter_by(id=employee_id).first()
        assignee_names = {u.name} if u else set()
    if team:
        team_names = {u.name for u in db.query(User).filter(User.role == team).all()}
        assignee_names = team_names if assignee_names is None else (assignee_names & team_names)

    result = []
    for p in projects:
        milestones = db.query(CustomMilestone).filter_by(project_id=p.id, is_active=True).all()
        tasks = db.query(CustomTask).filter_by(project_id=p.id).all()
        if assignee_names is not None:
            milestones = [m for m in milestones if m.assignee in assignee_names]
            tasks = [t for t in tasks if t.assignee in assignee_names]

        ms_total = len(milestones)
        ms_done = sum(1 for m in milestones if m.status == "Completed")
        ms_pct = round((ms_done / ms_total) * 100, 1) if ms_total else 0.0

        task_total = len(tasks)
        task_done = sum(1 for t in tasks if t.status == "Completed")
        task_pct = round((task_done / task_total) * 100, 1) if task_total else 0.0

        result.append({
            "project_id": p.id,
            "project_name": p.name,
            "milestone_total": ms_total,
            "milestone_completed": ms_done,
            "milestone_completion_pct": ms_pct,
            "task_total": task_total,
            "task_completed": task_done,
            "task_completion_pct": task_pct,
            # Overall progress indicator for the per-project bar/graph —
            # weighted average of milestone and task completion.
            "overall_progress_pct": round((ms_pct + task_pct) / 2, 1) if (ms_total or task_total) else 0.0,
        })
    return result
