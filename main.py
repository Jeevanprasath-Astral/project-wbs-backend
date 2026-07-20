from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.database import engine, Base
from app.models import models
from app.api.routes import (auth, projects, milestones, responses,
                             dashboard, export, assignments,
                             global_modules, global_team, work_hours,
                             custom_milestones, timesheet, costs,
                             timesheet_reports, project_reports,
                             profitability_report, project_billings,
                             team_utilization_report, cost_breakdown_report,
                             billing_statement_report, hours_tracker,
                             report_templates, attachments)
from fastapi.staticfiles import StaticFiles
from app.services.scheduler import start_scheduler, stop_scheduler
from app.utils.warmup import start_warmup, stop_warmup
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(name)s — %(levelname)s — %(message)s")
# Create tables individually so a pre-existing table (e.g. from a previous
# partial deploy) doesn't crash the whole startup sequence.
for _table in Base.metadata.sorted_tables:
    try:
        _table.create(bind=engine, checkfirst=True)
    except Exception as _e:
        logging.warning(f"Table '{_table.name}' creation skipped: {_e}")

def _run_lightweight_migrations():
    """Add columns that create_all() can't add to already-existing tables."""
    from sqlalchemy import text
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id)",
        "ALTER TABLE task_assignments ADD COLUMN IF NOT EXISTS planned_start TIMESTAMP",
        "ALTER TABLE task_assignments ADD COLUMN IF NOT EXISTS planned_end TIMESTAMP",
        "ALTER TABLE task_assignments ADD COLUMN IF NOT EXISTS actual_start TIMESTAMP",
        "ALTER TABLE task_assignments ADD COLUMN IF NOT EXISTS actual_end TIMESTAMP",
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS buffer_hours FLOAT DEFAULT 0",
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS buffer_category VARCHAR(100)",
        # Billable / Non-Billable flag on every logged hours entry.
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS is_billable BOOLEAN DEFAULT TRUE",
        # Work hours — granular level linkage (Milestone/Task/Subtask/Activity)
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS level VARCHAR(20)",
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS custom_milestone_id INTEGER REFERENCES custom_milestones(id)",
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS custom_task_id INTEGER REFERENCES custom_tasks(id)",
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS custom_subtask_id INTEGER REFERENCES custom_subtasks(id)",
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS activity_id INTEGER REFERENCES activities(id)",
        # Custom tasks — timeline + status (merged from Timeline Management)
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'Not Started'",
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS assignee VARCHAR(200)",
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS planned_start TIMESTAMP",
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS planned_end TIMESTAMP",
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS actual_start TIMESTAMP",
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS actual_end TIMESTAMP",
        # Custom subtasks — timeline
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS assignee VARCHAR(200)",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS planned_start TIMESTAMP",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS planned_end TIMESTAMP",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS actual_start TIMESTAMP",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS actual_end TIMESTAMP",
        # Time-of-day + Working Hours Management (Milestone/Task/Subtask/Activity)
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'Not Started'",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS assignee VARCHAR(200)",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS planned_start TIMESTAMP",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS planned_end TIMESTAMP",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS actual_start TIMESTAMP",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS actual_end TIMESTAMP",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS start_time VARCHAR(10)",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS end_time VARCHAR(10)",
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS start_time VARCHAR(10)",
        "ALTER TABLE custom_tasks ADD COLUMN IF NOT EXISTS end_time VARCHAR(10)",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS start_time VARCHAR(10)",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS end_time VARCHAR(10)",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS estimated_hours FLOAT DEFAULT 0",
        "ALTER TABLE activities ADD COLUMN IF NOT EXISTS start_time VARCHAR(10)",
        "ALTER TABLE activities ADD COLUMN IF NOT EXISTS end_time VARCHAR(10)",
        "ALTER TABLE activities ADD COLUMN IF NOT EXISTS estimated_hours FLOAT DEFAULT 0",
        # General Tasks — allow task assignments, work hours and notifications
        # to exist without being linked to a specific project.
        "ALTER TABLE task_assignments ALTER COLUMN project_id DROP NOT NULL",
        "ALTER TABLE work_hours ALTER COLUMN project_id DROP NOT NULL",
        "ALTER TABLE notifications ALTER COLUMN project_id DROP NOT NULL",
        # Assign Task — Task-level targeting. Null = "General" (standalone task
        # not tied to the Milestone/Task hierarchy of Milestone Configuration).
        "ALTER TABLE task_assignments ADD COLUMN IF NOT EXISTS custom_task_id INTEGER REFERENCES custom_tasks(id)",
        # Subtask single answer (input_type/response are on the model but were
        # missing here — on a DB whose custom_subtasks table predates these
        # columns, create_all() alone never adds them).
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS input_type VARCHAR(50) DEFAULT 'text'",
        "ALTER TABLE custom_subtasks ADD COLUMN IF NOT EXISTS response TEXT",
        # Cost Management — overall project budget, compared against the sum
        # of project_costs.cost rows.
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS budget FLOAT DEFAULT 0",
        # Milestone Templates feature was removed (superseded by Subtask
        # Reports below) — drop the provenance-tag columns and tables it had
        # added, in case this DB already ran the earlier migration.
        "ALTER TABLE custom_subtasks DROP COLUMN IF EXISTS template_subtask_id",
        "ALTER TABLE custom_tasks DROP COLUMN IF EXISTS template_task_id",
        "ALTER TABLE custom_milestones DROP COLUMN IF EXISTS template_milestone_id",
        "ALTER TABLE custom_milestones DROP COLUMN IF EXISTS template_id",
        "ALTER TABLE projects DROP COLUMN IF EXISTS milestone_template_id",
        "DROP TABLE IF EXISTS template_subtasks",
        "DROP TABLE IF EXISTS template_tasks",
        "DROP TABLE IF EXISTS template_milestones",
        "DROP TABLE IF EXISTS milestone_templates",
        # Subtask Reports — lets multiple reports (Report Number/Name/
        # Department + light tracking fields) point at one Milestone/Task/
        # Subtask instead of duplicating structure per report. Table itself
        # is created by create_all(); nothing to ALTER here yet.
        # Project Reports — schedule variance reason captured at milestone
        # level for the Timeline Report export.
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS schedule_variance_reason TEXT",
        # Profitability Report — billing amount (client contract value) on
        # Project and hourly cost rate on User for manpower cost calculation.
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS billing_amount FLOAT DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS cost_rate FLOAT DEFAULT 0",
        # Billing History — project_billings table is created by create_all()
        # since the ProjectBilling model was added before startup. No ALTER needed.
        # Keeping billing_amount column on projects for backward compat (unused now).
        # Milestone Iteration — revision tracking on existing custom_milestones rows.
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS iteration INTEGER DEFAULT 1",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS revision_reason VARCHAR(100)",
        "ALTER TABLE custom_milestones ADD COLUMN IF NOT EXISTS revision_description TEXT",
        "UPDATE custom_milestones SET iteration = 1 WHERE iteration IS NULL",
        # work_type — full classification replacing the boolean is_billable flag.
        # Values: Billable | Non-Billable | No Work | Training | R&D
        # Back-fill existing rows so no data is lost.
        "ALTER TABLE work_hours ADD COLUMN IF NOT EXISTS work_type VARCHAR(30)",
        "UPDATE work_hours SET work_type = CASE WHEN is_billable = TRUE THEN 'Billable' WHEN is_billable = FALSE THEN 'Non-Billable' ELSE 'No Work' END WHERE work_type IS NULL",
        # project_category — billing classification for My Projects grouping view.
        # Values: Billable | Non-Billable | R&D
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS project_category VARCHAR(30) DEFAULT 'Billable'",
        "UPDATE projects SET project_category = 'Billable' WHERE project_category IS NULL",
        # Milestone Reports — reports attached at the Milestone level (replacing
        # the old Subtask-level SubtaskReport for report association). Explicitly
        # create the table in case this DB was initialised before the model was
        # added (create_all only adds new tables; it can't add to existing DBs).
        """CREATE TABLE IF NOT EXISTS milestone_reports (
            id SERIAL PRIMARY KEY,
            milestone_id INTEGER NOT NULL REFERENCES custom_milestones(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            report_number VARCHAR(100) NOT NULL,
            report_name VARCHAR(300) NOT NULL,
            department VARCHAR(150),
            status VARCHAR(50) DEFAULT 'Not Started',
            assigned_to VARCHAR(200),
            due_date DATE,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_milestone_report_number UNIQUE (milestone_id, report_number)
        )""",
        # Custom Roles — user-defined roles that extend the built-in list.
        # Table is created by create_all() via the new CustomRole model; the
        # IF NOT EXISTS guard makes this idempotent on existing databases.
        """CREATE TABLE IF NOT EXISTS custom_roles (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ DEFAULT now()
        )""",
        # Assignment Categories — task classification labels (Business Development /
        # R&D / L&D + any custom ones the user adds).
        """CREATE TABLE IF NOT EXISTS assignment_categories (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ DEFAULT now()
        )""",
        # Category column on task_assignments — free-text label filled from the
        # assignment_categories dropdown (or left NULL for uncategorised tasks).
        "ALTER TABLE task_assignments ADD COLUMN IF NOT EXISTS category VARCHAR(100)",
        # Report Templates — reusable named sets of report definitions.
        """CREATE TABLE IF NOT EXISTS report_templates (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL UNIQUE,
            description TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMPTZ DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS report_template_items (
            id SERIAL PRIMARY KEY,
            template_id INTEGER NOT NULL REFERENCES report_templates(id) ON DELETE CASCADE,
            report_number VARCHAR(100) NOT NULL,
            report_name VARCHAR(300) NOT NULL,
            department VARCHAR(150),
            created_at TIMESTAMPTZ DEFAULT now()
        )""",
        # Attachments — polymorphic file uploads (milestone/task/subtask/activity/report).
        """CREATE TABLE IF NOT EXISTS attachments (
            id SERIAL PRIMARY KEY,
            entity_type VARCHAR(50) NOT NULL,
            entity_id INTEGER NOT NULL,
            original_filename VARCHAR(300) NOT NULL,
            stored_filename VARCHAR(300) NOT NULL,
            file_size INTEGER,
            mime_type VARCHAR(100),
            uploaded_by INTEGER REFERENCES users(id),
            created_at TIMESTAMPTZ DEFAULT now()
        )""",
        # Employee filter bug fix — Task #157 seeded 5 role-based demo accounts
        # using the role name as the user's display name (e.g. name="Project Manager").
        # The Global Dashboard / Workload employee filter then showed role names
        # instead of real names. Rename those placeholder accounts to proper
        # human names. Guard: only match rows where name = role exactly, so
        # real accounts whose names happen to match are not affected.
        "UPDATE users SET name = 'Arjun Ramachandran'   WHERE name = 'Project Manager'   AND role = 'Project Manager'",
        "UPDATE users SET name = 'Priya Krishnamurthy'  WHERE name = 'FC Lead'            AND role = 'FC Lead'",
        "UPDATE users SET name = 'Karthik Subramanian'  WHERE name = 'Technical Lead'     AND role = 'Technical Lead'",
        "UPDATE users SET name = 'Meena Sundaram'       WHERE name = 'HR Manager'         AND role = 'HR Manager'",
        "UPDATE users SET name = 'Suresh Natarajan'     WHERE name = 'Client Reviewer'    AND role = 'Client Reviewer'",
        # ── Performance: indexes on high-traffic FK columns ───────────────────
        # work_hours is the most-queried table (every page that shows actual
        # hours hits it). Without these indexes every filter is a full-table
        # sequential scan; with them PostgreSQL uses a B-tree index seek instead.
        "CREATE INDEX IF NOT EXISTS ix_work_hours_project_id         ON work_hours(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_work_hours_user_id            ON work_hours(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_work_hours_custom_milestone_id ON work_hours(custom_milestone_id)",
        "CREATE INDEX IF NOT EXISTS ix_work_hours_custom_task_id     ON work_hours(custom_task_id)",
        "CREATE INDEX IF NOT EXISTS ix_work_hours_custom_subtask_id  ON work_hours(custom_subtask_id)",
        "CREATE INDEX IF NOT EXISTS ix_work_hours_activity_id        ON work_hours(activity_id)",
        # task_assignments: filtered by project_id and custom_task_id on every
        # Milestone Config list load (was part of the N+1 pattern now fixed).
        "CREATE INDEX IF NOT EXISTS ix_task_assignments_project_id   ON task_assignments(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_task_assignments_custom_task_id ON task_assignments(custom_task_id)",
        "CREATE INDEX IF NOT EXISTS ix_task_assignments_assigned_to  ON task_assignments(assigned_to)",
        # Milestone hierarchy — filtered by project_id on every page open.
        "CREATE INDEX IF NOT EXISTS ix_custom_milestones_project_id  ON custom_milestones(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_custom_tasks_milestone_id     ON custom_tasks(milestone_id)",
        "CREATE INDEX IF NOT EXISTS ix_custom_tasks_project_id       ON custom_tasks(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_custom_subtasks_task_id       ON custom_subtasks(task_id)",
        "CREATE INDEX IF NOT EXISTS ix_custom_subtasks_project_id    ON custom_subtasks(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_activities_subtask_id         ON activities(subtask_id)",
        "CREATE INDEX IF NOT EXISTS ix_activities_project_id         ON activities(project_id)",
        # Notifications: the bell-icon unread count hits this on every page load.
        "CREATE INDEX IF NOT EXISTS ix_notifications_user_id         ON notifications(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_notifications_is_read         ON notifications(is_read)",
        # ── Milestone Config Redesign (Task Form Fields) ──────────────────────
        # TaskFormField replaces the Subtask → Question hierarchy. Each Task now
        # has a flat list of form fields (section_name = former subtask name).
        """CREATE TABLE IF NOT EXISTS task_form_fields (
            id            SERIAL PRIMARY KEY,
            task_id       INTEGER NOT NULL REFERENCES custom_tasks(id) ON DELETE CASCADE,
            milestone_id  INTEGER NOT NULL REFERENCES custom_milestones(id) ON DELETE CASCADE,
            project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            num           INTEGER DEFAULT 1,
            section_name  VARCHAR(300),
            question_text VARCHAR(500) NOT NULL,
            input_type    VARCHAR(50) DEFAULT 'text',
            response      TEXT,
            created_at    TIMESTAMPTZ DEFAULT now()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_task_form_fields_task_id ON task_form_fields(task_id)",
        "CREATE INDEX IF NOT EXISTS ix_task_form_fields_project_id ON task_form_fields(project_id)",
        # Rename milestone names to match new nomenclature. WHERE clause uses
        # LIKE so it catches any existing project's milestones safely. Idempotent.
        "UPDATE custom_milestones SET name = 'Deployment for UAT' WHERE num = 7 AND name = 'Deployment'",
        "UPDATE custom_milestones SET name = 'UAT for End User'   WHERE num = 8 AND name = 'UAT'",
        "UPDATE custom_milestones SET name = 'Post Live Support'  WHERE num = 10 AND name = 'Support'",
    ]
    try:
        with engine.begin() as conn:
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except Exception as e:
                    logging.warning(f"Migration step failed ({stmt}): {e}")
    except Exception as e:
        logging.warning(f"Lightweight migration pass failed: {e}")

_run_lightweight_migrations()

def _update_user_accounts():
    """Set real names, office emails and hashed passwords for the 5 role-based
    accounts.  Matches by OLD email OR new email so the function is idempotent
    across multiple deploys.  Each account runs in its own transaction so one
    failure cannot roll back the others."""
    from app.core.security import hash_password
    from sqlalchemy import text as _sql
    # (old_email, new_name, new_email, password)
    # old_email = what the seed put in; new_email = real office address.
    # If already migrated, old_email won't exist but new_email will — still a no-op update.
    accounts = [
        ("admin@wbs.com",
         "Jeevan Prasath. J", "jeevanprasath.j@astralbusinessconsulting.in", "DEC@jp2801"),
        ("pm@wbs.com",
         "Gayathri. P",       "gayathri.p@astralbusinessconsulting.com",      "PManager@2026"),
        ("hr@wbs.com",
         "Manikandan. S",     "manikandan.s@astralbusinessconsulting.com",     "HRUser@2026"),
        ("fc@wbs.com",
         "Manikandan. M",     "manikandan.m@astralbusinessconsulting.in",      "FCLead@2026"),
        ("tclead@wbs.com",
         "Sanjeev. V",        "sanjeev.v@astralbusinessconsulting.in",         "TCLead@2026"),
    ]
    for old_email, name, new_email, temp_pwd in accounts:
        try:
            with engine.begin() as _conn:
                # Only update name+email. Password is only set if the row is
                # being migrated from its OLD placeholder email — once the real
                # email is already in place the password is left untouched so
                # that a user-changed password survives redeploys.
                already_migrated = _conn.execute(_sql(
                    "SELECT id FROM users WHERE email = :new_email"
                ), {"new_email": new_email}).fetchone()

                if already_migrated:
                    # Row already has the real email — only sync name, leave password alone.
                    res = _conn.execute(_sql(
                        "UPDATE users SET name = :name WHERE email = :new_email"
                    ), {"name": name, "new_email": new_email})
                    logging.info(f"_update_user_accounts NAME-ONLY {new_email}")
                else:
                    # Still on old placeholder email — full migration with initial password.
                    res = _conn.execute(_sql(
                        """UPDATE users
                              SET name          = :name,
                                  email         = :new_email,
                                  password_hash = :ph
                            WHERE id = (
                                SELECT id FROM users
                                WHERE  email = :old_email
                                ORDER  BY id
                                LIMIT  1
                            )"""
                    ), {"name": name, "new_email": new_email,
                        "ph": hash_password(temp_pwd), "old_email": old_email})
                    if res.rowcount:
                        logging.info(f"_update_user_accounts MIGRATED {old_email!r} → {new_email}")
                    else:
                        logging.warning(f"_update_user_accounts SKIP {old_email!r} — not found")
        except Exception as _ex:
            logging.error(f"_update_user_accounts FAIL {old_email!r}: {_ex}")

_update_user_accounts()

app = FastAPI(title=settings.APP_NAME, description="Project WBS API", version="2.0.0")

app.add_middleware(CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for router in [auth, projects, milestones, responses, dashboard,
               export, assignments, global_modules, global_team, work_hours,
               custom_milestones, timesheet, costs, timesheet_reports,
               project_reports, profitability_report, project_billings,
               team_utilization_report, cost_breakdown_report,
               billing_statement_report, hours_tracker,
               report_templates, attachments]:
    app.include_router(router.router, prefix="/api")

# Extra routers from global_team module (custom roles + assignment categories)
app.include_router(global_team._roles_router, prefix="/api")
app.include_router(global_team._cats_router, prefix="/api")

# File storage is handled by Cloudinary — no local StaticFiles mount needed.

def _backfill_subtask_questions(db):
    """One-time backfill for projects that already added a Milestone/Task/
    Subtask from the standard template *before* the from-template routes
    were fixed to also copy multi-question form data. Those CustomSubtasks
    were created with only a flat input_type and silently lost their extra
    questions (e.g. "Understand client business operations" should carry 10
    questions, not 1). For every existing CustomSubtask with zero questions
    whose standard counterpart (matched by Milestone/Task/Subtask num) has
    questions, copy them in now. Subtasks that already have questions, or
    whose standard counterpart never had more than the flat answer, are
    left untouched.
    """
    from app.models.models import (CustomMilestone, CustomTask, CustomSubtask,
                                    Milestone, Task, Subtask, SubtaskQuestion)
    from sqlalchemy.orm import joinedload
    cms = db.query(CustomMilestone).options(
        joinedload(CustomMilestone.tasks).joinedload(CustomTask.subtasks).joinedload(CustomSubtask.questions)
    ).all()
    added = 0
    for cm in cms:
        std_ms = db.query(Milestone).options(
            joinedload(Milestone.tasks).joinedload(Task.subtasks).joinedload(Subtask.questions)
        ).filter_by(num=cm.num).first()
        if not std_ms:
            continue
        for ct in cm.tasks:
            std_task = next((t for t in std_ms.tasks if t.num == ct.num), None)
            if not std_task:
                continue
            for cs in ct.subtasks:
                if cs.questions:
                    continue
                std_sub = next((s for s in std_task.subtasks if s.num == cs.num), None)
                if not std_sub or not std_sub.questions:
                    continue
                for q in sorted(std_sub.questions, key=lambda x: x.num or 0):
                    db.add(SubtaskQuestion(subtask_id=cs.id, project_id=cs.project_id,
                                            num=q.num, question_text=q.question_text,
                                            input_type=q.input_type or "text"))
                    added += 1
    if added:
        db.commit()
        logging.info(f"Backfilled {added} subtask question(s) onto existing custom subtasks")


# ── Excel-driven mapping: Subtask → new Task name (for M5-M10) ───────────────
# For milestones 5-10 each original Subtask becomes an independent Task.
# Key: (milestone_num, original_task_num, subtask_num) → new Task name
# None = subtask has no new Task name (gets folded into parent Task form)
_SUBTASK_TO_NEW_TASK: dict = {
    # M5 Development
    (5, 1, 1): "Database object creation",
    (5, 1, 2): "Data extraction development",
    (5, 1, 3): "Business logic development",
    (5, 1, 4): "Report development",
    (5, 1, 5): "Dashboard development",
    (5, 1, 6): "Validation implementation",
    (5, 1, 7): "Internal developer testing",
    (5, 1, 8): "Bug fixing",
    # M6 Internal Testing
    (6, 1, 1): "Prepare test scenarios",
    (6, 1, 2): "App testing",
    (6, 1, 3): None,   # stays under App testing form
    (6, 1, 4): None,
    (6, 1, 5): None,
    (6, 1, 6): None,
    (6, 1, 7): "Retest",
    (6, 1, 8): None,
    # M7 Deployment for UAT
    (7, 1, 1): "Server Readiness",
    (7, 1, 2): "Master data Deploy",
    (7, 1, 3): "Deploy solution to UAT environment",
    (7, 1, 4): "Load sample data",
    (7, 1, 5): "Share UAT version",
    (7, 2, 1): "Execute smoke testing",
    (7, 2, 2): "Verify deployment",
    # M8 UAT for End User
    (8, 1, 1): "Provide user training",
    (8, 1, 2): "Conduct UAT walkthrough",
    (8, 1, 3): "End User testing",
    (8, 1, 4): "Validate outputs",
    (8, 1, 5): None,
    (8, 1, 6): None,
    (8, 1, 7): "Fix UAT defects",
    (8, 1, 8): "Re-deploy updated version",
    (8, 1, 9): "UAT sign-off",
    # M9 Go Live
    (9, 1, 1): "Deploy production version",
    (9, 1, 2): "Configure production environment",
    (9, 1, 3): "Validate production data",
    (9, 1, 4): "Perform sanity testing",
    (9, 1, 5): "Obtain go-live approval",
    (9, 1, 6): "Release to users",
    # M10 Post Live Support
    (10, 1, 1): "Monitor application/report",
    (10, 1, 2): "Resolve production issues",
    (10, 1, 3): None,
    (10, 1, 4): None,
    (10, 1, 5): "Handover project documents",
    (10, 1, 6): "Project closure",
}

def _migrate_form_fields(db):
    """One-time migration: convert existing CustomSubtask+SubtaskQuestion rows
    into TaskFormField rows.

    Strategy:
    - M1-M4: Subtasks fold into their parent Task as Form sections.
      Each SubtaskQuestion becomes a TaskFormField with section_name=subtask.name.
    - M5-M10: Subtasks with a mapped new Task name get their own new CustomTask
      (if not already present), and their Questions become FormFields of that
      new Task. Subtasks with no mapping (None) get folded into the nearest
      preceding named Task.
    - WorkHours rows with a custom_subtask_id are re-pointed to the parent
      (or newly promoted) Task.
    """
    from app.models.models import (
        CustomMilestone, CustomTask, CustomSubtask, SubtaskQuestion,
        TaskFormField, WorkHours
    )
    from sqlalchemy.orm import joinedload

    # Only run if task_form_fields table is empty (idempotent guard)
    existing = db.execute(text("SELECT COUNT(*) FROM task_form_fields")).scalar()
    if existing > 0:
        logging.info(f"_migrate_form_fields: already seeded ({existing} rows), skipping")
        return

    added_fields = 0
    added_tasks  = 0
    updated_wh   = 0

    milestones = db.query(CustomMilestone).o