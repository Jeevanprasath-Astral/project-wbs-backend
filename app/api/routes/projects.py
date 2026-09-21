from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.db.database import get_db
from app.models.models import (Project, ProjectMilestone, Milestone, User, ProjectMember, ProjectBilling,
                               CustomMilestone, CustomTask, CustomSubtask, SubtaskQuestion, SubtaskReport,
                               Activity, TaskFormField, MilestoneReport, ProjectReport, WorkHours,
                               SubtaskStatus, Response, Notification, AuditLog, ProjectCost,
                               TaskAssignment, FinancialAuditLog)
from app.schemas.schemas import ProjectCreate, ProjectOut, ProjectUpdate
from app.core.deps import get_current_user
from app.core.permissions import is_team_manager, can_create_project
from app.services.audit_service import log_action
from app.core.security import hash_password
from app.services.email_service import send_email, send_welcome_email, send_mailbox_link_email
import os
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, timezone

router = APIRouter(prefix="/projects", tags=["Projects"])

class AddMemberRequest(BaseModel):
    name: str
    email: str
    role: str
    password: Optional[str] = "wbs123"

class NewUserRequest(BaseModel):
    name: str
    email: str
    role: str
    password: str = "wbs123"

class StatusReportRequest(BaseModel):
    to_emails: str          # comma-separated email addresses
    note: Optional[str] = ""
    completed_this_week: Optional[str] = ""
    plan_next_week: Optional[str] = ""

def _init_project_milestones(db: Session, project: Project):
    milestones = db.query(Milestone).order_by(Milestone.num).all()
    for ms in milestones:
        pm = ProjectMilestone(
            project_id=project.id, milestone_id=ms.id,
            num=ms.num, name=ms.name, status="Not Started", progress=0.0,
        )
        db.add(pm)
    db.flush()

