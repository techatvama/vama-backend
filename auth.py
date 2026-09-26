"""Authentication & account-activation system.

Covers: account creation hooks, activation, login (JWT access+refresh),
forgot/reset password, RBAC dependencies, rate limiting, audit logging,
and a pluggable email service (SMTP in prod, console log in dev).

Credentials are retrofitted onto the existing `staff` and `students` tables;
`parents` is a dedicated identity type. A subject is (subject_type, subject_id)
where subject_type ∈ {staff, student, parent}.
"""
import os
import json
import time
import smtplib
import logging
from email.mime.text import MIMEText
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database import get_db
from models import Staff, Student, AuthToken, AuditLog, LoginAttempt, AppSetting
import security

logger = logging.getLogger("auth")

# Statuses
PENDING, ACTIVE, SUSPENDED, DISABLED = (
    "pending_activation", "active", "suspended", "disabled",
)

SUBJECT_MODELS = {"staff": Staff, "student": Student}

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


# ══════════════════════════ Subject helpers ══════════════════════════

def _model_for(subject_type: str):
    model = SUBJECT_MODELS.get(subject_type)
    if not model:
        raise HTTPException(status_code=400, detail="Invalid subject type")
    return model


def load_subject(db: Session, subject_type: str, subject_id: int):
    return db.query(_model_for(subject_type)).filter(_model_for(subject_type).id == subject_id).first()


def find_by_email(db: Session, email: str):
    """Search every identity table for an email. Returns (subject_type, obj) or (None, None)."""
    email = (email or "").strip().lower()
    if not email:
        return None, None
    for stype, model in SUBJECT_MODELS.items():
        obj = db.query(model).filter(model.email.ilike(email)).first()
        if obj:
            return stype, obj
    return None, None


def staff_email_exists(db: Session, email: str) -> bool:
    """Student.email allows duplicates (siblings can share a family login),
    but a student account still shouldn't collide with a staff/admin login —
    used when creating a student, in place of the full email_exists check."""
    email = (email or "").strip().lower()
    if not email:
        return False
    return db.query(Staff).filter(Staff.email.ilike(email)).first() is not None


def email_exists(db: Session, email: str) -> bool:
    stype, _ = find_by_email(db, email)
    return stype is not None


def verify_credentials(db: Session, obj, password: str) -> bool:
    """Verify a password against an identity, transparently migrating legacy
    plaintext passwords to Argon2 on first successful match.

    - If a hash exists, verify against it.
    - Else if a legacy plaintext password exists and matches, accept and
      upgrade it to a hash (one-time migration).
    - A pending account with neither (created under the new flow) cannot log
      in until it is activated.
    """
    if obj.password_hash:
        if security.verify_password(password, obj.password_hash):
            return True
        return False
    legacy = getattr(obj, "password", None)
    if legacy and password and legacy == password:
        obj.password_hash = security.hash_password(password)  # migrate to hash
        if hasattr(obj, "password"):
            obj.password = None                                # purge plaintext
        return True
    return False


def roles_for(subject_type: str, obj) -> list:
    """Map an identity to its RBAC roles. Built as a list to support
    multiple roles per user in the future."""
    if subject_type == "staff":
        mapping = {"super_admin": "super_admin", "center_admin": "center_admin", "teacher": "staff"}
        return [mapping.get(obj.access_role or "teacher", "staff")]
    if subject_type == "student":
        return ["student"]
    return []


def display_name(subject_type: str, obj) -> str:
    if subject_type == "staff":
        return obj.name or obj.email
    parts = [getattr(obj, "first_name", None), getattr(obj, "last_name", None)]
    return " ".join(p for p in parts if p) or obj.email


# ══════════════════════════ Email service (pluggable) ══════════════════════════

