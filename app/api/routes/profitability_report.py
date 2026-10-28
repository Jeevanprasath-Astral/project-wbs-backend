"""Profitability Report — project-level Excel export.

Columns per project row:
  Project | Billing (Revenue) | Total Hours | Billable Hours | Utilization %
  | Manpower Cost | Direct Expenses | Indirect / Overhead | Total Cost
  | Net Profit / Loss | Net Margin % | Labor Yield %

Formulas:
  Manpower Cost      = SUM(work_hours.hours_spent × user.cost_rate) per project
  Direct Expenses    = SUM(project_costs.cost) WHERE category != 'Indirect / Overhead'
  Indirect / Overhead= SUM(project_costs.cost) WHERE category = 'Indirect / Overhead'
  Total Cost         = Manpower Cost + Direct Expenses + Indirect / Overhead
  Net Profit / Loss  = Billing Amount − Total Cost
  Net Margin %       = Net Profit / Billing Amount × 100   (blank if Billing = 0)
  Labor Yield %      = (Billing − Direct Expenses) / Manpower Cost × 100  (blank if Manpower Cost = 0)
  Utilization %      = Billable Hours / Total Hours × 100  (blank if Total Hours = 0)

Date filter (start_date / end_date, both optional, inclusive):
  Hours & Manpower Cost → work_hours.date
  Direct / Indirect     → project_costs.date
  Billing               → actual_billing_date, else the linked milestone's
                          planned_end (the Planned Billing Date). Entries with
                          neither date are excluded when a date filter is set.
  No dates → all-time figures (previous behaviour).
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from datetime import date as date_cls
from typing import Optional
from io import BytesIO
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from sqlalchemy import or_, and_

from app.db.database import get_db
from app.models.models import Project, WorkHours, ProjectCost, User, ProjectBilling, CustomMilestone
from app.core.deps import get_current_user

router = APIRouter(prefix="/profitability-report", tags=["Profitability Report"])

INDIRECT_CATEGORY = "Indirect / Overhead"


# ── xlsx builder ─────────────────────────────────────────────────────────────
def _build_xlsx(title: str, headers: list, rows: list, filename: str, subtitle: str = ""):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]

    def fill(hex_c): return PatternFill("solid", fgColor=hex_c)
    def bdr():
        s = Side(style="thin", color="CCCCCC")
        return Border(left=s, right=s, top=s, bottom=s)

    HDR_FILL  = fill("1F3864")
    COL_FILL  = fill("BDD7EE")
    EVEN_FILL = fill("EBF3FB")
    ODD_FILL  = fill("FFFFFF")
    PROFIT_FILL = fill("E2EFDA")   # green tint for profit rows
    LOSS_FILL   = fill("FCE4D6")   # red tint for loss rows

    ncols = len(headers)
    last_col = chr(ord("A") + ncols - 1)

    # Title row
    ws.merge_cells(f"A1:{last_col}1")
    c = ws["A1"]
    c.value = title
    c.font = Font(bold=True, color="FFFFFF", size=12, name="Calibri")
    c.fill = HDR_FILL; c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24

    if subtitle:
        ws.merge_cells(f"A2:{last_col}2")
        c2 = ws["A2"]
        c2.value = subtitle
        c2.font = Font(italic=True, color="555555", size=9, name="Calibri")
        c2.fill = fill("D9E8F5"); c2.alignment = Alignment(horizontal="left")
        ws.row_dimensions[2].height = 16
        header_row = 4
    else:
        header_row = 3

    for col, h in enumerate(headers, 1):
        hc = ws.cell(header_row, col, h)
        hc.font = Font(bold=True, color="1F3864", size=9, name="Calibri")
        hc.fill = COL_FILL; hc.border = bdr()
        hc.alignment = Alignment(horizontal="center", wrap_text=True)
    ws.row_dimensions[header_row].height = 28

    row_num = header_row + 1
    for i, r in enumerate(rows):
        # Detect profit/loss from Net Profit column (index 9)
        net = r[9] if len(r) > 9 else None
        if isinstance(net, (int, float)):
            bg = PROFIT_FILL if net >= 0 else LOSS_FILL
        else:
            bg = EVEN_FILL if i % 2 == 0 else ODD_FILL

        for col, val in enumerate(r, 1):
            cell = ws.cell(row_num, col, val if val not in (None, "") else "—")
            cell.font = Font(size=9, name="Calibri")
            cell.fill = bg; cell.border = bdr()
            # Right-align numeric columns
            if isinstance(val, (int, float)):
                cell.alignment = Alignment(horizontal="right")
        row_num += 1

    if not rows:
        ws.merge_cells(f"A{row_num}:{last_col}{row_num}")
        ws.cell(row_num, 1, "No projects found for the selected filters")
        ws.cell(row_num, 1).font = Font(italic=True, size=9, color="999999")

    # Column widths
    col_widths = [28, 16, 12, 14, 13, 16, 16, 18, 13, 16, 13, 16]
    for i, w in enumerate(col_widths[:ncols], 1):
        ws.column_dimensions[chr(ord("A") + i - 1)].width = w

    output = BytesIO()
    wb.save(output); output.seek(0)
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"})


def _fmt(v, decimals=2):
    """Round float to decimals; return as float (openpyxl handles numeric cells)."""
    if v is None: return 0.0
    return round(float(v), decimals)

def _pct(numerator, denominator, decimals=1):
    """Safe percentage string. Returns float or '' if denominator is 0."""
    if not denominator: return ""
    return round(float(numerator) / float(denominator) * 100, decimals)


# ── Core calculation helper ───────────────────────────────────────────────────
def _parse_date(v: Optional[str]):
    if not v:
        return None
    try:
        return date_cls.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"Invalid date format '{v}' — use YYYY-MM-DD")


def _empty_totals():
    return {"total_hours": 0.0, "billable_hours": 0.0, "manpower_cost": 0.0,
            "direct_expenses": 0.0, "indirect_cost": 0.0, "billing": 0.0}


def _bulk_metrics(db: Session, project_ids, start=None, end=None) -> dict:
    """Profitability metrics for many projects in 3 queries total
    (previously 5+ queries per project). Returns {project_id: metrics}."""
    pids = list({p for p in project_ids if p is not None})
    raw = {pid: _empty_totals() for pid in pids}
    if not pids:
        return {}

    # ── Hours + Manpower Cost (one grouped query) ────────────────────────────
    # Billable: explicit work_type='Billable' OR legacy rows where work_type is
    # NULL but is_billable=True (stored before work_type existed).
    billable_cond = or_(
        WorkHours.work_type == 'Billable',
        and_(WorkHours.work_type.is_(None), WorkHours.is_billable == True),
    )
    hq = (db.query(
            WorkHours.project_id,
            func.coalesce(func.sum(WorkHours.hours_spent), 0.0),
            func.coalesce(func.sum(case((billable_cond, WorkHours.hours_spent), else_=0.0)), 0.0),
            func.coalesce(func.sum(WorkHours.hours_spent * func.coalesce(User.cost_rate, 0.0)), 0.0),
          )
          .outerjoin(User, User.id == WorkHours.user_id)
          .filter(WorkHours.project_id.in_(pids)))
    if start: hq = hq.filter(WorkHours.date >= start)
    if end:   hq = hq.filter(WorkHours.date <= end)
    for pid, total, billable, manpower in hq.group_by(WorkHours.project_id).all():
        raw[pid]["total_hours"]    = float(total or 0)
        raw[pid]["billable_hours"] = float(billable or 0)
        raw[pid]["manpower_cost"]  = float(manpower or 0)

    # ── Cost entries (one grouped query) ─────────────────────────────────────
    is_indirect = ProjectCost.category == INDIRECT_CATEGORY
    cq = (db.query(
            ProjectCost.project_id,
            func.coalesce(func.sum(case((is_indirect, 0.0), else_=ProjectCost.cost)), 0.0),
            func.coalesce(func.sum(case((is_indirect, ProjectCost.cost), else_=0.0)), 0.0),
          )
          .filter(ProjectCost.project_id.in_(pids)))
    if start: cq = cq.filter(ProjectCost.date >= start)
    if end:   cq = cq.filter(ProjectCost.date <= end)
    for pid, direct, indirect in cq.group_by(ProjectCost.project_id).all():
        raw[pid]["direct_expenses"] = float(direct or 0)
        raw[pid]["indirect_cost"]   = float(indirect or 0)

    # ── Billing (one query; date resolved in Python so it works the same on
    # every database) — actual amount if billed, else the planned amount ──────
    bq = (db.query(ProjectBilling.project_id,
                   ProjectBilling.actual_billing_amount,
                   ProjectBilling.planned_billing_amount,
                   ProjectBilling.actual_billing_date,
                   CustomMilestone.planned_end)
          .outerjoin(CustomMilestone, CustomMilestone.id == ProjectBilling.milestone_id)
          .filter(ProjectBilling.project_id.in_(pids)))
    for pid, actual_amt, planned_amt, actual_dt, planned_end in bq.all():
        if start or end:
            bill_date = actual_dt or (planned_end.date() if planned_end else None)
            if bill_date is None:
                continue
            if start and bill_date < start:
                continue
            if end and bill_date > end:
                continue
        amt = actual_amt if actual_amt is not None else planned_amt
        if amt is not None:
            raw[pid]["billing"] += float(amt)

    return {pid: _derive(r) for pid, r in raw.items()}


def _derive(r: dict) -> dict:
    """Derived KPIs — same formulas as before."""
    billing, manpower = r["billing"], r["manpower_cost"]
    direct, indirect = r["direct_expenses"], r["indirect_cost"]
    total_cost = manpower + direct + indirect
    net_profit = billing - total_cost
    return {
        "billing":         _fmt(billing),
        "total_hours":     _fmt(r["total_hours"]),
        "billable_hours":  _fmt(r["billable_hours"]),
        "util_pct":        _pct(r["billable_hours"], r["total_hours"]),
        "manpower_cost":   _fmt(manpower),
        "direct_expenses": _fmt(direct),
        "indirect_cost":   _fmt(indirect),
        "total_cost":      _fmt(total_cost),
        "net_profit":      _fmt(net_profit),
        "margin_pct":      _pct(net_profit, billing),
        "recovery_pct":    _pct(billing - direct, manpower),
    }


def _project_metrics(db: Session, project: Project, start=None, end=None):
    """Single-project wrapper kept for compatibility."""
    return _bulk_metrics(db, [project.id], start, end).get(project.id) or _derive(_empty_totals())


# ── Report endpoint ───────────────────────────────────────────────────────────
@router.get("/export")
def profitability_report_export(
    project_id:  Optional[int] = None,
    status:      Optional[str] = None,
    start_date:  Optional[str] = None,
    end_date:    Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Project)
    if project_id:
        q = q.filter(Project.id == project_id)
    if status:
        q = q.filter(Project.status == status)
    projects = q.order_by(Project.name).all()

    headers = [
        "Project", "Billing (Revenue ₹)", "Total Hours", "Billable Hours",
        "Utilization %", "Manpower Cost (₹)", "Direct Expenses (₹)",
        "Indirect / Overhead (₹)", "Total Cost (₹)", "Net Profit / Loss (₹)",
        "Net Margin %", "Labor Yield %",
    ]

    start, end = _parse_date(start_date), _parse_date(end_date)
    metrics = _bulk_metrics(db, [p.id for p in projects], start, end)

    rows = []
    for p in projects:
        m = metrics[p.id]
        rows.append([
            p.name,
            m["billing"],
            m["total_hours"],
            m["billable_hours"],
            m["util_pct"] if m["util_pct"] != "" else "—",
            m["manpower_cost"],
            m["direct_expenses"],
            m["indirect_cost"],
            m["total_cost"],
            m["net_profit"],
            m["margin_pct"] if m["margin_pct"] != "" else "—",
            m["recovery_pct"] if m["recovery_pct"] != "" else "—",
        ])

    subtitle_parts = []
    if start_date or end_date:
        subtitle_parts.append(f"Period: {start_date or '—'} → {end_date or '—'}")
    if status:
        subtitle_parts.append(f"Status: {status}")

    return _build_xlsx(
        "Profitability Report", headers, rows,
        "profitability-report.xlsx",
        " | ".join(subtitle_parts),
    )


# ── JSON preview endpoint ─────────────────────────────────────────────────────
@router.get("/data")
def profitability_report_data(
    project_id:  Optional[int] = None,
    status:      Optional[str] = None,
    start_date:  Optional[str] = None,
    end_date:    Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Project)
    if project_id:
        q = q.filter(Project.id == project_id)
    if status:
        q = q.filter(Project.status == status)
    projects = q.order_by(Project.name).all()

    start, end = _parse_date(start_date), _parse_date(end_date)
    metrics = _bulk_metrics(db, [p.id for p in projects], start, end)

    rows = []
    for p in projects:
        m = metrics[p.id]
        rows.append({
            "project":         p.name,
            "status":          p.status or "—",
            "billing":         m["billing"],
            "total_hours":     m["total_hours"],
            "billable_hours":  m["billable_hours"],
            "util_pct":        m["util_pct"] if m["util_pct"] != "" else None,
            "manpower_cost":   m["manpower_cost"],
            "direct_expenses": m["direct_expenses"],
            "indirect_cost":   m["indirect_cost"],
            "total_cost":      m["total_cost"],
            "net_profit":      m["net_profit"],
            "margin_pct":      m["margin_pct"] if m["margin_pct"] != "" else None,
            "recovery_pct":    m["recovery_pct"] if m["recovery_pct"] != "" else None,
        })
    return {"rows": rows}


# ── Filter options ────────────────────────────────────────────────────────────
@router.get("/filter-options")
def filter_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    projects = db.query(Project).order_by(Project.name).all()
    statuses = (
        db.query(Project.status)
        .filter(Project.status.isnot(None))
        .distinct().order_by(Project.status).all()
    )
    return {
        "projects": [{"id": p.id, "name": p.name} for p in projects],
        "statuses": [s[0] for s in statuses],
    }
