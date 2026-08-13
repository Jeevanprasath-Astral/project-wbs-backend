from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings

# bcrypt is self-contained — does NOT depend on SECRET_KEY.
# Changing SECRET_KEY on Render will never invalidate stored passwords.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """Hash password using bcrypt (salted, self-contained)."""
    return _pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    """Verify password.

    Supports two formats during the HMAC→bcrypt migration window:
    - bcrypt ($2b$ / $2a$ prefix) — new hashes, no SECRET_KEY dependency.
    - HMAC-SHA256 (64-char hex) — legacy hashes from before the switch.

    Once all users have logged in at least once with the new code, the
    HMAC branch below can be removed.
    """
    try:
        if hashed.startswith('$2'):
            # Modern bcrypt hash
            return _pwd_context.verify(plain, hashed)
        # Legacy HMAC-SHA256 fallback (64-char lowercase hex)
        import hashlib
        import hmac as _hmac
        secret = settings.SECRET_KEY.encode('utf-8')
        expected = _hmac.new(secret, plain.encode('utf-8'), hashlib.sha256).hexdigest()
        return _hmac.compare_digest(expected, hashed)
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
