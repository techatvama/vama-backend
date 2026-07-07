"""Security primitives: password hashing (Argon2id), password policy,
JWT access/refresh tokens, and single-use activation/reset token handling.

All knobs are env-overridable so the same code runs across branches and
environments without modification.
"""
import os
import re
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from passlib.context import CryptContext
from jose import jwt, JWTError

# ──────────────────────────────────────────────────────────────────────────
# Password hashing — Argon2id (OWASP-preferred, memory-hard)
# ──────────────────────────────────────────────────────────────────────────
# OWASP 2023 Argon2id minimums: m=19456 (19 MB), t=2, p=1
# Default passlib params (m=102400, t=2, p=8) are too slow on constrained cloud VMs.
pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated=["bcrypt"],
    argon2__time_cost=2,
    argon2__memory_cost=19456,
    argon2__parallelism=1,
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash uses outdated parameters and should be upgraded."""
    try:
        return pwd_context.needs_update(password_hash)
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
# Password policy
# ──────────────────────────────────────────────────────────────────────────
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))


def validate_password_strength(password: str) -> Optional[str]:
    """Return an error message if the password is too weak, else None."""
    if password is None or len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters long"
    if not re.search(r"[A-Z]", password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r"[0-9]", password):
        return "Password must contain at least one number"
    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must contain at least one special character"
    return None


# ──────────────────────────────────────────────────────────────────────────
# JWT access / refresh tokens
# ──────────────────────────────────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME_INSECURE_DEV_SECRET")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "7"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(subject: str, roles: list, extra: Optional[dict] = None) -> str:
    payload = {
        "sub": subject,            # e.g. "staff:12" / "student:5" / "parent:3"
        "roles": roles,            # e.g. ["super_admin"], ["student"], ["parent","teacher"]
        "type": "access",
        "iat": _now(),
        "exp": _now() + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": _now(),
        "exp": _now() + timedelta(days=REFRESH_TOKEN_DAYS),
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str, expected_type: Optional[str] = None) -> Optional[dict]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    if expected_type and payload.get("type") != expected_type:
        return None
    return payload


# ──────────────────────────────────────────────────────────────────────────
# Single-use activation / reset tokens
# Raw token is sent in the email link; only its SHA-256 hash is stored.
# ──────────────────────────────────────────────────────────────────────────
TOKEN_EXPIRY_MINUTES = int(os.getenv("AUTH_TOKEN_EXPIRY_MINUTES", "60"))


def generate_secure_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Send raw in the link, store the hash."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def token_expiry() -> datetime:
    return _now() + timedelta(minutes=TOKEN_EXPIRY_MINUTES)
