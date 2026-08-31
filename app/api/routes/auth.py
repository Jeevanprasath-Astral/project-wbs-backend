from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.models import User, PasswordResetToken
from app.schemas.schemas import (LoginRequest, TokenResponse, UserCreate, UserOut,
                                  ForgotPasswordRequest, ResetPasswordRequest)
from app.core.security import verify_password, hash_password, create_access_token
from app.core.deps import get_current_user
from app.services.email_service import send_password_reset_email, send_email, _brevo_check
from app.core.config import settings
from datetime import datetime, timedelta
import secrets

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    # Fetch ALL rows with this email (supports dual-role accounts sharing one email)
    users = db.query(User).filter(User.email == payload.email).all()
    matched_user = None
    for u in users:
        if verify_password(payload.password, u.password_hash):
            matched_user = u
            # Silently upgrade legacy HMAC hash to bcrypt on first successful login.
            # This is the self-service migration path for all non-seeded accounts.
            if not u.password_hash.startswith('$2'):
                u.password_hash = hash_password(payload.password)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            break
    if not matched_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token({"sub": str(matched_user.id), "role": matched_user.role})
    return {"token": token, "user": {"id": matched_user.id, "name": matched_user.name,
                                     "email": matched_user.email, "role": matched_user.role}}

@router.post("/register", response_model=UserOut)
def register(payload: UserCreate, db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)):
    if current_user.role != "Admin":
        raise HTTPException(status_code=403, detail="Only Admin can create accounts via this endpoint")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@router.get("/me")
def me(current_user: User = Depends(get_current_user),
       db: Session = Depends(get_db)):
    """Return user profile + live role-permission map."""
    try:
        from app.api.routes.role_permissions import get_permissions_for_role
        permissions = get_permissions_for_role(db, current_user.role)
    except Exception:
        permissions = None
    return {
        "id":          current_user.id,
        "name":        current_user.name,
        "email":       current_user.email,
        "role":        current_user.role,
        "is_active":   current_user.is_active,
        "cost_rate":   getattr(current_user, "cost_rate", 0.0),
        "permissions": permissions,
    }

@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    import logging as _log
    # Always return the same generic message — never reveal whether the email exists
    try:
        user = db.query(User).filter(User.email == payload.email).first()
        if user:
            # Delete any existing tokens for this email
            db.query(PasswordResetToken).filter(
                PasswordResetToken.email == payload.email
            ).delete()
            # Generate a secure random token (expires in 15 minutes)
            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(minutes=15)
            db.add(PasswordResetToken(email=payload.email, token=token, expires_at=expires_at))
            db.commit()
            # Build reset link using FRONTEND_URL from config
            reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
            sent = send_password_reset_email(to=user.email, name=user.name, reset_link=reset_link)
            if sent:
                _log.info(f"Password reset email dispatched to {user.email}")
            else:
                brevo_check = _brevo_check()
                _log.error(
                    f"Password reset email FAILED for {user.email!r}. "
                    f"Brevo account check: {brevo_check}. "
                    f"BREVO_API_KEY set: {bool(settings.BREVO_API_KEY)}. "
                    f"MAIL_FROM: {settings.MAIL_FROM!r}. "
                    f"MAIL_ENABLED: {settings.MAIL_ENABLED}. "
                    f"Diagnostic: GET /api/auth/email-status?confirm_token=axon-fix-2026 "
                    f"or GET /api/auth/test-email?to={user.email}&confirm_token=axon-fix-2026"
                )
        else:
            _log.info(f"Forgot-password: email not found in DB: {payload.email}")
    except Exception as _exc:
        _log.error(f"forgot_password error: {_exc}")
        try:
            db.rollback()
        except Exception:
            pass
    return {"message": "If this email is registered, a password reset link has been sent."}

@router.get("/debug-login")
def debug_login(email: str, password: str, confirm_token: str, db: Session = Depends(get_db)):
    """TEMPORARY debug — remove after fix."""
    if confirm_token != "axon-fix-2026":
        raise HTTPException(status_code=403, detail="Invalid confirm_token")
    users = db.query(User).filter(User.email == email).all()
    results = []
    for u in users:
        results.append({
            "id": u.id,
            "role": u.role,
            "stored_hash": u.password_hash[:20] + "…",
            "match": verify_password(password, u.password_hash),
        })
    return {"results": results}

@router.get("/emergency-reset")
def emergency_reset(email: str, new_password: str, confirm_token: str, db: Session = Depends(get_db)):
    """TEMPORARY one-use endpoint — remove after login is fixed."""
    if confirm_token != "axon-fix-2026":
        raise HTTPException(status_code=403, detail="Invalid confirm_token")
    users = db.query(User).filter(User.email == email).all()
    if not users:
        raise HTTPException(status_code=404, detail="No user found with that email")
    new_hash = hash_password(new_password)
    for u in users:
        u.password_hash = new_hash
    db.commit()
    return {"message": f"Password updated for {len(users)} account(s) with email {email}", "rows": len(users)}

@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    token_row = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == payload.token
    ).first()
    if not token_row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")
    if datetime.utcnow() > token_row.expires_at:
        db.delete(token_row)
        db.commit()
        raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")
    user = db.query(User).filter(User.email == token_row.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found.")
    user.password_hash = hash_password(payload.new_password)
    db.delete(token_row)
    db.commit()
    return {"message": "Password updated successfully. You can now log in with your new password."}


# ── Email diagnostics (admin/debug only — gated by confirm_token) ─────────────

@router.get("/email-status")
def email_status(confirm_token: str):
    """Check Brevo configuration without sending anything.

    Usage: GET /api/auth/email-status?confirm_token=axon-fix-2026
    """
    if confirm_token != "axon-fix-2026":
        raise HTTPException(status_code=403, detail="Invalid confirm_token")
    check = _brevo_check()
    return {
        "mail_enabled":    settings.MAIL_ENABLED,
        "brevo_key_set":   bool(settings.BREVO_API_KEY),
        "brevo_key_prefix": settings.BREVO_API_KEY[:12] + "…" if settings.BREVO_API_KEY else "(not set)",
        "mail_from":       settings.MAIL_FROM,
        "frontend_url":    settings.FRONTEND_URL,
        "brevo_account":   check,
    }


@router.get("/test-email")
def test_email(to: str, confirm_token: str):
    """Send a real test email to verify Brevo is working end-to-end.

    Usage: GET /api/auth/test-email?to=you@example.com&confirm_token=axon-fix-2026
    """
    if confirm_token != "axon-fix-2026":
        raise HTTPException(status_code=403, detail="Invalid confirm_token")
    sent = send_email(
        to=to,
        subject="Axon WBS — Email Test",
        body=f"""
        <div style="font-family:Arial,sans-serif;padding:24px;max-width:480px;">
          <h2 style="color:#0f172a;">✅ Email is working!</h2>
          <p>This is a test email sent from <strong>Axon WBS</strong>.</p>
          <p>Sender: <code>{settings.MAIL_FROM}</code></p>
          <p>If you received this, Brevo is configured correctly.</p>
        </div>
        """,
    )
    if sent:
        return {"sent": True, "message": f"Test email dispatched to {to}. Check inbox (and spam folder)."}
    brevo = _brevo_check()
    return {
        "sent": False,
        "message": "Email failed to send. See details below.",
        "brevo_account": brevo,
        "mail_enabled":  settings.MAIL_ENABLED,
        "brevo_key_set": bool(settings.BREVO_API_KEY),
        "mail_from":     settings.MAIL_FROM,
    }
