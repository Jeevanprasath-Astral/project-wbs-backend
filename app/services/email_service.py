import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def send_email(to: str, subject: str, body: str) -> bool:
    if not settings.MAIL_ENABLED or not settings.MAIL_USERNAME:
        logger.info(f"[EMAIL DISABLED] To: {to} | Subject: {subject}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = settings.MAIL_FROM
        msg["To"]      = to
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP(settings.MAIL_SERVER, settings.MAIL_PORT) as server:
            if settings.MAIL_STARTTLS:
                server.starttls()
            server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
            server.send_message(msg)
        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Email failed to {to}: {e}")
        return False

def email_task_assigned(to: str, assignee: str, task: str, project: str, due_date: str = None):
    subject = f"[{project}] Task assigned to you — {task}"
    body = f"""
    <p>Hi {assignee},</p>
    <p>A new task has been assigned to you in <strong>{project}</strong>:</p>
    <p><strong>Task:</strong> {task}</p>
    {f'<p><strong>Due Date:</strong> {due_date}</p>' if due_date else ''}
    <p>Please log in to review and start working on this task.</p>
    <p>Regards,<br>Project WBS System</p>
    """
    return send_email(to, subject, body)

def email_overdue(to: str, name: str, item: str, project: str, due_date: str):
    subject = f"[OVERDUE] {item} — {project}"
    body = f"""
    <p>Hi {name},</p>
    <p>The following item is <strong style="color:red">OVERDUE</strong> in <strong>{project}</strong>:</p>
    <p><strong>{item}</strong></p>
    <p><strong>Due date was:</strong> {due_date}</p>
    <p>Please update the status or contact your project manager.</p>
    <p>Regards,<br>Project WBS System</p>
    """
    return send_email(to, subject, body)

def email_milestone_complete(to: str, milestone: str, project: str, completed_date: str):
    subject = f"[COMPLETED] Milestone — {milestone} | {project}"
    body = f"""
    <p>Dear Team,</p>
    <p>Milestone <strong>{milestone}</strong> has been successfully completed in <strong>{project}</strong>.</p>
    <p><strong>Completion Date:</strong> {completed_date}</p>
    <p>Great work! Please proceed to the next milestone.</p>
    <p>Regards,<br>Project WBS System</p>
    """
    return send_email(to, subject, body)

def email_due_reminder(to: str, name: str, item: str, project: str, days_left: int, due_date: str):
    subject = f"[REMINDER] {item} due in {days_left} day(s) — {project}"
    body = f"""
    <p>Hi {name},</p>
    <p>This is a reminder that the following item is due soon in <strong>{project}</strong>:</p>
    <p><strong>{item}</strong></p>
    <p><strong>Due Date:</strong> {due_date}</p>
    <p><strong>Days Remaining:</strong> {days_left}</p>
    <p>Please ensure timely completion.</p>
    <p>Regards,<br>Project WBS System</p>
    """
    return send_email(to, subject, body)
