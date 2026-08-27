"""
One-time migration: assign sequential num values (1, 2, 3, ...) to all
custom_tasks that currently have num = NULL, grouped by milestone_id and
ordered by id (creation order).

Run from the backend folder:
    DATABASE_URL=<your-render-db-url> python fix_task_nums.py

Or simply:
    python fix_task_nums.py     (uses .env / environment DATABASE_URL)
"""

import os
import sys

# ── Allow running from the backend directory directly ────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, text
from app.core.config import settings

DATABASE_URL = os.getenv("DATABASE_URL") or settings.DATABASE_URL

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set.")
    sys.exit(1)

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    # Fetch all null-num tasks, grouped by milestone, ordered by id
    rows = conn.execute(text("""
        SELECT id, milestone_id
        FROM custom_tasks
        WHERE num IS NULL
        ORDER BY milestone_id, id
    """)).fetchall()

    if not rows:
        print("No tasks with NULL num found. Nothing to do.")
        sys.exit(0)

    print(f"Found {len(rows)} task(s) with num=NULL. Assigning sequential nums...")

    # Group by milestone_id
    from collections import defaultdict
    by_milestone = defaultdict(list)
    for row in rows:
        by_milestone[row.milestone_id].append(row.id)

    total_updated = 0
    for milestone_id, task_ids in by_milestone.items():
        # Find the current max num already assigned in this milestone (may be 0)
        result = conn.execute(text("""
            SELECT COALESCE(MAX(num), 0)
            FROM custom_tasks
            WHERE milestone_id = :mid AND num IS NOT NULL
        """), {"mid": milestone_id})
        max_existing = result.scalar()

        for i, task_id in enumerate(task_ids, start=1):
            new_num = max_existing + i
            conn.execute(text("""
                UPDATE custom_tasks SET num = :num WHERE id = :id
            """), {"num": new_num, "id": task_id})
            print(f"  Milestone {milestone_id} | Task id={task_id} → num={new_num}")
            total_updated += 1

    print(f"\nDone. {total_updated} task(s) updated.")
