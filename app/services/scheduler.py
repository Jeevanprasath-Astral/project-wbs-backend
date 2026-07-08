from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func
from app.db.database import SessionLocal
from app.models.models import (ProjectMilestone, SubtaskStatus, Notification, Project,
                                User, CustomMilestone, CustomTask, CustomSubtask, Activity)
from app.services.notification_service import create_notification
from app.services.email_service import email_overdue, email_due_reminder
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def _resolve_user_by_name(db: Session, name):
    if not name:
        return None
    return db.query(User).filter(sa_func.lower(User.name) == name.strip().lower()).first()


def _overdue_duration(planned_end: datetime, now: datetime) -> str:
    delta = now - planned_end
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days}d {hours}h"
    return f"{hours}h"


def _notify_overdue(db: Session, project: Project, milestone_name: str, task_name: str,
                     assignee_name, planned_end, entity_type: str, entity_id: int, now: datetime):
    """Requirement 3: auto-generate an email to the assigned person AND a
    notification containing Project Name, Milestone Name, Task Name,
    Assigned Person, Planned End Date, and Overdue Duration."""
    user = _resolve_user_by_name(db, assignee_name)
    project_name = project.name if project else "—"
    duration = _overdue_duration(planned_end, now) if planned_end else "—"
    due_str = planned_end.strftime("%Y-%m-%d %H:%M") if planned_end else "—"
    message = (
        f"OVERDUE — Project: {project_name} | Milestone: {milestone_name} | "
        f"Task: {task_name} | Assigned: {assignee_name or '—'} | "
        f"Planned End: {due_str} | Overdue by: {duration}"
    )
    email_subject = f"[{project_name}] OVERDUE — {task_name or milestone_name}"
    email_body = f"""
    <p>Hi {user.name if user else (assignee_name or '')},</p>
    <p>The following item is now <strong>overdue</strong>:</p>
    <p><strong>Project:</strong> {project_name}</p>
    <p><strong>Milestone:</strong> {milestone_name}</p>
    {f'<p><strong>Task:</strong> {task_name}</p>' if task_name else ''}
    <p><strong>Planned End Date:</strong> {due_str}</p>
    <p><strong>Overdue Duration:</strong> {duration}</p>
    <p>Please update the status or take corrective action as soon as possible.</p>
    <p>Regards,<br>Project WBS System</p>
    """
    create_notification(
        db, project.id if project else None, "overdue", message,
        user_id=user.id if user else None,
        email_to=user.email if user else None,
        send_now=bool(user and user.email),
        email_subject=email_subject,
        email_body=email_body,
    )


def check_overdue_and_reminders():
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        reminder_threshold = now + timedelta(days=2)

        # ── OLD path: ProjectMilestone (standard milestones) ────────────────
        milestones = db.query(ProjectMilestone).filter(
            ProjectMilestone.planned_end < now,
            ProjectMilestone.status.notin_(["Completed"]),
        ).all()

        for ms in milestones:
            project = db.query(Project).filter_by(id=ms.project_id).first()
            was_overdue = ms.status == "Overdue"
            ms.status = "Overdue"
            if not was_overdue:
                _notify_overdue(db, project, ms.name or f"M{ms.num:02d}", None,
                                 ms.assignee, ms.planned_end, "project_milestone", ms.id, now)
                logger.info(f"Marked overdue: Milestone {ms.num} project {ms.project_id}")

        upcoming = db.query(ProjectMilestone).filter(
            ProjectMilestone.planned_end > now,
            ProjectMilestone.planned_end <= reminder_threshold,
            ProjectMilestone.status.notin_(["Completed"]),
        ).all()

        for ms in upcoming:
            days_left = (ms.planned_end - now).days
            create_notification(
                db, ms.project_id, "reminder",
                f"Milestone {ms.num:02d} '{ms.name}' is due in {days_left} day(s).",
            )

        # ── NEW path: CustomMilestone / CustomTask / CustomSubtask / Activity ─
        custom_milestones = db.query(CustomMilestone).filter(
            CustomMilestone.planned_end < now,
            CustomMilestone.status.notin_(["Completed", "Overdue"]),
        ).all()
        for cm in custom_milestones:
            project = db.query(Project).filter_by(id=cm.project_id).first()
            cm.status = "Overdue"
            _notify_overdue(db, project, cm.name, None, cm.assignee, cm.planned_end,
                             "custom_milestone", cm.id, now)

        custom_tasks = db.query(CustomTask).filter(
            CustomTask.planned_end < now,
            CustomTask.status.notin_(["Completed", "Overdue"]),
        ).all()
        for ct in custom_tasks:
            project = db.query(Project).filter_by(id=ct.project_id).first()
            ms = db.query(CustomMilestone).filter_by(id=ct.milestone_id).first()
            ct.status = "Overdue"
            _notify_overdue(db, project, ms.name if ms else "—", ct.name, ct.assignee,
                             ct.planned_end, "custom_task", ct.id, now)

        custom_subtasks = db.query(CustomSubtask).filter(
            CustomSubtask.planned_end < now,
            CustomSubtask.status.notin_(["Completed", "Overdue"]),
        ).all()
        for cs in custom_subtasks:
            task = db.query(CustomTask).filter_by(id=cs.task_id).first()
            project = db.query(Project).filter_by(id=cs.project_id).first()
            ms = db.query(CustomMilestone).filter_by(id=task.milestone_id).first() if task else None
            cs.status = "Overdue"
            _notify_overdue(db, project, ms.name if ms else "—",
                             f"{task.name if task else '—'} / {cs.name}", cs.assignee,
                             cs.planned_end, "custom_subtask", cs.id, now)

        activities = db.query(Activity).filter(
            Activity.planned_end < now,
            Activity.status.notin_(["Completed", "Overdue"]),
        ).all()
        for act in activities:
            subtask = db.query(CustomSubtask).filter_by(id=act.subtask_id).first()
            task = db.query(CustomTask).filter_by(id=subtask.task_id).first() if subtask else None
            ms = db.query(CustomMilestone).filter_by(id=task.milestone_id).first() if task else None
            project = db.query(Project).filter_by(id=act.project_id).first()
            act.status = "Overdue"
            _notify_overdue(db, project, ms.name if ms else "—",
                             f"{task.name if task else '—'} / {act.name}", act.assignee,
                             act.planned_end, "activity", act.id, now)

        # Due-soon reminders for the new hierarchy (Task level is the most
        # actionable granularity for a reminder).
        upcoming_tasks = db.query(CustomTask).filter(
            CustomTask.planned_end > now,
            CustomTask.planned_end <= reminder_threshold,
            CustomTask.status.notin_(["Completed", "Overdue"]),
        ).all()
        for ct in upcoming_tasks:
            days_left = (ct.planned_end - now).days
            create_notification(
                db, ct.project_id, "reminder",
                f"Task '{ct.name}' is due in {days_left} day(s).",
            )

        db.commit()
        logger.info("Overdue check completed.")
    except Exception as e:
        logger.error(f"Overdue check error: {e}")
        db.rollback()
    finally:
        db.close()

def start_scheduler():
    scheduler.add_job(check_overdue_and_reminders, "interval", hours=6, id="overdue_check")
    scheduler.start()
    logger.info("Scheduler started — checking every 6 hours.")

def stop_scheduler():
    scheduler.shutdown()
