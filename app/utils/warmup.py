from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from app.db.database import SessionLocal
import logging

logger = logging.getLogger(__name__)

def ping_db():
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception as e:
        logger.warning(f"DB ping failed: {e}")

warmup_scheduler = BackgroundScheduler()

def start_warmup():
    warmup_scheduler.add_job(ping_db, "interval", minutes=4, id="db_warmup")
    warmup_scheduler.start()
    logger.info("DB warmup started — pinging Neon every 4 minutes.")

def stop_warmup():
    try:
        warmup_scheduler.shutdown()
    except:
        pass
