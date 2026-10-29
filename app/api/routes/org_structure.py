"""Organisation structure — Group → Company → Division.

Projects are linked to a Division (Project.division_id); Company and Group
follow from the division. Used by the Group Billing Dashboard and the
project create/edit forms.

  GET    /org-structure                      — full tree (any logged-in user)
  POST   /org-structure/groups               — Admin
  PATCH  /org-structure/groups/{id}          — Admin
  DELETE /org-structure/groups/{id}          — Admin, only when it has no companies
  POST   /org-structure/companies            — Admin
  PATCH  /org-structure/companies/{id}       — Admin
  DELETE /org-structure/companies/{id}       — Admin, only when it has no divisions
  POST   /org-structure/divisions            — Admin
  PATCH  /org-structure/divisions/{id}       — Admin
  DELETE /org-structure/divisions/{id}       — Admin, only when no project uses it
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.deps import get_current_user
from app.core.permissions import is_admin
from app.db.database import get_db
from app.models.models import OrgGroup, Company, Division, Project, User
from app.services.audit_service import log_action

router = APIRouter(prefix="/org-structure", tags=["Org Structure"])


# ── Schemas ───────────────────────────────────────────────────────────────────
class GroupIn(BaseModel):
    name: str

class GroupUpdate(BaseModel):
    name: Optional[str] = None

class CompanyIn(BaseModel):
    group_id: int
    name: str

class CompanyUpdate(BaseModel):
    group_id: Optional[int] = None
    name: Optional[str] = None

class DivisionIn(BaseModel):
    company_id: int
    name: str

class DivisionUpdate(BaseModel):
    company_id: Optional[int] = None
    name: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _require_admin(user: User):
    if not is_admin(user):
        raise HTTPException(403, "Only Admin can change the organisation structure")


def _clean(name: Optional[str], what: str) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, f"{what} name is required")
    if len(name) > 150:
        raise HTTPException(400, f"{what} name must be 150 characters or fewer")
    return name


def _get_or_404(db: Session, model, obj_id: int, what: str):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, f"{what} not found")
    return obj


def _log(db: Session, user: User, action: str, entity: str, entity_id: int, description: str):
    log_action(db, actor=user.name, action=action, description=description,
               entity_type=entity, entity_id=entity_id, user_id=user.id)


def build_tree(db: Session) -> dict:
    """Group → Company → Division tree with project counts per division.
    Four queries regardless of size."""
    groups = (db.query(OrgGroup)
                .options(selectinload(OrgGroup.companies).selectinload(Company.divisions))
                .order_by(OrgGroup.name).all())
    counts = dict(db.query(Project.division_id, func.count(Project.id))
                    .filter(Project.division_id.isnot(None))
                    .group_by(Project.division_id).all())
    unassigned = db.query(func.count(Project.id)).filter(Project.division_id.is_(None)).scalar() or 0
    tree = []
    for g in groups:
        companies = []
        for c in sorted(g.companies, key=lambda x: x.name.lower()):
            divisions = [{"id": d.id, "name": d.name, "project_count": counts.get(d.id, 0)}
                         for d in sorted(c.divisions, key=lambda x: x.name.lower())]
            companies.append({"id": c.id, "name": c.name, "divisions": divisions,
                              "project_count": sum(d["project_count"] for d in divisions)})
        tree.append({"id": g.id, "name": g.name, "companies": companies,
                     "project_count": sum(c["project_count"] for c in companies)})
    return {"groups": tree, "unassigned_project_count": unassigned}


# ── Read ──────────────────────────────────────────────────────────────────────
@router.get("")
def get_tree(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return build_tree(db)


# ── Groups ────────────────────────────────────────────────────────────────────
@router.post("/groups", status_code=201)
def create_group(payload: GroupIn, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    name = _clean(payload.name, "Group")
    if db.query(OrgGroup).filter(func.lower(OrgGroup.name) == name.lower()).first():
        raise HTTPException(400, f"A group named '{name}' already exists")
    g = OrgGroup(name=name)
    db.add(g); db.flush()
    _log(db, current_user, "create", "org_group", g.id, f"Created group '{name}'")
    db.commit()
    return {"id": g.id, "name": g.name}


@router.patch("/groups/{group_id}")
def update_group(group_id: int, payload: GroupUpdate, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    g = _get_or_404(db, OrgGroup, group_id, "Group")
    if payload.name is not None:
        name = _clean(payload.name, "Group")
        clash = db.query(OrgGroup).filter(func.lower(OrgGroup.name) == name.lower(),
                                          OrgGroup.id != group_id).first()
        if clash:
            raise HTTPException(400, f"A group named '{name}' already exists")
        _log(db, current_user, "update", "org_group", g.id, f"Renamed group '{g.name}' to '{name}'")
        g.name = name
    db.commit()
    return {"id": g.id, "name": g.name}


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    g = _get_or_404(db, OrgGroup, group_id, "Group")
    if db.query(Company.id).filter(Company.group_id == group_id).first():
        raise HTTPException(400, "Move or delete this group's companies first")
    _log(db, current_user, "delete", "org_group", g.id, f"Deleted group '{g.name}'")
    db.delete(g); db.commit()
    return {"status": "deleted"}


# ── Companies ─────────────────────────────────────────────────────────────────
def _company_name_clash(db: Session, group_id: int, name: str, exclude_id: Optional[int] = None):
    q = db.query(Company).filter(Company.group_id == group_id,
                                 func.lower(Company.name) == name.lower())
    if exclude_id:
        q = q.filter(Company.id != exclude_id)
    return q.first() is not None


@router.post("/companies", status_code=201)
def create_company(payload: CompanyIn, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    _get_or_404(db, OrgGroup, payload.group_id, "Group")
    name = _clean(payload.name, "Company")
    if _company_name_clash(db, payload.group_id, name):
        raise HTTPException(400, f"This group already has a company named '{name}'")
    c = Company(group_id=payload.group_id, name=name)
    db.add(c); db.flush()
    _log(db, current_user, "create", "company", c.id, f"Created company '{name}'")
    db.commit()
    return {"id": c.id, "group_id": c.group_id, "name": c.name}


@router.patch("/companies/{company_id}")
def update_company(company_id: int, payload: CompanyUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    c = _get_or_404(db, Company, company_id, "Company")
    group_id = payload.group_id if payload.group_id is not None else c.group_id
    if payload.group_id is not None:
        _get_or_404(db, OrgGroup, payload.group_id, "Group")
    name = _clean(payload.name, "Company") if payload.name is not None else c.name
    if _company_name_clash(db, group_id, name, exclude_id=company_id):
        raise HTTPException(400, f"This group already has a company named '{name}'")
    _log(db, current_user, "update", "company", c.id, f"Updated company '{c.name}'")
    c.group_id, c.name = group_id, name
    db.commit()
    return {"id": c.id, "group_id": c.group_id, "name": c.name}


@router.delete("/companies/{company_id}")
def delete_company(company_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    c = _get_or_404(db, Company, company_id, "Company")
    if db.query(Division.id).filter(Division.company_id == company_id).first():
        raise HTTPException(400, "Move or delete this company's divisions first")
    _log(db, current_user, "delete", "company", c.id, f"Deleted company '{c.name}'")
    db.delete(c); db.commit()
    return {"status": "deleted"}


# ── Divisions ─────────────────────────────────────────────────────────────────
def _division_name_clash(db: Session, company_id: int, name: str, exclude_id: Optional[int] = None):
    q = db.query(Division).filter(Division.company_id == company_id,
                                  func.lower(Division.name) == name.lower())
    if exclude_id:
        q = q.filter(Division.id != exclude_id)
    return q.first() is not None


@router.post("/divisions", status_code=201)
def create_division(payload: DivisionIn, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    _get_or_404(db, Company, payload.company_id, "Company")
    name = _clean(payload.name, "Division")
    if _division_name_clash(db, payload.company_id, name):
        raise HTTPException(400, f"This company already has a division named '{name}'")
    d = Division(company_id=payload.company_id, name=name)
    db.add(d); db.flush()
    _log(db, current_user, "create", "division", d.id, f"Created division '{name}'")
    db.commit()
    return {"id": d.id, "company_id": d.company_id, "name": d.name}


@router.patch("/divisions/{division_id}")
def update_division(division_id: int, payload: DivisionUpdate, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    d = _get_or_404(db, Division, division_id, "Division")
    company_id = payload.company_id if payload.company_id is not None else d.company_id
    if payload.company_id is not None:
        _get_or_404(db, Company, payload.company_id, "Company")
    name = _clean(payload.name, "Division") if payload.name is not None else d.name
    if _division_name_clash(db, company_id, name, exclude_id=division_id):
        raise HTTPException(400, f"This company already has a division named '{name}'")
    _log(db, current_user, "update", "division", d.id, f"Updated division '{d.name}'")
    d.company_id, d.name = company_id, name
    db.commit()
    return {"id": d.id, "company_id": d.company_id, "name": d.name}


@router.delete("/divisions/{division_id}")
def delete_division(division_id: int, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    d = _get_or_404(db, Division, division_id, "Division")
    in_use = db.query(func.count(Project.id)).filter(Project.division_id == division_id).scalar() or 0
    if in_use:
        raise HTTPException(400, f"{in_use} project(s) are in this division — move them first")
    _log(db, current_user, "delete", "division", d.id, f"Deleted division '{d.name}'")
    db.delete(d); db.commit()
    return {"status": "deleted"}
