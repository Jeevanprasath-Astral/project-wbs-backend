"""Project Billing History — Admin-managed CRUD.

Each project can have multiple billing entries (Milestone Payment,
Change Request, Due Payment, etc.). The profitability report uses
SUM(planned_billing_amount / actual_billing_amount) as revenue.

Planned Billing Date is DERIVED from the linked milestone's planned_end
and is never stored or entered manually — it's always in sync with the
milestone.

Endpoints:
  GET  /project-billings/billing-types            list billing type options
  GET  /project-billings/{project_id}             list entries for a project
  GET  /project-billings/{project_id}/milestones  list milestones (+ planned_end) for dropdown
  POST /project-billings/{project_id}             create entry  [Admin / HR only]
  PATCH /project-billings/entry/{entry_id}        update entry  [Admin / HR only]
  DELETE /project-billings/entry/{entry_id}       delete entry  [Admin / HR only]
"""
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date as date_type
from pydantic import BaseModel

from app.db.database import get_db
from app.models.models import (
    ProjectBilling, FinancialAuditLog, Project, CustomMilestone, User,
)
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
    planned_billing_amount: float
    actual_billing_date:    Optional[date_type] = None
    actual_billing_amount:  Optional[float]     = None
    billing_type:           Optional[str]       = None
    description:            Optional[str]       = None
    milestone_id:           Optional[int]       = None
    remarks:                Optional[str]       = None


class BillingUpdate(BaseModel):
    planned_billing_amount: Optional[float]     = None
    actual_billing_date:    Optional[date_type] = None
    actual_billing_amount:  Optional[float]     = None
    billing_type:           Optional[str]       = None
    description:            Optional[str]       = None
    milestone_id:           Optional[int]       = None
    remarks:                Optional[str]       = None


def _require_admin(user: User):
    if user.role not in ("Admin", "HR"):
        raise HTTPException(403, "Admin or HR only")


def _resolve_milestone(db: Session, milestone_id: Optional[int]):
    """Returns (name, planned_billing_date_str) for a milestone, or (None, None).
    planned_end is a DateTime column — we extract just the date part (YYYY-MM-DD)."""
    if not milestone_id:
        return None, None
    m = db.query(CustomMilestone).filter_by(id=milestone_id).first()
    if not m:
        return None, None
    pbd = m.planned_end.strftime('%Y-%m-%d') if m.planned_end else None
    return m.name, pbd


def _billing_out(b: ProjectBilling, milestone_name: Optional[str], planned_billing_date: Optional[str]):
    return {
        "id":                     b.id,
        "project_id":             b.project_id,
        "milestone_id":           b.milestone_id,
        "milestone_name":         milestone_name,
        # planned_billing_date is always derived from milestone.planned_end — never entered manually
        "planned_billing_date":   planned_billing_date,
        "planned_billing_amount": float(b.planned_billing_amount or 0),
        "actual_billing_date":    str(b.actual_billing_date) if b.actual_billing_date else None,
        "actual_billing_amount":  float(b.actual_billing_amount) if b.actual_billing_amount is not None else None,
        "billing_type":           b.billing_type,
        "description":            b.description,
        "remarks":                b.remarks,
        "created_at":             str(b.created_at) if b.created_at else None,
    }


def _snapshot(b: ProjectBilling, milestone_name: Optional[str], planned_billing_date: Optional[str]) -> str:
    return json.dumps(_billing_out(b, milestone_name, planned_billing_date))


def _write_audit(
    db: Session,
    action: str,
    billing: ProjectBilling,
    user_id: int,
    milestone_name: Optional[str],
    planned_billing_date: Optional[str],
):
    log = FinancialAuditLog(
        project_id = billing.project_id,
        billing_id = billing.id,
        action     = action,
        changed_by = user_id,
        snapshot   = _snapshot(billing, milestone_name, planned_billing_date),
    )
    db.add(log)


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

    if payload.planned_billing_amount is not None:
        b.planned_billing_amount = payload.planned_billing_amount
    if payload.actual_billing_date is not None:
        b.actual_billing_date = payload.actual_billing_date
    if payload.actual_billing_amount is not None:
        b.actual_billing_amount = payload.actual_billing_amount
    if payload.billing_type is not None:
        b.billing_type = payload.billing_type
    if payload.description is not None:
        b.description = payload.description
    # milestone_id always synced — None explicitly clears the link
    b.milestone_id = payload.milestone_id
    if payload.remarks is not None:
        b.remarks = payload.remarks

    db.flush()
    ms_name, pbd = _resolve_milestone(db, b.milestone_id)
    _write_audit(db, "Updated", b, current_user.id, ms_name, pbd)
    db.commit(); db.refresh(b)
    return _billing_out(b, ms_name, pbd)


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

    ms_name, pbd = _resolve_milestone(db, b.milestone_id)
    _write_audit(db, "Deleted", b, current_user.id, ms_name, pbd)
    db.delete(b)
    db.commit()
    return None


# ── Project-scoped routes ─────────────────────────────────────────────────────

@router.get("/{project_id}/milestones")
def list_project_milestones(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List active milestones with planned_end so the frontend can auto-fill
    Planned Billing Date when the user selects a milestone."""
    rows = (
        db.query(CustomMilestone)
        .filter(CustomMilestone.project_id == project_id,
                CustomMilestone.is_active == True)
        .order_by(CustomMilestone.num)
        .all()
    )
    return [
        {
            "id":          m.id,
            "name":        m.name,
            "num":         m.num,
            "planned_end": m.planned_end.strftime('%Y-%m-%d') if m.planned_end else None,
        }
        for m in rows
    ]


@router.get("/{project_id}")
def list_billings(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(ProjectBilling)
        .filter(ProjectBilling.project_id == project_id)
        .order_by(ProjectBilling.created_at.desc())
        .all()
    )
    out = []
    for b in rows:
        ms_name, pbd = _resolve_milestone(db, b.milestone_id)
        out.append(_billing_out(b, ms_name, pbd))
    return out


@router.get("/{project_id}/audit-log")
def list_billing_audit_log(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(FinancialAuditLog)
        .filter(FinancialAuditLog.project_id == project_id)
        .order_by(FinancialAuditLog.changed_at.desc())
        .all()
    )
    out = []
    for r in rows:
        u = db.query(User.name).filter_by(id=r.changed_by).first()
        out.append({
            "id":         r.id,
            "billing_id": r.billing_id,
            "action":     r.action,
            "changed_by": u.name if u else "—",
            "changed_at": str(r.changed_at) if r.changed_at else None,
            "snapshot":   json.loads(r.snapshot) if r.snapshot else None,
        })
    return out


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
        project_id             = project_id,
        milestone_id           = payload.milestone_id,
        planned_billing_amount = payload.planned_billing_amount,
        actual_billing_date    = payload.actual_billing_date,
        actual_billing_amount  = payload.actual_billing_amount,
        billing_type           = payload.billing_type,
        description            = payload.description,
        remarks                = payload.remarks,
        created_by             = current_user.id,
    )
    db.add(b); db.flush()   # get b.id before audit log
    ms_name, pbd = _resolve_milestone(db, b.milestone_id)
    _write_audit(db, "Created", b, current_user.id, ms_name, pbd)
    db.commit(); db.refresh(b)
    return _billing_out(b, ms_name, pbd)
