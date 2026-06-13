from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.models.models import ProjectMilestone, SubtaskStatus, Notification
from app.services.notification_service import create_notification
from app.services.email_service import email_overdue, email_due_reminder
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()

def check_overdue_and_reminders():
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()
        reminder_threshold = now + timedelta(days=2)

        # Check milestone overdue
        milestones = db.query(ProjectMilestone).filter(
            ProjectMilestone.planned_end < now,
            ProjectMilestone.status.notin_(["Completed"]),
        ).all()

        for ms in milestones:
            if ms.status != "Overdue":
                ms.status = "Overdue"
                create_notification(
                    db, ms.project_id, "overdue",
                    f"Milestone {ms.num:02d} '{ms.name}' is overdue.",
                    email_to=None,
                )
                logger.info(f"Marked overdue: Milestone {ms.num} project {ms.project_id}")

        # Check due date reminders (2 days ahead)
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