def send_email(to: str, subject: str, body: str):
    """Send via SMTP when configured; otherwise log to console (dev mode).

    Returns True on success, False otherwise. Never raises — callers (e.g.
    forgot-password) must not break or leak based on delivery outcome.
    """
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "no-reply@vama.academy"
    if not host:
        logger.warning("[DEV EMAIL] To:%s | %s\n%s", to, subject, body)
        print(f"\n──── DEV EMAIL (SMTP_HOST not set — not actually sent) ────\nTo: {to}\nSubject: {subject}\n{body}\n──────────────────────────────────────────────────────────\n")
        return False

    user, pwd = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    if user and not pwd:
        print(f"⚠️  SMTP_PASSWORD is empty — cannot send to {to}. "
              f"Add a Gmail App Password to .env (SMTP_PASSWORD).")
        logger.error("SMTP_PASSWORD empty; email to %s not sent", to)
        return False

    msg = MIMEText(body, "html")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as srv:
            srv.starttls()
            if user and pwd:
                srv.login(user, pwd)
            srv.send_message(msg)
        print(f"📧 Email sent to {to} ({subject})")
        return True
    except Exception as e:
        print(f"❌ Email send FAILED to {to}: {e}")
        logger.error("Email send failed to %s: %s", to, e)
        return False


def _org_brand(db: Session, center_id: Optional[int] = None) -> dict:
    """Academy name + logo for email branding, with per-center override."""
    rows = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like("org.%")).all()}
    academy = rows.get("org.academy_name") or "Vama Academy for Music & Performing Arts"
    logo_url = rows.get("org.logo_url") or ""
    if center_id:
        prefix = f"center_{center_id}_org."
        crows = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like(f"{prefix}%")).all()}
        academy = crows.get(f"{prefix}academy_name") or academy
        logo_url = crows.get(f"{prefix}logo_url") or logo_url
    return {"academy_name": academy, "logo_url": logo_url}


