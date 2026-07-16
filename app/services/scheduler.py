from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func
from app.db.database import SessionLocal
from app.models.models import (ProjectMilestone, SubtaskStatus, Notification, Project,
                                User, CustomMilestone, CustomTask, CustomSubtask, Activity,
                                WorkHours, LeaveRequest, Permission, Holiday)
from app.services.notification_service import create_notification
from app.services.email_service import email_overdue, email_due_reminder, send_email
from datetime import datetime, timedelta, date as date_type
import logging
import os
import calendar
import urllib.request

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
            user = _resolve_user_by_name(db, ms.assignee)
            project = db.query(Project).filter_by(id=ms.project_id).first()
            project_name = project.name if project else "—"
            due_str = ms.planned_end.strftime("%Y-%m-%d") if ms.planned_end else "—"
            email_subject = f"[{project_name}] Reminder — Milestone due in {days_left} day(s)"
            email_body = f"""
            <p>Hi {user.name if user else (ms.assignee or 'Team')},</p>
            <p>This is a reminder that the following milestone is due soon:</p>
            <p><strong>Project:</strong> {project_name}</p>
            <p><strong>Milestone:</strong> {ms.name or f'M{ms.num:02d}'}</p>
            <p><strong>Due Date:</strong> {due_str}</p>
            <p><strong>Days Remaining:</strong> {days_left}</p>
            <p>Please ensure timely completion.</p>
            <p>Regards,<br>Project WBS System</p>
            """
            create_notification(
                db, ms.project_id, "reminder",
                f"Milestone {ms.num:02d} '{ms.name}' is due in {days_left} day(s).",
                user_id=user.id if user else None,
                email_to=user.email if user else None,
                send_now=bool(user and user.email),
                email_subject=email_subject,
                email_body=email_body,
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
            user = _resolve_user_by_name(db, ct.assignee)
            project = db.query(Project).filter_by(id=ct.project_id).first()
            project_name = project.name if project else "—"
            due_str = ct.planned_end.strftime("%Y-%m-%d") if ct.planned_end else "—"
            email_subject = f"[{project_name}] Reminder — Task due in {days_left} day(s)"
            email_body = f"""
            <p>Hi {user.name if user else (ct.assignee or 'Team')},</p>
            <p>This is a reminder that the following task is due soon:</p>
            <p><strong>Project:</strong> {project_name}</p>
            <p><strong>Task:</strong> {ct.name}</p>
            <p><strong>Due Date:</strong> {due_str}</p>
            <p><strong>Days Remaining:</strong> {days_left}</p>
            <p>Please ensure timely completion.</p>
            <p>Regards,<br>Project WBS System</p>
            """
            create_notification(
                db, ct.project_id, "reminder",
                f"Task '{ct.name}' is due in {days_left} day(s).",
                user_id=user.id if user else None,
                email_to=user.email if user else None,
                send_now=bool(user and user.email),
                email_subject=email_subject,
                email_body=email_body,
            )

        db.commit()
        logger.info("Overdue check completed.")
    except Exception as e:
        logger.error(f"Overdue check error: {e}")
        db.rollback()
    finally:
        db.close()

def _keepalive_ping():
    """Ping /api/ping every 10 min so Render free tier never idles long enough
    to spin the server down (Render sleeps after 15 min of inactivity; 10-min
    interval gives a safe buffer).  Runs inside the server process, invisible
    to users.  Render injects RENDER_EXTERNAL_URL automatically.  On localhost
    that variable is absent so the ping is skipped silently."""
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        return  # not on Render -- skip
    try:
        url = base_url + "/api/ping"
        with urllib.request.urlopen(url, timeout=10) as resp:
            logger.info("Keepalive ping -> %s  status=%s", url, resp.status)
    except Exception as e:
        logger.warning("Keepalive ping failed: %s", e)


def send_monthly_appreciation():
    """Runs on the 1st of every month at 09:00 — checks the previous month.

    Logic:
    1. Compute every Mon-Fri in the previous calendar month.
    2. Remove global company holidays (Holiday.project_id IS NULL).
    3. For each active user:
       a. Remove their approved leave days (LeaveRequest status='Approved').
       b. Remove their approved permission days (Permission status='Approved').
       c. On every remaining required working day, verify SUM(hours_spent) >= 7
          from WorkHours rows for that user+date.
    4. Users who pass all checks get an appreciation email.
    """
    db: Session = SessionLocal()
    try:
        today = datetime.utcnow().date()
        # Previous month boundaries
        first_of_this_month = today.replace(day=1)
        last_of_prev_month  = first_of_this_month - timedelta(days=1)
        first_of_prev_month = last_of_prev_month.replace(day=1)
        year  = first_of_prev_month.year
        month = first_of_prev_month.month

        month_label = first_of_prev_month.strftime("%B %Y")

        # All Mon–Fri in the previous month
        _, days_in_month = calendar.monthrange(year, month)
        working_days: set[date_type] = set()
        for d in range(1, days_in_month + 1):
            dt = date_type(year, month, d)
            if dt.weekday() < 5:  # 0=Mon … 4=Fri
                working_days.add(dt)

        # Remove global company holidays
        global_holidays = db.query(Holiday.date).filter(Holiday.project_id.is_(None)).all()
        holiday_dates = {row[0] for row in global_holidays}
        working_days -= holiday_dates

        if not working_days:
            logger.info("send_monthly_appreciation: no working days in %s — skipping.", month_label)
            return

        active_users = db.query(User).filter(User.is_active == True).all()
        sent_count = 0

        for user in active_users:
            # Days the user had approved leave (inclusive date range)
            leaves = db.query(LeaveRequest).filter(
                LeaveRequest.user_id == user.id,
                LeaveRequest.status == "Approved",
                LeaveRequest.date_from <= last_of_prev_month,
                LeaveRequest.date_to   >= first_of_prev_month,
            ).all()

            leave_dates: set[date_type] = set()
            for leave in leaves:
                cur = max(leave.date_from, first_of_prev_month)
                end = min(leave.date_to,   last_of_prev_month)
                while cur <= end:
                    leave_dates.add(cur)
                    cur += timedelta(days=1)

            # Days the user had approved permissions
            perm_dates = {
                row[0]
                for row in db.query(Permission.date).filter(
                    Permission.user_id == user.id,
                    Permission.status  == "Approved",
                    Permission.date    >= first_of_prev_month,
                    Permission.date    <= last_of_prev_month,
                ).all()
            }

            required_days = working_days - leave_dates - perm_dates
            if not required_days:
                # Entire month was holidays/leave — give the benefit of the doubt
                continue

            # Check timesheet coverage: each required day needs >= 7 hours
            hours_by_date: dict[date_type, float] = {}
            wh_rows = db.query(WorkHours.date, sa_func.sum(WorkHours.hours_spent)).filter(
                WorkHours.user_id == user.id,
                WorkHours.date    >= first_of_prev_month,
                WorkHours.date    <= last_of_prev_month,
            ).group_by(WorkHours.date).all()
            for wh_date, total in wh_rows:
                hours_by_date[wh_date] = total or 0.0

            missed_days = [d for d in required_days if hours_by_date.get(d, 0.0) < 7.0]

            if missed_days:
                logger.info(
                    "send_monthly_appreciation: %s missed %d day(s) in %s — skipping.",
                    user.name, len(missed_days), month_label,
                )
                continue

            # User qualifies — send appreciation email
            body = f"""
            <div style="font-family:Arial,sans-serif;max-width:540px;margin:0 auto;">
              <div style="background:linear-gradient(135deg,#091525,#0f2448);padding:28px 32px;text-align:center;border-radius:12px 12px 0 0;">
                <h1 style="color:#fff;font-size:22px;margin:0;letter-spacing:0.04em;">AXON</h1>
                <p style="color:#4a6080;font-size:10px;margin:4px 0 0;letter-spacing:0.08em;">REQUIREMENT &amp; TRACKING SYSTEM</p>
              </div>
              <div style="background:#f8fafc;padding:32px 36px;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 12px 12px;">
                <p style="font-size:22px;text-align:center;margin:0 0 8px;">🌟</p>
                <h2 style="font-size:18px;color:#1e40af;text-align:center;margin:0 0 20px;">
                  Timesheet Appreciation — {month_label}
                </h2>
                <p style="font-size:15px;color:#0f172a;margin:0 0 12px;">Hi <strong>{user.name}</strong>,</p>
                <p style="font-size:14px;color:#334155;line-height:1.7;margin:0 0 20px;">
                  Congratulations! You've successfully logged <strong>7+ hours</strong> on every
                  working day in <strong>{month_label}</strong>. Your dedication and consistent
                  time-tracking help the team stay on track and make better project decisions.
                </p>
                <div style="background:linear-gradient(135deg,#eff6ff,#dbeafe);border:1px solid #bfdbfe;
                            border-radius:10px;padding:16px 20px;margin-bottom:24px;text-align:center;">
                  <p style="font-size:14px;font-weight:600;color:#1e40af;margin:0;">
                    Perfect Timesheet Attendance ✅
                  </p>
                  <p style="font-size:13px;color:#3b82f6;margin:6px 0 0;">{month_label}</p>
                </div>
                <p style="font-size:13px;color:#94a3b8;margin:0;">
                  Keep it up!<br>
                  <strong style="color:#64748b;">Axon WBS Team</strong><br>
                  <span style="font-size:11px;">by Connectome</span>
                </p>
              </div>
            </div>
            """
            sent = send_email(
                to=user.email,
                subject=f"🌟 Timesheet Appreciation — {month_label} | Axon WBS",
                body=body,
            )
            if sent:
                sent_count += 1
                logger.info("Appreciation email sent to %s for %s.", user.name, month_label)

        logger.info(
            "send_monthly_appreciation: %d/%d users received appreciation emails for %s.",
            sent_count, len(active_users), month_label,
        )
    except Exception as e:
        logger.error("send_monthly_appreciation error: %s", e)
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(check_overdue_and_reminders, "interval", hours=6, id="overdue_check")
    scheduler.add_job(_keepalive_ping, "interval", minutes=10, id="keepalive_ping")
    # 1st of every month at 09:00 UTC — sends appreciation emails for previous month
    scheduler.add_job(send_monthly_appreciation, "cron", day=1, hour=9, minute=0, id="monthly_appreciation")
    scheduler.start()
    logger.info("Scheduler started -- overdue check every 6 h, keepalive ping every 10 min, appreciation email 1st of month at 09:00.")

def stop_scheduler():
    scheduler.shutdown()
