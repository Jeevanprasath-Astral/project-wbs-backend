from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from io import BytesIO
from app.db.database import get_db
from app.models.models import (Project, ProjectMilestone, Milestone, Task,
                                Subtask, Question, Response, SubtaskStatus, User)
from app.core.deps import get_current_user
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

router = APIRouter(tags=["Export"])

# ── Shared helpers ────────────────────────────────────────────────────────────
def _load_project_data(db, project_id):
    """Load all milestone data in bulk to avoid N+1 queries."""
    from sqlalchemy.orm import joinedload
    project = db.query(Project).filter_by(id=project_id).first()
    pms = db.query(ProjectMilestone).filter_by(
        project_id=project_id).order_by(ProjectMilestone.num).all()
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


# ── Excel Export ──────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/export/xlsx")
def export_excel(project_id: int, milestone: int = None, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    project, pms, milestones, resp_by_q, resp_by_sub, ss_by_sub = _load_project_data(db, project_id)
    if milestone:
        pms = [pm for pm in pms if pm.num == milestone]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # Styles
    def fill(hex_c): return PatternFill("solid", fgColor=hex_c)
    def bdr():
        s = Side(style="thin", color="CCCCCC")
        return Border(left=s, right=s, top=s, bottom=s)

    HDR_FILL  = fill("1F3864"); SUB_FILL  = fill("2E75B6")
    TASK_FILL = fill("5B9BD5"); COL_FILL  = fill("BDD7EE")
    EVEN_FILL = fill("EBF3FB"); ODD_FILL  = fill("FFFFFF")
    DONE_FILL = fill("E2EFDA"); PROG_FILL = fill("FFF2CC")
    OVER_FILL = fill("FCE4EC"); TODO_FILL = fill("F0F0F0")

    STATUS_FILLS = {
        "Completed":   DONE_FILL, "In Progress": PROG_FILL,
        "Overdue":     OVER_FILL, "Not Started": TODO_FILL,
    }
    STATUS_COLORS = {
        "Completed":   "375623", "In Progress": "7F6000",
        "Overdue":     "A32D2D", "Not Started": "666666",
    }

    for pm in pms:
        ms = milestones.get(pm.num)
        if not ms: continue

        ws = wb.create_sheet(f"M{pm.num:02d}-{ms.name[:18]}")
        for col, w in zip("ABCDEF", [5, 36, 36, 22, 16, 20]):
            ws.column_dimensions[col].width = w

        # Title row
        ws.merge_cells("A1:F1")
        c = ws["A1"]
        c.value = f"M{pm.num:02d} — {ms.name}  |  Status: {pm.status}  |  Progress: {pm.progress}%"
        c.font = Font(bold=True, color="FFFFFF", size=11, name="Calibri")
        c.fill = HDR_FILL; c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[1].height = 24

        # Project info row
        ws.merge_cells("A2:F2")
        c2 = ws["A2"]
        c2.value = f"Project: {project.name if project else ''}  |  Client: {project.client if project else ''}  |  Exported by: {current_user.name}"
        c2.font = Font(italic=True, color="555555", size=9, name="Calibri")
        c2.fill = fill("D9E8F5"); c2.alignment = Alignment(horizontal="left")
        ws.row_dimensions[2].height = 16

        row = 4
        for task in sorted(ms.tasks, key=lambda x: x.num or 0):
            # Task header
            ws.merge_cells(f"A{row}:F{row}")
            tc = ws.cell(row, 1, f"  Task {task.num:02d} — {task.name.upper()}")
            tc.font = Font(bold=True, color="FFFFFF", size=10, name="Calibri")
            tc.fill = TASK_FILL; ws.row_dimensions[row].height = 20
            row += 1

            # Column headers
            for col, h in enumerate(["#", "Subtask / Question", "Response / Input", "Owner", "Status", "Signed Off"], 1):
                hc = ws.cell(row, col, h)
                hc.font = Font(bold=True, color="1F3864", size=9, name="Calibri")
                hc.fill = COL_FILL; hc.border = bdr()
                hc.alignment = Alignment(horizontal="center")
            ws.row_dimensions[row].height = 17; row += 1

            q_row_idx = 0
            for sub in sorted(task.subtasks, key=lambda x: x.num or 0):
                ss = ss_by_sub.get(sub.id)
                sub_status = ss.status if ss else "Not Started"
                signed = ss.reviewer if ss and ss.signed_off_at else "—"
                s_fill = STATUS_FILLS.get(sub_status, TODO_FILL)
                s_color = STATUS_COLORS.get(sub_status, "666666")

                if sub.is_format and sub.questions:
                    # Subtask group header
                    ws.merge_cells(f"B{row}:F{row}")
                    sh = ws.cell(row, 2, f"▸  {sub.name}")
                    sh.font = Font(bold=True, size=9, color="1F3864", name="Calibri")
                    sh.fill = fill("EEF4FB"); sh.border = bdr()
                    ws.cell(row, 1).fill = fill("EEF4FB")
                    ws.cell(row, 1).border = bdr()
                    ws.row_dimensions[row].height = 17; row += 1

                    for q in sorted(sub.questions, key=lambda x: x.num or 0):
                        q_row_idx += 1
                        bg = EVEN_FILL if q_row_idx % 2 == 0 else ODD_FILL
                        val = resp_by_q.get(q.id, "")

                        ws.cell(row, 1, q.num).font = Font(size=9, color="888888", name="Calibri")
                        ws.cell(row, 1).fill = bg; ws.cell(row, 1).border = bdr()
                        ws.cell(row, 1).alignment = Alignment(horizontal="center")

                        ws.cell(row, 2, q.question_text).font = Font(size=9, name="Calibri")
                        ws.cell(row, 2).fill = bg; ws.cell(row, 2).border = bdr()

                        resp_cell = ws.cell(row, 3, val if val else "")
                        resp_cell.font = Font(size=9, color="0D47A1" if val else "BBBBBB", name="Calibri",
                                              italic=not bool(val))
                        resp_cell.value = val if val else "— not filled —"
                        resp_cell.fill = bg if val else fill("FFFDE7"); resp_cell.border = bdr()

                        ws.cell(row, 4, task.responsibility).font = Font(size=9, color="444444", name="Calibri")
                        ws.cell(row, 4).fill = bg; ws.cell(row, 4).border = bdr()
                        ws.cell(row, 4).alignment = Alignment(horizontal="center")

                        sc = ws.cell(row, 5, sub_status)
                        sc.font = Font(size=9, bold=True, color=s_color, name="Calibri")
                        sc.fill = s_fill; sc.border = bdr()
                        sc.alignment = Alignment(horizontal="center")

                        ws.cell(row, 6, signed).font = Font(size=9, color="375623" if signed != "—" else "BBBBBB", name="Calibri")
                        ws.cell(row, 6).fill = bg; ws.cell(row, 6).border = bdr()
                        ws.cell(row, 6).alignment = Alignment(horizontal="center")

                        ws.row_dimensions[row].height = 17; row += 1
                else:
                    q_row_idx += 1
                    bg = EVEN_FILL if q_row_idx % 2 == 0 else ODD_FILL
                    val = resp_by_sub.get(sub.id, "")

                    ws.cell(row, 1, sub.num).font = Font(size=9, name="Calibri")
                    ws.cell(row, 1).fill = bg; ws.cell(row, 1).border = bdr()
                    ws.cell(row, 1).alignment = Alignment(horizontal="center")

                    ws.cell(row, 2, sub.name).font = Font(size=9, bold=True, name="Calibri")
                    ws.cell(row, 2).fill = bg; ws.cell(row, 2).border = bdr()

                    resp_cell = ws.cell(row, 3, val if val else "")
                    resp_cell.font = Font(size=9, color="0D47A1" if val else "BBBBBB", name="Calibri",
                                          italic=not bool(val))
                    resp_cell.value = val if val else "— not filled —"
                    resp_cell.fill = bg if val else fill("FFFDE7"); resp_cell.border = bdr()

                    ws.cell(row, 4, task.responsibility).font = Font(size=9, color="444444", name="Calibri")
                    ws.cell(row, 4).fill = bg; ws.cell(row, 4).border = bdr()
                    ws.cell(row, 4).alignment = Alignment(horizontal="center")

                    sc = ws.cell(row, 5, sub_status)
                    sc.font = Font(size=9, bold=True, color=s_color, name="Calibri")
                    sc.fill = s_fill; sc.border = bdr()
                    sc.alignment = Alignment(horizontal="center")

                    ws.cell(row, 6, signed).font = Font(size=9, color="375623" if signed != "—" else "BBBBBB", name="Calibri")
                    ws.cell(row, 6).fill = bg; ws.cell(row, 6).border = bdr()
                    ws.cell(row, 6).alignment = Alignment(horizontal="center")

                    ws.row_dimensions[row].height = 17; row += 1

            row += 1  # gap between tasks

    output = BytesIO()
    wb.save(output); output.seek(0)
    return StreamingResponse(output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=project-wbs-{project_id}.xlsx"})


# ── PDF Export ────────────────────────────────────────────────────────────────
@router.get("/projects/{project_id}/export/pdf")
def export_pdf(project_id: int, milestone: int = None, db: Session = Depends(get_db),
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
    if milestone:
        pms = [pm for pm in pms if pm.num == milestone]

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
        ["Total milestones:", "10"],
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

        # Milestone header
        ms_hdr = Table([[Paragraph(
            f"Milestone {pm.num:02d} — {ms.name.upper()}   |   Status: {pm.status}   |   Progress: {pm.progress:.1f}%",
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
