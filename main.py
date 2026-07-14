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
Base.metadata.create_all(bind=engine)

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
    """One-time migration: set real names, office emails and hashed temp
    passwords for the 5 role-based accounts. Idempotent -- subsequent runs
    are no-ops because we guard on the role AND on the email still being the
    old placeholder value. Once a user changes their own password the
    password_hash guard no longer matches, so we only touch untouched rows."""
    from app.db.database import SessionLocal
    from app.models.models import User
    from app.core.security import hash_password
    accounts = [
        ("Admin",          "Jeevan Prasath. J", "jeevanprasath.j@astralbusinessconsulting.in",  "Admin@2026"),
        ("Project Manager","Gayathri. P",        "gayathri.p@astralbusinessconsulting.com",       "PManager@2026"),
        ("HR Manager",     "Manikandan. S",      "manikandan.s@astralbusinessconsulting.com",     "HRUser@2026"),
        ("FC Lead",        "Manikandan. M",      "manikandan.m@astralbusinessconsulting.in",      "FCLead@2026"),
        ("Technical Lead", "Sanjeev. V",         "sanjeev.v@astralbusinessconsulting.in",         "TCLead@2026"),
    ]
    db = SessionLocal()
    try:
        for role, name, email, temp_pwd in accounts:
            user = db.query(User).filter(User.role == role).first()
            if user:
                user.name         = name
                user.email        = email
                user.password_hash = hash_password(temp_pwd)
        db.commit()
        logging.info("User account migration completed.")
    except Exception as e:
        db.rollback()
        logging.warning(f"User account migration failed: {e}")
    finally:
        db.close()

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


@app.on_event("startup")
def startup():
    start_scheduler(); start_warmup()
    try:
        from app.db.database import SessionLocal
        from app.services.progress_service import fix_existing_progress
        db = SessionLocal()
        fix_existing_progress(db)
        db.close()
    except Exception as e:
        logging.warning(f"Startup fix failed: {e}")
    try:
        from app.db.database import SessionLocal
        db = SessionLocal()
        _backfill_subtask_questions(db)
        db.close()
    except Exception as e:
        logging.warning(f"Subtask-question backfill failed: {e}")
    logging.info(f"{settings.APP_NAME} v2.0 started")

@app.on_event("shutdown")
def shutdown():
    stop_scheduler(); stop_warmup()

@app.get("/")
def root(): return {"message": f"{settings.APP_NAME} API v2.0", "docs": "/docs"}

@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/api/ping")
def ping(): return {"status": "ok"}

@app.get("/api/seed-database")
def seed_database():
    try:
        from seed import seed
        seed()
        return {"status": "success", "message": "Database seeded!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/fix-passwords")
def fix_passwords():
    try:
        from app.db.database import SessionLocal
        from app.models.models import User
        from app.core.security import hash_password
        db = SessionLocal()
        for email, pwd in [("admin@wbs.com","admin123"),("fc@wbs.com","fc123"),("tech@wbs.com","tech123"),("client@wbs.com","client123")]:
            u = db.query(User).filter_by(email=email).first()
            if u: u.password_hash = hash_password(pwd)
        db.commit(); db.close()
        return {"status": "success", "message": "Passwords updated! Login: admin@wbs.com / admin123"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
