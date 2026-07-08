from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List
from datetime import datetime, timedelta
from app.db.database import get_db
from app.models.models import (Project, ProjectMilestone, Notification, AuditLog,
                                User, Subtask, Task, Milestone, SubtaskStatus,
                                ProjectMember, Response, TaskAssignment,
                                CustomTask, CustomSubtask, Activity)
from app.schemas.schemas import NotificationOut, AuditOut
from app.core.deps import get_current_user

router = APIRouter(tags=["Dashboard"])

# ── Dashboard ─────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/dashboard")
def get_dashboard(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        project = db.query(Project).filter_by(id=project_id).first()
        if not project:
            raise HTTPException(404, "Project not found")

        # Get only the milestones the user selected for this project
        from app.models.models import CustomMilestone
        selected_nums = {
            cm.num for cm in db.query(CustomMilestone).filter_by(
                project_id=project_id, is_active=True
            ).all()
        }

        all_pms = db.query(ProjectMilestone).filter_by(
            project_id=project_id
        ).order_by(ProjectMilestone.num).all()

        # If user has configured custom milestones, show only those
        # Otherwise show all (fallback for projects not yet configured)
        if selected_nums:
            pms = [pm for pm in all_pms if pm.num in selected_nums]
        else:
            pms = []  # No milestones configured yet — show empty

        # ── Summary ───────────────────────────────────────────────────────────
        total     = len(pms)
        completed = sum(1 for m in pms if m.status == "Completed")
        in_prog   = sum(1 for m in pms if m.status == "In Progress")
        overdue   = sum(1 for m in pms if m.status == "Overdue")
        progress  = round(sum(m.progress for m in pms) / total, 1) if total else 0.0

        # ── Total subtask counts — scoped to THIS project's selected
        # milestones only. Previously `total_subs` counted every Subtask row
        # in the entire template with no project filter at all, so any
        # project showed the same template-wide total (e.g. "108") no
        # matter how many milestones it actually had selected — visibly
        # wrong on a single-project dashboard and inconsistent with every
        # other number on this page, which is scoped to `pms`/selected_nums.
        if selected_nums:
            total_subs = db.query(func.count(Subtask.id)).join(
                Task, Subtask.task_id == Task.id
            ).join(
                Milestone, Task.milestone_id == Milestone.id
            ).filter(Milestone.num.in_(selected_nums)).scalar() or 0
        else:
            total_subs = 0
        done_subs  = db.query(func.count(SubtaskStatus.id)).filter_by(
            project_id=project_id, status="Completed"
        ).scalar() or 0

        # ── Milestone list ────────────────────────────────────────────────────
        ms_list = [
            {
                "num": pm.num, "name": pm.name,
                "status": pm.status, "progress": pm.progress,
                "assignee": pm.assignee, "planned_end": pm.planned_end,
            }
            for pm in pms
        ]

        # ── Notifications — latest 20, enriched with the assignee's name so
        # the dashboard card can group by user and show counts only ──────────
        notifs = db.query(Notification).filter_by(
            project_id=project_id
        ).order_by(Notification.created_at.desc()).limit(20).all()

        notif_user_cache = {}
        notif_list = []
        for n in notifs:
            uname = None
            if n.user_id:
                if n.user_id not in notif_user_cache:
                    u = db.query(User).filter_by(id=n.user_id).first()
                    notif_user_cache[n.user_id] = u.name if u else None
                uname = notif_user_cache[n.user_id]
            notif_list.append({
                "id": n.id, "type": n.type, "message": n.message,
                "email_to": n.email_to, "email_sent": n.email_sent,
                "read": n.read, "created_at": n.created_at,
                "user_id": n.user_id, "user_name": uname,
            })

        # ── Overdue items ─────────────────────────────────────────────────────
        overdue_items = [
            {
                "id": pm.id, "name": pm.name, "milestone": pm.num,
                "assignee": pm.assignee or "Unassigned",
                "due_date": pm.planned_end,
            }
            for pm in pms if pm.status == "Overdue"
        ]

        # ── Team workload — project-scoped, using real TaskAssignment data ─────
        # (previously broken: queried ALL milestones with no project_id filter
        # and matched by role-substring against template tasks, not actual
        # assignments). Split into Milestone-based Tasks vs General Tasks per
        # requirement: milestone_num set = tied to a Milestone; null = General.
        members = db.query(ProjectMember).filter_by(project_id=project_id).all()
        member_ids = [m.user_id for m in members]
        users_map = {u.id: u for u in db.query(User).filter(User.id.in_(member_ids)).all()} if member_ids else {}
        proj_assignments = db.query(TaskAssignment).filter_by(project_id=project_id).all()
        now_wl = datetime.utcnow()

        def _wl_stats(rows):
            total     = len(rows)
            completed = sum(1 for a in rows if a.status == "Completed")
            in_prog   = sum(1 for a in rows if a.status == "In Progress")
            overdue_n = sum(1 for a in rows if a.due_date and a.due_date < now_wl and a.status != "Completed")
            return {"total": total, "completed": completed, "in_progress": in_prog, "overdue": overdue_n}

        workload = []
        for m in members:
            u = users_map.get(m.user_id)
            if not u:
                continue
            mine = [a for a in proj_assignments if a.assigned_to == u.id]
            milestone_rows = [a for a in mine if a.milestone_num is not None]
            general_rows   = [a for a in mine if a.milestone_num is None]
            workload.append({
                "user_id": u.id,
                "name": u.name,
                "role": u.role,
                "tasks": len(mine),  # backward-compat total used by older UI
                "milestone_tasks": _wl_stats(milestone_rows),
                "general_tasks":   _wl_stats(general_rows),
            })

        # ── Upcoming deadlines — next 14 days ─────────────────────────────────
        # Sourced from the live Milestone Configuration hierarchy (Milestone/
        # Task/Subtask/Activity) + Task Assignments — the legacy
        # ProjectMilestone table is no longer kept in sync with the
        # Milestone Configuration module, so it can't be the source of truth.
        soon = datetime.utcnow() + timedelta(days=14)
        now  = datetime.utcnow()
        deadline_list = []

        cm_rows = db.query(CustomMilestone).filter(
            CustomMilestone.project_id == project_id,
            CustomMilestone.planned_end != None,
            CustomMilestone.planned_end > now,
            CustomMilestone.planned_end <= soon,
            CustomMilestone.status != "Completed",
        ).all()
        for cm in cm_rows:
            deadline_list.append({
                "id": f"ms-{cm.id}", "name": f"🏁 {cm.name}",
                "assignee": cm.assignee or "Unassigned",
                "due_date": cm.planned_end,
            })

        ct_rows = db.query(CustomTask).filter(
            CustomTask.project_id == project_id,
            CustomTask.planned_end != None,
            CustomTask.planned_end > now,
            CustomTask.planned_end <= soon,
            CustomTask.status != "Completed",
        ).all()
        for t in ct_rows:
            deadline_list.append({
                "id": f"task-{t.id}", "name": f"🧩 {t.name}",
                "assignee": t.assignee or "Unassigned",
                "due_date": t.planned_end,
            })

        cs_rows = db.query(CustomSubtask).filter(
            CustomSubtask.project_id == project_id,
            CustomSubtask.planned_end != None,
            CustomSubtask.planned_end > now,
            CustomSubtask.planned_end <= soon,
            CustomSubtask.status != "Completed",
        ).all()
        for s in cs_rows:
            deadline_list.append({
                "id": f"sub-{s.id}", "name": f"📝 {s.name}",
                "assignee": s.assignee or "Unassigned",
                "due_date": s.planned_end,
            })

        act_rows = db.query(Activity).filter(
            Activity.project_id == project_id,
            Activity.planned_end != None,
            Activity.planned_end > now,
            Activity.planned_end <= soon,
            Activity.status != "Completed",
        ).all()
        for act in act_rows:
            deadline_list.append({
                "id": f"act-{act.id}", "name": f"⚡ {act.name}",
                "assignee": act.assignee or "Unassigned",
                "due_date": act.planned_end,
            })

        ta_rows = db.query(TaskAssignment).filter(
            TaskAssignment.project_id == project_id,
            TaskAssignment.due_date != None,
            TaskAssignment.due_date > now,
            TaskAssignment.due_date <= soon,
            TaskAssignment.status != "Completed",
        ).all()
        if ta_rows:
            assignee_ids = {a.assigned_to for a in ta_rows}
            assignee_map = {u.id: u for u in db.query(User).filter(User.id.in_(assignee_ids)).all()}
            for a in ta_rows:
                au = assignee_map.get(a.assigned_to)
                deadline_list.append({
                    "id": f"asg-{a.id}", "name": f"📌 {a.title}",
                    "assignee": au.name if au else "Unassigned",
                    "due_date": a.due_date,
                })

        deadline_list.sort(key=lambda d: d["due_date"])
        deadline_list = deadline_list[:8]

        # ── Audit trail — latest 10 ───────────────────────────────────────────
        audit_rows = db.query(AuditLog).filter_by(
            project_id=project_id
        ).order_by(AuditLog.created_at.desc()).limit(10).all()

        audit_list = [
            {
                "id": a.id, "actor": a.actor,
                "description": a.description,
                "old_value": a.old_value, "new_value": a.new_value,
                "created_at": a.created_at,
            }
            for a in audit_rows
        ]

        return {
            "summary": {
                "total":       total,
                "completed":   completed,
                "in_progress": in_prog,
                "overdue":     overdue,
                "not_started": total - completed - in_prog - overdue,
                "progress":    progress,
                "done_tasks":  done_subs,
                "total_tasks": total_subs,
            },
            "milestones":    ms_list,
            "notifications": notif_list,
            "overdue_items": overdue_items,
            "workload":      workload,
            "deadlines":     deadline_list,
            "audit":         audit_list,
        }

    except HTTPException:
        raise
    except Exception as e:
        # Return safe fallback so dashboard always loads
        import logging
        logging.error(f"Dashboard error for project {project_id}: {e}", exc_info=True)
        raise HTTPException(500, f"Dashboard error: {str(e)}")


# ── Notifications ─────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/notifications", response_model=List[NotificationOut])
def list_notifications(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    rows = db.query(Notification).filter_by(
        project_id=project_id
    ).order_by(Notification.created_at.desc()).all()

    # Requirement 7(b): resolve the assigned person's name so the frontend can
    # group notifications per user and show a count instead of full message
    # text by default.
    user_cache = {}
    out = []
    for n in rows:
        uname = None
        if n.user_id:
            if n.user_id not in user_cache:
                u = db.query(User).filter_by(id=n.user_id).first()
                user_cache[n.user_id] = u.name if u else None
            uname = user_cache[n.user_id]
        out.append({
            "id": n.id, "type": n.type, "message": n.message,
            "email_to": n.email_to, "email_sent": n.email_sent,
            "read": n.read, "created_at": n.created_at,
            "user_id": n.user_id, "user_name": uname,
        })
    return out


@router.patch("/notifications/{notif_id}/read")
def mark_read(
    notif_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    n = db.query(Notification).filter_by(id=notif_id).first()
    if n:
        n.read = True
        db.commit()
    return {"status": "ok"}


# ── Audit ─────────────────────────────────────────────────────────────────────
_CREATE_ACTIONS = {"add_milestone", "create_milestone", "create_task", "add_task",
                    "add_subtask", "add_activity", "assign_task", "create"}

@router.get("/projects/{project_id}/audit", response_model=List[AuditOut])
def list_audit(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    rows = db.query(AuditLog).filter_by(
        project_id=project_id
    ).order_by(AuditLog.created_at.desc()).all()

    # Requirement 4: split Created By / Modified By. "Created By" = the actor
    # of the earliest create-type entry for the same entity_type+entity_id;
    # "Modified By" = this row's own actor (most recent change).
    creator_cache = {}
    out = []
    for a in rows:
        key = (a.entity_type, a.entity_id)
        created_by = a.actor
        if a.entity_type and a.entity_id is not None:
            if key not in creator_cache:
                earliest = db.query(AuditLog).filter(
                    AuditLog.project_id == project_id,
                    AuditLog.entity_type == a.entity_type,
                    AuditLog.entity_id == a.entity_id,
                ).order_by(AuditLog.created_at.asc()).first()
                creator_cache[key] = earliest.actor if earliest else a.actor
            created_by = creator_cache[key]
        out.append({
            "id": a.id, "actor": a.actor, "entity_type": a.entity_type,
            "entity_id": a.entity_id, "action": a.action, "description": a.description,
            "old_value": a.old_value, "new_value": a.new_value, "created_at": a.created_at,
            "created_by": created_by, "modified_by": a.actor,
        })
    return out
