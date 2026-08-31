"""
Role Access Control — configurable permission matrix stored in DB.
Endpoints:
  GET  /role-permissions              → full matrix (all roles × modules)
  GET  /role-permissions/{role}       → single role's permissions
  PUT  /role-permissions/{role}/{module}  → update one cell
  POST /role-permissions/reset/{role}    → reset role to factory defaults (Admin only)

Only Admin, Project Manager, and HR can call these endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.models import RolePermission
from app.core.deps import get_current_user
from app.models.models import User
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/role-permissions", tags=["Role Permissions"])

# ── Roles allowed to manage permissions ──────────────────────────────────────
_MANAGE_ROLES = {"Admin", "Project Manager", "HR"}

# ── All modules in the system ─────────────────────────────────────────────────
ALL_MODULES = [
    {"key": "proposals",          "label": "📋 Proposal Estimates"},
    {"key": "financial_settings", "label": "💰 Financial Settings"},
    {"key": "global_assignments", "label": "📌 Task Assignments"},
    {"key": "milestones",         "label": "🏁 Milestone Config"},
    {"key": "timesheet",          "label": "🗓️ Timesheet Calendar"},
    {"key": "reports",            "label": "📊 Reports"},
    {"key": "audit_log",          "label": "🔍 Audit Log"},
    {"key": "team_hub",           "label": "🤝 Team Hub"},
    {"key": "projects",           "label": "🗂️ Projects"},
    {"key": "working_hours",      "label": "⏱️ Working Hours"},
]

# ── Default permissions seed (role → module → actions) ───────────────────────
# Admin is always full — enforced in code, not just data.
DEFAULTS = [
    # role,                   module,               view,  create, edit,  delete
    ("Admin",                 "proposals",           True,  True,  True,  True),
    ("Admin",                 "financial_settings",  True,  True,  True,  True),
    ("Admin",                 "global_assignments",  True,  True,  True,  True),
    ("Admin",                 "milestones",          True,  True,  True,  True),
    ("Admin",                 "timesheet",           True,  True,  True,  True),
    ("Admin",                 "reports",             True,  True,  True,  True),
    ("Admin",                 "audit_log",           True,  False, False, False),
    ("Admin",                 "team_hub",            True,  True,  True,  True),
    ("Admin",                 "projects",            True,  True,  True,  True),
    ("Admin",                 "working_hours",       True,  True,  True,  True),

    ("Project Manager",       "proposals",           True,  True,  True,  True),
    ("Project Manager",       "financial_settings",  True,  True,  True,  False),
    ("Project Manager",       "global_assignments",  True,  True,  True,  True),
    ("Project Manager",       "milestones",          True,  True,  True,  True),
    ("Project Manager",       "timesheet",           True,  True,  True,  False),
    ("Project Manager",       "reports",             True,  False, False, False),
    ("Project Manager",       "audit_log",           True,  False, False, False),
    ("Project Manager",       "team_hub",            True,  True,  True,  False),
    ("Project Manager",       "projects",            True,  True,  True,  False),
    ("Project Manager",       "working_hours",       True,  True,  True,  False),

    ("FC Lead",               "proposals",           True,  True,  True,  False),
    ("FC Lead",               "financial_settings",  True,  True,  True,  False),
    ("FC Lead",               "global_assignments",  True,  True,  True,  True),
    ("FC Lead",               "milestones",          True,  True,  True,  True),
    ("FC Lead",               "timesheet",           True,  False, False, False),
    ("FC Lead",               "reports",             True,  False, False, False),
    ("FC Lead",               "audit_log",           True,  False, False, False),
    ("FC Lead",               "team_hub",            True,  False, False, False),
    ("FC Lead",               "projects",            True,  True,  True,  False),
    ("FC Lead",               "working_hours",       True,  False, False, False),

    ("TC Lead",               "proposals",           True,  True,  True,  False),
    ("TC Lead",               "financial_settings",  True,  True,  True,  False),
    ("TC Lead",               "global_assignments",  True,  True,  True,  True),
    ("TC Lead",               "milestones",          True,  True,  True,  True),
    ("TC Lead",               "timesheet",           True,  False, False, False),
    ("TC Lead",               "reports",             True,  False, False, False),
    ("TC Lead",               "audit_log",           True,  False, False, False),
    ("TC Lead",               "team_hub",            True,  False, False, False),
    ("TC Lead",               "projects",            True,  True,  True,  False),
    ("TC Lead",               "working_hours",       True,  False, False, False),

    ("BD",                    "proposals",           True,  True,  True,  False),
    ("BD",                    "financial_settings",  False, False, False, False),
    ("BD",                    "global_assignments",  True,  True,  True,  False),
    ("BD",                    "milestones",          True,  True,  True,  False),
    ("BD",                    "timesheet",           True,  True,  True,  False),
    ("BD",                    "reports",             True,  False, False, False),
    ("BD",                    "audit_log",           False, False, False, False),
    ("BD",                    "team_hub",            True,  False, False, False),
    ("BD",                    "projects",            True,  False, False, False),
    ("BD",                    "working_hours",       True,  True,  False, False),

    ("HR",                    "proposals",           False, False, False, False),
    ("HR",                    "financial_settings",  True,  True,  True,  False),
    ("HR",                    "global_assignments",  False, False, False, False),
    ("HR",                    "milestones",          False, False, False, False),
    ("HR",                    "timesheet",           True,  True,  True,  True),
    ("HR",                    "reports",             True,  False, False, False),
    ("HR",                    "audit_log",           False, False, False, False),
    ("HR",                    "team_hub",            True,  True,  True,  True),
    ("HR",                    "projects",            False, False, False, False),
    ("HR",                    "working_hours",       True,  True,  True,  False),

    ("Associate Data Analyst","proposals",           True,  False, False, False),
    ("Associate Data Analyst","financial_settings",  False, False, False, False),
    ("Associate Data Analyst","global_assignments",  True,  True,  True,  False),
    ("Associate Data Analyst","milestones",          True,  True,  True,  False),
    ("Associate Data Analyst","timesheet",           True,  True,  False, False),
    ("Associate Data Analyst","reports",             True,  False, False, False),
    ("Associate Data Analyst","audit_log",           True,  False, False, False),
    ("Associate Data Analyst","team_hub",            True,  False, False, False),
    ("Associate Data Analyst","projects",            True,  False, False, False),
    ("Associate Data Analyst","working_hours",       True,  True,  False, False),

    ("Associate",             "proposals",           True,  False, False, False),
    ("Associate",             "financial_settings",  False, False, False, False),
    ("Associate",             "global_assignments",  True,  True,  False, False),
    ("Associate",             "milestones",          True,  True,  True,  False),
    ("Associate",             "timesheet",           True,  True,  False, False),
    ("Associate",             "reports",             True,  False, False, False),
    ("Associate",             "audit_log",           False, False, False, False),
    ("Associate",             "team_hub",            True,  False, False, False),
    ("Associate",             "projects",            True,  False, False, False),
    ("Associate",             "working_hours",       True,  True,  False, False),

    ("Client",                "proposals",           False, False, False, False),
    ("Client",                "financial_settings",  False, False, False, False),
    ("Client",                "global_assignments",  False, False, False, False),
    ("Client",                "milestones",          True,  False, False, False),
    ("Client",                "timesheet",           False, False, False, False),
    ("Client",                "reports",             True,  False, False, False),
    ("Client",                "audit_log",           False, False, False, False),
    ("Client",                "team_hub",            False, False, False, False),
    ("Client",                "projects",            True,  False, False, False),
    ("Client",                "working_hours",       False, False, False, False),

    ("Functional Consultant", "proposals",           True,  False, False, False),
    ("Functional Consultant", "financial_settings",  False, False, False, False),
    ("Functional Consultant", "global_assignments",  True,  True,  False, False),
    ("Functional Consultant", "milestones",          True,  True,  True,  False),
    ("Functional Consultant", "timesheet",           True,  True,  False, False),
    ("Functional Consultant", "reports",             True,  False, False, False),
    ("Functional Consultant", "audit_log",           False, False, False, False),
    ("Functional Consultant", "team_hub",            True,  False, False, False),
    ("Functional Consultant", "projects",            True,  False, False, False),
    ("Functional Consultant", "working_hours",       True,  True,  False, False),

    ("Technical Team",        "proposals",           True,  False, False, False),
    ("Technical Team",        "financial_settings",  False, False, False, False),
    ("Technical Team",        "global_assignments",  True,  True,  False, False),
    ("Technical Team",        "milestones",          True,  True,  True,  False),
    ("Technical Team",        "timesheet",           True,  True,  False, False),
    ("Technical Team",        "reports",             True,  False, False, False),
    ("Technical Team",        "audit_log",           False, False, False, False),
    ("Technical Team",        "team_hub",            True,  False, False, False),
    ("Technical Team",        "projects",            True,  False, False, False),
    ("Technical Team",        "working_hours",       True,  True,  False, False),
]


def seed_defaults(db: Session):
    """Seed role_permissions with defaults — only inserts missing rows, never overwrites."""
    existing = {(r.role, r.module) for r in db.query(RolePermission.role, RolePermission.module).all()}
    to_insert = []
    for role, module, view, create, edit, delete in DEFAULTS:
        if (role, module) not in existing:
            to_insert.append(RolePermission(
                role=role, module=module,
                can_view=view, can_create=create, can_edit=edit, can_delete=delete,
            ))
    if to_insert:
        db.bulk_save_objects(to_insert)
        db.commit()


def get_permissions_for_role(db: Session, role: str) -> dict:
    """Return {module: {view,create,edit,delete}} for the given role.
    Admin always returns full access regardless of DB."""
    if role == "Admin":
        return {m["key"]: {"view": True, "create": True, "edit": True, "delete": True}
                for m in ALL_MODULES}
    rows = db.query(RolePermission).filter(RolePermission.role == role).all()
    result = {}
    for r in rows:
        result[r.module] = {
            "view":   r.can_view,
            "create": r.can_create,
            "edit":   r.can_edit,
            "delete": r.can_delete,
        }
    # Fill any missing modules with no-access
    for m in ALL_MODULES:
        if m["key"] not in result:
            result[m["key"]] = {"view": False, "create": False, "edit": False, "delete": False}
    return result


def _require_manage(user: User):
    if user.role not in _MANAGE_ROLES:
        raise HTTPException(403, "Only Admin, Project Manager, or HR can manage role permissions")


# ── Pydantic ─────────────────────────────────────────────────────────────────
class PermissionUpdate(BaseModel):
    can_view:   Optional[bool] = None
    can_create: Optional[bool] = None
    can_edit:   Optional[bool] = None
    can_delete: Optional[bool] = None


# ── Routes ───────────────────────────────────────────────────────────────────
@router.get("")
def get_all_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Full matrix — all roles × all modules."""
    _require_manage(current_user)
    from app.core.permissions import ALL_ROLES, LEGACY_ROLES
    all_roles = ALL_ROLES + LEGACY_ROLES
    matrix = {}
    for role in all_roles:
        matrix[role] = get_permissions_for_role(db, role)
    return {"modules": ALL_MODULES, "matrix": matrix}


