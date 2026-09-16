from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from io import BytesIO
from datetime import datetime
from app.db.database import get_db
from app.models.models import (Project, ProjectMilestone, Milestone, Task,
                                Subtask, Question, Response, SubtaskStatus, User,
                                CustomMilestone, CustomTask, CustomSubtask,
                                Activity, WorkHours, SubtaskQuestion)
from app.core.deps import get_current_user
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

router = APIRouter(tags=["Export"])

# ── Shared helpers ────────────────────────────────────────────────────────────
def _load_project_data(db, project_id):
    """Load all milestone data in bulk to avoid N+1 queries.

    "All Milestones" must only ever mean the milestones actually selected
    for THIS project via Milestone Configuration (CustomMilestone), not the
    full standard 10-milestone catalog. Different projects can select
    different subsets, so this filter is computed fresh per project.
    """
    from sqlalchemy.orm import joinedload
    project = db.query(Project).filter_by(id=project_id).first()
    selected_nums = {
        cm.num for cm in db.query(CustomMilestone).filter_by(
            project_id=project_id, is_active=True
        ).all()
    }
    pm_query = db.query(ProjectMilestone).filter_by(project_id=project_id)
    if selected_nums:
        pm_query = pm_query.filter(ProjectMilestone.num.in_(selected_nums))
    else:
        # No milestones selected yet for this project — export nothing
        # rather than silently falling back to all 10 standard milestones.
        pm_query = pm_query.filter(ProjectMilestone.num.in_([-1]))
    pms = pm_query.order_by(ProjectMilestone.num).all()
    milestones = {
        ms.num: ms for ms in db.query(Milestone).options(
            joinedload(Milestone.tasks)
            .joinedload(Task.subtasks)
            .joinedload(Subtask.questions)
        ).all()
    }
    # Bulk load all responses and statuses
    from sqlalchemy import or_
    all_responses = db.query(Response).filter_by(project_id=project_id).all()
    resp_by_question = {r.question_id: r.value for r in all_responses if r.question_id}
    resp_by_subtask  = {r.subtask_id: r.value  for r in all_responses if r.subtask_id and not r.question_id}
    all_ss = db.query(SubtaskStatus).filter_by(project_id=project_id).all()
    ss_by_subtask = {ss.subtask_id: ss for ss in all_ss}
    return project, pms, milestones, resp_by_question, resp_by_subtask, ss_by_subtask


# ── helpers ───────────────────────────────────────────────────────────────────
def _days_between(start, end) -> str:
    """Return integer day count between two DateTime values, or '' if either is None."""
    if not start or not end:
        return ""
    try:
        s = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        e = end   if isinstance(end,   datetime) else datetime.fromisoformat(str(end))
        return max(0, (e - s).days)
    except Exception:
        return ""


def _fmt_date(dt) -> str:
    """Return YYYY-MM-DD string or '' if None."""
    if not dt:
        return ""
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(dt)[:10]


