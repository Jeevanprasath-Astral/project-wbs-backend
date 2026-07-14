from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.models import User, PasswordResetToken
from app.schemas.schemas import (LoginRequest, TokenResponse, UserCreate, UserOut,
                                  ForgotPasswordRequest, ResetPasswordRequest)
from app.core.security import verify_password, hash_password, create_access_token
from app.core.deps import get_current_user
from app.services.email_service import send_password_reset_email
from app.core.config import settings
from datetime import datetime, timedelta
import secrets

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {"token": token, "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}

@router.post("/register", response_model=UserOut)
def register(payload: UserCreate, db: Session = Depends(get_db)):
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

@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Always return the same message — never reveal if email exists
    user = db.query(User).filter(User.email == payload.email).first()
    if user:
        # Delete any existing tokens for this email
        db.query(PasswordResetToken).filter(PasswordResetToken.email == payload.email).delete()
        # Generate a secure random token (expires in 15 minutes)
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=15)
        db.add(PasswordResetToken(email=payload.email, token=token, expires_at=expires_at))
        db.commit()
        # Build reset link using FRONTEND_URL from config
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        send_password_reset_email(to=user.email, name=user.name, reset_link=reset_link)
    return {"message": "If this email is registered, a password reset link has been sent."}

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