@router.get("/{role}")
def get_role_permissions(
    role: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Single role's permission map."""
    _require_manage(current_user)
    return get_permissions_for_role(db, role)


@router.put("/{role}/{module}")
def update_permission(
    role: str,
    module: str,
    payload: PermissionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle one permission cell. Admin role is locked — cannot be reduced."""
    _require_manage(current_user)
    if role == "Admin":
        raise HTTPException(400, "Admin permissions cannot be modified")
    valid_modules = {m["key"] for m in ALL_MODULES}
    if module not in valid_modules:
        raise HTTPException(400, f"Unknown module: {module}")

    row = db.query(RolePermission).filter_by(role=role, module=module).first()
    if not row:
        # Create with defaults (no access) then apply update
        row = RolePermission(role=role, module=module)
        db.add(row)

    if payload.can_view   is not None: row.can_view   = payload.can_view
    if payload.can_create is not None: row.can_create = payload.can_create
    if payload.can_edit   is not None: row.can_edit   = payload.can_edit
    if payload.can_delete is not None: row.can_delete = payload.can_delete
    db.commit()
    db.refresh(row)
    return {
        "role": role, "module": module,
        "view": row.can_view, "create": row.can_create,
        "edit": row.can_edit, "delete": row.can_delete,
    }


@router.post("/reset/{role}")
def reset_role_permissions(
    role: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reset a role's permissions back to factory defaults (Admin only)."""
    if current_user.role != "Admin":
        raise HTTPException(403, "Only Admin can reset role permissions")
    if role == "Admin":
        raise HTTPException(400, "Admin permissions cannot be reset")

    defaults_for_role = {
        (r, m): (v, c, e, d) for r, m, v, c, e, d in DEFAULTS if r == role
    }
    rows = db.query(RolePermission).filter(RolePermission.role == role).all()
    for row in rows:
        key = (role, row.module)
        if key in defaults_for_role:
            v, c, e, d = defaults_for_role[key]
            row.can_view, row.can_create, row.can_edit, row.can_delete = v, c, e, d
    db.commit()
    return {"message": f"Permissions for '{role}' reset to defaults."}
