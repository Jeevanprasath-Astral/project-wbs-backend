from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.database import engine, Base
from app.models import models
from app.api.routes import auth, projects, milestones, responses, dashboard, export, assignments
from app.services.scheduler import start_scheduler, stop_scheduler
from app.utils.warmup import start_warmup, stop_warmup
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(name)s — %(levelname)s — %(message)s")

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    description="Project WBS — Requirement Gathering & Tracking System API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,        prefix="/api")
app.include_router(projects.router,    prefix="/api")
app.include_router(milestones.router,  prefix="/api")
app.include_router(responses.router,   prefix="/api")
app.include_router(dashboard.router,   prefix="/api")
app.include_router(export.router,      prefix="/api")
app.include_router(assignments.router, prefix="/api")

@app.on_event("startup")
def startup():
    start_scheduler()
    start_warmup()
    # Fix any existing progress values > 100%
    try:
        from app.db.database import SessionLocal
        from app.services.progress_service import fix_existing_progress
        db = SessionLocal()
        fixed = fix_existing_progress(db)
        if fixed:
            logging.info(f"Fixed {fixed} milestones with progress > 100%")
        db.close()
    except Exception as e:
        logging.warning(f"Could not fix progress values: {e}")
    logging.info(f"{settings.APP_NAME} started on port {settings.APP_PORT}")

@app.on_event("shutdown")
def shutdown():
    stop_scheduler()
    stop_warmup()

@app.get("/")
def root():
    return {"message": f"{settings.APP_NAME} API is running", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/ping")
def ping():
    return {"status": "ok", "app": settings.APP_NAME}

@app.get("/api/seed-database")
def seed_database():
    try:
        from seed import seed
        seed()
        return {"status": "success", "message": "Database seeded successfully!"}
    except Exception as e:
        return {"status": "already seeded or error", "message": str(e)}

@app.get("/api/reset-users")
def reset_users():
    try:
        from app.db.database import SessionLocal
        from app.models.models import User
        from app.core.security import hash_password
        db = SessionLocal()
        db.query(User).delete()
        db.commit()
        users = [
            User(name="Admin User", email="admin@wbs.com", password_hash=hash_password("admin123"), role="Admin"),
            User(name="Priya Krishnan", email="fc@wbs.com", password_hash=hash_password("fc123"), role="Functional Consultant"),
            User(name="Arun Dev", email="tech@wbs.com", password_hash=hash_password("tech123"), role="Technical Team"),
            User(name="Client Reviewer", email="client@wbs.com", password_hash=hash_password("client123"), role="Client"),
        ]
        for u in users:
            db.add(u)
        db.commit()
        db.close()
        return {"status": "success", "message": "Users reset successfully! Login: admin@wbs.com / admin123"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/fix-passwords")
def fix_passwords():
    try:
        from app.db.database import SessionLocal
        from app.models.models import User
        from app.core.security import hash_password
        db = SessionLocal()
        updates = [
            ("admin@wbs.com",  "admin123"),
            ("fc@wbs.com",     "fc123"),
            ("tech@wbs.com",   "tech123"),
            ("client@wbs.com", "client123"),
        ]
        for email, password in updates:
            user = db.query(User).filter_by(email=email).first()
            if user:
                user.password_hash = hash_password(password)
        db.commit()
        db.close()
        return {"status": "success", "message": "Passwords updated! Login: admin@wbs.com / admin123"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
