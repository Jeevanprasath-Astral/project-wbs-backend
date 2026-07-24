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
from app.services.email_service import send_email, send_welcome_email
import os
from pydantic import BaseModel
from typing import Optional

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
    # All authenticated users see all projects.
    # Role-based permissions within each project are enforced at the action level.
    return db.query(Project).order_by(Project.created_at.desc()).all()

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

@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    p = db.query(Project).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    return p

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
