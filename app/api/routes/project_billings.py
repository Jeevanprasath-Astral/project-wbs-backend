"""Project Billing History — Admin-managed CRUD.

Each project can have multiple billing entries (Milestone Payment,
Change Request, Due Payment, etc.). The profitability report uses
SUM(amount) across all entries as the project's Revenue figure,
replacing the old single billing_amount column on Project.

Endpoints:
  GET  /project-billings/billing-types            list billing type options
  GET  /project-billings/{project_id}             list entries for a project
  GET  /project-billings/{project_id}/milestones  list milestones for dropdown
  POST /project-billings/{project_id}             create entry  [Admin only]
  PATCH /project-billings/entry/{entry_id}        update entry  [Admin only]
  DELETE /project-billings/entry/{entry_id}       delete entry  [Admin only]
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import date as date_type
from pydantic import BaseModel

from app.db.database import get_db
from app.models.models import ProjectBilling, Project, CustomMilestone, User
from app.core.deps import get_current_user

router = APIRouter(prefix="/project-billings", tags=["Project Billings"])

BILLING_TYPES = [
    "Milestone Payment",
    "New Requirements",
    "Change Request",
    "Due Payment",
    "Overtime Charges",
    "Additional Scope",
    "Miscellaneous",
]


# ── Schemas ──────────────────────────────────────────────────────────────────
class BillingCreate(BaseModel):
    date:         date_type
    amount:       float
    billing_type: Optional[str] = None
    description:  Optional[str] = None
    milestone_id: Optional[int] = None
    remarks:      Optional[str] = None


class BillingUpdate(BaseModel):
    date:         Optional[date_type] = None
    amount:       Optional[float]     = None
    billing_type: Optional[str]       = None
    description:  Optional[str]       = None
    milestone_id: Optional[int]       = None
    remarks:      Optional[str]       = None


def _require_admin(user: User):
    if user.role not in ("Admin", "HR"):
        raise HTTPException(403, "Admin or HR only")


def _billing_out(b: ProjectBilling, milestone_name: Optional[str] = None):
    return {
        "id":             b.id,
        "project_id":     b.project_id,
        "date":           str(b.date) if b.date else None,
        "amount":         float(b.amount or 0),
        "billing_type":   b.billing_type,
        "description":    b.description,
        "milestone_id":   b.milestone_id,
        "milestone_name": milestone_name,
        "remarks":        b.remarks,
        "created_at":     str(b.created_at) if b.created_at else None,
    }


def _resolve_milestone_name(db: Session, milestone_id: Optional[int]) -> Optional[str]:
    if not milestone_id:
        return None
    m = db.query(CustomMilestone.name).filter_by(id=milestone_id).first()
    return m.name if m else None


# ── Static / type routes (must be defined before /{project_id}) ──────────────

@router.get("/billing-types")
def get_billing_types(current_user: User = Depends(get_current_user)):
    return BILLING_TYPES


@router.patch("/entry/{entry_id}")
def update_billing(
    entry_id: int,
    payload: BillingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    b = db.query(ProjectBilling).filter_by(id=entry_id).first()
    if not b:
        raise HTTPException(404, "Billing entry not found")

    if payload.date is not None:         b.date         = payload.date
    if payload.amount is not None:       b.amount       = payload.amount
    if payload.billing_type is not None: b.billing_type = payload.billing_type
    if payload.description is not None:  b.description  = payload.description
    # milestone_id is always updated — frontend always submits current value;
    # sending None explicitly clears the link.
    b.milestone_id = payload.milestone_id
    if payload.remarks is not None:      b.remarks      = payload.remarks

    db.commit(); db.refresh(b)
    return _billing_out(b, _resolve_milestone_name(db, b.milestone_id))


@router.delete("/entry/{entry_id}", status_code=204)
def delete_billing(
    entry_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    b = db.query(ProjectBilling).filter_by(id=entry_id).first()
    if not b:
        raise HTTPException(404, "Billing entry not found")
    db.delete(b); db.commit()
    return None


# ── Project-scoped routes ─────────────────────────────────────────────────────

@router.get("/{project_id}/milestones")
def list_project_milestones(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(CustomMilestone)
        .filter(CustomMilestone.project_id == project_id,
                CustomMilestone.is_active == True)
        .order_by(CustomMilestone.num)
        .all()
    )
    return [{"id": m.id, "name": m.name, "num": m.num} for m in rows]


@router.get("/{project_id}")
def list_billings(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(ProjectBilling)
        .filter(ProjectBilling.project_id == project_id)
        .order_by(ProjectBilling.date.desc())
        .all()
    )
    return [_billing_out(b, _resolve_milestone_name(db, b.milestone_id)) for b in rows]


@router.post("/{project_id}", status_code=201)
def create_billing(
    project_id: int,
    payload: BillingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    proj = db.query(Project).filter_by(id=project_id).first()
    if not proj:
        raise HTTPException(404, "Project not found")

    b = ProjectBilling(
        project_id   = project_id,
        date         = payload.date,
        amount       = payload.amount,
        billing_type = payload.billing_type,
        description  = payload.description,
        milestone_id = payload.milestone_id,
        remarks      = payload.remarks,
        created_by   = current_user.id,
    )
    db.add(b); db.commit(); db.refresh(b)
    return _billing_out(b, _resolve_milestone_name(db, b.milestone_id))