@router.get("", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # All authenticated users see all projects — EXCEPT demo users, who are
    # scoped to is_demo=True projects only so real client data stays hidden.
    q = db.query(Project)
    if getattr(current_user, 'is_demo', False):
        q = q.filter(Project.is_demo == True)
    projects = q.order_by(Project.created_at.desc()).all()

    # ── Compute live progress from CustomMilestone task completion ──────────────
    # Progress = average of each active milestone's completion %.
    # Milestone % = completed_tasks / total_tasks * 100 (100 if status=Completed).
    # This matches exactly what Milestone Config shows per-milestone.
    project_ids = [p.id for p in projects]

    # 1. Bulk-fetch all active CustomMilestones per project (one query)
    active_cms = (
        db.query(CustomMilestone.id, CustomMilestone.project_id, CustomMilestone.status)
        .filter(
            CustomMilestone.project_id.in_(project_ids),
            CustomMilestone.is_active == True,
        )
        .all()
    )
    # Map: project_id → list of (milestone_id, status)
    project_ms_map: dict[int, list[tuple]] = {}
    all_cm_ids = []
    for row in active_cms:
        project_ms_map.setdefault(row.project_id, []).append((row.id, row.status))
        all_cm_ids.append(row.id)

    # 2. Bulk-fetch CustomTask statuses for all those milestones (one query)
    task_status_map: dict[int, list[str]] = {}  # milestone_id → [task statuses]
    if all_cm_ids:
        from app.models.models import CustomTask as _CT
        task_rows = (
            db.query(_CT.milestone_id, _CT.status)
            .filter(_CT.milestone_id.in_(all_cm_ids))
            .all()
        )
        for row in task_rows:
            task_status_map.setdefault(row.milestone_id, []).append(row.status or "Not Started")

    # 3. Compute per-project progress from milestone task completion
    def _ms_pct(ms_id: int, ms_status: str) -> float:
        if ms_status == "Completed":
            return 100.0
        task_statuses = task_status_map.get(ms_id, [])
        if not task_statuses:
            return 0.0
        done = sum(1 for s in task_statuses if s == "Completed")
        return round((done / len(task_statuses)) * 100, 1)

    # Return plain dicts so we can inject the computed progress without
    # mutating the ORM object (which would trigger a dirty-write on commit).
    result = []
    for p in projects:
        ms_list = project_ms_map.get(p.id, [])
        if ms_list:
            pcts = [_ms_pct(ms_id, ms_status) for ms_id, ms_status in ms_list]
            computed = round(sum(pcts) / len(pcts), 1)
        else:
            computed = 0.0
        d = {c.name: getattr(p, c.name) for c in p.__table__.columns}
        d['progress'] = computed
        result.append(d)
    return result

@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not can_create_project(current_user):
        raise HTTPException(403, "Only Admin and Project Manager can create projects")
    project = Project(**payload.model_dump(), created_by=current_user.id, status="Not Started", progress=0.0)
    db.add(project)
    db.flush()
    _init_project_milestones(db, project)
    db.add(ProjectMember(project_id=project.id, user_id=current_user.id, role=current_user.role))
    log_action(db, actor=current_user.name, action="create",
               description=f"Project '{project.name}' created",
               project_id=project.id, entity_type="project",
               entity_id=project.id, user_id=current_user.id)
    db.commit()
    db.refresh(project)
    return project

@router.get("/progress-batch")
def get_projects_progress_batch(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Return [{id, progress}] for ALL projects — 2 queries, no heavy joins.
    Used by My Projects page to enrich the project list with live % values
    without depending on the stored Project.progress column (always 0)."""
    q = db.query(Project.id)
    if getattr(current_user, 'is_demo', False):
        q = q.filter(Project.is_demo == True)
    projects = q.all()
    project_ids = [p.id for p in projects]
    if not project_ids:
        return []

    # 1. All active CustomMilestones (id, project_id, status) across all projects
    active_cms = (
        db.query(CustomMilestone.id, CustomMilestone.project_id, CustomMilestone.status)
        .filter(
            CustomMilestone.project_id.in_(project_ids),
            CustomMilestone.is_active == True,
        )
        .all()
    )
    project_ms_map: dict = {}
    all_cm_ids = []
    for row in active_cms:
        project_ms_map.setdefault(row.project_id, []).append((row.id, row.status))
        all_cm_ids.append(row.id)

    # 2. All CustomTask statuses for those milestones
    task_status_map: dict = {}
    if all_cm_ids:
        task_rows = (
            db.query(CustomTask.milestone_id, CustomTask.status)
            .filter(CustomTask.milestone_id.in_(all_cm_ids))
            .all()
        )
        for row in task_rows:
            task_status_map.setdefault(row.milestone_id, []).append(
                row.status or "Not Started"
            )

    def _ms_pct(ms_id: int, ms_status: str) -> float:
        if ms_status == "Completed":
            return 100.0
        statuses = task_status_map.get(ms_id, [])
        if not statuses:
            return 0.0
        done = sum(1 for s in statuses if s == "Completed")
        return round((done / len(statuses)) * 100, 1)

    result = []
    for pid in project_ids:
        ms_list = project_ms_map.get(pid, [])
        if ms_list:
            pcts = [_ms_pct(ms_id, ms_status) for ms_id, ms_status in ms_list]
            proj_pct = round(sum(pcts) / len(pcts), 1)
        else:
            proj_pct = 0.0
        result.append({"id": pid, "progress": proj_pct})
    return result


@router.get("/{project_id}/milestone-progress")
def get_project_milestone_progress(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return {project_pct, milestones:[{num, status, pct}]} — 2 queries only.
    Lightweight endpoint used by Dashboard, AppLayout (sidebar), and
    any other component that needs live progress without the heavy
    CustomMilestone endpoint (which joins subtasks/activities/form-fields)."""
    active_cms = (
        db.query(CustomMilestone.id, CustomMilestone.num, CustomMilestone.status)
        .filter_by(project_id=project_id, is_active=True)
        .order_by(CustomMilestone.num)
        .all()
    )
    if not active_cms:
        return {"project_pct": 0.0, "milestones": []}

    cm_ids = [row.id for row in active_cms]
    task_rows = (
        db.query(CustomTask.milestone_id, CustomTask.status)
        .filter(CustomTask.milestone_id.in_(cm_ids))
        .all()
    )
    task_map: dict = {}
    for row in task_rows:
        task_map.setdefault(row.milestone_id, []).append(row.status or "Not Started")

    def _pct(cm_id: int, cm_status: str) -> float:
        if cm_status == "Completed":
            return 100.0
        statuses = task_map.get(cm_id, [])
        if not statuses:
            return 0.0
        done = sum(1 for s in statuses if s == "Completed")
        return round((done / len(statuses)) * 100, 1)

    ms_out = [
        {"num": row.num, "status": row.status, "pct": _pct(row.id, row.status)}
        for row in active_cms
    ]
    project_pct = round(sum(m["pct"] for m in ms_out) / len(ms_out), 1) if ms_out else 0.0
    return {"project_pct": project_pct, "milestones": ms_out}


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = db.query(Project).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")

    # ── Compute live progress from CustomMilestone task completion ───────────
    active_cms = (
        db.query(CustomMilestone.id, CustomMilestone.status)
        .filter(CustomMilestone.project_id == project_id, CustomMilestone.is_active == True)
        .all()
    )
    if active_cms:
        cm_ids = [row.id for row in active_cms]
        task_rows = (
            db.query(CustomTask.milestone_id, CustomTask.status)
            .filter(CustomTask.milestone_id.in_(cm_ids))
            .all()
        )
        task_map: dict = {}
        for row in task_rows:
            task_map.setdefault(row.milestone_id, []).append(row.status or "Not Started")

        def _ms_pct(ms_id: int, ms_status: str) -> float:
            if ms_status == "Completed":
                return 100.0
            statuses = task_map.get(ms_id, [])
            if not statuses:
                return 0.0
            done = sum(1 for s in statuses if s == "Completed")
            return round((done / len(statuses)) * 100, 1)

        pcts = [_ms_pct(row.id, row.status) for row in active_cms]
        computed_progress = round(sum(pcts) / len(pcts), 1)
    else:
        computed_progress = 0.0

    # Return as a dict so we can override the stored (stale) progress value
    d = {c.name: getattr(p, c.name) for c in p.__table__.columns}
    d['progress'] = computed_progress
    return d

@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = db.query(Project).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    log_action(db, actor=current_user.name, action="update",
               description="Project updated", project_id=project_id,
               entity_type="project", entity_id=project_id, user_id=current_user.id)
    db.commit()
    db.refresh(p)
    return p

@router.get("/{project_id}/team")
def get_team(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    members = db.query(ProjectMember).filter_by(project_id=project_id).all()
    if not members:
        return []
    # Batch-fetch all users in a single query instead of one per member
    user_ids = [m.user_id for m in members]
    user_map = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    result = []
    for m in members:
        user = user_map.get(m.user_id)
        if user:
            result.append({
                "member_id": m.id,
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": user.role,
                "task_count": 0,
                "is_active": user.is_active,
            })
    return result

@router.get("/{project_id}/all-users")
def get_all_users(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Get all users not already in this project."""
    existing_ids = [m.user_id for m in db.query(ProjectMember).filter_by(project_id=project_id).all()]
    users = db.query(User).filter(User.is_active == True, ~User.id.in_(existing_ids)).all()
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role} for u in users]

@router.post("/{project_id}/team/add-existing")
def add_existing_member(project_id: int, payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Add an existing user to the project."""
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can add team members")
    user_id = payload.get("user_id")
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    existing = db.query(ProjectMember).filter_by(project_id=project_id, user_id=user_id).first()
    if existing:
        raise HTTPException(400, "User already in project")
    db.add(ProjectMember(project_id=project_id, user_id=user_id, role=user.role))
    log_action(db, actor=current_user.name, action="add_member",
               description=f"Added {user.name} to project",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    # Notify the user they've been added to the project
    project = db.query(Project).filter_by(id=project_id).first()
    project_name = project.name if project else f"Project #{project_id}"
    send_email(
        to=user.email,
        subject=f"You've been added to {project_name} — Axon WBS",
        body=f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;">
          <div style="background:linear-gradient(135deg,#091525,#0f2448);padding:24px 32px;text-align:center;border-radius:12px 12px 0 0;">
            <h1 style="color:#fff;font-size:20px;margin:0;letter-spacing:0.04em;">AXON</h1>
            <p style="color:#4a6080;font-size:10px;margin:4px 0 0;">REQUIREMENT &amp; TRACKING SYSTEM</p>
          </div>
          <div style="background:#f8fafc;padding:28px 32px;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 12px 12px;">
            <p style="font-size:15px;color:#0f172a;margin:0 0 12px;">Hi <strong>{user.name}</strong>,</p>
            <p style="font-size:14px;color:#334155;line-height:1.6;margin:0 0 20px;">
              You have been added to the project <strong>{project_name}</strong> on Axon WBS.
              Please log in to access your project dashboard and assigned tasks.
            </p>
            <p style="font-size:13px;color:#94a3b8;margin:0;">Regards,<br>
              <strong style="color:#64748b;">Axon WBS Team</strong></p>
          </div>
        </div>
        """,
    )
    return {"status": "ok", "message": f"{user.name} added to project"}

@router.post("/{project_id}/team/add-new")
def add_new_member(project_id: int, payload: AddMemberRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Create a new user and add them to the project."""
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can add team members")
    if db.query(User).filter_by(email=payload.email).first():
        raise HTTPException(400, "Email already registered")
    user = User(
        name=payload.name, email=payload.email,
        password_hash=hash_password(payload.password or "wbs123"),
        role=payload.role, is_active=True
    )
    db.add(user)
    db.flush()
    db.add(ProjectMember(project_id=project_id, user_id=user.id, role=user.role))
    log_action(db, actor=current_user.name, action="add_member",
               description=f"Created and added {user.name} to project",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    # Send welcome email with credentials + project context
    project = db.query(Project).filter_by(id=project_id).first()
    project_name = project.name if project else f"Project #{project_id}"
    app_url = os.environ.get("FRONTEND_URL", "https://axon-wbs.netlify.app")
    send_welcome_email(
        to=user.email,
        name=user.name,
        temp_password=payload.password or "wbs123",
        app_url=app_url,
    )
    return {"status": "ok", "message": f"{user.name} created and added to project"}

@router.get("/{project_id}/weekly-summary")
def get_weekly_summary(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Auto-detect completed-this-week and upcoming milestones for the status-report modal pre-fill."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ahead = now + timedelta(days=14)

    milestones = (
        db.query(CustomMilestone)
        .filter(CustomMilestone.project_id == project_id, CustomMilestone.is_active == True)
        .order_by(CustomMilestone.num)
        .all()
    )

    completed_lines = []
    upcoming_lines = []

    for ms in milestones:
        # ── Completed this week ────────────────────────────────────────────────
        if ms.status == "Completed" and ms.actual_end:
            ae = ms.actual_end
            if ae.tzinfo is None:
                ae = ae.replace(tzinfo=timezone.utc)
            if ae >= week_ago:
                completed_lines.append(f"• M{ms.num:02d} {ms.name} — completed")

        # ── Upcoming / in-progress ─────────────────────────────────────────────
        if ms.status != "Completed":
            if ms.status == "In Progress":
                line = f"• M{ms.num:02d} {ms.name} (in progress"
                if ms.planned_end:
                    pe = ms.planned_end
                    if pe.tzinfo is None:
                        pe = pe.replace(tzinfo=timezone.utc)
                    line += f", due {pe.strftime('%d %b %Y')}"
                line += ")"
                upcoming_lines.append(line)
            elif ms.planned_end:
                pe = ms.planned_end
                if pe.tzinfo is None:
                    pe = pe.replace(tzinfo=timezone.utc)
                if pe <= two_weeks_ahead:
                    upcoming_lines.append(
                        f"• M{ms.num:02d} {ms.name} — due {pe.strftime('%d %b %Y')}"
                    )

    return {
        "completed_this_week": "\n".join(completed_lines),
        "plan_next_week": "\n".join(upcoming_lines),
    }


def _build_status_report_html(
    project: "Project",
    milestones: list,
    task_map: dict,
    sender_name: str,
    note: str,
    completed_this_week: str,
    plan_next_week: str,
    project_pct: float,
    report_date: str,
) -> str:
    """Build the full HTML for the project status report email."""

    def _ms_pct(ms) -> float:
        if ms.status == "Completed":
            return 100.0
        statuses = task_map.get(ms.id, [])
        if not statuses:
            return 0.0
        done = sum(1 for s in statuses if s == "Completed")
        return round((done / len(statuses)) * 100, 1)

    STATUS_STYLE = {
        "Completed":   ("✅", "#166534", "#dcfce7"),
        "In Progress": ("⚡", "#d97706", "#fef3c7"),
        "Not Started": ("⏸",  "#64748b", "#f1f5f9"),
        "On Hold":     ("⏳", "#c2410c", "#fff7ed"),
    }

    def _status_badge(status: str) -> str:
        icon, color, bg = STATUS_STYLE.get(status, ("⏸", "#64748b", "#f1f5f9"))
        return (
            f'<span style="background:{bg};color:{color};padding:2px 10px;'
            f'border-radius:99px;font-size:11px;font-weight:700;">'
            f'{icon} {status}</span>'
        )

    def _fmt_date(dt) -> str:
        if not dt:
            return "—"
        try:
            return dt.strftime("%d %b %Y")
        except Exception:
            return str(dt)[:10]

    def _bullet_rows(text: str) -> str:
        if not text or not text.strip():
            return '<span style="color:#94a3b8;font-size:11px;">—</span>'
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        return "<br>".join(
            f'<span style="color:#374151;font-size:11px;line-height:1.7;">{l}</span>'
            for l in lines
        )

    # Project summary table rows
    proj_start = _fmt_date(project.start_date) if hasattr(project, 'start_date') and project.start_date else "—"
    proj_end   = _fmt_date(project.end_date)   if hasattr(project, 'end_date')   and project.end_date   else "—"
    proj_client = getattr(project, 'client', '—') or '—'

    # Note section (only if provided)
    note_html = ""
    if note and note.strip():
        note_html = f"""
        <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;
                    padding:10px 14px;margin-bottom:16px;font-size:13px;color:#92400e;">
          <strong>Note from {sender_name}:</strong> {note.strip()}
        </div>"""

    # Weekly update cards (only if content provided)
    weekly_html = ""
    has_completed = bool(completed_this_week and completed_this_week.strip())
    has_plan = bool(plan_next_week and plan_next_week.strip())
    if has_completed or has_plan:
        completed_col = f"""
        <td style="width:50%;vertical-align:top;padding-right:5px;">
          <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
                      padding:10px 12px;height:100%;box-sizing:border-box;">
            <div style="font-size:12px;font-weight:700;color:#166534;margin-bottom:6px;">
              ✅ Completed This Week
            </div>
            {_bullet_rows(completed_this_week)}
          </div>
        </td>""" if has_completed else ""

        plan_col = f"""
        <td style="width:50%;vertical-align:top;padding-left:5px;">
          <div style="background:#eff6ff;border:1px solid #93c5fd;border-radius:8px;
                      padding:10px 12px;height:100%;box-sizing:border-box;">
            <div style="font-size:12px;font-weight:700;color:#1d4ed8;margin-bottom:6px;">
              📅 Plan for Next Week
            </div>
            {_bullet_rows(plan_next_week)}
          </div>
        </td>""" if has_plan else ""

        weekly_html = f"""
        <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
          <tr>{completed_col}{plan_col}</tr>
        </table>"""

    # Milestone rows
    ms_rows = ""
    for ms in milestones:
        pct = _ms_pct(ms)
        ms_rows += f"""
        <tr>
          <td style="padding:4px 8px;border:1px solid #e2e8f0;color:#475569;font-size:11px;white-space:nowrap;">
            M{ms.num:02d}
          </td>
          <td style="padding:4px 8px;border:1px solid #e2e8f0;color:#334155;font-size:11px;">
            {ms.name or "—"}
          </td>
          <td style="padding:4px 8px;border:1px solid #e2e8f0;font-size:11px;">
            {_status_badge(ms.status or "Not Started")}
          </td>
          <td style="padding:4px 8px;border:1px solid #e2e8f0;color:#475569;font-size:11px;white-space:nowrap;">
            {_fmt_date(ms.planned_end)}
          </td>
          <td style="padding:4px 8px;border:1px solid #e2e8f0;color:#475569;font-size:11px;white-space:nowrap;">
            {_fmt_date(ms.actual_end)}
          </td>
          <td style="padding:4px 8px;border:1px solid #e2e8f0;color:#475569;font-size:11px;text-align:center;">
            {pct:.0f}%
          </td>
        </tr>"""

    return f"""
<div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;">

  <!-- Header -->
  <div style="background:#091525;padding:22px 28px;text-align:center;
              border-radius:12px 12px 0 0;">
    <h1 style="color:#fff;font-size:22px;margin:0;letter-spacing:.04em;">AXON WBS</h1>
    <p style="color:#4a6080;font-size:11px;margin:5px 0 0;letter-spacing:.08em;">
      PROJECT STATUS REPORT
    </p>
  </div>

  <!-- Body -->
  <div style="padding:22px 28px;background:#f8fafc;border:1px solid #e2e8f0;
              border-top:0;border-radius:0 0 12px 12px;">

    {note_html}

    <!-- Project summary -->
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:12px;">
      <tr>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;font-weight:bold;
                   background:#f1f5f9;width:32%;color:#334155;">Project</td>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;color:#475569;">
          {project.name}
        </td>
      </tr>
      <tr>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;font-weight:bold;
                   background:#f1f5f9;color:#334155;">Client</td>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;color:#475569;">
          {proj_client}
        </td>
      </tr>
      <tr>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;font-weight:bold;
                   background:#f1f5f9;color:#334155;">Overall Status</td>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;">
          {_status_badge(project.status or "Not Started")}
          &nbsp;<span style="color:#475569;font-size:12px;">{project_pct:.0f}% complete</span>
        </td>
      </tr>
      <tr>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;font-weight:bold;
                   background:#f1f5f9;color:#334155;">Timeline</td>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;color:#475569;">
          {proj_start} → {proj_end}
        </td>
      </tr>
      <tr>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;font-weight:bold;
                   background:#f1f5f9;color:#334155;">Report Date</td>
        <td style="padding:5px 8px;border:1px solid #e2e8f0;color:#475569;">
          {report_date}
        </td>
      </tr>
    </table>

    {weekly_html}

    <!-- Milestone overview -->
    <h3 style="font-size:13px;color:#334155;margin:14px 0 6px;font-weight:700;">
      Milestone Overview
    </h3>
    <table style="width:100%;border-collapse:collapse;font-size:12px;">
      <tr style="background:#f1f5f9;">
        <th style="padding:5px 8px;border:1px solid #e2e8f0;text-align:left;
                   color:#475569;font-size:11px;">#</th>
        <th style="padding:5px 8px;border:1px solid #e2e8f0;text-align:left;
                   color:#475569;font-size:11px;">Milestone</th>
        <th style="padding:5px 8px;border:1px solid #e2e8f0;text-align:left;
                   color:#475569;font-size:11px;">Status</th>
        <th style="padding:5px 8px;border:1px solid #e2e8f0;text-align:left;
                   color:#475569;font-size:11px;">Planned End</th>
        <th style="padding:5px 8px;border:1px solid #e2e8f0;text-align:left;
                   color:#475569;font-size:11px;">Actual End</th>
        <th style="padding:5px 8px;border:1px solid #e2e8f0;text-align:center;
                   color:#475569;font-size:11px;">Done</th>
      </tr>
      {ms_rows}
    </table>

    <p style="font-size:11px;color:#94a3b8;margin-top:20px;text-align:center;">
      Axon WBS &nbsp;·&nbsp; by Connectome
    </p>
  </div>
</div>"""


@router.post("/{project_id}/send-status-report")
def send_status_report(
    project_id: int,
    payload: StatusReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Build and send a project status report email to the client.
    Pure HTML email — no attachment — works on Brevo Free plan."""

    # ── Fetch project ──────────────────────────────────────────────────────────
    project = db.query(Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(404, "Project not found")

    # ── Compute live progress ─────────────────────────────────────────────────
    active_cms = (
        db.query(CustomMilestone)
        .filter(CustomMilestone.project_id == project_id, CustomMilestone.is_active == True)
        .order_by(CustomMilestone.num)
        .all()
    )
    cm_ids = [ms.id for ms in active_cms]
    task_map: dict = {}
    if cm_ids:
        task_rows = (
            db.query(CustomTask.milestone_id, CustomTask.status)
            .filter(CustomTask.milestone_id.in_(cm_ids))
            .all()
        )
        for row in task_rows:
            task_map.setdefault(row.milestone_id, []).append(row.status or "Not Started")

    def _ms_pct(ms) -> float:
        if ms.status == "Completed":
            return 100.0
        statuses = task_map.get(ms.id, [])
        if not statuses:
            return 0.0
        done = sum(1 for s in statuses if s == "Completed")
        return round((done / len(statuses)) * 100, 1)

    pcts = [_ms_pct(ms) for ms in active_cms]
    project_pct = round(sum(pcts) / len(pcts), 1) if pcts else 0.0

    # ── Parse recipient list ──────────────────────────────────────────────────
    to_list = [e.strip() for e in payload.to_emails.split(",") if e.strip()]
    if not to_list:
        raise HTTPException(400, "No valid recipient email addresses provided")

    # ── Build email ───────────────────────────────────────────────────────────
    report_date = datetime.now(timezone.utc).strftime("%d %b %Y")
    body = _build_status_report_html(
        project=project,
        milestones=active_cms,
        task_map=task_map,
        sender_name=current_user.name,
        note=payload.note or "",
        completed_this_week=payload.completed_this_week or "",
        plan_next_week=payload.plan_next_week or "",
        project_pct=project_pct,
        report_date=report_date,
    )

    subject = f"Project Status Report — {project.name} ({report_date})"
    sent = send_mailbox_link_email(to_list=to_list, subject=subject, body=body)
    if not sent:
        raise HTTPException(500, "Failed to send email. Check server logs.")

    log_action(
        db, actor=current_user.name, action="send_status_report",
        description=f"Status report sent for '{project.name}' to {payload.to_emails}",
        project_id=project_id, entity_type="project",
        entity_id=project_id, user_id=current_user.id,
    )
    db.commit()
    return {"status": "ok", "message": f"Status report sent to {len(to_list)} recipient(s)"}


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Delete a project and all its related data (Admin only).

    Explicitly pre-deletes every child table in dependency order (leaf tables
    first) so PostgreSQL FK constraints are never violated.  We bypass SQLAlchemy
    ORM cascade entirely — mixing ORM cascade with synchronize_session=False
    bulk-deletes causes the session's identity map to go stale, which can leave
    cascade-only tables partially un-deleted and trigger FK violations.
    """
    if current_user.role != "Admin":
        raise HTTPException(403, "Only Admin can delete projects")
    p = db.query(Project).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")

    project_name = p.name   # capture before the row is gone

    try:
        # ── LEAF TABLES (deepest FKs first) ──────────────────────────────────

        # Standard milestone responses (project_id NOT NULL, not ORM-cascaded)
        db.query(Response).filter_by(project_id=project_id).delete(synchronize_session=False)

        # SubtaskStatus links both project_id (NOT NULL) and project_milestone_id (NOT NULL).
        # Must be deleted before project_milestones.
        db.query(SubtaskStatus).filter_by(project_id=project_id).delete(synchronize_session=False)

        # Billing entries
        db.query(ProjectBilling).filter_by(project_id=project_id).delete(synchronize_session=False)

        # ── CUSTOM MILESTONE TREE ─────────────────────────────────────────────
        # Deepest level first so FK constraints are always satisfied.
        db.query(Activity).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(SubtaskReport).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(SubtaskQuestion).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(TaskFormField).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(CustomSubtask).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(MilestoneReport).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(CustomTask).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(CustomMilestone).filter_by(project_id=project_id).delete(synchronize_session=False)

        # DA project-level reports
        db.query(ProjectReport).filter_by(project_id=project_id).delete(synchronize_session=False)

        # Standard project milestones (after SubtaskStatus cleared above)
        db.query(ProjectMilestone).filter_by(project_id=project_id).delete(synchronize_session=False)

        # ── DIRECT PROJECT CHILDREN ───────────────────────────────────────────
        db.query(ProjectMember).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(ProjectCost).filter_by(project_id=project_id).delete(synchronize_session=False)
        db.query(Notification).filter_by(project_id=project_id).delete(synchronize_session=False)
        # Delete all audit log entries for this project (history gone with the project).
        db.query(AuditLog).filter_by(project_id=project_id).delete(synchronize_session=False)

        # ── NULLABLE FK TABLES — set project_id to NULL (preserve history) ───
        # work_hours: NULL out project_id + all milestone-level FKs
        db.query(WorkHours).filter_by(project_id=project_id).update(
            {"project_id": None, "custom_milestone_id": None,
             "custom_task_id": None, "custom_subtask_id": None,
             "milestone_report_id": None},
            synchronize_session=False
        )
        # task_assignments: unlink from project, keep the assignment record
        db.query(TaskAssignment).filter(
            TaskAssignment.project_id == project_id
        ).update({"project_id": None}, synchronize_session=False)
        # financial audit log: keep billing history, just remove project link
        db.query(FinancialAuditLog).filter_by(project_id=project_id).update(
            {"project_id": None}, synchronize_session=False
        )

        # ── AUDIT LOG FOR THIS DELETE (project_id=None — project no longer exists) ──
        log_action(db, actor=current_user.name, action="delete",
                   description=f"Project '{project_name}' (id={project_id}) deleted",
                   project_id=None, entity_type="project",
                   entity_id=project_id, user_id=current_user.id)

        # ── FINALLY: DELETE THE PROJECT ROW ──────────────────────────────────
        db.query(Project).filter_by(id=project_id).delete(synchronize_session=False)
        db.commit()

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}")

    return {"status": "ok", "message": f"Project '{project_name}' deleted"}


@router.delete("/{project_id}/team/{member_id}")
def remove_member(project_id: int, member_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remove a member from the project."""
    if not is_team_manager(current_user):
        raise HTTPException(403, "Only Admin or HR can remove team members")
    member = db.query(ProjectMember).filter_by(id=member_id, project_id=project_id).first()
    if not member:
        raise HTTPException(404, "Member not found")
    user = db.query(User).filter_by(id=member.user_id).first()
    db.delete(member)
    log_action(db, actor=current_user.name, action="remove_member",
               description=f"Removed {user.name if user else 'user'} from project",
               project_id=project_id, user_id=current_user.id)
    db.commit()
    return {"status": "ok"}
