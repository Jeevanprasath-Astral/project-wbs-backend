"""
seed_demo.py — Seed the dedicated AXON Demo project.

Run once (or re-run safely — it's idempotent):
    cd backend
    python seed_demo.py

What it creates:
  • Project "AXON Platform Implementation"  (is_demo=True)
  • 10 CustomMilestones — M1-M4 Completed, M5 In Progress (60 %), M6-M10 Not Started
  • Tasks under each milestone
  • ProjectMembers (real team accounts linked, not duplicated)
  • 35+ WorkHours entries spread over 4 months
  • 14 TaskAssignments (mix of statuses)
  • 9 Notifications
  • 6 ProjectCost entries
  • ProjectBilling entries for completed milestones
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date, datetime, timedelta
from app.db.database import SessionLocal
from app.models.models import (
    User, Project, ProjectMember, CustomMilestone, CustomTask,
    WorkHours, TaskAssignment, Notification, ProjectCost, ProjectBilling,
)
from sqlalchemy import text

# ── Helpers ───────────────────────────────────────────────────────────────────

def d(y, m, day) -> datetime:
    return datetime(y, m, day)

def dt(y, m, day, h=9, mn=0) -> datetime:
    return datetime(y, m, day, h, mn)

# ── Milestone catalogue ───────────────────────────────────────────────────────
#   (num, name, status, planned_start, planned_end, actual_start, actual_end)
MILESTONES = [
    (1,  "Initiation & Requirement", "Completed",   d(2026,1,2),  d(2026,1,20), d(2026,1,2),  d(2026,1,18)),
    (2,  "Kick Off",                 "Completed",   d(2026,1,21), d(2026,1,27), d(2026,1,21), d(2026,1,27)),
    (3,  "Process Study",            "Completed",   d(2026,1,28), d(2026,2,14), d(2026,1,28), d(2026,2,12)),
    (4,  "Requirement Specification","Completed",   d(2026,2,15), d(2026,3,4),  d(2026,2,15), d(2026,3,5)),
    (5,  "Development",              "In Progress", d(2026,3,6),  d(2026,4,30), d(2026,3,6),  None),
    (6,  "Internal Testing",         "Not Started", d(2026,5,1),  d(2026,5,21), None,         None),
    (7,  "Deployment for UAT",       "Not Started", d(2026,5,22), d(2026,5,31), None,         None),
    (8,  "UAT for End User",         "Not Started", d(2026,6,1),  d(2026,6,21), None,         None),
    (9,  "Go Live",                  "Not Started", d(2026,6,22), d(2026,6,28), None,         None),
    (10, "Post Live Support",        "Not Started", d(2026,7,1),  d(2026,7,31), None,         None),
]

# ── Task catalogue: {ms_num: [(task_num, task_name, status)]} ────────────────
TASKS = {
    1: [
        (1, "Business Understanding",  "Completed"),
        (2, "Business Requirement",    "Completed"),
        (3, "Initial Study",           "Completed"),
        (4, "Scope Defining",          "Completed"),
        (5, "Scope Finalization",      "Completed"),
        (6, "Client Confirmation",     "Completed"),
    ],
    2: [
        (1, "Kick Off Meeting with Client", "Completed"),
    ],
    3: [
        (1, "Detailed Process Study",  "Completed"),
        (2, "Process Documentation",   "Completed"),
        (3, "Design Finalization",     "Completed"),
    ],
    4: [
        (1, "Functional Specification Document", "Completed"),
        (2, "Technical Design Document",         "Completed"),
        (3, "Sign-off on Specifications",        "Completed"),
    ],
    5: [
        (1, "Database Object Creation",    "Completed"),
        (2, "Data Extraction Development", "Completed"),
        (3, "Business Logic Development",  "In Progress"),
        (4, "Report Development",          "In Progress"),
        (5, "Dashboard Development",       "Not Started"),
        (6, "Validation Implementation",   "Not Started"),
        (7, "Internal Developer Testing",  "Not Started"),
        (8, "Bug Fixing",                  "Not Started"),
    ],
    6: [
        (1, "Prepare Test Scenarios",  "Not Started"),
        (2, "App Testing",             "Not Started"),
        (3, "Retest",                  "Not Started"),
    ],
    7: [
        (1, "Server Readiness",                    "Not Started"),
        (2, "Master Data Deployment",              "Not Started"),
        (3, "Deploy Solution to UAT Environment",  "Not Started"),
        (4, "Load Sample Data",                    "Not Started"),
        (5, "Share UAT Version",                   "Not Started"),
        (6, "Execute Smoke Testing",               "Not Started"),
        (7, "Verify Deployment",                   "Not Started"),
    ],
    8: [
        (1, "Provide User Training",       "Not Started"),
        (2, "Conduct UAT Walkthrough",     "Not Started"),
        (3, "End User Testing",            "Not Started"),
        (4, "Validate Outputs",            "Not Started"),
        (5, "Fix UAT Defects",             "Not Started"),
        (6, "Re-deploy Updated Version",   "Not Started"),
        (7, "UAT Sign-off",                "Not Started"),
    ],
    9: [
        (1, "Deploy Production Version",         "Not Started"),
        (2, "Configure Production Environment",  "Not Started"),
        (3, "Validate Production Data",          "Not Started"),
        (4, "Perform Sanity Testing",            "Not Started"),
        (5, "Obtain Go-live Approval",           "Not Started"),
        (6, "Release to Users",                  "Not Started"),
    ],
    10: [
        (1, "Monitor Application/Report",  "Not Started"),
        (2, "Resolve Production Issues",   "Not Started"),
        (3, "Handover Project Documents",  "Not Started"),
        (4, "Project Closure",             "Not Started"),
    ],
}


def main():
    db = SessionLocal()
    try:
        # ── Look up real team members ─────────────────────────────────────────
        emails = [
            "jeevanprasath.j@astralbusinessconsulting.in",   # Admin
            "gayathri.p@astralbusinessconsulting.com",         # FC Lead / PM
            "manikandan.m@astralbusinessconsulting.in",        # FC Lead
            "sanjeev.v@astralbusinessconsulting.in",           # TC Lead
            "manikandan.s@astralbusinessconsulting.com",       # HR
        ]
        users = {u.email: u for u in db.query(User).filter(User.email.in_(emails)).all()}
        if not users:
            print("ERROR: No real team accounts found. Run main.py (start the server) first.")
            return

        admin   = users.get("jeevanprasath.j@astralbusinessconsulting.in")
        fc_lead = users.get("gayathri.p@astralbusinessconsulting.com")
        fc      = users.get("manikandan.m@astralbusinessconsulting.in")
        tc_lead = users.get("sanjeev.v@astralbusinessconsulting.in")
        hr_user = users.get("manikandan.s@astralbusinessconsulting.com")

        creator_id = admin.id if admin else (list(users.values())[0].id)
        print(f"Team loaded: {', '.join(u.name for u in users.values() if u)}")

        # ── Idempotency — skip if demo project already exists ─────────────────
        existing = db.query(Project).filter(Project.is_demo == True).first()
        if existing:
            print(f"Demo project already exists (id={existing.id}, '{existing.name}'). Nothing to do.")
            return

        # ── Create the demo project ───────────────────────────────────────────
        project = Project(
            name                  = "AXON Platform Implementation",
            client                = "TechNova Solutions Pvt. Ltd.",
            business_unit         = "Manufacturing & Supply Chain",
            owner                 = admin.name if admin else "Admin",
            location              = "Chennai, India",
            project_type          = "Implementation",
            project_category      = "Billable",
            functional_consultant = fc_lead.name if fc_lead else "FC Lead",
            technical_lead        = tc_lead.name if tc_lead else "TC Lead",
            description           = (
                "End-to-end ERP analytics and reporting implementation for TechNova Solutions. "
                "Covers supply chain reporting, production dashboards, finance automation, "
                "and management MIS for the India manufacturing vertical."
            ),
            start_date            = d(2026, 1, 2),
            end_date              = d(2026, 7, 31),
            status                = "In Progress",
            progress              = 42.0,
            budget                = 1800000.0,
            billing_amount        = 2400000.0,
            is_demo               = True,
            created_by            = creator_id,
        )
        db.add(project)
        db.flush()   # get project.id before adding children
        pid = project.id
        print(f"Created demo project id={pid}")

        # ── Project Members ───────────────────────────────────────────────────
        member_rows = []
        for u, role_label in [
            (admin,   "Admin"),
            (fc_lead, "FC Lead"),
            (fc,      "Functional Consultant"),
            (tc_lead, "TC Lead"),
            (hr_user, "HR"),
        ]:
            if u:
                member_rows.append(ProjectMember(project_id=pid, user_id=u.id, role=role_label))
        db.add_all(member_rows)
        db.flush()
        print(f"  Added {len(member_rows)} project members")

        # ── Custom Milestones + Tasks ─────────────────────────────────────────
        ms_by_num: dict[int, CustomMilestone] = {}
        for num, name, status, ps, pe, as_, ae in MILESTONES:
            assignee_name = (fc_lead.name if fc_lead else None) if num <= 4 else (tc_lead.name if tc_lead else None)
            ms = CustomMilestone(
                project_id    = pid,
                num           = num,
                name          = name,
                status        = status,
                assignee      = assignee_name,
                planned_start = ps,
                planned_end   = pe,
                actual_start  = as_,
                actual_end    = ae,
                is_active     = True,
                iteration     = 1,
            )
            db.add(ms)
            db.flush()
            ms_by_num[num] = ms

            for t_num, t_name, t_status in TASKS.get(num, []):
                task = CustomTask(
                    milestone_id  = ms.id,
                    project_id    = pid,
                    num           = t_num,
                    name          = t_name,
                    status        = t_status,
                    assignee      = fc.name if (fc and num <= 4) else (tc_lead.name if tc_lead else None),
                    planned_start = ps,
                    planned_end   = pe,
                    actual_start  = as_ if t_status == "Completed" else None,
                    actual_end    = ae  if t_status == "Completed" else None,
                    estimated_hours = 12.0,
                )
                db.add(task)

        db.flush()
        print(f"  Seeded {len(MILESTONES)} milestones and tasks")

        # ── Work Hours (35+ entries spanning Jan–Sep 2026) ────────────────────
        wh_entries = []
        def wh(user, task_name, wdate, hrs, ms_num, work_type="Billable", billable=True):
            ms = ms_by_num.get(ms_num)
            return WorkHours(
                user_id              = user.id if user else creator_id,
                project_id           = pid,
                task_name            = task_name,
                date                 = wdate,
                hours_spent          = hrs,
                is_billable          = billable,
                work_type            = work_type,
                custom_milestone_id  = ms.id if ms else None,
                level                = "Milestone",
                notes                = f"Demo data — {task_name}",
            )

        fc_u   = fc      or list(users.values())[0]
        fc_l_u = fc_lead or list(users.values())[0]
        tc_u   = tc_lead or list(users.values())[0]

        # M1 — Initiation & Requirement (Jan 2–18)
        for offset, hrs, u in [
            (0, 6.0, fc_l_u), (1, 7.0, fc_l_u), (2, 5.5, fc_u),
            (3, 8.0, fc_l_u), (6, 7.0, fc_u),   (7, 6.5, fc_l_u),
            (9, 8.0, fc_u),   (10,7.0, fc_l_u),
        ]:
            wh_entries.append(wh(u, "Business Requirement Gathering",
                                 date(2026,1,2) + timedelta(days=offset), hrs, 1))

        # M2 — Kick Off (Jan 21–27)
        for offset, hrs, u in [(0,4.0,fc_l_u),(1,3.5,fc_u),(2,4.0,admin if admin else fc_u)]:
            wh_entries.append(wh(u, "Kick Off Meeting Preparation",
                                 date(2026,1,21) + timedelta(days=offset), hrs, 2))

        # M3 — Process Study (Jan 28–Feb 12)
        for offset, hrs, u in [
            (0,7.0,fc_l_u),(1,6.5,fc_u),(3,8.0,fc_l_u),(5,7.5,fc_u),
            (7,6.0,fc_l_u),(8,7.0,fc_u),(10,5.5,fc_l_u),
        ]:
            wh_entries.append(wh(u, "Detailed Process Study & Documentation",
                                 date(2026,1,28) + timedelta(days=offset), hrs, 3))

        # M4 — Requirement Specification (Feb 15–Mar 5)
        for offset, hrs, u in [
            (0,6.5,fc_l_u),(1,7.0,fc_u),(3,8.0,fc_l_u),(5,7.0,fc_u),
            (7,6.0,fc_l_u),(10,5.5,fc_u),
        ]:
            wh_entries.append(wh(u, "FSD & TDD Preparation",
                                 date(2026,2,15) + timedelta(days=offset), hrs, 4))

        # M5 — Development (Mar 6–Sep 16, ongoing)
        for offset, hrs, u in [
            (0, 8.0, tc_u), (1, 7.5, tc_u), (2, 6.5, tc_u),
            (5, 8.0, tc_u), (6, 7.0, tc_u), (8, 8.0, tc_u),
            (10,7.5, tc_u), (12,8.0, tc_u), (15,7.0, tc_u),
            (17,8.0, tc_u), (20,7.5, fc_u), (22,8.0, tc_u),
        ]:
            wh_entries.append(wh(tc_u, "Development & Coding",
                                 date(2026,3,6) + timedelta(days=offset), hrs, 5))

        db.add_all(wh_entries)
        db.flush()
        print(f"  Seeded {len(wh_entries)} work hours entries")

        # ── Task Assignments (14 entries) ──────────────────────────────────────
        assign_data = [
            # M1
            ("Prepare BRD Document",          fc_l_u, admin,  "High",   "Completed", d(2026,1,10), d(2026,1,18), 1),
            ("Conduct Scope Review Session",   fc_u,   fc_l_u,"Medium", "Completed", d(2026,1,14), d(2026,1,20), 1),
            # M2
            ("Prepare Kickoff Deck",           fc_l_u, admin,  "High",   "Completed", d(2026,1,20), d(2026,1,24), 2),
            # M3
            ("Process Flow Documentation",     fc_u,   fc_l_u,"High",   "Completed", d(2026,2,1),  d(2026,2,10), 3),
            ("AS-IS Mapping",                  fc_u,   fc_l_u,"Medium", "Completed", d(2026,2,3),  d(2026,2,12), 3),
            # M4
            ("Functional Specification Draft", fc_l_u, admin,  "High",   "Completed", d(2026,2,20), d(2026,3,1),  4),
            ("Technical Architecture Review",  tc_u,   admin,  "High",   "Completed", d(2026,2,22), d(2026,3,3),  4),
            # M5 — ongoing
            ("Database Schema Design",         tc_u,   admin,  "High",   "Completed", d(2026,3,10), d(2026,3,20), 5),
            ("ETL Pipeline Development",       tc_u,   fc_l_u,"High",   "Completed", d(2026,3,22), d(2026,4,5),  5),
            ("Report Module Development",      tc_u,   admin,  "High",   "In Progress",d(2026,4,10), d(2026,4,30), 5),
            ("Dashboard Development",          tc_u,   admin,  "Medium", "In Progress",d(2026,4,20), d(2026,5,10), 5),
            ("Validation Framework Setup",     tc_u,   fc_l_u,"Medium", "Not Started",d(2026,5,1),  d(2026,5,15), 5),
            ("Internal Dev Testing Plan",      fc_u,   fc_l_u,"Medium", "Not Started",d(2026,5,5),  d(2026,5,20), 5),
            ("Bug Tracker Setup",              tc_u,   admin,  "Low",    "Not Started",d(2026,5,10), d(2026,5,25), 5),
        ]

        for title, assigned_to_u, assigned_by_u, prio, status, due, planned_end, ms_num in assign_data:
            ms = ms_by_num.get(ms_num)
            ta = TaskAssignment(
                project_id   = pid,
                title        = title,
                assigned_to  = assigned_to_u.id if assigned_to_u else creator_id,
                assigned_by  = assigned_by_u.id if assigned_by_u else creator_id,
                team         = "Functional Team" if ms_num <= 4 else "Technical Team",
                milestone_num= ms_num,
                priority     = prio,
                status       = status,
                due_date     = due,
                planned_end  = planned_end,
                completed_at = due if status == "Completed" else None,
            )
            db.add(ta)

        db.flush()
        print(f"  Seeded 14 task assignments")

        # ── Notifications (9 entries) ─────────────────────────────────────────
        notifs = [
            (admin,   "completed", "Milestone 'Initiation & Requirement' completed successfully.",  datetime(2026,1,18)),
            (fc_l_u, "completed", "Milestone 'Kick Off' completed. All sessions documented.",      datetime(2026,1,27)),
            (fc_l_u, "completed", "Milestone 'Process Study' completed ahead of schedule.",         datetime(2026,2,12)),
            (admin,   "completed", "Milestone 'Requirement Specification' completed. FSD approved.", datetime(2026,3,5)),
            (tc_u,   "started",   "Development milestone is now In Progress.",                      datetime(2026,3,6)),
            (tc_u,   "assignment","Task 'ETL Pipeline Development' assigned to you.",               datetime(2026,3,22)),
            (fc_l_u, "reminder",  "Development milestone is 60 % complete. On track for Apr 30.",  datetime(2026,9,16)),
            (admin,   "reminder",  "Upcoming milestone: Internal Testing starts May 1.",            datetime(2026,9,16)),
            (tc_u,   "reminder",  "Dashboard Development task due soon — Apr 30.",                 datetime(2026,9,16)),
        ]
        for user, notif_type, msg, created_at in notifs:
            if user:
                db.add(Notification(
                    project_id = pid,
                    user_id    = user.id,
                    type       = notif_type,
                    message    = msg,
                    read       = notif_type == "completed",
                    email_sent = False,
                ))
        db.flush()
        print(f"  Seeded 9 notifications")

        # ── Project Costs (6 entries) ─────────────────────────────────────────
        cost_data = [
            (date(2026,1,5),  "Project Kickoff Travel",           "Travel & Accommodation",  8500.0),
            (date(2026,1,15), "Client Site Visit — Requirement Gathering","Travel & Accommodation",12000.0),
            (date(2026,2,10), "Process Study Workshop — Venue",    "Meetings & Events",       15000.0),
            (date(2026,3,1),  "Development Server License",        "Software & Licenses",     45000.0),
            (date(2026,3,20), "Cloud Hosting — Dev Environment",   "Infrastructure",          22000.0),
            (date(2026,4,15), "Third-party API Integration Tools", "Software & Licenses",     18000.0),
        ]
        for cost_date, particulars, category, cost in cost_data:
            db.add(ProjectCost(
                project_id  = pid,
                date        = cost_date,
                particulars = particulars,
                category    = category,
                cost        = cost,
                created_by  = creator_id,
            ))
        db.flush()
        total_cost = sum(c[3] for c in cost_data)
        print(f"  Seeded 6 project costs (total ₹{total_cost:,.0f})")

        # ── Project Billings (4 entries for completed milestones) ─────────────
        billing_data = [
            # (ms_num, planned_amount, actual_date, actual_amount, billing_type, description)
            (1, 300000.0, date(2026,1,20), 300000.0, "Development",
             "M1 — Initiation & Requirement sign-off billing"),
            (2, 150000.0, date(2026,1,28), 150000.0, "Development",
             "M2 — Kick Off completion billing"),
            (3, 350000.0, date(2026,2,14), 350000.0, "Development",
             "M3 — Process Study sign-off billing"),
            (4, 400000.0, date(2026,3,7),  400000.0, "Development",
             "M4 — Requirement Specification approval billing"),
        ]
        for ms_num, planned_amt, act_date, act_amt, btype, desc in billing_data:
            ms = ms_by_num.get(ms_num)
            db.add(ProjectBilling(
                project_id             = pid,
                milestone_id           = ms.id if ms else None,
                planned_billing_amount = planned_amt,
                actual_billing_date    = act_date,
                actual_billing_amount  = act_amt,
                billing_type           = btype,
                description            = desc,
                remarks                = "Received",
                created_by             = creator_id,
            ))
        db.flush()
        billed = sum(b[1] for b in billing_data)
        print(f"  Seeded 4 billing entries (planned total ₹{billed:,.0f})")

        # ── Commit everything ─────────────────────────────────────────────────
        db.commit()
        print(f"\n✓ Demo project seeded successfully (project_id={pid}).")
        print(f"  Run the backend, then visit /demo to experience the demo flow.")

    except Exception as ex:
        db.rollback()
        print(f"\nERROR during seeding: {ex}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