# ── Excel Export ──────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/export/xlsx")
def export_excel(project_id: int, milestones: str = None, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """Export full Milestone Configuration data (CustomMilestone → CustomTask →
    CustomSubtask → Activity).  Produces one 'Overview' sheet summarising all
    active milestones, then one detailed sheet per milestone containing: a
    milestone details block, a Tasks table, a Subtasks table (with responses
    and actual consumed hours), and an Activities table."""

    project = db.query(Project).filter_by(id=project_id).first()

    # Load active custom milestones with full hierarchy (eager load to avoid N+1)
    # Include SubtaskQuestion so form answers are available without extra queries
    cm_q = (db.query(CustomMilestone)
              .filter_by(project_id=project_id, is_active=True)
              .options(
                  joinedload(CustomMilestone.tasks)
                  .joinedload(CustomTask.subtasks)
                  .joinedload(CustomSubtask.activities),
                  joinedload(CustomMilestone.tasks)
                  .joinedload(CustomTask.subtasks)
                  .joinedload(CustomSubtask.questions),
              )
              .order_by(CustomMilestone.num))
    if milestones:
        ms_nums = [int(x.strip()) for x in milestones.split(',') if x.strip().isdigit()]
        if ms_nums:
            cm_q = cm_q.filter(CustomMilestone.num.in_(ms_nums))
    custom_milestones = cm_q.all()

    # Load OLD template system data (Milestone→Task→Subtask→Question + Response/SubtaskStatus)
    # so that form responses entered via the standard milestone template are also exported.
    _, _, old_milestones, resp_by_question, resp_by_subtask, ss_by_subtask = _load_project_data(db, project_id)
    # Build lookup: (milestone_num, task_num) → Task (old template)
    old_task_lookup: dict = {}
    for ms_num, old_ms in old_milestones.items():
        for old_task in (old_ms.tasks if old_ms else []):
            old_task_lookup[(ms_num, old_task.num)] = old_task

    # Bulk-fetch actual consumed hours per subtask and per activity
    all_sub_ids = [s.id for cm in custom_milestones
                   for t  in cm.tasks
                   for s  in t.subtasks]
    all_act_ids = [a.id for cm in custom_milestones
                   for t  in cm.tasks
                   for s  in t.subtasks
                   for a  in s.activities]

    sub_actual: dict = {}
    if all_sub_ids:
        rows = (db.query(WorkHours.custom_subtask_id, func.sum(WorkHours.hours_spent))
                  .filter(WorkHours.custom_subtask_id.in_(all_sub_ids))
                  .group_by(WorkHours.custom_subtask_id).all())
        sub_actual = {r[0]: round(float(r[1] or 0), 2) for r in rows}

    act_actual: dict = {}
    if all_act_ids:
        rows = (db.query(WorkHours.activity_id, func.sum(WorkHours.hours_spent))
                  .filter(WorkHours.activity_id.in_(all_act_ids))
                  .group_by(WorkHours.activity_id).all())
        act_actual = {r[0]: round(float(r[1] or 0), 2) for r in rows}

    # ── Workbook helpers ──────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    def fill(hex_c): return PatternFill("solid", fgColor=hex_c)
    def bdr():
        s = Side(style="thin", color="CCCCCC")
        return Border(left=s, right=s, top=s, bottom=s)

    # Colour palette
    MS_HDR_FILL   = fill("1F3864")   # dark navy  — milestone title bars
    TASK_SEC_FILL = fill("3730A3")   # indigo     — Tasks section label
    SUB_SEC_FILL  = fill("7C3AED")   # violet     — Subtasks section label
    ACT_SEC_FILL  = fill("0D3E7A")   # dark blue  — Activities section label
    COL_FILL      = fill("BDD7EE")   # light blue — column headers
    INFO_FILL     = fill("D9E8F5")   # pale blue  — info / date rows
    EVEN_FILL     = fill("EBF3FB")
    ODD_FILL      = fill("FFFFFF")
    DONE_FILL     = fill("E2EFDA")
    PROG_FILL     = fill("FFF2CC")
    OVER_FILL     = fill("FCE4EC")
    TODO_FILL     = fill("F0F0F0")

    STATUS_FILLS = {
        "Completed": DONE_FILL, "In Progress": PROG_FILL,
        "Overdue":   OVER_FILL, "Not Started": TODO_FILL,
    }
    STATUS_COLORS = {
        "Completed": "375623", "In Progress": "7F6000",
        "Overdue":   "A32D2D", "Not Started": "666666",
    }

    # All sheets use 10 columns (A–J)
    LAST_COL = "J"
    N_COLS   = 10

    def _c(ws, row, col, value, bold=False, italic=False, color="333333",
           align="left", bg=None, wrap=False, size=9):
        """Write a styled cell."""
        cell = ws.cell(row, col, value if value not in (None, "") else "")
        cell.font = Font(size=size, bold=bold, italic=italic, color=color, name="Calibri")
        if bg:
            cell.fill = bg
        cell.border = bdr()
        cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
        return cell

    def _merge_row(ws, row, value, font_color="FFFFFF", bg=None, size=9,
                   bold=False, italic=False, height=17):
        """Write a full-width merged row."""
        ws.merge_cells(f"A{row}:{LAST_COL}{row}")
        cell = ws[f"A{row}"]
        cell.value = value
        cell.font = Font(size=size, bold=bold, italic=italic, color=font_color, name="Calibri")
        if bg:
            cell.fill = bg
        cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[row].height = height
        return row + 1

    def _section_label(ws, row, label, sec_fill):
        """Full-width section label with white bold text."""
        return _merge_row(ws, row, label, font_color="FFFFFF", bg=sec_fill,
                          size=10, bold=True, height=19)

    def _col_headers(ws, row, headers, widths=None):
        """Write column header row."""
        for ci, h in enumerate(headers, 1):
            hc = ws.cell(row, ci, h)
            hc.font = Font(bold=True, color="1F3864", size=9, name="Calibri")
            hc.fill = COL_FILL
            hc.border = bdr()
            hc.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[row].height = 17
        return row + 1

    def _empty_row(ws, row, msg="No data configured."):
        ws.merge_cells(f"A{row}:{LAST_COL}{row}")
        cell = ws[f"A{row}"]
        cell.value = msg
        cell.font = Font(italic=True, color="AAAAAA", size=9, name="Calibri")
        cell.alignment = Alignment(horizontal="center")
        ws.row_dimensions[row].height = 16
        return row + 1

    # ── Overview sheet ────────────────────────────────────────────────────────
    ws_ov = wb.create_sheet("Overview")
    OV_WIDTHS = [6, 28, 14, 22, 20, 13, 13, 13, 13, 38]
    for col_letter, w in zip("ABCDEFGHIJ", OV_WIDTHS):
        ws_ov.column_dimensions[col_letter].width = w

    # Title
    row = _merge_row(ws_ov, 1,
        f"PROJECT MILESTONE OVERVIEW — {(project.name or '').upper()}",
        font_color="FFFFFF", bg=MS_HDR_FILL, size=12, bold=True, height=26)

    # Sub-info
    row = _merge_row(ws_ov, row,
        f"Client: {project.client or '—'}   |   Owner: {project.owner or '—'}   |   "
        f"Exported by: {current_user.name}   |   Active milestones: {len(custom_milestones)}",
        font_color="555555", bg=INFO_FILL, size=9, italic=True, height=15)

    row += 1  # blank
    OV_HEADERS = ["#", "Milestone Name", "Status", "Assignee", "Responsible",
                  "Planned Start", "Planned End", "Actual Start", "Actual End", "Description"]
    row = _col_headers(ws_ov, row, OV_HEADERS)

    for ri, cm in enumerate(custom_milestones):
        s_fill  = STATUS_FILLS.get(cm.status or "Not Started", TODO_FILL)
        s_color = STATUS_COLORS.get(cm.status or "Not Started", "666666")
        row_bg  = EVEN_FILL if ri % 2 == 0 else ODD_FILL
        _c(ws_ov, row, 1,  f"M{cm.num:02d}", bold=True, align="center", bg=row_bg)
        _c(ws_ov, row, 2,  cm.name or "",    bold=True, bg=row_bg)
        _c(ws_ov, row, 3,  cm.status or "Not Started", bold=True,
           color=s_color, align="center", bg=s_fill)
        _c(ws_ov, row, 4,  cm.assignee   or "—", bg=row_bg)
        _c(ws_ov, row, 5,  cm.responsible or "—", bg=row_bg)
        _c(ws_ov, row, 6,  _fmt_date(cm.planned_start), align="center", bg=row_bg)
        _c(ws_ov, row, 7,  _fmt_date(cm.planned_end),   align="center", bg=row_bg)
        _c(ws_ov, row, 8,  _fmt_date(cm.actual_start),  align="center", bg=row_bg)
        _c(ws_ov, row, 9,  _fmt_date(cm.actual_end),    align="center", bg=row_bg)
        _c(ws_ov, row, 10, cm.description or "", wrap=True, bg=row_bg)
        ws_ov.row_dimensions[row].height = 17
        row += 1

    # ── Per-milestone sheets ──────────────────────────────────────────────────
    for cm in custom_milestones:
        sheet_name = f"M{cm.num:02d}-{cm.name[:16]}"
        ws = wb.create_sheet(sheet_name)

        # Column widths for 10 columns
        MS_COL_WIDTHS = [8, 28, 14, 22, 14, 13, 13, 13, 13, 13]
        for col_letter, w in zip("ABCDEFGHIJ", MS_COL_WIDTHS):
            ws.column_dimensions[col_letter].width = w

        row = 1

        # ── Milestone title bar ───────────────────────────────────────────────
        row = _merge_row(ws, row,
            f"  M{cm.num:02d} — {cm.name.upper()}",
            font_color="FFFFFF", bg=MS_HDR_FILL, size=12, bold=True, height=26)

        # Status / assignee sub-bar
        row = _merge_row(ws, row,
            f"  Status: {cm.status or 'Not Started'}   |   "
            f"Assignee: {cm.assignee or '—'}   |   "
            f"Responsible: {cm.responsible or '—'}",
            font_color="CADCFC", bg=fill("2D3A6B"), size=9, italic=True, height=15)

        # Dates / description bar
        dates_val = (
            f"  Planned: {_fmt_date(cm.planned_start) or '—'} → {_fmt_date(cm.planned_end) or '—'}"
            f"   |   Actual: {_fmt_date(cm.actual_start) or '—'} → {_fmt_date(cm.actual_end) or '—'}"
        )
        if cm.description:
            dates_val += f"   |   {cm.description}"
        row = _merge_row(ws, row, dates_val,
            font_color="555555", bg=INFO_FILL, size=9, italic=True, height=15)

        # Project / export info bar
        row = _merge_row(ws, row,
            f"  Project: {project.name if project else '—'}   |   "
            f"Client: {project.client if project else '—'}   |   "
            f"Exported by: {current_user.name}",
            font_color="888888", bg=fill("F8FBFF"), size=8, italic=True, height=14)

        row += 1  # blank gap

        tasks_sorted = sorted(cm.tasks, key=lambda x: x.num or 0)

        # ══ TASKS TABLE ═══════════════════════════════════════════════════════
        row = _section_label(ws, row, "  📋  TASKS", TASK_SEC_FILL)
        TASK_HDRS = ["Task #", "Task Name", "Status", "Assignee",
                     "Planned Start", "Planned End", "Actual Start", "Actual End",
                     "Est. Hours", "Responsibility"]
        row = _col_headers(ws, row, TASK_HDRS)

        if tasks_sorted:
            for ti, task in enumerate(tasks_sorted):
                t_status = task.status or "Not Started"
                t_sfill  = STATUS_FILLS.get(t_status, TODO_FILL)
                t_scolor = STATUS_COLORS.get(t_status, "666666")
                row_bg   = EVEN_FILL if ti % 2 == 0 else ODD_FILL
                _c(ws, row, 1,  f"T{task.num:02d}" if task.num else "—",
                   bold=True, align="center", bg=row_bg)
                _c(ws, row, 2,  task.name or "", bold=True, bg=row_bg, wrap=True)
                _c(ws, row, 3,  t_status, bold=True,
                   color=t_scolor, align="center", bg=t_sfill)
                _c(ws, row, 4,  task.assignee or "—", bg=row_bg)
                _c(ws, row, 5,  _fmt_date(task.planned_start), align="center", bg=row_bg)
                _c(ws, row, 6,  _fmt_date(task.planned_end),   align="center", bg=row_bg)
                _c(ws, row, 7,  _fmt_date(task.actual_start),  align="center", bg=row_bg)
                _c(ws, row, 8,  _fmt_date(task.actual_end),    align="center", bg=row_bg)
                _c(ws, row, 9,  task.estimated_hours or 0,     align="center", bg=row_bg)
                _c(ws, row, 10, task.responsibility or "—", bg=row_bg)
                ws.row_dimensions[row].height = 17
                row += 1
        else:
            row = _empty_row(ws, row, "No tasks configured for this milestone.")

        row += 1  # gap

        # ══ FORM DETAILS ══════════════════════════════════════════════════════
        # Shows only filled form Q&A — no raw subtask rows.
        # Layout: Task header (navy) → Form name header (light blue) → Q&A rows
        row = _section_label(ws, row, "  📋  FORM DETAILS", SUB_SEC_FILL)

        FORM_FILL     = fill("E8F4FD")   # light-blue  — question label cells
        ANS_FILL      = fill("FFFDE7")   # pale-yellow — answer cells
        TASK_HDR_FILL = fill("1F3864")   # dark navy   — task group header
        FORM_HDR_FILL = fill("DCE6F1")   # soft blue-gray — form/section header

        def _form_row(ws_s, r, q_label, ans_val):
            """Question (A-D merged, light blue) + Answer (E-J merged, yellow)."""
            ws_s.merge_cells(f"A{r}:D{r}")
            qc = ws_s[f"A{r}"]
            qc.value = f"    ↳ {q_label}"
            qc.font  = Font(size=8, italic=True, color="0D47A1", name="Calibri")
            qc.fill  = FORM_FILL; qc.border = bdr()
            qc.alignment = Alignment(horizontal="left", wrap_text=True)
            ws_s.merge_cells(f"E{r}:J{r}")
            ac = ws_s[f"E{r}"]
            ac.value = ans_val
            ac.font  = Font(size=8, color="333333", name="Calibri")
            ac.fill  = ANS_FILL; ac.border = bdr()
            ac.alignment = Alignment(horizontal="left", wrap_text=True)
            ws_s.row_dimensions[r].height = 14
            return r + 1

        def _task_hdr(ws_s, r, task_name):
            """Full-width dark navy task group header."""
            ws_s.merge_cells(f"A{r}:J{r}")
            c = ws_s[f"A{r}"]
            c.value = f"  📌  Task: {task_name}"
            c.font  = Font(size=9, bold=True, color="FFFFFF", name="Calibri")
            c.fill  = TASK_HDR_FILL; c.border = bdr()
            c.alignment = Alignment(horizontal="left", vertical="center")
            ws_s.row_dimensions[r].height = 18
            return r + 1

        def _form_hdr(ws_s, r, form_name, status=None):
            """Full-width form / section sub-header."""
            ws_s.merge_cells(f"A{r}:J{r}")
            c = ws_s[f"A{r}"]
            status_str = f"   [{status}]" if status else ""
            c.value = f"    📄  {form_name}{status_str}"
            c.font  = Font(size=8, bold=True, color="1F3864", name="Calibri")
            c.fill  = FORM_HDR_FILL; c.border = bdr()
            c.alignment = Alignment(horizontal="left", vertical="center")
            ws_s.row_dimensions[r].height = 16
            return r + 1

        has_form_data = False
        for task in tasks_sorted:
            task_has_data = False

            # ── NEW-SYSTEM: SubtaskQuestion answers ───────────────────────────
            for sub in sorted(task.subtasks, key=lambda x: x.num or 0):
                filled_qs = [
                    q for q in sorted(sub.questions, key=lambda x: x.num or 0)
                    if (q.response or "").strip()
                ]
                if not filled_qs:
                    continue
                if not task_has_data:
                    row = _task_hdr(ws, row, task.name or "—")
                    task_has_data = True
                    has_form_data = True
                row = _form_hdr(ws, row, sub.name or f"Subtask {sub.num}", sub.status)
                for q in filled_qs:
                    row = _form_row(ws, row,
                                    f"Q{q.num}: {q.question_text or ''}",
                                    q.response.strip())

            # ── OLD-SYSTEM: Milestone template form responses ─────────────────
            old_task = old_task_lookup.get((cm.num, task.num))
            old_subs = sorted(old_task.subtasks, key=lambda x: x.num or 0) if old_task else []

            for old_sub in old_subs:
                if old_sub.is_format and old_sub.questions:
                    filled = [
                        (q, resp_by_question.get(q.id, ""))
                        for q in sorted(old_sub.questions, key=lambda x: x.num or 0)
                        if resp_by_question.get(q.id, "")
                    ]
                    if not filled:
                        continue
                    if not task_has_data:
                        row = _task_hdr(ws, row, task.name or "—")
                        task_has_data = True
                        has_form_data = True
                    ss = ss_by_subtask.get(old_sub.id)
                    row = _form_hdr(ws, row,
                                    old_sub.name or f"Form {old_sub.num}",
                                    ss.status if ss else None)
                    for q, val in filled:
                        row = _form_row(ws, row,
                                        f"Q{q.num}: {q.question_text or ''}",
                                        val)
                else:
                    resp_val = (resp_by_subtask.get(old_sub.id) or "").strip()
                    if not resp_val:
                        continue
                    if not task_has_data:
                        row = _task_hdr(ws, row, task.name or "—")
                        task_has_data = True
                        has_form_data = True
                    ss = ss_by_subtask.get(old_sub.id)
                    row = _form_hdr(ws, row,
                                    old_sub.name or f"Item {old_sub.num}",
                                    ss.status if ss else None)
                    row = _form_row(ws, row, "Response", resp_val)

        if not has_form_data:
            row = _empty_row(ws, row, "No form details have been filled for this milestone.")

        row += 1  # gap

        # ══ ACTIVITIES TABLE ══════════════════════════════════════════════════
        all_activities = [
            (task, sub, act)
            for task in tasks_sorted
            for sub  in sorted(task.subtasks, key=lambda x: x.num or 0)
            for act  in sorted(sub.activities, key=lambda x: x.id)
        ]

        if all_activities:
            row = _section_label(ws, row, "  ⚡  ACTIVITIES", ACT_SEC_FILL)
            ACT_HDRS = ["Task", "Subtask", "Activity Name", "Status", "Assignee",
                        "Planned Start", "Planned End", "Actual Start",
                        "Actual End", "Est. Hours"]
            row = _col_headers(ws, row, ACT_HDRS)

            for ai, (task, sub, act) in enumerate(all_activities):
                a_status = act.status or "Not Started"
                a_sfill  = STATUS_FILLS.get(a_status, TODO_FILL)
                a_scolor = STATUS_COLORS.get(a_status, "666666")
                row_bg   = EVEN_FILL if ai % 2 == 0 else ODD_FILL
                _c(ws, row, 1,  task.name or "—", italic=True,
                   color="555555", bg=row_bg)
                _c(ws, row, 2,  sub.name  or "—", italic=True,
                   color="555555", bg=row_bg)
                _c(ws, row, 3,  act.name  or "", bold=True, bg=row_bg, wrap=True)
                _c(ws, row, 4,  a_status, bold=True,
                   color=a_scolor, align="center", bg=a_sfill)
                _c(ws, row, 5,  act.assignee or "—", bg=row_bg)
                _c(ws, row, 6,  _fmt_date(act.planned_start), align="center", bg=row_bg)
                _c(ws, row, 7,  _fmt_date(act.planned_end),   align="center", bg=row_bg)
                _c(ws, row, 8,  _fmt_date(act.actual_start),  align="center", bg=row_bg)
                _c(ws, row, 9,  _fmt_date(act.actual_end),    align="center", bg=row_bg)
                _c(ws, row, 10, act.estimated_hours or 0, align="center", bg=row_bg)
                ws.row_dimensions[row].height = 17
                row += 1

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=project-wbs-{project_id}.xlsx"})


# ── PDF Export ────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/export/pdf")
def export_pdf(project_id: int, milestones: str = None, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, PageBreak, HRFlowable)
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from datetime import date

    project, pms, milestones, resp_by_q, resp_by_sub, ss_by_sub = _load_project_data(db, project_id)
    if milestones:
        ms_nums = [int(x.strip()) for x in milestones.split(',') if x.strip().isdigit()]
        if ms_nums:
            pms = [pm for pm in pms if pm.num in ms_nums]

    # Build a num → CustomMilestone lookup so PDF headers show the actual status
    # rather than the ProjectMilestone default ("Not Started").
    cm_by_num = {
        cm.num: cm for cm in db.query(CustomMilestone).filter_by(
            project_id=project_id, is_active=True
        ).all()
    }

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                             leftMargin=1.5*cm, rightMargin=1.5*cm,
                             topMargin=1.5*cm, bottomMargin=1.5*cm)

    # Styles
    styles = getSampleStyleSheet()
    style_title   = ParagraphStyle("title",   fontSize=18, fontName="Helvetica-Bold",
                                   textColor=colors.HexColor("#1F3864"), spaceAfter=4, alignment=TA_LEFT)
    style_sub     = ParagraphStyle("sub",     fontSize=9,  fontName="Helvetica",
                                   textColor=colors.HexColor("#555555"), spaceAfter=12)
    style_ms_hdr  = ParagraphStyle("ms_hdr",  fontSize=12, fontName="Helvetica-Bold",
                                   textColor=colors.white, spaceAfter=0)
    style_task    = ParagraphStyle("task",    fontSize=10, fontName="Helvetica-Bold",
                                   textColor=colors.white, spaceAfter=0)
    style_subtask = ParagraphStyle("subtask", fontSize=9,  fontName="Helvetica-Bold",
                                   textColor=colors.HexColor("#1F3864"), spaceAfter=0)
    style_cell    = ParagraphStyle("cell",    fontSize=8,  fontName="Helvetica",
                                   textColor=colors.HexColor("#333333"), spaceAfter=0, leading=10)
    style_resp    = ParagraphStyle("resp",    fontSize=8,  fontName="Helvetica",
                                   textColor=colors.HexColor("#0D47A1"), spaceAfter=0, leading=10)
    style_empty   = ParagraphStyle("empty",   fontSize=8,  fontName="Helvetica-Oblique",
                                   textColor=colors.HexColor("#AAAAAA"), spaceAfter=0)

    # Color constants
    C_NAVY   = colors.HexColor("#1F3864")
    C_BLUE   = colors.HexColor("#2E75B6")
    C_LBLUE  = colors.HexColor("#5B9BD5")
    C_LIGHT  = colors.HexColor("#EBF3FB")
    C_WHITE  = colors.white
    C_HDRB   = colors.HexColor("#BDD7EE")
    C_DONE   = colors.HexColor("#E2EFDA")
    C_PROG   = colors.HexColor("#FFF2CC")
    C_OVER   = colors.HexColor("#FCE4EC")
    C_TODO   = colors.HexColor("#F0F0F0")
    C_UNFILL = colors.HexColor("#FFFDE7")

    STATUS_BG = {
        "Completed": C_DONE, "In Progress": C_PROG,
        "Overdue": C_OVER, "Not Started": C_TODO,
    }
    STATUS_FG = {
        "Completed": colors.HexColor("#375623"),
        "In Progress": colors.HexColor("#7F6000"),
        "Overdue": colors.HexColor("#A32D2D"),
        "Not Started": colors.HexColor("#666666"),
    }

    story = []
    page_w = landscape(A4)[0] - 3*cm
    col_widths = [1.2*cm, 8*cm, 9*cm, 4*cm, 3*cm, 3*cm]

    # ── Cover page ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("PROJECT WBS", ParagraphStyle("cover1", fontSize=32,
        fontName="Helvetica-Bold", textColor=C_NAVY, alignment=TA_CENTER)))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Requirement Gathering & Tracking Report",
        ParagraphStyle("cover2", fontSize=16, fontName="Helvetica",
                       textColor=C_BLUE, alignment=TA_CENTER)))
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="80%", thickness=2, color=C_NAVY, spaceAfter=0.5*cm))

    cover_data = [
        ["Project:", project.name if project else "—"],
        ["Client:", project.client if project else "—"],
        ["Owner:", project.owner if project else "—"],
        ["Exported by:", current_user.name],
        ["Export date:", date.today().strftime("%d %B %Y")],
        ["Total milestones:", str(len(pms))],
    ]
    cover_table = Table(cover_data, colWidths=[5*cm, 12*cm])
    cover_table.setStyle(TableStyle([
        ("FONTNAME",    (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE",    (0,0), (-1,-1), 11),
        ("FONTNAME",    (0,0), (0,-1),  "Helvetica-Bold"),
        ("TEXTCOLOR",   (0,0), (0,-1),  C_NAVY),
        ("TEXTCOLOR",   (1,0), (1,-1),  colors.HexColor("#333333")),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#F8F8FF"), C_WHITE]),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 12),
        ("ALIGN",       (0,0), (-1,-1), "LEFT"),
    ]))
    story.append(cover_table)
    story.append(PageBreak())

    # ── Milestone pages ───────────────────────────────────────────────────────
    for pm in pms:
        ms = milestones.get(pm.num)
        if not ms: continue

        # Milestone header — use CustomMilestone.status (actual configured value)
        _cm = cm_by_num.get(pm.num)
        _cm_status = _cm.status if _cm and _cm.status else pm.status
        ms_hdr = Table([[Paragraph(
            f"Milestone {pm.num:02d} — {ms.name.upper()}   |   Status: {_cm_status}   |   Progress: {pm.progress:.1f}%",
            style_ms_hdr)]], colWidths=[page_w])
        ms_hdr.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,-1), C_NAVY),
            ("TOPPADDING",    (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
            ("LEFTPADDING",   (0,0), (-1,-1), 10),
        ]))
        story.append(ms_hdr)
        story.append(Spacer(1, 0.2*cm))

        for task in sorted(ms.tasks, key=lambda x: x.num or 0):
            # Task header
            task_hdr = Table([[Paragraph(
                f"Task {task.num:02d} — {task.name}", style_task)]], colWidths=[page_w])
            task_hdr.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), C_LBLUE),
                ("TOPPADDING",    (0,0), (-1,-1), 6),
                ("BOTTOMPADDING", (0,0), (-1,-1), 6),
                ("LEFTPADDING",   (0,0), (-1,-1), 10),
            ]))
            story.append(task_hdr)

            # Column headers
            col_hdr = Table([[
                Paragraph("#", style_subtask),
                Paragraph("Subtask / Question", style_subtask),
                Paragraph("Response / Input", style_subtask),
                Paragraph("Owner", style_subtask),
                Paragraph("Status", style_subtask),
                Paragraph("Sign-off", style_subtask),
            ]], colWidths=col_widths)
            col_hdr.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,-1), C_HDRB),
                ("TOPPADDING",    (0,0), (-1,-1), 5),
                ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                ("LEFTPADDING",   (0,0), (-1,-1), 6),
                ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#CCCCCC")),
            ]))
            story.append(col_hdr)

            rows = []
            row_styles = []
            idx = 0

            for sub in sorted(task.subtasks, key=lambda x: x.num or 0):
                ss = ss_by_sub.get(sub.id)
                sub_status = ss.status if ss else "Not Started"
                signed = ss.reviewer if ss and ss.signed_off_at else "—"
                bg = STATUS_BG.get(sub_status, C_TODO)
                fg = STATUS_FG.get(sub_status, colors.HexColor("#666666"))

                if sub.is_format and sub.questions:
                    # Subtask group label
                    rows.append([
                        Paragraph("", style_cell),
                        Paragraph(f"▸  {sub.name}", style_subtask),
                        Paragraph("", style_cell),
                        Paragraph(task.responsibility, style_cell),
                        Paragraph(sub_status, ParagraphStyle("st", fontSize=8,
                            fontName="Helvetica-Bold", textColor=fg, spaceAfter=0)),
                        Paragraph(signed, style_cell),
                    ])
                    row_styles.append(("BACKGROUND", (0,idx), (-1,idx), colors.HexColor("#EEF4FB")))
                    idx += 1

                    for q in sorted(sub.questions, key=lambda x: x.num or 0):
                        val = resp_by_q.get(q.id, "")
                        alt_bg = C_LIGHT if idx % 2 == 0 else C_WHITE
                        rows.append([
                            Paragraph(str(q.num), style_cell),
                            Paragraph(q.question_text or "", style_cell),
                            Paragraph(val, style_resp) if val else Paragraph("— not filled —", style_empty),
                            Paragraph(task.responsibility, style_cell),
                            Paragraph(sub_status, ParagraphStyle("st2", fontSize=8,
                                fontName="Helvetica-Bold", textColor=fg, spaceAfter=0)),
                            Paragraph(signed, style_cell),
                        ])
                        row_styles.append(("BACKGROUND", (0,idx), (-1,idx), alt_bg if val else C_UNFILL))
                        idx += 1
                else:
                    val = resp_by_sub.get(sub.id, "")
                    alt_bg = C_LIGHT if idx % 2 == 0 else C_WHITE
                    rows.append([
                        Paragraph(str(sub.num or ""), style_cell),
                        Paragraph(sub.name, ParagraphStyle("sn", fontSize=9,
                            fontName="Helvetica-Bold", textColor=colors.HexColor("#1F3864"), spaceAfter=0)),
                        Paragraph(val, style_resp) if val else Paragraph("— not filled —", style_empty),
                        Paragraph(task.responsibility, style_cell),
                        Paragraph(sub_status, ParagraphStyle("st3", fontSize=8,
                            fontName="Helvetica-Bold", textColor=fg, spaceAfter=0)),
                        Paragraph(signed, style_cell),
                    ])
                    row_styles.append(("BACKGROUND", (0,idx), (-1,idx), alt_bg if val else C_UNFILL))
                    idx += 1

            if rows:
                t = Table(rows, colWidths=col_widths, repeatRows=0)
                ts = TableStyle([
                    ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#DDDDDD")),
                    ("TOPPADDING",    (0,0), (-1,-1), 4),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                    ("LEFTPADDING",   (0,0), (-1,-1), 6),
                    ("RIGHTPADDING",  (0,0), (-1,-1), 4),
                    ("VALIGN",        (0,0), (-1,-1), "TOP"),
                ] + row_styles)
                t.setStyle(ts)
                story.append(t)

            story.append(Spacer(1, 0.3*cm))

        story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)
    return StreamingResponse(buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=project-wbs-{project_id}.pdf"})


# ── Dashboard Summary Export ───────────────────────────────────────────────────
@router.get("/projects/{project_id}/export/dashboard-xlsx")
def export_dashboard_xlsx(project_id: int, db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_user)):
    """Export a concise Excel summary of the Project Dashboard:
    overall progress, milestone status table, task assignment counts,
    and work-hour summary — matching exactly what the Dashboard page shows."""
    from datetime import date as _date

    project = db.query(Project).filter_by(id=project_id).first()

    # Active custom milestones (progress computed from CustomTask statuses)
    active_cms = (db.query(CustomMilestone)
                    .filter_by(project_id=project_id, is_active=True)
                    .options(joinedload(CustomMilestone.tasks))
                    .order_by(CustomMilestone.num).all())

    # Task assignment counts
    from app.models.models import TaskAssignment
    assignments = db.query(TaskAssignment).filter_by(project_id=project_id).all()

    # Work hours summary
    wh_rows = db.query(WorkHours).filter_by(project_id=project_id).all()
    total_wh   = round(sum(float(r.hours_spent or 0) for r in wh_rows), 2)
    billed_wh  = round(sum(float(r.hours_spent or 0) for r in wh_rows if r.is_billable), 2)

    # Compute per-milestone progress
    def _ms_pct(cm):
        if cm.status == "Completed":
            return 100.0
        if not cm.tasks:
            return 0.0
        done = sum(1 for t in cm.tasks if t.status == "Completed")
        return round(done / len(cm.tasks) * 100, 1)

    ms_pcts = [_ms_pct(cm) for cm in active_cms]
    proj_pct = round(sum(ms_pcts) / len(ms_pcts), 1) if ms_pcts else 0.0

    # Milestone status counts
    status_counts = {"Completed": 0, "In Progress": 0, "Overdue": 0, "Not Started": 0, "On Hold": 0}
    for cm in active_cms:
        s = cm.status or "Not Started"
        status_counts[s] = status_counts.get(s, 0) + 1

    # Assignment counts
    asgn_total     = len(assignments)
    asgn_prog      = sum(1 for a in assignments if a.status == "In Progress")
    asgn_done      = sum(1 for a in assignments if a.status == "Completed")
    asgn_overdue   = sum(1 for a in assignments if a.status == "Overdue")

    # ── Build workbook ────────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dashboard Summary"

    def fill(hex_c):
        return PatternFill("solid", fgColor=hex_c)
    def bdr():
        s = Side(style="thin", color="CCCCCC")
        return Border(left=s, right=s, top=s, bottom=s)

    NAVY   = fill("1F3864")
    HDRB   = fill("BDD7EE")
    INFOB  = fill("D9E8F5")
    EVEN   = fill("EBF3FB")
    ODD    = fill("FFFFFF")
    STATUS_FILLS = {
        "Completed": fill("E2EFDA"), "In Progress": fill("FFF2CC"),
        "Overdue":   fill("FCE4EC"), "Not Started": fill("F0F0F0"),
        "On Hold":   fill("EDE7F6"),
    }
    STATUS_COLORS = {
        "Completed": "375623", "In Progress": "7F6000",
        "Overdue":   "A32D2D", "Not Started": "666666",
        "On Hold":   "4A148C",
    }

    LAST = "H"
    N    = 8
    col_widths = [5, 26, 14, 16, 16, 13, 13, 12]
    for letter, w in zip("ABCDEFGH", col_widths):
        ws.column_dimensions[letter].width = w

    def merge_row(r, val, fg="FFFFFF", bg=None, bold=False, italic=False, size=9, h=17):
        ws.merge_cells(f"A{r}:{LAST}{r}")
        c = ws[f"A{r}"]
        c.value = val
        c.font = Font(size=size, bold=bold, italic=italic, color=fg, name="Calibri")
        if bg: c.fill = bg
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[r].height = h
        return r + 1

    def cell(r, col, val, bold=False, italic=False, color="333333",
             align="left", bg=None, wrap=False, size=9):
        c = ws.cell(r, col, val if val not in (None, "") else "")
        c.font = Font(size=size, bold=bold, italic=italic, color=color, name="Calibri")
        if bg: c.fill = bg
        c.border = bdr()
        c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
        return c

    def col_hdr(r, headers):
        for ci, h in enumerate(headers, 1):
            c = ws.cell(r, ci, h)
            c.font = Font(bold=True, color="1F3864", size=9, name="Calibri")
            c.fill = HDRB; c.border = bdr()
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[r].height = 17
        return r + 1

    row = 1

    # Title
    row = merge_row(row, f"PROJECT DASHBOARD — {(project.name or '').upper()}",
                    fg="FFFFFF", bg=NAVY, bold=True, size=13, h=28)
    row = merge_row(row,
        f"Client: {project.client or '—'}   |   Owner: {project.owner or '—'}   |   "
        f"Exported by: {current_user.name}   |   Date: {_date.today().strftime('%d %B %Y')}",
        fg="555555", bg=INFOB, italic=True, size=8, h=14)
    row += 1

    # ── Overall progress bar (text representation) ────────────────────────────
    row = merge_row(row, "  📊  OVERALL PROGRESS", fg="FFFFFF",
                    bg=fill("3730A3"), bold=True, size=10, h=19)
    ws.merge_cells(f"A{row}:{LAST}{row}")
    pc = ws[f"A{row}"]
    pc.value = f"  Overall Project Progress: {proj_pct}%   ({sum(1 for cm in active_cms if cm.status == 'Completed')} of {len(active_cms)} milestones completed)"
    pc.font = Font(size=11, bold=True, color="1F3864", name="Calibri")
    pc.fill = INFOB; pc.border = bdr()
    pc.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[row].height = 20
    row += 1
    row += 1

    # ── Milestone Status Summary ───────────────────────────────────────────────
    row = merge_row(row, "  🏁  MILESTONE STATUS SUMMARY", fg="FFFFFF",
                    bg=fill("3730A3"), bold=True, size=10, h=19)
    row = col_hdr(row, ["#", "Milestone Name", "Status", "Assignee",
                         "Planned Start", "Planned End", "Progress %", "Tasks Done"])

    for i, cm in enumerate(active_cms):
        pct    = ms_pcts[i]
        done   = sum(1 for t in cm.tasks if t.status == "Completed")
        total  = len(cm.tasks)
        s      = cm.status or "Not Started"
        sfill  = STATUS_FILLS.get(s, fill("F0F0F0"))
        scolor = STATUS_COLORS.get(s, "666666")
        bg     = EVEN if i % 2 == 0 else ODD
        cell(row, 1, f"M{cm.num:02d}", bold=True, align="center", bg=bg)
        cell(row, 2, cm.name or "", bold=True, bg=bg)
        cell(row, 3, s, bold=True, color=scolor, align="center", bg=sfill)
        cell(row, 4, cm.assignee or "—", bg=bg)
        cell(row, 5, _fmt_date(cm.planned_start), align="center", bg=bg)
        cell(row, 6, _fmt_date(cm.planned_end),   align="center", bg=bg)
        cell(row, 7, f"{pct}%", bold=True, align="center", bg=bg,
             color="375623" if pct == 100 else ("7F6000" if pct > 0 else "666666"))
        cell(row, 8, f"{done} / {total}", align="center", bg=bg)
        ws.row_dimensions[row].height = 17
        row += 1

    row += 1

    # ── Task Assignments ──────────────────────────────────────────────────────
    row = merge_row(row, "  📌  TASK ASSIGNMENTS", fg="FFFFFF",
                    bg=fill("3730A3"), bold=True, size=10, h=19)
    row = col_hdr(row, ["Metric", "Value", "", "", "", "", "", ""])
    for label, val, color in [
        ("Total Assignments",     asgn_total,   "333333"),
        ("In Progress",           asgn_prog,    "7F6000"),
        ("Completed",             asgn_done,    "375623"),
        ("Overdue",               asgn_overdue, "A32D2D"),
    ]:
        cell(row, 1, label, bold=True, bg=ODD)
        cell(row, 2, val,   bold=True, color=color, align="center", bg=ODD)
        for ci in range(3, N + 1):
            ws.cell(row, ci).border = bdr()
        ws.row_dimensions[row].height = 16
        row += 1

    row += 1

    # ── Work Hours Summary ────────────────────────────────────────────────────
    row = merge_row(row, "  ⏱️  WORK HOURS SUMMARY", fg="FFFFFF",
                    bg=fill("3730A3"), bold=True, size=10, h=19)
    row = col_hdr(row, ["Metric", "Hours", "", "", "", "", "", ""])
    for label, val in [
        ("Total Hours Logged",  total_wh),
        ("Billable Hours",      billed_wh),
        ("Non-Billable Hours",  round(total_wh - billed_wh, 2)),
    ]:
        cell(row, 1, label, bold=True, bg=ODD)
        cell(row, 2, val,   align="center", bg=ODD)
        for ci in range(3, N + 1):
            ws.cell(row, ci).border = bdr()
        ws.row_dimensions[row].height = 16
        row += 1

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    proj_name = (project.name or "project").replace(" ", "-").lower()
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=dashboard-{proj_name}.xlsx"})
