from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
import hashlib
import hmac as hmac_lib
from app.core.config import settings

def hash_password(password: str) -> str:
    """Hash password using HMAC-SHA256 — works on all Python versions."""
    secret = settings.SECRET_KEY.encode('utf-8')
    return hmac_lib.new(secret, password.encode('utf-8'), hashlib.sha256).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    """Verify password using constant-time comparison."""
    try:
        expected = hash_password(plain)
        return hmac_lib.compare_digest(expected, hashed)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None