def _branded_email_html(*, academy: str, logo_url: str, heading: str, intro: str,
                        button_label: str, button_link: str, expiry_note: str) -> str:
    """Professional, on-brand HTML shell shared by activation/reset emails —
    mirrors the invoice email look (purple CTA, VAMA wordmark)."""
    logo = (f"<img src='{logo_url}' alt='{academy}' style='max-height:48px'>"
            if logo_url else
            "<div style='font-size:22px;font-weight:900;letter-spacing:2px;color:#c0392b'>VAMA</div>"
            "<div style='font-size:8px;letter-spacing:1px;color:#888'>ACADEMY FOR MUSIC &amp; PERFORMING ARTS</div>")
    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:auto;color:#1a1a1a;background:#f6f5fb;padding:32px 0">
      <div style="background:#fff;border-radius:12px;padding:36px;box-shadow:0 1px 4px rgba(0,0,0,0.06)">
        <div style="text-align:center;margin-bottom:24px">{logo}</div>
        <h2 style="font-size:20px;margin:0 0 12px;color:#1a1a1a">{heading}</h2>
        <p style="font-size:14px;line-height:1.6;color:#444;margin:0 0 24px">{intro}</p>
        <div style="text-align:center;margin:28px 0">
          <a href="{button_link}" style="display:inline-block;background:#463a7a;color:#fff;text-decoration:none;
             font-weight:700;padding:13px 32px;border-radius:8px;font-size:14px">{button_label}</a>
        </div>
        <p style="font-size:12px;color:#888;line-height:1.5;margin:0">{expiry_note}</p>
      </div>
      <p style="text-align:center;font-size:11px;color:#aaa;margin-top:20px">{academy}</p>
    </div>"""



# ══════════════════════════ Notification settings ══════════════════════════
# One master switch plus a toggle per category, stored as plain AppSetting
# key-value rows (same pattern as org.* settings) so no migration is needed.
# Master OFF suppresses every category unconditionally; master ON lets each
# category's own toggle decide. Password reset is deliberately never gated —
# blocking a student's only way back into their account for the sake of a
# marketing-style mute switch would be a support nightmare, not a feature.
NOTIF_CATEGORIES = ["class_reminders", "attendance", "invoices", "activation"]
NOTIF_CATEGORY_DEFAULTS = {"class_reminders": False, "attendance": False, "invoices": True, "activation": True}

# Editable templates: heading/intro/button_label are merged onto the fixed
# branded shell (logo, button link, expiry note) — the link/token machinery
# itself isn't editable, only the human-facing copy around it.
NOTIF_TEMPLATE_DEFAULTS = {
    "activation": {
        "subject": "Activate your account — {academy}",
        "heading": "Welcome, {name}",
        "intro": "An account has been created for you at {academy}. Set your password below to activate it and get started.",
        "button_label": "Activate my account",
    },
    "password_reset": {
        "subject": "Reset your password — {academy}",
        "heading": "Reset your password, {name}",
        "intro": "We received a request to reset the password on your account. Click below to choose a new one.",
        "button_label": "Reset my password",
    },
    "class_reminder": {
        "subject": "Reminder: {course} class on {date}",
        "heading": "Upcoming class reminder",
        "intro": "This is a reminder that {student_name} has a {course} class with {teacher} on {date} at {time}.",
    },
    "attendance_present": {
        "subject": "Attendance update — {course}",
        "heading": "Attendance recorded",
        "intro": "{student_name} was marked present for {course} on {date}.{feedback_line}",
    },
    "attendance_absent": {
        "subject": "Attendance update — {course}",
        "heading": "Attendance recorded",
        "intro": "{student_name} was marked absent for {course} on {date}.",
    },
}


def _notif_settings(db: Session) -> dict:
    rows = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like("notif.enabled.%")).all()}
    return {
        "master": (rows.get("notif.enabled.master", "true") == "true"),
        **{c: (rows.get(f"notif.enabled.{c}", str(NOTIF_CATEGORY_DEFAULTS[c]).lower()) == "true")
           for c in NOTIF_CATEGORIES},
    }


def _notif_enabled(db: Session, category: str) -> bool:
    s = _notif_settings(db)
    return s["master"] and s.get(category, False)


def _notif_template(db: Session, kind: str) -> dict:
    defaults = NOTIF_TEMPLATE_DEFAULTS[kind]
    rows = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like(f"notif.template.{kind}.%")).all()}
    return {field: rows.get(f"notif.template.{kind}.{field}") or default
            for field, default in defaults.items()}


def _send_activation_email(db: Session, to: str, name: str, raw_token: str, center_id: Optional[int] = None):
    link = f"{FRONTEND_URL}/activate?token={raw_token}"
    brand = _org_brand(db, center_id)
    tpl = _notif_template(db, "activation")
    ctx = {"academy": brand["academy_name"], "name": name}
    html = _branded_email_html(
        academy=brand["academy_name"], logo_url=brand["logo_url"],
        heading=tpl["heading"].format(**ctx),
        intro=tpl["intro"].format(**ctx),
        button_label=tpl["button_label"], button_link=link,
        expiry_note=f"This link expires in {security.TOKEN_EXPIRY_MINUTES} minutes and can only be used once. "
                     "If you weren't expecting this, you can safely ignore this email.",
    )
    send_email(to, tpl["subject"].format(**ctx), html)


def _send_reset_email(db: Session, to: str, name: str, raw_token: str, center_id: Optional[int] = None):
    link = f"{FRONTEND_URL}/reset-password?token={raw_token}"
    brand = _org_brand(db, center_id)
    tpl = _notif_template(db, "password_reset")
    ctx = {"academy": brand["academy_name"], "name": name}
    html = _branded_email_html(
        academy=brand["academy_name"], logo_url=brand["logo_url"],
        heading=tpl["heading"].format(**ctx),
        intro=tpl["intro"].format(**ctx),
        button_label=tpl["button_label"], button_link=link,
        expiry_note=f"This link expires in {security.TOKEN_EXPIRY_MINUTES} minutes. "
                     "If you didn't request this, you can safely ignore this email.",
    )
    send_email(to, tpl["subject"].format(**ctx), html)


# ══════════════════════════ Audit + login history ══════════════════════════

def audit(db: Session, action: str, *, actor=None, subject=None,
          request: Optional[Request] = None, detail: Optional[dict] = None,
          center_id: Optional[int] = None):
    """Record a security-relevant event. actor/subject are (type, id) tuples."""
    ip = ua = None
    if request:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
    # Fall back to center_id embedded in detail dict if not passed explicitly
    resolved_center_id = center_id
    if resolved_center_id is None and isinstance(detail, dict):
        resolved_center_id = detail.get("center_id")
    db.add(AuditLog(
        action=action,
        actor_type=actor[0] if actor else None,
        actor_id=actor[1] if actor else None,
        subject_type=subject[0] if subject else None,
        subject_id=subject[1] if subject else None,
        center_id=resolved_center_id,
        ip_address=ip, user_agent=ua,
        detail=json.dumps(detail) if detail else None,
    ))


def record_login_attempt(db: Session, email: str, success: bool, reason: str,
                         subject=None, request: Optional[Request] = None):
    ip = ua = None
    if request:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
    db.add(LoginAttempt(
        email=(email or "").lower(), success=success, reason=reason,
        subject_type=subject[0] if subject else None,
        subject_id=subject[1] if subject else None,
        ip_address=ip, user_agent=ua,
    ))


# ══════════════════════════ Rate limiting (in-memory) ══════════════════════════
# NOTE: process-local. For multi-instance/franchise scale, back this with Redis.

class _RateLimiter:
    def __init__(self):
        self._hits: dict[str, list] = {}

    def check(self, key: str, max_hits: int, window_seconds: int):
        now = time.time()
        bucket = [t for t in self._hits.get(key, []) if now - t < window_seconds]
        if len(bucket) >= max_hits:
            raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
        bucket.append(now)
        self._hits[key] = bucket


rate_limiter = _RateLimiter()


def _client_ip(request: Request) -> str:
    return request.client.host if request and request.client else "unknown"


# ══════════════════════════ Token issuance ══════════════════════════

def issue_auth_token(db: Session, subject_type: str, subject_id: int, purpose: str) -> str:
    """Create a single-use token row, return the raw token to embed in a link."""
    raw, token_hash = security.generate_secure_token()
    db.add(AuthToken(
        token_hash=token_hash, purpose=purpose,
        subject_type=subject_type, subject_id=subject_id,
        expires_at=security.token_expiry(),
    ))
    return raw


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise to a UTC-aware datetime. Postgres TIMESTAMPTZ returns aware
    values; SQLite (and some drivers) return naive — treat those as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _token_is_valid(tok: Optional[AuthToken], now: datetime) -> bool:
    return bool(tok and tok.used_at is None and _as_aware(tok.expires_at) >= now)


def consume_auth_token(db: Session, raw_token: str, purpose: str) -> AuthToken:
    """Validate and mark a token used. Raises 400 if invalid/expired/used."""
    token_hash = security.hash_token(raw_token or "")
    tok = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == token_hash, AuthToken.purpose == purpose)
        .first()
    )
    now = datetime.now(timezone.utc)
    if not _token_is_valid(tok, now):
        raise HTTPException(status_code=400, detail="This link is invalid, already used, or expired")
    tok.used_at = now
    return tok


# ══════════════════════════ Account creation (called by hooks) ══════════════════════════

def provision_account(db: Session, subject_type: str, obj, *, actor=None,
                      request: Optional[Request] = None, send_activation: bool = True,
                      notify_email: Optional[str] = None):
    """Initialise a freshly-created identity as a pending account and send the
    activation email. Caller is responsible for committing the session.

    notify_email: deliver the activation email here instead of obj.email.
    Used for sibling accounts whose stored `email` is a synthetic, unique
    login handle derived from the family's real address — the real address
    (kept on notify_email/guardian_email) is what must actually receive mail.

    Returns the raw activation token for dev/UI use."""
    obj.account_status = PENDING
    obj.password_hash = None
    db.flush()  # ensure obj.id is populated
    audit(db, "user.created", actor=actor or (subject_type, obj.id),
          subject=(subject_type, obj.id), request=request,
          detail={"email": obj.email, "roles": roles_for(subject_type, obj)})
    activation_token = None
    deliver_to = notify_email or obj.email
    if send_activation and deliver_to:
        activation_token = issue_auth_token(db, subject_type, obj.id, "activation")
        # The activation-email toggle only applies to students (item 5 of the
        # notification settings) — staff accounts always get theirs, since
        # that's an internal admin action, not a family-facing notification.
        if subject_type != "student" or _notif_enabled(db, "activation"):
            _send_activation_email(db, deliver_to, display_name(subject_type, obj), activation_token,
                                   center_id=getattr(obj, "center_id", None))
    return activation_token


# ══════════════════════════ RBAC dependencies ══════════════════════════

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_subject(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = security.decode_token(creds.credentials, expected_type="access")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        subject_type, subject_id = payload["sub"].split(":")
        subject_id = int(subject_id)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed token subject")
    obj = load_subject(db, subject_type, subject_id)
    if not obj or obj.account_status != ACTIVE:
        raise HTTPException(status_code=401, detail="Account is not active")
    return {
        "type": subject_type, "id": subject_id, "obj": obj,
        "roles": payload.get("roles", []),
        "email": obj.email,
    }


def require_roles(*allowed_roles):
    """Dependency factory enforcing that the caller has at least one role."""
    def _dep(current: dict = Depends(get_current_subject)):
        if not set(current["roles"]) & set(allowed_roles):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current
    return _dep


# ══════════════════════════ Routes ══════════════════════════

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    rate_limiter.check(f"login:{_client_ip(request)}", max_hits=10, window_seconds=300)

    subject_type, obj = find_by_email(db, email)
    if not obj:
        record_login_attempt(db, email, False, "unknown_email", request=request)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    subject = (subject_type, obj.id)
    if not verify_credentials(db, obj, password):
        obj.failed_login_count = (obj.failed_login_count or 0) + 1
        record_login_attempt(db, email, False, "bad_password", subject=subject, request=request)
        audit(db, "login.failed", subject=subject, request=request)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if obj.account_status != ACTIVE:
        msg = {
            PENDING:  "Your account is pending activation. Check your email for the activation link.",
            SUSPENDED: "Your account is suspended. Please contact support.",
            DISABLED:  "Your account has been disabled.",
        }.get(obj.account_status, "Account is not active")
        record_login_attempt(db, email, False, "not_active", subject=subject, request=request)
        db.commit()
        raise HTTPException(status_code=403, detail=msg)

    # Success
    obj.failed_login_count = 0
    obj.last_login_at = datetime.now(timezone.utc)
    if security.needs_rehash(obj.password_hash):
        obj.password_hash = security.hash_password(password)
    roles = roles_for(subject_type, obj)
    sub = f"{subject_type}:{obj.id}"
    record_login_attempt(db, email, True, "ok", subject=subject, request=request)
    audit(db, "login.success", subject=subject, request=request)
    db.commit()
    return {
        "access_token": security.create_access_token(sub, roles),
        "refresh_token": security.create_refresh_token(sub),
        "token_type": "bearer",
        "user": {"id": obj.id, "type": subject_type, "email": obj.email,
                 "name": display_name(subject_type, obj), "roles": roles},
    }


@router.post("/refresh")
async def refresh(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    payload = security.decode_token(body.get("refresh_token") or "", expected_type="refresh")
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    subject_type, subject_id = payload["sub"].split(":")
    obj = load_subject(db, subject_type, int(subject_id))
    if not obj or obj.account_status != ACTIVE:
        raise HTTPException(status_code=401, detail="Account is not active")
    roles = roles_for(subject_type, obj)
    return {
        "access_token": security.create_access_token(payload["sub"], roles),
        "token_type": "bearer",
    }


@router.get("/validate-token")
def validate_token(token: str, purpose: str = "activation", db: Session = Depends(get_db)):
    """Lightweight check so the frontend can show/hide the set-password form."""
    token_hash = security.hash_token(token or "")
    tok = (
        db.query(AuthToken)
        .filter(AuthToken.token_hash == token_hash, AuthToken.purpose == purpose)
        .first()
    )
    now = datetime.now(timezone.utc)
    return {"valid": _token_is_valid(tok, now)}


@router.post("/activate")
async def activate(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    password = body.get("password") or ""
    err = security.validate_password_strength(password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    tok = consume_auth_token(db, body.get("token"), "activation")
    obj = load_subject(db, tok.subject_type, tok.subject_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Account not found")
    obj.password_hash = security.hash_password(password)
    obj.account_status = ACTIVE
    audit(db, "account.activated", subject=(tok.subject_type, tok.subject_id), request=request)
    audit(db, "password.changed", subject=(tok.subject_type, tok.subject_id), request=request)
    db.commit()
    return {"message": "Account activated. You can now log in.", "redirect": "/login"}


@router.post("/forgot-password")
async def forgot_password(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    rate_limiter.check(f"forgot:{_client_ip(request)}", max_hits=5, window_seconds=900)
    subject_type, obj = find_by_email(db, email)
    # Only act for activatable accounts, but never reveal which path we took.
    if obj and obj.account_status in (ACTIVE, PENDING):
        raw = issue_auth_token(db, subject_type, obj.id, "password_reset")
        _send_reset_email(db, obj.email, display_name(subject_type, obj), raw,
                          center_id=getattr(obj, "center_id", None))
        audit(db, "password.reset_requested", subject=(subject_type, obj.id), request=request)
        db.commit()
    return {"message": "If an account exists for that email, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    password = body.get("password") or ""
    err = security.validate_password_strength(password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    tok = consume_auth_token(db, body.get("token"), "password_reset")
    obj = load_subject(db, tok.subject_type, tok.subject_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Account not found")
    obj.password_hash = security.hash_password(password)
    if obj.account_status == PENDING:
        obj.account_status = ACTIVE  # reset doubles as activation if never activated
    obj.failed_login_count = 0
    audit(db, "password.changed", subject=(tok.subject_type, tok.subject_id),
          request=request, detail={"via": "reset"})
    db.commit()
    return {"message": "Password updated. You can now log in.", "redirect": "/login"}


@router.post("/change-password")
async def change_password(request: Request, current: dict = Depends(get_current_subject),
                          db: Session = Depends(get_db)):
    body = await request.json()
    obj = current["obj"]
    if not security.verify_password(body.get("current_password") or "", obj.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    new_pw = body.get("new_password") or ""
    err = security.validate_password_strength(new_pw)
    if err:
        raise HTTPException(status_code=400, detail=err)
    obj.password_hash = security.hash_password(new_pw)
    audit(db, "password.changed", subject=(current["type"], current["id"]),
          actor=(current["type"], current["id"]), request=request, detail={"via": "self"})
    db.commit()
    return {"message": "Password changed successfully"}


def linked_students(db: Session, student) -> list:
    """Sibling student accounts under the same guardian — the children a
    parent can view/switch between from a single login. Always includes the
    current student so the frontend can render a complete child switcher.

    Two children can be linked either via a shared guardian_email, or simply
    by both having the same login email on their own Student.email field
    (common when a parent's email was entered directly for each child
    rather than through a separate guardian_email) — either signal counts."""
    q = db.query(Student).filter(Student.account_status != DISABLED)
    conditions = []
    if student.guardian_email:
        conditions.append(Student.guardian_email.ilike(student.guardian_email))
    if student.email:
        conditions.append(Student.email.ilike(student.email))
    q = q.filter(or_(*conditions)) if conditions else q.filter(Student.id == student.id)
    rows = q.all()
    if student.id not in [s.id for s in rows]:
        rows.append(student)
    return [{"id": s.id, "first_name": s.first_name, "last_name": s.last_name,
             "email": s.email, "current_grade": s.current_grade} for s in rows]


@router.get("/me")
def me(current: dict = Depends(get_current_subject), db: Session = Depends(get_db)):
    obj, stype = current["obj"], current["type"]
    data = {
        "id": obj.id, "type": stype, "email": obj.email,
        "name": display_name(stype, obj), "roles": current["roles"],
        "status": obj.account_status,
    }
    # Student login = the parent/guardian. Surface all linked children so the
    # frontend can offer a switcher across siblings.
    if stype == "student":
        children = linked_students(db, obj)
        data["children"] = children
        data["is_multi_child"] = len(children) > 1
    return data


@router.post("/switch-student")
async def switch_student(request: Request, current: dict = Depends(get_current_subject),
                         db: Session = Depends(get_db)):
    """Issue an access token for a sibling student under the same guardian,
    so a parent can switch between their children without re-logging-in."""
    if current["type"] != "student":
        raise HTTPException(status_code=403, detail="Only student accounts have linked children")
    body = await request.json()
    target_id = body.get("student_id")
    allowed_ids = {c["id"] for c in linked_students(db, current["obj"])}
    if target_id not in allowed_ids:
        raise HTTPException(status_code=403, detail="That student is not linked to this account")
    target = load_subject(db, "student", target_id)
    if not target or target.account_status != ACTIVE:
        raise HTTPException(status_code=400, detail="Target student account is not active")
    audit(db, "student.switched", actor=("student", current["id"]),
          subject=("student", target_id), request=request)
    db.commit()
    sub = f"student:{target.id}"
    return {
        "access_token": security.create_access_token(sub, ["student"]),
        "token_type": "bearer",
        "user": {"id": target.id, "type": "student", "email": target.email,
                 "name": display_name("student", target), "roles": ["student"]},
    }
