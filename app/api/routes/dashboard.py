from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List
from datetime import datetime, timedelta
from app.db.database import get_db
from app.models.models import (Project, ProjectMilestone, Notification, AuditLog,
                                User, Subtask, Task, Milestone, SubtaskStatus,
                                ProjectMember, Response)
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

        pms = db.query(ProjectMilestone).filter_by(
            project_id=project_id
        ).order_by(ProjectMilestone.num).all()

        # ── Summary ───────────────────────────────────────────────────────────
        total     = len(pms)
        completed = sum(1 for m in pms if m.status == "Completed")
        in_prog   = sum(1 for m in pms if m.status == "In Progress")
        overdue   = sum(1 for m in pms if m.status == "Overdue")
        progress  = round(sum(m.progress for m in pms) / total, 1) if total else 0.0

        # ── Total subtask counts — single query ───────────────────────────────
        total_subs = db.query(func.count(Subtask.id)).scalar() or 0
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

        # ── Notifications — latest 8 ──────────────────────────────────────────
        notifs = db.query(Notification).filter_by(
            project_id=project_id
        ).order_by(Notification.created_at.desc()).limit(8).all()

        notif_list = [
            {
                "id": n.id, "type": n.type, "message": n.message,
                "email_to": n.email_to, "email_sent": n.email_sent,
                "read": n.read, "created_at": n.created_at,
            }
            for n in notifs
        ]

        # ── Overdue items ─────────────────────────────────────────────────────
        overdue_items = [
            {
                "id": pm.id, "name": pm.name, "milestone": pm.num,
                "assignee": pm.assignee or "Unassigned",
                "due_date": pm.planned_end,
            }
            for pm in pms if pm.status == "Overdue"
        ]

        # ── Team workload — single query per member ───────────────────────────
        members = db.query(ProjectMember).filter_by(project_id=project_id).all()
        workload = []

        # Load all milestones with tasks in one query
        all_milestones = db.query(Milestone).options(
            joinedload(Milestone.tasks).joinedload(Task.subtasks)
        ).all()

        for m in members:
            u = db.query(User).filter_by(id=m.user_id).first()
            if not u:
                continue
            # Count subtasks where user's role matches task responsibility
            role_tasks = sum(
                len(t.subtasks)
                for ms in all_milestones
                for t in ms.tasks
                if t.responsibility and u.role.lower() in t.responsibility.lower()
            )
            workload.append({
                "user_id": u.id,
                "name": u.name,
                "role": u.role,
                "tasks": role_tasks,
            })

        # ── Upcoming deadlines — next 14 days ─────────────────────────────────
        soon = datetime.utcnow() + timedelta(days=14)
        now  = datetime.utcnow()

        deadline_pms = db.query(ProjectMilestone).filter(
            ProjectMilestone.project_id == project_id,
            ProjectMilestone.planned_end != None,
            ProjectMilestone.planned_end > now,
            ProjectMilestone.planned_end <= soon,
            ProjectMilestone.status.notin_(["Completed"]),
        ).order_by(ProjectMilestone.planned_end).limit(5).all()

        deadline_list = [
            {
                "id": d.id, "name": d.name,
                "assignee": d.assignee or "Unassigned",
                "due_date": d.planned_end,
            }
            for d in deadline_pms
        ]

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
    return db.query(Notification).filter_by(
        project_id=project_id
    ).order_by(Notification.created_at.desc()).all()


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
@router.get("/projects/{project_id}/audit", response_model=List[AuditOut])
def list_audit(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(AuditLog).filter_by(
        project_id=project_id
    ).order_by(AuditLog.created_at.desc()).all()
