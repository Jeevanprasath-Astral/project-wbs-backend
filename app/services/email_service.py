import urllib.request as _urllib
import json as _json
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def send_email(to: str, subject: str, body: str) -> bool:
    if not settings.MAIL_ENABLED:
        logger.info(f"[EMAIL DISABLED] To: {to} | Subject: {subject}")
        return False
    if not settings.RESEND_API_KEY:
        logger.error("RESEND_API_KEY is not set — email not sent")
        return False
    try:
        payload = _json.dumps({
            "from": settings.MAIL_FROM,
            "to": [to],
            "subject": subject,
            "html": body
        }).encode("utf-8")
        req = _urllib.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json"
            }
        )
        with _urllib.urlopen(req, timeout=15) as resp:
            logger.info(f"Email sent to {to}: {subject} (HTTP {resp.status})")
        return True
    except _urllib.error.HTTPError as http_err:
        err_body = http_err.read().decode("utf-8", errors="replace")
        logger.error(f"Email failed to {to}: HTTP {http_err.code} — {err_body}")
        return False
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

def send_password_reset_email(to: str, name: str, reset_link: str):
    subject = "Reset your Axon WBS password"
    body = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;background:#f8fafc;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;">
      <div style="background:linear-gradient(135deg,#091525,#0f2448);padding:32px 36px;text-align:center;">
        <h1 style="color:#fff;font-size:22px;margin:0;letter-spacing:0.04em;">AXON</h1>
        <p style="color:#4a6080;font-size:11px;margin:4px 0 0;letter-spacing:0.08em;">REQUIREMENT &amp; TRACKING SYSTEM</p>
      </div>
      <div style="padding:36px;">
        <p style="font-size:15px;color:#0f172a;margin:0 0 12px;">Hi <strong>{name}</strong>,</p>
        <p style="font-size:14px;color:#334155;margin:0 0 24px;line-height:1.6;">
          We received a request to reset your password for <strong>Axon WBS</strong>.
          Click the button below to set a new password.
        </p>
        <div style="text-align:center;margin:28px 0;">
          <a href="{reset_link}"
             style="display:inline-block;background:linear-gradient(135deg,#1d6ec6,#0d3e7a);
                    color:#fff;font-size:15px;font-weight:700;padding:14px 32px;
                    border-radius:10px;text-decoration:none;letter-spacing:0.01em;">
            Reset My Password
          </a>
        </div>
        <p style="font-size:12px;color:#64748b;margin:0 0 8px;">Or copy this link into your browser:</p>
        <p style="font-size:11px;color:#1d6ec6;word-break:break-all;background:#f1f5f9;
                  padding:10px 12px;border-radius:6px;margin:0 0 24px;">{reset_link}</p>
        <div style="background:#fef9ec;border:1px solid #fde68a;border-radius:8px;padding:12px 14px;margin-bottom:24px;">
          <p style="font-size:12px;color:#92400e;margin:0;">
            <strong>This link expires in 15 minutes.</strong>
            If you did not request a password reset, please ignore this email.
            Your password will remain unchanged.
          </p>
        </div>
        <p style="font-size:13px;color:#94a3b8;margin:0;">Regards,<br><strong style="color:#64748b;">Axon WBS Team</strong><br>
        <span style="font-size:11px;">by Connectome</span></p>
      </div>
    </div>
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
