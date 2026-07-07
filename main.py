from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, func
from sqlalchemy.orm import Session
from typing import Optional
import os as _osmod
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_osmod.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== DB Setup ====================
from database import engine, get_db, Base, SessionLocal
from models import (
    Center, Staff, Student, Grade, Subject, ExamSession,
    Syllabus, SyllabusModule, SyllabusContent, StudentProgress,
    Batch, ClassSession, StudentEnrollment, Attendance, Material,
    Package, StudentPackage, Invoice, Subscription, AppSetting,
    AuditLog,
    Room, Holiday, ClassTemplate, RecurrenceRule, ClassOccurrence,
    Enrollment, StudentInstructor,
    InvoiceItem, InvoiceInstallment, InvoicePayment, PaymentMode, InvoiceTemplate,
    StudentApplication, LearningEnrollment
)
import scheduling
import crud
from schemas import StaffCreate
from auth import router as auth_router, provision_account, email_exists, verify_credentials, audit, issue_auth_token, _send_activation_email, display_name, linked_students, send_email, roles_for, require_roles
import security
import enrollment as _enrollment_module

app.include_router(auth_router)
app.include_router(_enrollment_module.router)


@app.on_event("startup")
async def startup_event():
    try:
        Base.metadata.create_all(bind=engine)
        _run_migrations()
        _seed_defaults()
        _migrate_scheduling_v2()
        _seed_payment_config()
        print("✅ Database ready")
    except Exception as e:
        print(f"❌ Startup error: {e}")


def _run_migrations():
    """Add new columns to existing tables without Alembic."""
    migrations = [
        "ALTER TABLE staff ADD COLUMN IF NOT EXISTS password VARCHAR",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS password VARCHAR",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS current_grade VARCHAR DEFAULT 'Debut'",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS syllabus_type VARCHAR DEFAULT 'Trinity'",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS is_exam_student BOOLEAN DEFAULT FALSE",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS exam_date VARCHAR",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS instrument VARCHAR",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS teacher_id INTEGER REFERENCES staff(id)",
        "ALTER TABLE grades ADD COLUMN IF NOT EXISTS display_order INTEGER DEFAULT 0",
        "ALTER TABLE exam_sessions ALTER COLUMN grade_id DROP NOT NULL",
        "ALTER TABLE exam_sessions ALTER COLUMN subject_id DROP NOT NULL",
        # Payment tables (idempotent — CREATE TABLE IF NOT EXISTS handled by SQLAlchemy, these add missing columns)
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS invoice_number VARCHAR",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS tax_amount FLOAT DEFAULT 0",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS discount_amount FLOAT DEFAULT 0",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS total_amount FLOAT DEFAULT 0",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS paid_amount FLOAT DEFAULT 0",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payment_type VARCHAR",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS payment_mode VARCHAR",
        "ALTER TABLE class_sessions ADD COLUMN IF NOT EXISTS is_published BOOLEAN DEFAULT TRUE",
        "ALTER TABLE class_sessions ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'scheduled'",
        "ALTER TABLE attendances ADD COLUMN IF NOT EXISTS notes TEXT",
        "ALTER TABLE attendances ADD COLUMN IF NOT EXISTS enrollment_type VARCHAR DEFAULT 'single_session'",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS sessions_count INTEGER",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS attendance_sessions INTEGER",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS notes TEXT",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS sessions_total INTEGER",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS sessions_used INTEGER DEFAULT 0",
        # ── Per-occurrence roster overrides (recurrence-aware add/remove student) ──
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS coupon_code VARCHAR",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS has_installments BOOLEAN DEFAULT FALSE",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS discount_percentage FLOAT DEFAULT 0",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS template_id INTEGER",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS internal_notes TEXT",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS session_duration_minutes INTEGER DEFAULT 60",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS makeup_validity_days INTEGER",
        "ALTER TABLE packages ADD COLUMN IF NOT EXISTS cancellation_window_hours INTEGER DEFAULT 24",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS create_offset_days INTEGER DEFAULT 0",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS first_invoice_date VARCHAR",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS next_invoice_date VARCHAR",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS end_type VARCHAR DEFAULT 'never'",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS timezone VARCHAR DEFAULT 'Asia/Calcutta'",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS auto_email BOOLEAN DEFAULT TRUE",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS template_id INTEGER",
        "ALTER TABLE class_enrollments ADD COLUMN IF NOT EXISTS occurrence_id INTEGER",
        "ALTER TABLE class_enrollments ADD COLUMN IF NOT EXISTS kind VARCHAR DEFAULT 'include'",
        "ALTER TABLE class_enrollments DROP CONSTRAINT IF EXISTS uq_enrollment_template_student",
        """DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_enrollment_template_student_occ') THEN
                ALTER TABLE class_enrollments ADD CONSTRAINT uq_enrollment_template_student_occ
                    UNIQUE (template_id, student_id, occurrence_id);
            END IF;
        END $$;""",
        # ── Auth / account activation retrofit ──
        "ALTER TABLE staff ADD COLUMN IF NOT EXISTS password_hash VARCHAR",
        "ALTER TABLE staff ADD COLUMN IF NOT EXISTS account_status VARCHAR DEFAULT 'pending_activation'",
        "ALTER TABLE staff ADD COLUMN IF NOT EXISTS failed_login_count INTEGER DEFAULT 0",
        "ALTER TABLE staff ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS password_hash VARCHAR",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS account_status VARCHAR DEFAULT 'pending_activation'",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS failed_login_count INTEGER DEFAULT 0",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS guardian_email VARCHAR",
        # ── Materials table drift (model has columns the DB lacked) ──
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS url VARCHAR",
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS file_type VARCHAR",
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS student_id INTEGER",
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS batch_id INTEGER",
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS uploaded_by INTEGER",
        "ALTER TABLE materials ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()",
        # Pre-existing accounts with a legacy plaintext password are treated as active
        # so the rollout doesn't lock anyone out; they can reset to get a real hash.
        "UPDATE staff SET account_status = 'active' WHERE account_status IS NULL OR (password IS NOT NULL AND password_hash IS NULL)",
        "UPDATE students SET account_status = 'active' WHERE account_status IS NULL OR (password IS NOT NULL AND password_hash IS NULL)",
        """CREATE TABLE IF NOT EXISTS centers (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL UNIQUE,
            address VARCHAR,
            phone VARCHAR,
            email VARCHAR,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "ALTER TABLE staff ADD COLUMN IF NOT EXISTS access_role VARCHAR DEFAULT 'teacher'",
        "ALTER TABLE staff ADD COLUMN IF NOT EXISTS center_id INTEGER REFERENCES centers(id)",
        "ALTER TABLE students ADD COLUMN IF NOT EXISTS center_id INTEGER REFERENCES centers(id)",
        "ALTER TABLE batches ADD COLUMN IF NOT EXISTS center_id INTEGER REFERENCES centers(id)",
        """CREATE TABLE IF NOT EXISTS app_settings (
            key VARCHAR PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        # ── Phase 2: Multi-Center Data Gaps (Security Hardening) ──
        # Phase 2A: Add center_id to StudentApplication
        "ALTER TABLE student_applications ADD COLUMN IF NOT EXISTS center_id INTEGER REFERENCES centers(id)",
        # Phase 2B: Add center_id to Invoice (direct instead of via subquery)
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS center_id INTEGER REFERENCES centers(id)",
        # Backfill invoices.center_id from linked student
        "UPDATE invoices SET center_id = (SELECT center_id FROM students WHERE students.id = invoices.student_id) WHERE center_id IS NULL",
        # Phase 2C: Add center_id to AuditLog
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS center_id INTEGER",
        # Phase 2D: Add center_id to app_settings for per-center configuration
        "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS center_id INTEGER",
        # ── class_sessions.batch_id was NOT NULL in DB but nullable in model ──
        "ALTER TABLE class_sessions ALTER COLUMN batch_id DROP NOT NULL",
        # ── Syllabus modules missing 'order' column ──
        "ALTER TABLE syllabus_modules ADD COLUMN IF NOT EXISTS \"order\" INTEGER DEFAULT 1",
        "ALTER TABLE syllabus_modules ADD COLUMN IF NOT EXISTS weight FLOAT DEFAULT 1.0",
        "ALTER TABLE syllabus_contents ADD COLUMN IF NOT EXISTS weight FLOAT DEFAULT 1.0",
        "ALTER TABLE syllabus_contents ADD COLUMN IF NOT EXISTS content_type VARCHAR DEFAULT 'exercise'",
        # ── Enrollment module — learning_enrollments master record ──
        """CREATE TABLE IF NOT EXISTS learning_enrollments (
            id SERIAL PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id),
            teacher_id INTEGER NOT NULL REFERENCES staff(id),
            subject VARCHAR NOT NULL,
            syllabus_type VARCHAR NOT NULL DEFAULT 'Trinity',
            grade VARCHAR NOT NULL DEFAULT 'Debut',
            fee_package_id INTEGER REFERENCES packages(id),
            center_id INTEGER REFERENCES centers(id),
            status VARCHAR NOT NULL DEFAULT 'active',
            start_date VARCHAR,
            end_date VARCHAR,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_learning_enrollment_student_subject UNIQUE (student_id, subject)
        )""",
        # ── Performance indexes for session loading (N+1 query fix) ──
        "CREATE INDEX IF NOT EXISTS ix_attendance_session ON attendances(session_id)",
        "CREATE INDEX IF NOT EXISTS ix_attendance_student ON attendances(student_id)",
        "CREATE INDEX IF NOT EXISTS ix_enrollment_occurrence ON class_enrollments(occurrence_id)",
        "CREATE INDEX IF NOT EXISTS ix_enrollment_student_occ ON class_enrollments(student_id, occurrence_id)",
        "CREATE INDEX IF NOT EXISTS ix_enrollment_template_occ_status ON class_enrollments(template_id, occurrence_id, status)",
    ]
    for sql in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
        except Exception:
            pass


def _seed_defaults():
    """Seed Grade, Subject, and ExamSession rows if tables are empty."""
    with engine.connect() as conn:
        if conn.execute(text("SELECT COUNT(*) FROM grades")).scalar() == 0:
            grades = [
                "Debut", "Grade 1", "Grade 2", "Grade 3",
                "Grade 4", "Grade 5", "Grade 6", "Grade 7", "Grade 8"
            ]
            for i, name in enumerate(grades):
                conn.execute(
                    text("INSERT INTO grades (name, display_order) VALUES (:name, :order)"),
                    {"name": name, "order": i}
                )

        if conn.execute(text("SELECT COUNT(*) FROM subjects")).scalar() == 0:
            subjects = ["Piano", "Guitar", "Violin", "Vocals", "Drums", "Keyboard", "Flute", "Tabla"]
            for name in subjects:
                conn.execute(
                    text("INSERT INTO subjects (name, is_active) VALUES (:name, TRUE)"),
                    {"name": name}
                )

        # Seed centers
        if conn.execute(text("SELECT COUNT(*) FROM centers")).scalar() == 0:
            centers = [
                ("Vama - Gunjur",           "Gunjur, Bengaluru"),
                ("Vama - Varthur",          "Varthur, Bengaluru"),
                ("Vama - Kadubeesnahali",   "Kadubeesnahali, Bengaluru"),
            ]
            for name, address in centers:
                conn.execute(
                    text("INSERT INTO centers (name, address, is_active) VALUES (:name, :address, TRUE)"),
                    {"name": name, "address": address}
                )
            conn.commit()

        # Backfill any centers with NULL is_active (Python-side default doesn't apply to raw SQL)
        conn.execute(text("UPDATE centers SET is_active = TRUE WHERE is_active IS NULL"))
        conn.commit()

        # Assign students to centers based on nearest_vama_center
        conn.execute(text("""
            UPDATE students s
            SET center_id = c.id
            FROM centers c
            WHERE s.nearest_vama_center = c.name
              AND s.center_id IS NULL
        """))
        conn.commit()

        # Designate super_admin: first staff with 'Admin' role or email = techatvama@gmail.com
        conn.execute(text("""
            UPDATE staff SET access_role = 'super_admin'
            WHERE (role ILIKE '%super%admin%' OR email = 'techatvama@gmail.com')
              AND access_role != 'super_admin'
        """))
        conn.commit()

        # Ensure the primary super-admin account always exists and is active.
        # No password is seeded — the admin sets one via "Forgot password".
        conn.execute(text("""
            INSERT INTO staff (name, role, access_role, phone, email, account_status, calendar, takes_classes)
            VALUES ('Super Admin', 'Admin', 'super_admin', '0000000000',
                    'techatvama@gmail.com', 'active', TRUE, TRUE)
            ON CONFLICT (email) DO UPDATE
              SET access_role = 'super_admin', account_status = 'active'
        """))
        conn.commit()

    # Seed exam_sessions separately to handle schema variations
    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM exam_sessions")).scalar()
            if count == 0:
                exam_sessions_data = [
                    ("Trinity March 2026", "Trinity", True),
                    ("Trinity June 2026", "Trinity", True),
                    ("Trinity December 2026", "Trinity", True),
                    ("ABRSM April 2026", "ABRSM", True),
                    ("RSL Summer 2026", "RSL", True),
                ]
                for name, board, active in exam_sessions_data:
                    try:
                        conn.execute(
                            text("INSERT INTO exam_sessions (name, exam_board, is_active) VALUES (:name, :board, :active)"),
                            {"name": name, "board": board, "active": active}
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
    except Exception as e:
        print(f"Exam session seed warning: {e}")


def _migrate_scheduling_v2():
    """One-time backfill from legacy batches/class_sessions into the v2 model.

    Idempotent — guarded by an app_settings sentinel. Preserves occurrence ids
    in parity with class_sessions.id so existing `attendances.session_id` stays
    valid, advances the occurrence id sequence past them, and relaxes the
    attendances FK so it can reference occurrences going forward.
    """
    db = SessionLocal()
    try:
        sentinel = db.query(AppSetting).filter(AppSetting.key == "scheduling_v2_migrated").first()
        if sentinel and sentinel.value == "1":
            return

        # Postgres-only housekeeping (skipped harmlessly on SQLite).
        is_pg = engine.dialect.name == "postgresql"
        if is_pg:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE attendances DROP CONSTRAINT IF EXISTS attendances_session_id_fkey"))
                conn.commit()

        # Map each legacy Batch → ClassTemplate (+ synthesized RecurrenceRule).
        batches = db.query(Batch).all()
        batch_to_template = {}
        for b in batches:
            sessions = db.query(ClassSession).filter(ClassSession.batch_id == b.id).order_by(ClassSession.date).all()

            def _safe_iso(v):
                try:
                    return _d.fromisoformat(v) if isinstance(v, str) and v else None
                except ValueError:
                    return None

            dated = [s for s in sessions if _safe_iso(s.date)]
            if dated:
                start_time = dated[0].start_time or "10:00"
                end_time = dated[0].end_time or "11:00"
                weekdays = sorted({_safe_iso(s.date).weekday() for s in dated})
                by_weekday = ",".join(scheduling.WEEKDAY_CODES[w] for w in weekdays) or None
                start_date = dated[0].date
                end_date = dated[-1].date
            else:
                start_time, end_time = "10:00", "11:00"
                by_weekday, start_date, end_date = None, _d.today().isoformat(), None

            t = ClassTemplate(
                name=b.name or (b.subject or "Class"), course=b.subject,
                teacher_id=b.teacher_id, center_id=b.center_id,
                start_time=start_time, end_time=end_time, capacity=10,
                status="active", legacy_batch_id=b.id,
            )
            db.add(t)
            db.flush()
            db.add(RecurrenceRule(
                template_id=t.id, freq="custom" if by_weekday else "weekly",
                interval=1, by_weekday=by_weekday, start_date=start_date, end_date=end_date,
            ))
            batch_to_template[b.id] = t.id

        # Each ClassSession → ClassOccurrence WITH THE SAME id (attendance parity).
        for s in db.query(ClassSession).all():
            if db.query(ClassOccurrence).filter(ClassOccurrence.id == s.id).first():
                continue
            db.add(ClassOccurrence(
                id=s.id, template_id=batch_to_template.get(s.batch_id),
                date=s.date, start_time=s.start_time, end_time=s.end_time,
                teacher_id=s.teacher_id, status=s.status or "scheduled",
                is_published=s.is_published if s.is_published is not None else True,
            ))
        db.flush()

        # Each active StudentEnrollment → template-level Enrollment.
        for e in db.query(StudentEnrollment).all():
            tid = batch_to_template.get(e.batch_id)
            if not tid:
                continue
            if db.query(Enrollment).filter(Enrollment.template_id == tid, Enrollment.student_id == e.student_id).first():
                continue
            db.add(Enrollment(template_id=tid, student_id=e.student_id, status="active"))

        db.commit()

        # Advance the occurrence id sequence past the copied session ids (Postgres).
        if is_pg:
            with engine.connect() as conn:
                conn.execute(text(
                    "SELECT setval('class_occurrences_id_seq', "
                    "GREATEST((SELECT COALESCE(MAX(id),1) FROM class_occurrences), 1))"
                ))
                conn.commit()

        if sentinel:
            sentinel.value = "1"
        else:
            db.add(AppSetting(key="scheduling_v2_migrated", value="1"))
        db.commit()
        print(f"✅ Scheduling v2 migration: {len(batches)} templates from batches")
    except Exception as e:
        db.rollback()
        print(f"⚠️  Scheduling v2 migration warning: {e}")
    finally:
        db.close()

    # Backfill student_instructors from each student's primary teacher+instrument.
    db = SessionLocal()
    try:
        sentinel = db.query(AppSetting).filter(AppSetting.key == "student_instructors_seeded").first()
        if not (sentinel and sentinel.value == "1"):
            for s in db.query(Student).filter(Student.teacher_id.isnot(None)).all():
                inst = s.instrument or s.desired_course
                exists = db.query(StudentInstructor).filter(
                    StudentInstructor.student_id == s.id, StudentInstructor.teacher_id == s.teacher_id,
                    StudentInstructor.instrument == inst).first()
                if not exists:
                    db.add(StudentInstructor(student_id=s.id, teacher_id=s.teacher_id, instrument=inst))
            if sentinel:
                sentinel.value = "1"
            else:
                db.add(AppSetting(key="student_instructors_seeded", value="1"))
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"⚠️  student_instructors backfill warning: {e}")
    finally:
        db.close()


def _seed_payment_config():
    """Seed default payment modes + academy/invoice defaults once (idempotent)."""
    db = SessionLocal()
    try:
        if db.query(PaymentMode).count() == 0:
            for i, name in enumerate(["UPI", "Cash", "Credit Card", "Debit Card", "Bank Transfer", "Cheque"]):
                db.add(PaymentMode(name=name, is_active=True, sort_order=i))
            db.commit()
        # Academy / invoice defaults (only fill blanks — never overwrite admin edits).
        defaults = {
            "academy_name": "Vama Academy for Music & Performing Arts",
            "address": "215, behind Udupi Grand Restaurant, Vinayaka Nagar,\nGunjur Village, Varthur,\nBengaluru, Karnataka, India 560087",
            "phone": "+91 97400 12337",
            "email": "vamaacademy.varthur@gmail.com",
            "website": "https://vamaacademy.in/",
            "gst_number": "29AVTPS1253R1ZF",
            "invoice_notes": (
                "WELCOME!\n"
                "Thank you for choosing us to embark on your musical journey. We're thrilled to have you join our "
                "vibrant community of music enthusiasts! Our dedicated team of experienced teachers is here to inspire "
                "and guide you every step of the way. Get ready to unleash your creativity, explore new melodies, and "
                "discover the joy of making music.\n\n"
                "TERMS & CONDITIONS\n"
                "We are committed to providing the best possible musical education and experience for our students. We "
                "understand that circumstances may arise that require changes to your enrollment, but please note that "
                "fees once paid will not be refunded under any circumstances. This includes but is not limited to "
                "withdrawal from classes, changes in scheduling, or any other reason."
            ),
        }
        for k, v in defaults.items():
            key = f"org.{k}"
            if not db.query(AppSetting).filter(AppSetting.key == key).first():
                db.add(AppSetting(key=key, value=v))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"⚠️  payment config seed warning: {e}")
    finally:
        db.close()


# ==================== Auth ====================

@app.post("/student/login")
async def student_login(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    student = db.query(Student).filter(
        Student.email.ilike(email)
    ).first()

    if not student:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    import asyncio
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, verify_credentials, db, student, password)
    if not ok:
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if (student.account_status or "active") != "active":
        raise HTTPException(status_code=403, detail="Your account is not active. Please activate it via the link sent to your email.")

    # Generate JWT token for security Phase 1
    roles = roles_for("student", student)
    sub = f"student:{student.id}"
    access_token = security.create_access_token(sub, roles)

    db.commit()

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "student": {
            "id": student.id,
            "first_name": student.first_name,
            "last_name": student.last_name,
            "email": student.email,
            "course": student.desired_course or "",
            "instrument": student.instrument or student.desired_course or "",
            "grade": student.current_grade or "Debut",
            "primary_phone_number": student.primary_phone_number or "",
            "nearest_vama_center": student.nearest_vama_center or "",
            "desired_course": student.desired_course or "",
            "current_grade": student.current_grade or "Debut",
            "syllabus_type": student.syllabus_type or "Trinity",
            "is_exam_student": student.is_exam_student or False,
            "exam_date": student.exam_date,
        }
    }


@app.get("/student/{student_id}/siblings")
def get_student_siblings(student_id: int, db: Session = Depends(get_db)):
    """Children linked to the same guardian — powers the parent child-switcher."""
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return linked_students(db, student)


@app.post("/teacher/login")
async def teacher_login(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    teacher = db.query(Staff).filter(
        Staff.email.ilike(email)
    ).first()

    if not teacher:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    import asyncio
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, verify_credentials, db, teacher, password)
    if not ok:
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if (teacher.account_status or "active") != "active":
        raise HTTPException(status_code=403, detail="Your account is not active. Please activate it via the link sent to your email.")

    # Generate JWT token for security Phase 1
    roles = roles_for("staff", teacher)
    sub = f"staff:{teacher.id}"
    access_token = security.create_access_token(sub, roles)

    db.commit()

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "teacher": {
            "id": teacher.id,
            "name": teacher.name,
            "first_name": teacher.first_name or teacher.name.split()[0],
            "last_name": teacher.last_name or (teacher.name.split()[1] if len(teacher.name.split()) > 1 else ""),
            "email": teacher.email,
            "role": teacher.role,
            "phone": teacher.phone,
        }
    }


# ==================== Students ====================

@app.get("/students")
def get_students(center_id: Optional[int] = None, page: Optional[int] = None, limit: int = 50,
                db: Session = Depends(get_db),
                current = Depends(require_roles("super_admin", "center_admin", "teacher"))):
    """List all students, optionally filtered by center. Phase 6: Paginated if page param provided."""
    q = db.query(Student)
    # Phase 1A: Center admin only sees their center's students
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(Student.center_id == current["obj"].center_id)
    elif center_id:
        q = q.filter(Student.center_id == center_id)

    extras = _profile_extras_map(db)

    def format_student(s):
        return {
            "id": s.id,
            "first_name": s.first_name,
            "last_name": s.last_name,
            "email": s.email,
            "guardian_email": s.guardian_email or "",
            "primary_phone_number": s.primary_phone_number or "",
            "date_of_birth": s.date_of_birth or "",
            "gender": s.gender or "",
            "address": s.address or "",
            "desired_course": s.desired_course or "",
            "instrument": s.instrument or s.desired_course or "",
            "nearest_vama_center": s.nearest_vama_center or "",
            "preferred_mode_of_contact": s.preferred_mode_of_contact or "",
            "current_grade": s.current_grade or "Debut",
            "syllabus_type": s.syllabus_type or "Trinity",
            "is_exam_student": s.is_exam_student or False,
            "exam_date": s.exam_date,
            "teacher_id": s.teacher_id,
            "created_at": s.created_at.isoformat() if s.created_at else "",
            **_extra_fields_dict(extras.get(s.id)),
        }

    # If no page param, return array (backward compatibility)
    if page is None:
        students = q.order_by(Student.first_name).all()
        return [format_student(s) for s in students]

    # If page param provided, return paginated response
    total = q.count()
    students = q.order_by(Student.first_name).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [format_student(s) for s in students],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


@app.get("/admin/students-overview")
def admin_students_overview(center_id: Optional[int] = None, db: Session = Depends(get_db),
                            current=Depends(require_roles("super_admin", "center_admin", "teacher"))):
    """Enriched roster for the Enrollment Manager: each student with assigned
    instructor, curriculum, exam status, a live progress %, and a 'portal_ready'
    flag (instrument + instructor set → packages/booking unlock in their portal).
    Computed with prefetched maps (no N+1)."""
    from sqlalchemy import func as _func
    q = db.query(Student)
    caller = current.get("obj")
    # Center admins and teachers only see their own center's students.
    if caller and getattr(caller, "access_role", None) in ("center_admin", "teacher") and getattr(caller, "center_id", None):
        q = q.filter(Student.center_id == caller.center_id)
    elif center_id:
        q = q.filter(Student.center_id == center_id)
    students = q.order_by(Student.first_name).all()

    staff_map = {s.id: s.name for s in db.query(Staff).all()}
    center_map = {c.id: c.name for c in db.query(Center).all()}
    # Build enrollment map for enriching tracks with grade/syllabus/fee
    enroll_map = {}
    for le in db.query(LearningEnrollment).all():
        enroll_map[(le.student_id, le.subject)] = le
    pkg_map_ov = {p.id: p.name for p in db.query(Package).all()}

    tracks_by_student = {}
    for ti in db.query(StudentInstructor).all():
        le = enroll_map.get((ti.student_id, ti.instrument or ""))
        tracks_by_student.setdefault(ti.student_id, []).append({
            "id": ti.id,
            "teacher_id": ti.teacher_id,
            "teacher_name": staff_map.get(ti.teacher_id),
            "instrument": ti.instrument or "",
            "enrollment_id": le.id if le else None,
            "grade": le.grade if le else "Debut",
            "syllabus_type": le.syllabus_type if le else "Trinity",
            "status": le.status if le else "active",
            "fee_package_id": le.fee_package_id if le else None,
            "fee_package_name": pkg_map_ov.get(le.fee_package_id) if le else None,
        })
    # Build learning_enrollments map per student for the enrollment module
    pkg_map = {p.id: p.name for p in db.query(Package).all()}
    enrollments_by_student = {}
    for le in db.query(LearningEnrollment).all():
        enrollments_by_student.setdefault(le.student_id, []).append({
            "id": le.id,
            "subject": le.subject,
            "teacher_id": le.teacher_id,
            "teacher_name": staff_map.get(le.teacher_id),
            "grade": le.grade,
            "syllabus_type": le.syllabus_type,
            "fee_package_id": le.fee_package_id,
            "fee_package_name": pkg_map.get(le.fee_package_id),
            "status": le.status,
            "start_date": le.start_date,
        })
    done_counts = dict(db.query(StudentProgress.student_id, _func.count())
                       .filter(StudentProgress.status == "done")
                       .group_by(StudentProgress.student_id).all())
    total_prog = dict(db.query(StudentProgress.student_id, _func.count())
                      .group_by(StudentProgress.student_id).all())
    # Syllabus content counts keyed by (grade, type) for the canonical denominator.
    syll_counts = {}
    try:
        for syl in db.query(Syllabus).all():
            syll_counts[(syl.grade_name, syl.syllabus_type)] = sum(len(m.contents) for m in syl.modules)
    except Exception:
        pass  # syllabus_modules.order column may not yet exist; progress % will fall back to 0

    rows = []
    for s in students:
        total = syll_counts.get((s.current_grade, s.syllabus_type), 0) or total_prog.get(s.id, 0)
        done = done_counts.get(s.id, 0)
        rows.append({
            "id": s.id,
            "first_name": s.first_name, "last_name": s.last_name,
            "email": s.email, "primary_phone_number": s.primary_phone_number or "",
            "instrument": s.instrument or s.desired_course or "",
            "desired_course": s.desired_course or "",
            "current_grade": s.current_grade or "Debut",
            "syllabus_type": s.syllabus_type or "Trinity",
            "is_exam_student": bool(s.is_exam_student),
            "exam_date": s.exam_date,
            "teacher_id": s.teacher_id,
            "teacher_name": staff_map.get(s.teacher_id),
            "center_id": s.center_id,
            "center_name": center_map.get(s.center_id),
            "progress_done": done, "progress_total": total,
            "progress_pct": round(done / total * 100) if total else 0,
            "portal_ready": bool(s.instrument and s.teacher_id),
            "tracks": tracks_by_student.get(s.id, []),
            "enrollments": enrollments_by_student.get(s.id, []),
        })
    return rows


def _sync_student_primary(db, student):
    """Mirror the first track onto Student.instrument/teacher_id for the portal
    and progress views (which remain single-track aware)."""
    first = (db.query(StudentInstructor)
             .filter(StudentInstructor.student_id == student.id)
             .order_by(StudentInstructor.id).first())
    student.teacher_id = first.teacher_id if first else None
    if first and first.instrument:
        student.instrument = first.instrument


def _instructor_track_dict(ti: StudentInstructor, db) -> dict:
    """Return a track row enriched with LearningEnrollment grade/syllabus/fee data."""
    staff_map = {s.id: s.name for s in db.query(Staff).all()}
    enroll = db.query(LearningEnrollment).filter(
        LearningEnrollment.student_id == ti.student_id,
        LearningEnrollment.subject == (ti.instrument or ""),
    ).first()
    pkg = db.query(Package).filter(Package.id == enroll.fee_package_id).first() if enroll and enroll.fee_package_id else None
    return {
        "id": ti.id,
        "teacher_id": ti.teacher_id,
        "teacher_name": staff_map.get(ti.teacher_id),
        "instrument": ti.instrument or "",
        # enrollment-level fields
        "enrollment_id": enroll.id if enroll else None,
        "grade": enroll.grade if enroll else "Debut",
        "syllabus_type": enroll.syllabus_type if enroll else "Trinity",
        "status": enroll.status if enroll else "active",
        "start_date": enroll.start_date if enroll else None,
        "fee_package_id": enroll.fee_package_id if enroll else None,
        "fee_package_name": pkg.name if pkg else None,
        "fee_package_price": pkg.price if pkg else None,
    }


@app.get("/admin/students/{student_id}/instructors")
def list_student_instructors(student_id: int, db: Session = Depends(get_db)):
    rows = db.query(StudentInstructor).filter(
        StudentInstructor.student_id == student_id
    ).order_by(StudentInstructor.id).all()
    return [_instructor_track_dict(ti, db) for ti in rows]


@app.post("/admin/students/{student_id}/instructors")
async def add_student_instructor(student_id: int, request: Request, db: Session = Depends(get_db)):
    """Assign teacher + subject (instrument) + grade + syllabus to a student.

    Creates a StudentInstructor track AND a LearningEnrollment so that grade,
    syllabus, and fee package are stored per-subject — not globally on the student.
    """
    body = await request.json()
    teacher_id = body.get("teacher_id")
    instrument = (body.get("instrument") or "").strip() or None
    grade = body.get("grade", "Debut")
    syllabus_type = body.get("syllabus_type", "Trinity")

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if not teacher_id:
        raise HTTPException(status_code=400, detail="teacher_id is required")

    # Upsert StudentInstructor (backward compat)
    existing = db.query(StudentInstructor).filter(
        StudentInstructor.student_id == student_id,
        StudentInstructor.teacher_id == teacher_id,
        StudentInstructor.instrument == instrument,
    ).first()
    if not existing:
        existing = StudentInstructor(
            student_id=student_id, teacher_id=teacher_id, instrument=instrument
        )
        db.add(existing)
        db.flush()

    # Upsert LearningEnrollment — one per student+subject
    enroll = db.query(LearningEnrollment).filter(
        LearningEnrollment.student_id == student_id,
        LearningEnrollment.subject == (instrument or ""),
    ).first()

    fee_package_id = body.get("fee_package_id") or _enrollment_module._auto_package(db, instrument or "", grade)

    if enroll:
        enroll.teacher_id = teacher_id
        enroll.grade = grade
        enroll.syllabus_type = syllabus_type
        enroll.status = "active"
        if fee_package_id:
            enroll.fee_package_id = fee_package_id
    else:
        enroll = LearningEnrollment(
            student_id=student_id,
            teacher_id=teacher_id,
            subject=instrument or "",
            grade=grade,
            syllabus_type=syllabus_type,
            fee_package_id=fee_package_id,
            center_id=student.center_id,
            status="active",
            start_date=body.get("start_date") or str(__import__("datetime").date.today()),
        )
        db.add(enroll)

    _sync_student_primary(db, student)
    db.commit()
    db.refresh(existing)
    return _instructor_track_dict(existing, db)


@app.put("/admin/students/{student_id}/instructors/{track_id}")
async def update_student_instructor(student_id: int, track_id: int, request: Request, db: Session = Depends(get_db)):
    """Update grade, syllabus, teacher, or subject on an existing track.
    Fee package is automatically re-mapped when grade or subject changes.
    """
    body = await request.json()
    ti = db.query(StudentInstructor).filter(
        StudentInstructor.id == track_id,
        StudentInstructor.student_id == student_id,
    ).first()
    if not ti:
        raise HTTPException(status_code=404, detail="Track not found")

    subject = ti.instrument or ""

    if "teacher_id" in body:
        ti.teacher_id = body["teacher_id"]
    if "instrument" in body:
        ti.instrument = (body["instrument"] or "").strip() or None
        subject = ti.instrument or ""

    # Update the matching LearningEnrollment
    enroll = db.query(LearningEnrollment).filter(
        LearningEnrollment.student_id == student_id,
        LearningEnrollment.subject == subject,
    ).first()

    grade = body.get("grade", enroll.grade if enroll else "Debut")
    syllabus_type = body.get("syllabus_type", enroll.syllabus_type if enroll else "Trinity")
    fee_package_id = body.get("fee_package_id") or _enrollment_module._auto_package(db, subject, grade)

    if enroll:
        enroll.teacher_id = ti.teacher_id
        enroll.grade = grade
        enroll.syllabus_type = syllabus_type
        if fee_package_id:
            enroll.fee_package_id = fee_package_id
        if "status" in body:
            enroll.status = body["status"]
    else:
        student = db.query(Student).filter(Student.id == student_id).first()
        enroll = LearningEnrollment(
            student_id=student_id,
            teacher_id=ti.teacher_id,
            subject=subject,
            grade=grade,
            syllabus_type=syllabus_type,
            fee_package_id=fee_package_id,
            center_id=student.center_id if student else None,
            status="active",
        )
        db.add(enroll)

    student = db.query(Student).filter(Student.id == student_id).first()
    if student:
        _sync_student_primary(db, student)
    db.commit()
    db.refresh(ti)
    return _instructor_track_dict(ti, db)


@app.delete("/admin/students/{student_id}/instructors/{track_id}")
def remove_student_instructor(student_id: int, track_id: int, db: Session = Depends(get_db)):
    ti = db.query(StudentInstructor).filter(
        StudentInstructor.id == track_id, StudentInstructor.student_id == student_id).first()
    if not ti:
        raise HTTPException(status_code=404, detail="Track not found")

    # Deactivate the matching LearningEnrollment
    enroll = db.query(LearningEnrollment).filter(
        LearningEnrollment.student_id == student_id,
        LearningEnrollment.subject == (ti.instrument or ""),
    ).first()
    if enroll:
        enroll.status = "completed"

    db.delete(ti)
    db.flush()
    student = db.query(Student).filter(Student.id == student_id).first()
    if student:
        _sync_student_primary(db, student)
    db.commit()
    return {"message": "Instructor removed"}


@app.post("/students")
async def create_student(request: Request, db: Session = Depends(get_db),
                        current = Depends(require_roles("super_admin", "center_admin"))):
    """Create a new student in the database and provision a pending-activation account."""
    body = await request.json()
    email = (body.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    # Email must be unique across the entire system (staff/students/parents).
    if email_exists(db, email):
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    # Center admins automatically assign new students to their center.
    caller = current.get("obj")
    center_id = body.get("center_id")
    if not center_id and caller and getattr(caller, "access_role", None) == "center_admin":
        center_id = getattr(caller, "center_id", None)

    student = Student(
        first_name=body.get("first_name", ""),
        last_name=body.get("last_name", ""),
        email=email,
        guardian_email=(body.get("guardian_email") or "").strip().lower() or None,
        primary_phone_number=body.get("primary_phone_number", ""),
        gender=body.get("gender"),
        address=body.get("address"),
        desired_course=body.get("desired_course"),
        nearest_vama_center=body.get("nearest_vama_center"),
        current_grade=body.get("current_grade", "Debut"),
        syllabus_type=body.get("syllabus_type", "Trinity"),
        instrument=body.get("instrument") or body.get("desired_course"),
        teacher_id=body.get("teacher_id"),
        center_id=center_id,
    )
    db.add(student)
    # No default password — provision a pending account + send activation email.
    provision_account(db, "student", student, request=request)
    if any(f in body for f in EXTRA_PROFILE_FIELDS):
        extra = StudentApplication(
            first_name=student.first_name, last_name=student.last_name, email=student.email,
            status="approved", student_id=student.id,
            **{f: body.get(f) for f in EXTRA_PROFILE_FIELDS if f in body},
        )
        db.add(extra)
    db.commit()
    # Phase 4A: Audit student creation
    audit(db, "student.created", subject=("staff", current["id"]), request=request,
          detail={"student_id": student.id, "center_id": student.center_id})
    db.commit()
    db.refresh(student)
    return {"id": student.id, "first_name": student.first_name, "last_name": student.last_name}


@app.put("/students/{student_id}")
async def update_student(student_id: int, request: Request, db: Session = Depends(get_db),
                        current = Depends(require_roles("super_admin", "center_admin"))):
    """Update student — handles both portal fields and general fields."""
    body = await request.json()

    student = db.query(Student).filter(Student.id == student_id).first()
    if student:
        # Portal / teacher-assigned fields
        if "current_grade" in body:
            student.current_grade = body["current_grade"]
        if "syllabus_type" in body:
            student.syllabus_type = body["syllabus_type"]
        if "is_exam_student" in body:
            student.is_exam_student = body["is_exam_student"]
        if "exam_date" in body:
            student.exam_date = body["exam_date"]
        if "instrument" in body:
            student.instrument = body["instrument"]
        if "teacher_id" in body:
            student.teacher_id = body["teacher_id"]
        if body.get("password"):
            # Route password changes through hashing + policy, never store plaintext.
            err = security.validate_password_strength(body["password"])
            if err:
                raise HTTPException(status_code=400, detail=err)
            student.password_hash = security.hash_password(body["password"])
            student.password = None
            if (student.account_status or "active") != "active":
                student.account_status = "active"
        # General fields
        if "first_name" in body or "First_Name" in body:
            student.first_name = body.get("first_name") or body.get("First_Name")
        if "last_name" in body or "Last_Name" in body:
            student.last_name = body.get("last_name") or body.get("Last_Name")
        if "email" in body or "Email" in body:
            student.email = body.get("email") or body.get("Email")
        if "guardian_email" in body:
            student.guardian_email = (body.get("guardian_email") or "").strip().lower() or None
        if "primary_phone_number" in body or "Primary_Phone_Number" in body:
            student.primary_phone_number = body.get("primary_phone_number") or body.get("Primary_Phone_Number")
        if "desired_course" in body or "Desired_Course" in body:
            student.desired_course = body.get("desired_course") or body.get("Desired_Course")
        if "nearest_vama_center" in body or "Nearest_Vama_Center" in body:
            student.nearest_vama_center = body.get("nearest_vama_center") or body.get("Nearest_Vama_Center")
        if "preferred_mode_of_contact" in body:
            student.preferred_mode_of_contact = body.get("preferred_mode_of_contact")

        # Fields with no column on Student (parent name, city, allergies, etc.) —
        # kept on the linked StudentApplication row so the Student table stays as-is.
        if any(f in body for f in EXTRA_PROFILE_FIELDS):
            extra = _get_or_create_profile_extra(db, student)
            for f in EXTRA_PROFILE_FIELDS:
                if f in body:
                    setattr(extra, f, body[f])

        db.commit()
        # Phase 4A: Audit student.updated
        audit(db, "student.updated", subject=("staff", current["id"]), request=request,
              detail={"student_id": student.id, "center_id": student.center_id})
        db.commit()
        db.refresh(student)
        extra = db.query(StudentApplication).filter(StudentApplication.student_id == student.id).first()
        return {
            "id": student.id,
            "first_name": student.first_name,
            "last_name": student.last_name,
            "email": student.email,
            "guardian_email": student.guardian_email or "",
            "primary_phone_number": student.primary_phone_number or "",
            "date_of_birth": student.date_of_birth or "",
            "gender": student.gender or "",
            "address": student.address or "",
            "desired_course": student.desired_course or "",
            "instrument": student.instrument or student.desired_course or "",
            "nearest_vama_center": student.nearest_vama_center or "",
            "preferred_mode_of_contact": student.preferred_mode_of_contact or "",
            "current_grade": student.current_grade or "Debut",
            "syllabus_type": student.syllabus_type or "Trinity",
            "is_exam_student": student.is_exam_student or False,
            "exam_date": student.exam_date,
            "teacher_id": student.teacher_id,
            "center_id": student.center_id,
            "created_at": student.created_at.isoformat() if student.created_at else "",
            **_extra_fields_dict(extra),
        }

    raise HTTPException(status_code=404, detail="Student not found")


# ==================== Student Applications (Public Intake) ====================

# Profile fields collected on intake that aren't columns on Student — they're kept on
# the linked StudentApplication row instead, so the table structure for Student never
# has to change. One application row per student; created on first edit if the student
# didn't originate from the public form.
EXTRA_PROFILE_FIELDS = [
    "parent_name", "city", "state", "state_code", "class_frequency",
    "emergency_contact", "blood_group", "allergies", "referrer",
]


def _get_or_create_profile_extra(db: Session, student: "Student") -> "StudentApplication":
    extra = db.query(StudentApplication).filter(StudentApplication.student_id == student.id).first()
    if not extra:
        extra = StudentApplication(
            first_name=student.first_name, last_name=student.last_name, email=student.email,
            status="approved", student_id=student.id,
        )
        db.add(extra)
        db.flush()
    return extra


def _extra_fields_dict(extra: Optional["StudentApplication"]) -> dict:
    return {f: (getattr(extra, f, None) or "") for f in EXTRA_PROFILE_FIELDS}


def _profile_extras_map(db: Session) -> dict:
    rows = db.query(StudentApplication).filter(StudentApplication.student_id.isnot(None)).all()
    return {a.student_id: a for a in rows}


def _application_dict(a: "StudentApplication") -> dict:
    return {
        "id": a.id, "first_name": a.first_name, "last_name": a.last_name, "email": a.email,
        "guardian_email": a.guardian_email, "primary_phone_number": a.primary_phone_number,
        "emergency_contact": a.emergency_contact, "date_of_birth": a.date_of_birth, "gender": a.gender,
        "parent_name": a.parent_name, "address": a.address, "city": a.city, "state": a.state,
        "state_code": a.state_code,
        "desired_course": a.desired_course, "class_frequency": a.class_frequency,
        "nearest_vama_center": a.nearest_vama_center, "preferred_mode_of_contact": a.preferred_mode_of_contact,
        "blood_group": a.blood_group, "allergies": a.allergies, "referrer": a.referrer, "notes": a.notes,
        "status": a.status, "rejection_reason": a.rejection_reason, "student_id": a.student_id,
        "reviewed_by": a.reviewed_by, "reviewed_at": a.reviewed_at, "created_at": a.created_at,
    }


@app.post("/public/student-applications")
async def submit_student_application(request: Request, db: Session = Depends(get_db)):
    """Public enrollment form — directly creates a Student record with no approval step."""
    body = await request.json()
    first_name = (body.get("first_name") or "").strip()
    last_name = (body.get("last_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    primary_phone_number = (body.get("primary_phone_number") or "").strip()
    if not first_name or not last_name or not email or not primary_phone_number:
        raise HTTPException(status_code=400, detail="First name, last name, email, and phone number are required")

    if email_exists(db, email):
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    # Resolve center
    center_id = None
    nearest_center = (body.get("nearest_vama_center") or "").strip()
    if nearest_center:
        center = db.query(Center).filter(Center.name.ilike(nearest_center)).first()
        if center:
            center_id = center.id

    guardian_email = (body.get("guardian_email") or "").strip().lower() or None

    # Create Student with only the columns that exist on the Student table
    student = Student(
        first_name=first_name,
        last_name=last_name,
        email=email,
        guardian_email=guardian_email,
        primary_phone_number=primary_phone_number,
        date_of_birth=body.get("date_of_birth") or None,
        gender=body.get("gender") or None,
        address=body.get("address") or None,
        desired_course=body.get("desired_course") or None,
        nearest_vama_center=nearest_center or None,
        preferred_mode_of_contact=body.get("preferred_mode_of_contact") or None,
        center_id=center_id,
    )
    db.add(student)
    db.flush()

    # Application log entry stores all extra fields and links to the student
    application = StudentApplication(
        first_name=first_name,
        last_name=last_name,
        email=email,
        guardian_email=guardian_email,
        primary_phone_number=primary_phone_number,
        emergency_contact=body.get("emergency_contact") or None,
        date_of_birth=body.get("date_of_birth") or None,
        gender=body.get("gender") or None,
        parent_name=body.get("parent_name") or None,
        address=body.get("address") or None,
        city=body.get("city") or None,
        state=body.get("state") or None,
        desired_course=body.get("desired_course") or None,
        class_frequency=body.get("class_frequency") or None,
        nearest_vama_center=nearest_center or None,
        preferred_mode_of_contact=body.get("preferred_mode_of_contact") or None,
        blood_group=body.get("blood_group") or None,
        allergies=body.get("allergies") or None,
        referrer=body.get("referrer") or None,
        notes=body.get("notes") or None,
        center_id=center_id,
        status="approved",
        student_id=student.id,
    )
    db.add(application)
    provision_account(db, "student", student, request=request)
    db.commit()
    db.refresh(student)
    return {"id": student.id, "message": "Enrollment successful"}


@app.get("/admin/student-applications")
def list_student_applications(status: Optional[str] = None, center_id: Optional[int] = None, page: Optional[int] = None, limit: int = 50,
                              db: Session = Depends(get_db),
                              current = Depends(require_roles("super_admin", "center_admin"))):
    """List student applications. Phase 6: Paginated if page param provided, else returns array."""
    q = db.query(StudentApplication)
    if status:
        q = q.filter(StudentApplication.status == status)
    # Phase 2A: Center admin only sees their center's applications
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(StudentApplication.center_id == current["obj"].center_id)
    elif center_id is not None:
        q = q.filter(StudentApplication.center_id == center_id)

    # If no page param, return array (backward compatibility)
    if page is None:
        apps = q.order_by(StudentApplication.created_at.desc()).all()
        return [_application_dict(a) for a in apps]

    # If page param provided, return paginated response
    total = q.count()
    apps = q.order_by(StudentApplication.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [_application_dict(a) for a in apps],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


@app.get("/admin/student-applications/{application_id}")
def get_student_application(application_id: int, db: Session = Depends(get_db),
                           current = Depends(require_roles("super_admin", "center_admin"))):
    a = db.query(StudentApplication).filter(StudentApplication.id == application_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    # Phase 2A: Center admin can only see their center's applications
    if current.get("obj").access_role == "center_admin" and a.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return _application_dict(a)


@app.post("/admin/student-applications/{application_id}/approve")
async def approve_student_application(application_id: int, request: Request, db: Session = Depends(get_db),
                                      current = Depends(require_roles("super_admin", "center_admin"))):
    """Approve an application: creates the real Student record and provisions
    a pending-activation account for them, same as adding a student directly."""
    a = db.query(StudentApplication).filter(StudentApplication.id == application_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    if a.status != "pending":
        raise HTTPException(status_code=400, detail=f"Application is already {a.status}")
    if email_exists(db, a.email):
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    student = Student(
        first_name=a.first_name,
        last_name=a.last_name,
        email=a.email,
        guardian_email=a.guardian_email,
        primary_phone_number=a.primary_phone_number,
        date_of_birth=a.date_of_birth,
        gender=a.gender,
        address=a.address,
        desired_course=a.desired_course,
        nearest_vama_center=a.nearest_vama_center,
        preferred_mode_of_contact=a.preferred_mode_of_contact,
        current_grade=body.get("current_grade", "Debut"),
        syllabus_type=body.get("syllabus_type", "Trinity"),
        instrument=body.get("instrument") or a.desired_course,
        teacher_id=body.get("teacher_id"),
        center_id=a.center_id,  # Phase 2A: Copy center from application
    )
    db.add(student)
    provision_account(db, "student", student, request=request)

    a.status = "approved"
    a.student_id = student.id
    a.reviewed_by = body.get("reviewed_by")
    a.reviewed_at = func.now()
    db.commit()
    db.refresh(student)
    return {"id": student.id, "first_name": student.first_name, "last_name": student.last_name}


@app.post("/admin/student-applications/{application_id}/reject")
async def reject_student_application(application_id: int, request: Request, db: Session = Depends(get_db),
                                     current = Depends(require_roles("super_admin", "center_admin"))):
    a = db.query(StudentApplication).filter(StudentApplication.id == application_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    # Phase 2A: Center admin can only reject their center's applications
    if current.get("obj").access_role == "center_admin" and a.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if a.status != "pending":
        raise HTTPException(status_code=400, detail=f"Application is already {a.status}")
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    a.status = "rejected"
    a.rejection_reason = body.get("reason")
    a.reviewed_by = body.get("reviewed_by")
    a.reviewed_at = func.now()
    db.commit()
    return {"message": "Application rejected"}


@app.delete("/admin/student-applications/{application_id}")
def delete_student_application(application_id: int, db: Session = Depends(get_db),
                               current = Depends(require_roles("super_admin", "center_admin"))):
    a = db.query(StudentApplication).filter(StudentApplication.id == application_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    # Phase 2A: Center admin can only delete their center's applications
    if current.get("obj").access_role == "center_admin" and a.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Access denied")
    db.delete(a)
    db.commit()
    return {"message": "Application deleted"}


def sessions_used_for_package(db: Session, sp: "StudentPackage") -> int:
    """Live count of sessions consumed by a package.

    Computed from attendance instead of a stored counter so it can never
    drift: counts 'present' attendance records for this student whose
    session date falls within the package's validity window. Session dates
    are stored as 'YYYY-MM-DD' strings, which compare correctly as text.
    """
    return _consumption_count(db, sp, makeup=False)


def makeup_used_for_package(db: Session, sp: "StudentPackage") -> int:
    """Live count of make-up sessions consumed (present on makeup occurrences)."""
    return _consumption_count(db, sp, makeup=True)


def _consumption_count(db: Session, sp, makeup: bool) -> int:
    """Present attendances joined to OCCURRENCES (the v2 source of truth, not the
    legacy class_sessions) within the package window, split by regular vs makeup."""
    if not sp:
        return 0
    q = (
        db.query(Attendance)
        .join(ClassOccurrence, Attendance.session_id == ClassOccurrence.id)
        .filter(
            Attendance.student_id == sp.student_id,
            Attendance.status == "present",
            ClassOccurrence.is_makeup == (True if makeup else False),
            ClassOccurrence.date >= sp.start_date,
        )
    )
    if sp.end_date:
        q = q.filter(ClassOccurrence.date <= sp.end_date)
    return q.count()


def resolve_package_state(db: Session, sp: "StudentPackage") -> Optional[dict]:
    """Single source of truth for a student package's live state.

    Derives used/remaining counts and the effective status from attendance
    and dates rather than trusting the stored `status`. Returns None if `sp`
    is falsy. `effective_status` is one of:
        active | expired | exhausted | cancelled | paused
    """
    from datetime import date as _date
    if not sp:
        return None
    pkg = db.query(Package).filter(Package.id == sp.package_id).first()
    total = (pkg.total_sessions if pkg and pkg.total_sessions else 0)
    used = sessions_used_for_package(db, sp)
    remaining = total - used
    makeup_allowed = (pkg.makeup_sessions if pkg else 0) or 0
    makeup_used = makeup_used_for_package(db, sp)
    makeup_remaining = max(0, makeup_allowed - makeup_used)
    today = str(_date.today())
    is_expired = bool(sp.end_date) and today > sp.end_date
    is_exhausted = total > 0 and remaining <= 0

    # Stored terminal states win; otherwise derive from dates/counts.
    if sp.status in ("cancelled", "paused"):
        effective = sp.status
    elif is_expired:
        effective = "expired"
    elif is_exhausted:
        effective = "exhausted"
    else:
        effective = "active"

    return {
        "student_package_id": sp.id,
        "package_id": sp.package_id,
        "package_name": pkg.name if pkg else None,
        "sessions_total": total,
        "sessions_used": used,
        "sessions_remaining": remaining,
        "makeup_allowed": makeup_allowed,
        "makeup_used": makeup_used,
        "makeup_remaining": makeup_remaining,
        "start_date": sp.start_date,
        "end_date": sp.end_date,
        "is_expired": is_expired,
        "is_exhausted": is_exhausted,
        "effective_status": effective,
        "stored_status": sp.status,
    }


def get_active_student_package(db: Session, student_id: int, persist: bool = True):
    """Return the active StudentPackage for a student, or None.

    Lazily reconciles stored status. When the current package is expired OR
    exhausted, auto-activates the next queued package (if any) so sessions
    only decrement from the new package after the current one is fully used.
    """
    from datetime import date as _date
    sp = (
        db.query(StudentPackage)
        .filter(StudentPackage.student_id == student_id, StudentPackage.status == "active")
        .order_by(StudentPackage.created_at.desc())
        .first()
    )
    if not sp:
        # Try to promote a queued package
        queued = (
            db.query(StudentPackage)
            .filter(StudentPackage.student_id == student_id, StudentPackage.status == "queued")
            .order_by(StudentPackage.created_at.asc())
            .first()
        )
        if queued and persist:
            queued.status = "active"
            queued.start_date = str(_date.today())
            db.commit()
            return queued
        return queued if queued else None

    state = resolve_package_state(db, sp)
    if state and (state["is_expired"] or state["is_exhausted"]):
        if persist:
            sp.status = "expired" if state["is_expired"] else "exhausted"
            # Promote queued package
            queued = (
                db.query(StudentPackage)
                .filter(StudentPackage.student_id == student_id, StudentPackage.status == "queued")
                .order_by(StudentPackage.created_at.asc())
                .first()
            )
            if queued:
                queued.status = "active"
                queued.start_date = str(_date.today())
                db.commit()
                return queued
            db.commit()
        return None
    return sp


def assert_can_book(db: Session, student_id: int, session, count: int = 1, is_makeup: bool = False):
    """Validation engine: raise HTTP 400 unless the student can consume the
    occurrence. Enforces active package, not-expired, within validity window,
    and — depending on type — regular session quota OR makeup quota. Makeup is
    a separate quota and remains usable even when regular sessions are exhausted."""
    sp = get_active_student_package(db, student_id)
    if not sp:
        raise HTTPException(status_code=400,
            detail="No active package. Assign an active package before booking or marking attendance.")
    state = resolve_package_state(db, sp)
    if state["stored_status"] in ("cancelled", "paused"):
        raise HTTPException(status_code=400, detail=f"Package '{state['package_name']}' is {state['stored_status']}.")
    if state["is_expired"]:
        raise HTTPException(status_code=400,
            detail=f"Package '{state['package_name']}' expired on {sp.end_date}. Renew before marking attendance.")
    # Occurrence must fall inside the package validity window.
    if session is not None and getattr(session, "date", None):
        if session.date < sp.start_date or (sp.end_date and session.date > sp.end_date):
            raise HTTPException(status_code=400,
                detail="This class is outside the active package's validity period.")
    if is_makeup:
        if state["makeup_allowed"] <= 0:
            raise HTTPException(status_code=400, detail="This package does not include makeup sessions.")
        if state["makeup_remaining"] < count:
            raise HTTPException(status_code=400,
                detail=f"Makeup limit reached: {state['makeup_used']} of {state['makeup_allowed']} makeup sessions already used.")
    else:
        if state["sessions_total"] > 0 and state["sessions_remaining"] < count:
            raise HTTPException(status_code=400,
                detail=f"Sessions exhausted: {state['sessions_remaining']} of {state['sessions_total']} remaining. Renew or add sessions.")
    return sp


def student_warnings(db: Session, student_id: int) -> list:
    """Non-blocking advisories for the admin UI: expired/exhausted/low package,
    makeup limit reached, and overdue invoices."""
    from datetime import date as _date
    out = []
    sp = get_active_student_package(db, student_id, persist=False)
    if not sp:
        out.append({"level": "warn", "message": "No active package"})
    else:
        st = resolve_package_state(db, sp)
        if st["is_expired"]:
            out.append({"level": "error", "message": f"Package expired on {sp.end_date}"})
        elif st["sessions_total"] > 0 and st["sessions_remaining"] <= 0:
            out.append({"level": "error", "message": "All package sessions used"})
        elif st["sessions_total"] > 0 and st["sessions_remaining"] <= 2:
            out.append({"level": "warn", "message": f"Only {st['sessions_remaining']} session(s) left"})
        if st["makeup_allowed"] > 0 and st["makeup_remaining"] <= 0:
            out.append({"level": "warn", "message": "Makeup limit reached"})
    today = str(_date.today())
    overdue = db.query(Invoice).filter(
        Invoice.student_id == student_id,
        Invoice.status.in_(["pending", "partial", "overdue"]),
        Invoice.due_date < today,
    ).all()
    for inv in overdue:
        out.append({"level": "error", "message": f"Invoice {inv.invoice_number} overdue (due {inv.due_date})"})
    return out


@app.get("/scheduling/students/{student_id}/warnings")
def get_student_warnings(student_id: int, db: Session = Depends(get_db)):
    return student_warnings(db, student_id)


@app.get("/admin/student/{student_id}/complete-profile")
def get_student_complete_profile(student_id: int, db: Session = Depends(get_db)):
    """Aggregate full student profile: info, enrollments, attendance, payments, progress."""
    from datetime import date as _date
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    teacher = db.query(Staff).filter(Staff.id == student.teacher_id).first() if student.teacher_id else None

    # ── Enrollments + attendance ────────────────────────────
    enrollments = db.query(StudentEnrollment).filter(StudentEnrollment.student_id == student_id).all()
    enrollment_data = []
    grand_total_classes = 0
    grand_total_attended = 0

    for enr in enrollments:
        batch = db.query(Batch).filter(Batch.id == enr.batch_id).first()
        if not batch:
            continue
        batch_teacher = db.query(Staff).filter(Staff.id == batch.teacher_id).first() if batch.teacher_id else None
        sessions = db.query(ClassSession).filter(ClassSession.batch_id == enr.batch_id).all()
        session_ids = [s.id for s in sessions]
        attended = db.query(Attendance).filter(
            Attendance.session_id.in_(session_ids),
            Attendance.student_id == student_id,
            Attendance.status == "present"
        ).count() if session_ids else 0
        total = len(sessions)
        grand_total_classes += total
        grand_total_attended += attended
        enrollment_data.append({
            "id": enr.id,
            "subject": batch.subject or batch.name,
            "batch_name": batch.name,
            "teacher": batch_teacher.name if batch_teacher else "—",
            "start_date": enr.enrolled_at.isoformat() if enr.enrolled_at else None,
            "status": "active",
            "total_classes": total,
            "attended": attended,
            "missed": total - attended,
            "attendance_rate": round((attended / total) * 100) if total else 0,
        })

    # ── Upcoming sessions ───────────────────────────────────
    today_str = _date.today().isoformat()
    upcoming = []
    for enr in enrollments:
        batch = db.query(Batch).filter(Batch.id == enr.batch_id).first()
        if not batch:
            continue
        batch_teacher = db.query(Staff).filter(Staff.id == batch.teacher_id).first() if batch.teacher_id else None
        sessions = db.query(ClassSession).filter(
            ClassSession.batch_id == enr.batch_id,
            ClassSession.date >= today_str
        ).order_by(ClassSession.date).limit(3).all()
        for s in sessions:
            upcoming.append({
                "id": s.id,
                "subject": batch.subject or batch.name,
                "date": s.date,
                "time": f"{s.start_time} - {s.end_time}",
                "teacher": batch_teacher.name if batch_teacher else "—",
            })
    upcoming = upcoming[:5]

    # ── Payment history ─────────────────────────────────────
    invoices = db.query(Invoice).filter(Invoice.student_id == student_id).order_by(Invoice.issue_date.desc()).all()
    total_fees = sum(i.total_amount for i in invoices)
    fees_paid  = sum(i.paid_amount or 0 for i in invoices)
    payment_history = [{
        "id": inv.id,
        "invoice_number": inv.invoice_number,
        "date": inv.issue_date,
        "paid_date": inv.paid_date,
        "amount": inv.total_amount,
        "paid_amount": inv.paid_amount or 0,
        "type": inv.payment_type or "Package",
        "status": inv.status,
    } for inv in invoices]

    # ── Active package (live state) ─────────────────────────
    active_sp = (
        db.query(StudentPackage)
        .filter(StudentPackage.student_id == student_id, StudentPackage.status == "active")
        .order_by(StudentPackage.created_at.desc())
        .first()
    )
    package_info = None
    if active_sp:
        state = resolve_package_state(db, active_sp)
        package_info = {
            "id": state["package_id"],
            "name": state["package_name"],
            "sessions_total": state["sessions_total"],
            "sessions_used": state["sessions_used"],
            "sessions_remaining": state["sessions_remaining"],
            "start_date": state["start_date"],
            "end_date": state["end_date"],
            "is_expired": state["is_expired"],
            "is_exhausted": state["is_exhausted"],
            "status": state["effective_status"],
        }

    # ── Progress summary ────────────────────────────────────
    progress_records = db.query(StudentProgress).filter(StudentProgress.student_id == student_id).all()
    done_count   = sum(1 for p in progress_records if p.status == "done")
    inprog_count = sum(1 for p in progress_records if p.status == "in-progress")

    att_pct = round((grand_total_attended / grand_total_classes) * 100) if grand_total_classes else 0
    extra = db.query(StudentApplication).filter(StudentApplication.student_id == student_id).first()

    return {
        "id": student.id,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "email": student.email,
        "guardian_email": student.guardian_email or "",
        "primary_phone_number": student.primary_phone_number or "",
        "address": student.address or "",
        "date_of_birth": student.date_of_birth,
        "gender": student.gender or "",
        "nearest_vama_center": student.nearest_vama_center or "",
        "preferred_mode_of_contact": student.preferred_mode_of_contact or "",
        "enrollment_date": student.created_at.isoformat() if student.created_at else None,
        "status": "active",
        "current_grade": student.current_grade or "Debut",
        "desired_course": student.desired_course or "",
        "instrument": student.instrument or student.desired_course or "",
        "syllabus_type": student.syllabus_type or "Trinity",
        "teacher": {"id": teacher.id, "name": teacher.name} if teacher else None,
        **_extra_fields_dict(extra),
        "financial": {
            "total_fees": total_fees,
            "fees_paid": fees_paid,
            "outstanding": total_fees - fees_paid,
            "payment_history": payment_history,
        },
        "enrollments": enrollment_data,
        "upcoming_classes": upcoming,
        "active_package": package_info,
        "performance": {
            "attendance_percentage": att_pct,
            "total_classes": grand_total_classes,
            "total_attended": grand_total_attended,
            "progress_items_total": len(progress_records),
            "progress_items_done": done_count,
            "progress_items_in_progress": inprog_count,
        },
    }


# ==================== Progress / Syllabus ====================

def _build_progress_response(student: Student, db: Session):
    """Build the full progress response for a student."""
    # Find the syllabus matching student's grade + syllabus_type
    syllabus = db.query(Syllabus).filter(
        Syllabus.grade_name == student.current_grade,
        Syllabus.syllabus_type == student.syllabus_type
    ).first()

    # Fall back to any syllabus for their instrument/course
    if not syllabus:
        syllabus = db.query(Syllabus).filter(
            Syllabus.subject == (student.instrument or student.desired_course)
        ).first()

    student_data = {
        "id": student.id,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "name": f"{student.first_name} {student.last_name}",
        "email": student.email,
        "instrument": student.instrument or student.desired_course or "Music",
        "grade": student.current_grade or "Debut",
        "current_grade": student.current_grade or "Debut",
        "desired_course": student.desired_course or "",
        "primary_phone_number": student.primary_phone_number or "",
        "nearest_vama_center": student.nearest_vama_center or "",
        "syllabus_type": student.syllabus_type or "Trinity",
        "is_exam_student": student.is_exam_student or False,
        "exam_date": student.exam_date,
    }

    if not syllabus:
        return {"student": student_data, "syllabus": None}

    # Build progress lookup: content_id → progress record
    progress_map = {}
    for p in db.query(StudentProgress).filter(
        StudentProgress.student_id == student.id
    ).all():
        progress_map[p.content_id] = p

    modules_data = []
    for module in syllabus.modules:
        contents_data = []
        for content in module.contents:
            prog = progress_map.get(content.id)
            contents_data.append({
                "id": content.id,
                "name": content.name,
                "content_type": content.content_type,
                "weight": content.weight,
                "progress": {
                    "status": prog.status if prog else "not-yet",
                    "notes": prog.notes if prog else "",
                    "completed_at": prog.completed_at.isoformat() if (prog and prog.completed_at) else None,
                } if prog else {
                    "status": "not-yet",
                    "notes": "",
                    "completed_at": None,
                }
            })
        modules_data.append({
            "id": module.id,
            "order": module.order,
            "name": module.name,
            "weight": module.weight,
            "contents": contents_data,
        })

    return {
        "student": student_data,
        "syllabus": {
            "id": syllabus.id,
            "name": syllabus.name,
            "modules": modules_data,
        }
    }


@app.get("/students/{student_id}/progress")
def get_student_progress(student_id: int, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return _build_progress_response(student, db)


@app.post("/students/{student_id}/progress/{content_id}")
async def update_student_progress(
    student_id: int,
    content_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    body = await request.json()

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    content = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    prog = db.query(StudentProgress).filter(
        StudentProgress.student_id == student_id,
        StudentProgress.content_id == content_id
    ).first()

    if not prog:
        prog = StudentProgress(
            student_id=student_id,
            content_id=content_id,
        )
        db.add(prog)

    if "status" in body:
        prog.status = body["status"]
    if "notes" in body:
        prog.notes = body["notes"]
    if "completed_at" in body:
        if body["completed_at"]:
            from datetime import datetime
            try:
                prog.completed_at = datetime.fromisoformat(body["completed_at"].replace("Z", "+00:00"))
            except Exception:
                prog.completed_at = None
        else:
            prog.completed_at = None

    db.commit()
    db.refresh(prog)
    return {"status": prog.status, "notes": prog.notes}


@app.post("/teacher/students/{student_id}/modules/{module_id}/contents")
async def teacher_add_content_item(
    student_id: int,
    module_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: dict = Depends(require_roles("staff", "center_admin", "super_admin"))
):
    body = await request.json()
    name = (body.get("name") or "").strip()
    content_type = body.get("content_type") or "piece"

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    module = db.query(SyllabusModule).filter(SyllabusModule.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    new_content = SyllabusContent(
        module_id=module_id,
        name=name,
        content_type=content_type,
        weight=1.0,
    )
    db.add(new_content)
    db.flush()

    # Rebalance all contents in this module to equal proportional weight
    all_contents = db.query(SyllabusContent).filter(SyllabusContent.module_id == module_id).all()
    if all_contents:
        equal_weight = round(100.0 / len(all_contents), 4)
        for c in all_contents:
            c.weight = equal_weight

    db.commit()

    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return _build_progress_response(student, db)


# ==================== Sessions ====================

_SUBJECT_COLORS = {
    "Piano":    "#463a7a",
    "Guitar":   "#059669",
    "Violin":   "#2563eb",
    "Vocals":   "#d97706",
    "Drums":    "#dc2626",
    "Keyboard": "#7c3aed",
    "Flute":    "#0891b2",
    "Tabla":    "#78716c",
}


def _session_to_dict(s: ClassSession, db: Session):
    enrolled = db.query(StudentEnrollment).filter(
        StudentEnrollment.batch_id == s.batch_id
    ).all() if s.batch_id else []
    students_in_batch = []
    for e in enrolled:
        stu = db.query(Student).filter(Student.id == e.student_id).first()
        if stu:
            students_in_batch.append({
                "id": stu.id,
                "first_name": stu.first_name,
                "last_name": stu.last_name,
            })

    subject = (s.batch.subject if s.batch else "") or ""
    teacher_id = s.teacher_id or (s.batch.teacher_id if s.batch else None)
    teacher = db.query(Staff).filter(Staff.id == teacher_id).first() if teacher_id else None

    return {
        "id": s.id,
        "date": s.date,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "notes": s.notes,
        "teacher_id": teacher_id,
        "teacher_name": teacher.name if teacher else None,
        "enrollment_count": len(students_in_batch),
        "enrolled_students": students_in_batch,
        "batch": {
            "id": s.batch.id,
            "name": s.batch.name,
            "subject": subject,
            "teacher_id": s.batch.teacher_id,
            "color_tag": _SUBJECT_COLORS.get(subject, "#64748b"),
            "capacity": 10,
        } if s.batch else None,
        "attendances": [
            {"id": a.id, "student_id": a.student_id, "status": a.status}
            for a in s.attendances
        ],
    }


def _occ_session_shape(db, occ, stu_map=None, student_id=None):
    """Single-occurrence shape — kept for the one-off session-detail endpoint.
    For lists use _build_session_shapes_bulk."""
    t = occ.template
    teacher = db.query(Staff).filter(Staff.id == occ.teacher_id).first() if occ.teacher_id else None
    roster_ids = _occurrence_roster_ids(db, occ) if occ.template_id else set()
    students = []
    for sid in roster_ids:
        s = (stu_map.get(sid) if stu_map else None) or db.query(Student).filter(Student.id == sid).first()
        if s:
            students.append({"id": s.id, "first_name": s.first_name, "last_name": s.last_name})
    atts = db.query(Attendance).filter(Attendance.session_id == occ.id).all()
    subject = t.course if t else ""
    my_att = None
    if student_id:
        my_att_rec = next((a for a in atts if a.student_id == student_id), None)
        my_att = my_att_rec.status if my_att_rec else None
    return {
        "id": occ.id, "date": occ.date, "start_time": occ.start_time, "end_time": occ.end_time,
        "notes": occ.notes, "status": occ.status, "is_published": occ.is_published,
        "teacher_id": occ.teacher_id, "teacher_name": teacher.name if teacher else None,
        "enrollment_count": len(students), "enrolled_students": students,
        "batch": {"id": t.id if t else None, "name": t.name if t else None, "subject": subject,
                  "teacher_id": occ.teacher_id, "teacher": {"name": teacher.name} if teacher else None,
                  "color_tag": _template_color(occ.template_id, occ.start_time), "capacity": t.capacity if t else None},
        "attendances": [{"id": a.id, "student_id": a.student_id, "status": a.status} for a in atts],
        "my_attendance": my_att,
    }


def _build_session_shapes_bulk(db, occs, student_id=None):
    """Build session shapes for many occurrences using bulk queries (no N+1)."""
    from collections import defaultdict
    if not occs:
        return []

    occ_ids     = [o.id for o in occs]
    template_ids = list({o.template_id for o in occs if o.template_id})
    teacher_ids  = list({o.teacher_id  for o in occs if o.teacher_id})

    # ── 1. Templates ──────────────────────────────────────────────────────────
    tpl_map = {t.id: t for t in db.query(ClassTemplate).filter(
        ClassTemplate.id.in_(template_ids or [-1])).all()}

    # ── 2. Teachers ───────────────────────────────────────────────────────────
    teacher_map = {s.id: s for s in db.query(Staff).filter(
        Staff.id.in_(teacher_ids or [-1])).all()}

    # ── 3. Baseline enrollments for all templates (one query) ─────────────────
    baseline_rows = db.query(Enrollment).filter(
        Enrollment.template_id.in_(template_ids or [-1]),
        Enrollment.occurrence_id.is_(None),
        Enrollment.status == "active"
    ).all()
    baseline_by_tpl = defaultdict(set)
    for e in baseline_rows:
        baseline_by_tpl[e.template_id].add(e.student_id)

    # ── 4. Per-occurrence enrollment overrides (one query) ────────────────────
    override_rows = db.query(Enrollment).filter(
        Enrollment.occurrence_id.in_(occ_ids)).all()
    overrides_by_occ = defaultdict(list)
    for e in override_rows:
        overrides_by_occ[e.occurrence_id].append(e)

    # ── 5. Effective roster per occurrence, collect all student IDs ───────────
    roster_by_occ = {}
    all_student_ids = set()
    for occ in occs:
        base = set(baseline_by_tpl.get(occ.template_id, set())) if occ.template_id else set()
        inc, exc = set(), set()
        for e in overrides_by_occ.get(occ.id, []):
            (inc if e.kind == "include" else exc).add(e.student_id)
        roster = (base - exc) | inc
        roster_by_occ[occ.id] = roster
        all_student_ids |= roster

    # ── 6. Students (one query) ───────────────────────────────────────────────
    stu_map = {s.id: s for s in db.query(Student).filter(
        Student.id.in_(all_student_ids or [-1])).all()}

    # ── 7. Attendances (one query) ────────────────────────────────────────────
    att_rows = db.query(Attendance).filter(
        Attendance.session_id.in_(occ_ids)).all()
    att_by_occ = defaultdict(list)
    for a in att_rows:
        att_by_occ[a.session_id].append(a)

    # ── 8. Assemble ───────────────────────────────────────────────────────────
    results = []
    for occ in occs:
        t        = tpl_map.get(occ.template_id)
        teacher  = teacher_map.get(occ.teacher_id)
        roster   = roster_by_occ.get(occ.id, set())
        students = [{"id": s.id, "first_name": s.first_name, "last_name": s.last_name}
                    for sid in roster if (s := stu_map.get(sid))]
        atts     = att_by_occ.get(occ.id, [])
        subject  = t.course if t else ""
        my_att   = None
        if student_id:
            rec = next((a for a in atts if a.student_id == student_id), None)
            my_att = rec.status if rec else None
        results.append({
            "id": occ.id, "date": occ.date, "start_time": occ.start_time, "end_time": occ.end_time,
            "notes": occ.notes, "status": occ.status, "is_published": occ.is_published,
            "teacher_id": occ.teacher_id, "teacher_name": teacher.name if teacher else None,
            "enrollment_count": len(students), "enrolled_students": students,
            "batch": {"id": t.id if t else None, "name": t.name if t else None, "subject": subject,
                      "teacher_id": occ.teacher_id,
                      "teacher": {"name": teacher.name} if teacher else None,
                      "color_tag": _template_color(occ.template_id, occ.start_time),
                      "capacity": t.capacity if t else None},
            "attendances": [{"id": a.id, "student_id": a.student_id, "status": a.status} for a in atts],
            "my_attendance": my_att,
        })
    return results


@app.get("/teacher/{teacher_id}/sessions")
def get_teacher_sessions(teacher_id: int, start: Optional[str] = None,
                         end: Optional[str] = None, db: Session = Depends(get_db)):
    from datetime import date as _dd, timedelta
    # Default to current month if no range given (avoids loading entire history)
    if not start and not end:
        today = _dd.today()
        start = today.replace(day=1).isoformat()
        end   = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        end   = end.isoformat()
    q = db.query(ClassOccurrence).filter(ClassOccurrence.teacher_id == teacher_id)
    if start:
        q = q.filter(ClassOccurrence.date >= start)
    if end:
        q = q.filter(ClassOccurrence.date <= end)
    occs = q.order_by(ClassOccurrence.date, ClassOccurrence.start_time).all()
    return _build_session_shapes_bulk(db, occs)


@app.get("/student/{student_id}/sessions")
def get_student_sessions(student_id: int, start: Optional[str] = None,
                         end: Optional[str] = None, db: Session = Depends(get_db)):
    from datetime import date as _dd, timedelta
    # Default to current month
    if not start and not end:
        today = _dd.today()
        start = today.replace(day=1).isoformat()
        end   = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        end   = end.isoformat()

    # ── 1. Templates the student is enrolled on (baseline) ───────────────────
    base_tpls = [e.template_id for e in db.query(Enrollment).filter(
        Enrollment.student_id == student_id, Enrollment.occurrence_id.is_(None),
        Enrollment.status == "active").all()]

    # ── 2. Per-occurrence overrides for this student ──────────────────────────
    overrides = db.query(Enrollment).filter(
        Enrollment.student_id == student_id, Enrollment.occurrence_id.isnot(None)).all()
    excl_ids = {e.occurrence_id for e in overrides if e.kind == "exclude"}
    incl_ids = {e.occurrence_id for e in overrides if e.kind == "include" and e.status == "active"}

    def _apply_range(q):
        if start: q = q.filter(ClassOccurrence.date >= start)
        if end:   q = q.filter(ClassOccurrence.date <= end)
        return q

    # ── 3. Base occurrences from enrolled templates ────────────────────────────
    base_occs  = _apply_range(db.query(ClassOccurrence).filter(
        ClassOccurrence.template_id.in_(base_tpls or [-1]))).all()
    final_ids  = ({o.id for o in base_occs} - excl_ids) | incl_ids

    # ── 4. Final occurrence list (includes override-includes from other templates)
    occs = _apply_range(db.query(ClassOccurrence).filter(
        ClassOccurrence.id.in_(final_ids or [-1]))) \
        .order_by(ClassOccurrence.date, ClassOccurrence.start_time).all()

    return _build_session_shapes_bulk(db, occs, student_id=student_id)


@app.get("/student/{student_id}/attendance")
def get_student_attendance(student_id: int, db: Session = Depends(get_db)):
    """Attendance records (marked by teachers on v2 occurrences) for the student
    portal Attendance page."""
    atts = db.query(Attendance).filter(Attendance.student_id == student_id).all()
    occ_ids = [a.session_id for a in atts]
    occ_map = {o.id: o for o in db.query(ClassOccurrence).filter(ClassOccurrence.id.in_(occ_ids or [-1])).all()}
    tpl_map = {t.id: t for t in db.query(ClassTemplate).filter(
        ClassTemplate.id.in_({o.template_id for o in occ_map.values() if o.template_id} or [-1])).all()}
    out = []
    for a in atts:
        o = occ_map.get(a.session_id)
        t = tpl_map.get(o.template_id) if o else None
        out.append({
            "id": a.id, "session_id": a.session_id, "status": a.status, "notes": a.notes,
            "created_at": a.marked_at.isoformat() if a.marked_at else None,
            "session": ({
                "date": o.date, "start_time": o.start_time, "end_time": o.end_time,
                "batch": {"subject": t.course if t else None, "name": t.name if t else None},
            } if o else None),
        })
    # Newest first (by occurrence date when available, else id).
    out.sort(key=lambda r: (r["session"]["date"] if r.get("session") else "", r["id"]), reverse=True)
    return out


# ── Student booking / reschedule (v2 occurrences; CANCELLED slots excluded) ──

def _bookable_occurrences_query(db, *, status_scheduled_only=True):
    q = db.query(ClassOccurrence)
    if status_scheduled_only:
        # Never offer a cancelled (or non-scheduled) slot for booking.
        q = q.filter(ClassOccurrence.status == "scheduled")
    return q


def _slot_counts(db, occ):
    t = occ.template
    cap = (t.capacity if t else 0) or 0
    cnt = len(_occurrence_roster_ids(db, occ)) if occ.template_id else 0
    return cap, cnt


@app.get("/student/{student_id}/instructor-slots")
def student_instructor_slots(student_id: int, session_id: int, slot_date: str, db: Session = Depends(get_db)):
    """Bookable slots on a date with the same instructor as `session_id`.
    Cancelled occurrences are never returned."""
    orig = db.query(ClassOccurrence).filter(ClassOccurrence.id == session_id).first()
    q = _bookable_occurrences_query(db).filter(ClassOccurrence.date == slot_date)
    if orig and orig.teacher_id:
        q = q.filter(ClassOccurrence.teacher_id == orig.teacher_id)
    out = []
    for o in q.all():
        if o.id == session_id:
            continue
        t = o.template
        cap, cnt = _slot_counts(db, o)
        out.append({
            "id": o.id, "date": o.date, "subject": t.course if t else "", "batch_name": t.name if t else "",
            "start_time": o.start_time, "end_time": o.end_time,
            "capacity": cap, "enrollment_count": cnt,
            "available_slots": max(0, cap - cnt), "is_fully_booked": cap > 0 and cnt >= cap,
        })
    return sorted(out, key=lambda s: s["start_time"])


@app.get("/student/{student_id}/available-slots")
def student_available_slots(student_id: int, start: Optional[str] = None, end: Optional[str] = None,
                            subject: Optional[str] = None, db: Session = Depends(get_db)):
    """Bookable slots in a date range, optionally by subject. Cancelled excluded."""
    staff_map = {s.id: s.name for s in db.query(Staff).all()}
    q = _bookable_occurrences_query(db)
    if start:
        q = q.filter(ClassOccurrence.date >= start)
    if end:
        q = q.filter(ClassOccurrence.date <= end)
    out = []
    for o in q.order_by(ClassOccurrence.date, ClassOccurrence.start_time).all():
        t = o.template
        if subject and t and (t.course or "") != subject:
            continue
        cap, cnt = _slot_counts(db, o)
        out.append({
            "id": o.id, "date": o.date, "start_time": o.start_time, "end_time": o.end_time,
            "subject": t.course if t else "", "teacher_name": staff_map.get(o.teacher_id),
            "enrolled": cnt, "capacity": cap, "batch_id": o.template_id,
        })
    return out


def _student_reschedule(db, student_id, old_id, new_id):
    old = db.query(ClassOccurrence).filter(ClassOccurrence.id == old_id).first()
    new = db.query(ClassOccurrence).filter(ClassOccurrence.id == new_id).first()
    if not new:
        raise HTTPException(status_code=404, detail="Target slot not found")
    if new.status == "cancelled":
        raise HTTPException(status_code=400, detail="That slot is cancelled and cannot be booked")
    base_new = _baseline_ids(db, new.template_id)
    roster_new = _occurrence_roster_ids(db, new, base_new)
    cap = new.template.capacity if new.template else None
    if cap and student_id not in roster_new and len(roster_new) >= cap:
        raise HTTPException(status_code=400, detail="That slot is fully booked")
    if old:
        _set_membership(db, old, student_id, present=False, base_ids=_baseline_ids(db, old.template_id))
    _set_membership(db, new, student_id, present=True, base_ids=base_new)
    db.commit()
    return {"message": "Rescheduled"}


@app.get("/student/{student_id}/package-status")
def student_package_status(student_id: int, db: Session = Depends(get_db)):
    """Single source of truth for student portal gating.
    Returns can_book, sessions remaining, makeup remaining, expiry, and block reason.
    Admin/teacher routes do NOT call this — only the student portal uses it."""
    from datetime import date as _date
    sp = get_active_student_package(db, student_id, persist=True)
    if not sp:
        # Check if there was a recently expired/exhausted package for a better message
        last = (
            db.query(StudentPackage)
            .filter(StudentPackage.student_id == student_id)
            .order_by(StudentPackage.created_at.desc())
            .first()
        )
        if last:
            state = resolve_package_state(db, last)
            reason = "expired" if state["is_expired"] else "exhausted" if state["is_exhausted"] else "no_package"
        else:
            reason = "no_package"
        return {
            "can_book": False,
            "block_reason": reason,
            "sessions_total": 0,
            "sessions_used": 0,
            "sessions_remaining": 0,
            "makeup_total": 0,
            "makeup_used": 0,
            "makeup_remaining": 0,
            "end_date": None,
            "days_left": None,
            "package_name": None,
            "status": reason,
        }

    state = resolve_package_state(db, sp)
    can_book = state["effective_status"] == "active"
    block_reason = None if can_book else state["effective_status"]

    days_left = None
    if sp.end_date:
        delta = (_date.fromisoformat(sp.end_date) - _date.today()).days
        days_left = max(0, delta)

    return {
        "can_book": can_book,
        "block_reason": block_reason,
        "package_name": state["package_name"],
        "sessions_total": state["sessions_total"],
        "sessions_used": state["sessions_used"],
        "sessions_remaining": state["sessions_remaining"],
        "makeup_total": state["makeup_allowed"],
        "makeup_used": state["makeup_used"],
        "makeup_remaining": state["makeup_remaining"],
        "end_date": sp.end_date,
        "days_left": days_left,
        "status": state["effective_status"],
    }


@app.post("/student/{student_id}/do-reschedule")
def student_do_reschedule(student_id: int, original_session_id: int, new_session_id: int,
                          reason: Optional[str] = None, db: Session = Depends(get_db)):
    # Gate: student must have an active package with makeup sessions remaining
    new_sess = db.query(ClassOccurrence).filter(ClassOccurrence.id == new_session_id).first()
    assert_can_book(db, student_id, new_sess, count=1, is_makeup=True)
    return _student_reschedule(db, student_id, original_session_id, new_session_id)


@app.post("/student/{student_id}/reschedule")
async def student_reschedule(student_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    old_id = body.get("old_session_id")
    new_id = body.get("new_session_id")
    new_sess = db.query(ClassOccurrence).filter(ClassOccurrence.id == new_id).first() if new_id else None
    assert_can_book(db, student_id, new_sess, count=1, is_makeup=True)
    return _student_reschedule(db, student_id, old_id, new_id)


@app.get("/calendar/filtered")
def calendar_filtered(
    start: Optional[str] = None,
    end: Optional[str] = None,
    enrollment_filter: Optional[int] = None,
    center_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Return all sessions in a date range, optionally filtered to a student's batches or center.
    Returns the full session shape expected by the Scheduler / TeacherCalendar:
    batch.subject, batch.teacher_id, batch.color_tag, enrolled_students, etc.
    """
    q = db.query(ClassSession)
    if start:
        q = q.filter(ClassSession.date >= start)
    if end:
        q = q.filter(ClassSession.date <= end)
    if enrollment_filter:
        enrolled_batch_ids = [
            e.batch_id for e in db.query(StudentEnrollment).filter(
                StudentEnrollment.student_id == enrollment_filter
            ).all()
        ]
        if not enrolled_batch_ids:
            return []
        q = q.filter(ClassSession.batch_id.in_(enrolled_batch_ids))
    if center_id:
        center_batch_ids = [b.id for b in db.query(Batch).filter(Batch.center_id == center_id).all()]
        q = q.filter(ClassSession.batch_id.in_(center_batch_ids)) if center_batch_ids else q.filter(False)

    sessions = q.order_by(ClassSession.date, ClassSession.start_time).all()

    # Pre-fetch to avoid N+1 queries
    students_map = {s.id: s for s in db.query(Student).all()}
    staff_map    = {s.id: s for s in db.query(Staff).all()}
    batch_ids    = list({s.batch_id for s in sessions if s.batch_id})
    enrollments_by_batch: dict = {}
    if batch_ids:
        for e in db.query(StudentEnrollment).filter(StudentEnrollment.batch_id.in_(batch_ids)).all():
            enrollments_by_batch.setdefault(e.batch_id, []).append(e.student_id)

    result = []
    for s in sessions:
        subject    = (s.batch.subject if s.batch else "") or ""
        teacher_id = s.teacher_id or (s.batch.teacher_id if s.batch else None)
        teacher    = staff_map.get(teacher_id)
        enrolled_students = [
            {"id": sid, "first_name": students_map[sid].first_name, "last_name": students_map[sid].last_name}
            for sid in enrollments_by_batch.get(s.batch_id, [])
            if sid in students_map
        ]
        result.append({
            "id":               s.id,
            "date":             s.date,
            "start_time":       s.start_time,
            "end_time":         s.end_time,
            "notes":            s.notes,
            "teacher_id":       teacher_id,
            "teacher_name":     teacher.name if teacher else None,
            "enrollment_count": len(enrolled_students),
            "enrolled_students": enrolled_students,
            "batch": {
                "id":         s.batch.id,
                "name":       s.batch.name,
                "subject":    subject,
                "teacher_id": s.batch.teacher_id,
                "color_tag":  _SUBJECT_COLORS.get(subject, "#64748b"),
                "capacity":   10,
            } if s.batch else None,
            "attendances": [
                {"id": a.id, "student_id": a.student_id, "status": a.status}
                for a in s.attendances
            ],
        })
    return result


# ==================== Teacher Students ====================

@app.get("/teacher/{teacher_id}/students")
def get_teacher_students(teacher_id: int, db: Session = Depends(get_db)):
    # Primary teacher OR any multi-instrument assignment to this teacher.
    ids = {s.id for s in db.query(Student).filter(Student.teacher_id == teacher_id).all()}
    ids |= {ti.student_id for ti in db.query(StudentInstructor).filter(StudentInstructor.teacher_id == teacher_id).all()}
    students = db.query(Student).filter(Student.id.in_(ids or [-1])).order_by(Student.first_name).all()
    return [
        {
            "id": s.id,
            "first_name": s.first_name,
            "last_name": s.last_name,
            "email": s.email,
            "primary_phone_number": s.primary_phone_number or "",
            "desired_course": s.desired_course or "",
            "instrument": s.instrument or s.desired_course or "",
            "current_grade": s.current_grade or "Debut",
            "syllabus_type": s.syllabus_type or "Trinity",
            "is_exam_student": s.is_exam_student or False,
            "exam_date": s.exam_date,
        }
        for s in students
    ]


# ==================== Materials ====================

@app.get("/teacher/{teacher_id}/materials")
def get_teacher_materials(teacher_id: int, db: Session = Depends(get_db)):
    materials = (
        db.query(Material)
        .filter(Material.uploaded_by == teacher_id)
        .order_by(Material.created_at.desc())
        .all()
    )
    return [
        {
            "id": m.id,
            "title": m.title,
            "file_type": m.file_type or "file",
            "file_url": m.url or "",
            "student_id": m.student_id,
            "created_at": m.created_at.isoformat() if m.created_at else "",
        }
        for m in materials
    ]


@app.post("/teacher/upload-material")
async def teacher_upload_material(
    teacher_id: int = Form(...),
    title: str = Form(...),
    student_ids: str = Form(default=""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    import uuid, os
    ext = os.path.splitext(file.filename or "")[1].lower()
    ext_to_type = {
        ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".webp": "image", ".mp3": "audio", ".wav": "audio",
        ".m4a": "audio", ".mp4": "video", ".mov": "video", ".mkv": "video",
    }
    file_type = ext_to_type.get(ext, "file")
    filename = f"{uuid.uuid4().hex}{ext}"
    save_dir = "static/materials"
    _osmod.makedirs(save_dir, exist_ok=True)
    contents = await file.read()
    with open(f"{save_dir}/{filename}", "wb") as f:
        f.write(contents)
    url = f"/static/materials/{filename}"

    ids = [int(x) for x in student_ids.split(",") if x.strip().isdigit()]
    if not ids:
        m = Material(title=title, file_type=file_type, url=url, uploaded_by=teacher_id, student_id=None)
        db.add(m)
    else:
        for sid in ids:
            m = Material(title=title, file_type=file_type, url=url, uploaded_by=teacher_id, student_id=sid)
            db.add(m)
    db.commit()
    return {"ok": True}


@app.get("/students/{student_id}/materials")
def get_student_materials(student_id: int, db: Session = Depends(get_db)):
    # Get materials assigned directly to student OR to any batch they're in
    enrollments = db.query(StudentEnrollment).filter(
        StudentEnrollment.student_id == student_id
    ).all()
    batch_ids = [e.batch_id for e in enrollments]

    from sqlalchemy import or_
    query = db.query(Material).filter(
        or_(
            Material.student_id == student_id,
            Material.batch_id.in_(batch_ids) if batch_ids else False
        )
    ).order_by(Material.created_at.desc())

    materials = query.all()
    return [
        {
            "id": m.id,
            "title": m.title,
            "file_type": m.file_type or "PDF",
            "url": m.url or "",
            "description": m.description or "",
            "created_at": m.created_at.isoformat() if m.created_at else "",
        }
        for m in materials
    ]


@app.post("/materials")
async def upload_material(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    material = Material(
        title=body.get("title", ""),
        file_type=body.get("file_type"),
        url=body.get("url"),
        description=body.get("description"),
        student_id=body.get("student_id"),
        batch_id=body.get("batch_id"),
        uploaded_by=body.get("uploaded_by"),
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return {"id": material.id, "title": material.title}


# ==================== Admin / Metadata ====================

@app.get("/admin/grades")
def get_grades(db: Session = Depends(get_db)):
    grades = db.query(Grade).order_by(Grade.display_order).all()
    return [{"id": g.id, "name": g.name, "level": g.display_order} for g in grades]


@app.get("/admin/subjects")
def get_subjects(db: Session = Depends(get_db)):
    subjects = db.query(Subject).filter(Subject.is_active == True).all()
    return [{"id": s.id, "name": s.name, "is_active": s.is_active} for s in subjects]


@app.get("/admin/exam-sessions")
def get_exam_sessions(db: Session = Depends(get_db)):
    sessions = db.query(ExamSession).all()
    return [
        {"id": e.id, "name": e.name, "exam_board": e.exam_board, "is_active": e.is_active}
        for e in sessions
    ]


@app.get("/admin/dashboard/stats")
def admin_dashboard_stats(
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin", "teacher")),
):
    """Aggregated counts for the curriculum dashboard, scoped to caller's center."""
    from models import Grade, Subject, ExamSession, Syllabus
    caller = current.get("obj")
    center_id = getattr(caller, "center_id", None) if getattr(caller, "access_role", None) in ("center_admin", "teacher") else None

    teacher_q = db.query(Staff)
    student_q = db.query(Student)
    assign_q = db.query(StudentInstructor)
    if center_id:
        teacher_q = teacher_q.filter(Staff.center_id == center_id)
        student_q = student_q.filter(Student.center_id == center_id)
        teacher_ids = {s.id for s in teacher_q.all()}
        assign_q = assign_q.filter(StudentInstructor.teacher_id.in_(teacher_ids or [-1]))

    return {
        "total_subjects": db.query(Subject).filter(Subject.is_active == True).count(),
        "total_grades": db.query(Grade).count(),
        "active_exams": db.query(ExamSession).filter(ExamSession.is_active == True).count(),
        "total_teachers": teacher_q.count(),
        "total_students": student_q.count(),
        "teacher_assignments": assign_q.count(),
        "total_syllabi": db.query(Syllabus).count(),
    }


# ==================== Syllabus Admin ====================

def _syllabus_full(s: Syllabus):
    return {
        "id": s.id, "name": s.name, "subject": s.subject,
        "grade_name": s.grade_name, "syllabus_type": s.syllabus_type,
        "modules": [
            {
                "id": m.id, "name": m.name, "order": m.order, "weight": m.weight,
                "contents": [
                    {"id": c.id, "name": c.name, "content_type": c.content_type, "weight": c.weight}
                    for c in m.contents
                ],
            }
            for m in s.modules
        ],
    }


@app.get("/admin/syllabi")
def list_syllabi(
    subject_id: Optional[int] = None,
    grade_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Syllabus)
    if subject_id:
        subj = db.query(Subject).filter(Subject.id == subject_id).first()
        if subj:
            q = q.filter(Syllabus.subject == subj.name)
    if grade_id:
        grade = db.query(Grade).filter(Grade.id == grade_id).first()
        if grade:
            q = q.filter(Syllabus.grade_name == grade.name)
    return [
        {"id": s.id, "name": s.name, "subject": s.subject,
         "grade_name": s.grade_name, "syllabus_type": s.syllabus_type}
        for s in q.all()
    ]


@app.get("/admin/syllabi/{syllabus_id}")
def get_syllabus(syllabus_id: int, db: Session = Depends(get_db)):
    s = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    return _syllabus_full(s)


@app.post("/admin/syllabi")
async def create_syllabus(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    # Accept both ID-based (from builder) and name-based references
    subject_name = body.get("subject")
    grade_name = body.get("grade_name")
    if body.get("subject_id"):
        subj = db.query(Subject).filter(Subject.id == body["subject_id"]).first()
        if subj:
            subject_name = subj.name
    if body.get("grade_id"):
        grade = db.query(Grade).filter(Grade.id == body["grade_id"]).first()
        if grade:
            grade_name = grade.name

    syllabus = Syllabus(
        name=body.get("name", f"{subject_name} - {grade_name} Syllabus"),
        subject=subject_name,
        grade_name=grade_name,
        syllabus_type=body.get("syllabus_type"),
    )
    db.add(syllabus)
    db.commit()
    db.refresh(syllabus)

    for mod_data in body.get("modules", []):
        module = SyllabusModule(
            syllabus_id=syllabus.id,
            order=mod_data.get("order", 1),
            name=mod_data.get("name", ""),
            weight=mod_data.get("weight", 1.0),
        )
        db.add(module)
        db.flush()
        for content_data in mod_data.get("contents", []):
            db.add(SyllabusContent(
                module_id=module.id,
                name=content_data.get("name", ""),
                content_type=content_data.get("content_type", "exercise"),
                weight=content_data.get("weight", 1.0),
            ))
    db.commit()
    db.refresh(syllabus)
    return _syllabus_full(syllabus)


@app.delete("/admin/syllabi/{syllabus_id}")
def delete_syllabus(syllabus_id: int, db: Session = Depends(get_db)):
    s = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    db.delete(s)
    db.commit()
    return {"message": "Syllabus deleted"}


# ── Syllabus Modules ──────────────────────────────────────────────────────────

@app.post("/admin/modules")
async def create_module(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    syllabus_id = body.get("syllabus_id")
    if not syllabus_id or not db.query(Syllabus).filter(Syllabus.id == syllabus_id).first():
        raise HTTPException(status_code=404, detail="Syllabus not found")
    last = db.query(SyllabusModule).filter(SyllabusModule.syllabus_id == syllabus_id).count()
    m = SyllabusModule(
        syllabus_id=syllabus_id,
        name=body.get("name", ""),
        weight=float(body.get("weight", 1.0)),
        order=last + 1,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"id": m.id, "name": m.name, "order": m.order, "weight": m.weight, "contents": []}


@app.put("/admin/modules/{module_id}")
async def update_module(module_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    m = db.query(SyllabusModule).filter(SyllabusModule.id == module_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Module not found")
    if "name" in body:
        m.name = body["name"]
    if "weight" in body:
        m.weight = float(body["weight"])
    if "order" in body:
        m.order = int(body["order"])
    db.commit()
    return {"id": m.id, "name": m.name, "order": m.order, "weight": m.weight}


@app.delete("/admin/modules/{module_id}")
def delete_module(module_id: int, db: Session = Depends(get_db)):
    m = db.query(SyllabusModule).filter(SyllabusModule.id == module_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Module not found")
    db.delete(m)
    db.commit()
    return {"message": "Module deleted"}


# ── Syllabus Contents ─────────────────────────────────────────────────────────

@app.post("/admin/contents")
async def create_content(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    module_id = body.get("module_id")
    if not module_id or not db.query(SyllabusModule).filter(SyllabusModule.id == module_id).first():
        raise HTTPException(status_code=404, detail="Module not found")
    c = SyllabusContent(
        module_id=module_id,
        name=body.get("name", ""),
        content_type=body.get("content_type", "exercise"),
        weight=float(body.get("weight", 1.0)),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "name": c.name, "content_type": c.content_type, "weight": c.weight}


@app.put("/admin/contents/{content_id}")
async def update_content(content_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    c = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Content not found")
    if "name" in body:
        c.name = body["name"]
    if "content_type" in body:
        c.content_type = body["content_type"]
    if "weight" in body:
        c.weight = float(body["weight"])
    db.commit()
    return {"id": c.id, "name": c.name, "content_type": c.content_type, "weight": c.weight}


@app.delete("/admin/contents/{content_id}")
def delete_content(content_id: int, db: Session = Depends(get_db)):
    c = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Content not found")
    # Remove progress records that reference this content before deleting
    from models import StudentProgress as _SP
    db.query(_SP).filter(_SP.content_id == content_id).delete()
    db.delete(c)
    db.commit()
    return {"message": "Content deleted"}


# ==================== Staff ====================

@app.get("/staff")
def get_staff(center_id: Optional[int] = None, db: Session = Depends(get_db),
             current = Depends(require_roles("super_admin", "center_admin"))):
    q = db.query(Staff)
    # Phase 1A: Center admin only sees their center's staff
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(Staff.center_id == current["obj"].center_id)
    elif center_id:
        q = q.filter(Staff.center_id == center_id)
    staff_list = q.order_by(Staff.name).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "first_name": s.first_name,
            "last_name": s.last_name,
            "role": s.role,
            "access_role": s.access_role or "teacher",
            "center_id": s.center_id,
            "email": s.email,
            "phone": s.phone,
            "calendar": s.calendar,
            "takesClasses": s.takes_classes,
        }
        for s in staff_list
    ]


@app.post("/add-staff")
async def add_staff(request: Request, db: Session = Depends(get_db),
                   current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    email = (body.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    if email_exists(db, email):
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    # Center admins automatically assign new staff to their center.
    caller = current.get("obj")
    center_id = body.get("center_id")
    if not center_id and caller and getattr(caller, "access_role", None) == "center_admin":
        center_id = getattr(caller, "center_id", None)

    staff_data = StaffCreate(
        name=body.get("name", f"{body.get('firstName', '')} {body.get('lastName', '')}"),
        role=body.get("role", ""),
        phone=body.get("phone", ""),
        email=email,
        calendar=body.get("calendar", True),
        firstName=body.get("firstName", ""),
        lastName=body.get("lastName", ""),
        takesClasses=body.get("takesClasses", True)
    )
    new_staff = crud.create_staff(db, staff_data)
    if center_id:
        new_staff.center_id = center_id
    # No default password — provision a pending account + send activation email.
    provision_account(db, "staff", new_staff, request=request)
    db.commit()
    # Phase 4A: Audit staff creation
    audit(db, "staff.created", subject=("staff", current["id"]), request=request,
          detail={"staff_id": new_staff.id, "center_id": new_staff.center_id})
    db.commit()
    return {
        "id": new_staff.id,
        "name": new_staff.name,
        "role": new_staff.role,
        "email": new_staff.email,
        "phone": new_staff.phone,
        "calendar": new_staff.calendar,
        "firstName": new_staff.first_name,
        "lastName": new_staff.last_name,
        "takesClasses": new_staff.takes_classes
    }


@app.put("/staff/{staff_id}")
async def update_staff(staff_id: int, request: Request, db: Session = Depends(get_db),
                      current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    db_staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not db_staff:
        raise HTTPException(status_code=404, detail="Staff not found")

    for field, col in [
        ("name", "name"), ("role", "role"), ("phone", "phone"),
        ("email", "email"), ("calendar", "calendar"),
    ]:
        if field in body:
            setattr(db_staff, col, body[field])
    if body.get("password"):
        # Route password changes through hashing + policy, never store plaintext.
        err = security.validate_password_strength(body["password"])
        if err:
            raise HTTPException(status_code=400, detail=err)
        db_staff.password_hash = security.hash_password(body["password"])
        db_staff.password = None
        if (db_staff.account_status or "active") != "active":
            db_staff.account_status = "active"
    if "firstName" in body:
        db_staff.first_name = body["firstName"]
    if "lastName" in body:
        db_staff.last_name = body["lastName"]
    if "takesClasses" in body:
        db_staff.takes_classes = body["takesClasses"]

    db.commit()
    # Phase 4A: Audit staff.updated
    audit(db, "staff.updated", subject=("staff", current["id"]), request=request,
          detail={"staff_id": db_staff.id, "center_id": db_staff.center_id})
    db.commit()
    db.refresh(db_staff)
    return {
        "id": db_staff.id, "name": db_staff.name, "role": db_staff.role,
        "access_role": db_staff.access_role or "teacher",
        "center_id": db_staff.center_id,
        "email": db_staff.email, "phone": db_staff.phone, "calendar": db_staff.calendar,
        "firstName": db_staff.first_name, "lastName": db_staff.last_name,
        "first_name": db_staff.first_name, "last_name": db_staff.last_name,
        "takesClasses": db_staff.takes_classes
    }


@app.patch("/staff/{staff_id}/calendar")
async def toggle_staff_calendar(staff_id: int, enabled: bool, db: Session = Depends(get_db),
                                current = Depends(require_roles("super_admin", "center_admin"))):
    db_staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not db_staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    db_staff.calendar = enabled
    db.commit()
    return {"id": db_staff.id, "calendar": db_staff.calendar}


# ==================== Batches & Class Sessions ====================

@app.get("/batches")
def get_batches(center_id: Optional[int] = None, db: Session = Depends(get_db),
               current = Depends(require_roles("super_admin", "center_admin", "teacher"))):
    q = db.query(Batch)
    # Phase 1A: Center admin only sees their center's batches
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(Batch.center_id == current["obj"].center_id)
    elif center_id:
        q = q.filter(Batch.center_id == center_id)
    return [
        {"id": b.id, "name": b.name, "subject": b.subject, "teacher_id": b.teacher_id, "center_id": b.center_id}
        for b in q.all()
    ]


@app.post("/batches")
async def create_batch(request: Request, db: Session = Depends(get_db),
                      current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    batch = Batch(
        name=body.get("name", ""),
        subject=body.get("subject"),
        teacher_id=body.get("teacher_id"),
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return {"id": batch.id, "name": batch.name}


@app.post("/sessions")
async def create_session(request: Request, db: Session = Depends(get_db),
                         current=Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    if not body.get("date") or not body.get("start_time") or not body.get("end_time"):
        raise HTTPException(status_code=400, detail="date, start_time, and end_time are required")
    session = ClassSession(
        batch_id=body.get("batch_id"),
        teacher_id=body.get("teacher_id"),
        date=body.get("date", ""),
        start_time=body.get("start_time", ""),
        end_time=body.get("end_time", ""),
        notes=body.get("notes"),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"id": session.id, "date": session.date, "start_time": session.start_time,
            "end_time": session.end_time, "teacher_id": session.teacher_id}


@app.get("/sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db)):
    """Single session with batch/teacher info — used by the session details page."""
    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if sess:
        return _session_to_dict(sess, db)
    # Fall back to scheduling v2 ClassOccurrence (IDs returned by /teacher/{id}/sessions)
    occ = db.query(ClassOccurrence).filter(ClassOccurrence.id == session_id).first()
    if not occ:
        raise HTTPException(status_code=404, detail="Session not found")
    return _occ_session_shape(db, occ)


@app.get("/sessions/{session_id}/students")
def get_session_students(session_id: int, db: Session = Depends(get_db)):
    """Return all students for this session, including their attendance status."""
    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        # Fall back to ClassOccurrence (scheduling v2)
        occ = db.query(ClassOccurrence).filter(ClassOccurrence.id == session_id).first()
        if not occ:
            raise HTTPException(status_code=404, detail="Session not found")

        # Build roster from Enrollment table (baseline − excludes ∪ includes)
        base_ids = _baseline_ids(db, occ.template_id)
        roster_ids = _occurrence_roster_ids(db, occ, base_ids)

        students_map = {s.id: s for s in db.query(Student).filter(Student.id.in_(roster_ids)).all()} if roster_ids else {}
        att_map = {a.student_id: a for a in db.query(Attendance).filter(Attendance.session_id == session_id).all()}

        result = []
        for sid in roster_ids:
            stu = students_map.get(sid)
            if not stu:
                continue
            att = att_map.get(sid)
            result.append({
                "id": stu.id,
                "first_name": stu.first_name,
                "last_name": stu.last_name,
                "email": stu.email or "",
                "current_grade": stu.current_grade or "",
                "desired_course": stu.desired_course or stu.instrument or "",
                "enrollment_type": "recurring",
                "attendance": {"id": att.id, "status": att.status, "notes": att.notes} if att else None,
            })
        return result

    students_map = {s.id: s for s in db.query(Student).all()}
    result = []
    for att in db.query(Attendance).filter(Attendance.session_id == session_id).all():
        stu = students_map.get(att.student_id)
        if not stu:
            continue
        result.append({
            "id": stu.id,
            "first_name": stu.first_name,
            "last_name": stu.last_name,
            "email": stu.email or "",
            "current_grade": stu.current_grade or "",
            "desired_course": stu.desired_course or stu.instrument or "",
            "enrollment_type": att.enrollment_type or "single_session",
            "attendance": {"id": att.id, "status": att.status, "notes": att.notes},
        })
    return result


@app.post("/sessions/{session_id}/enroll")
def enroll_student_in_session(
    session_id: int,
    student_id: int,
    enrollment_type: str = "single_session",
    db: Session = Depends(get_db)
):
    from datetime import datetime as _dt

    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if not db.query(Student).filter(Student.id == student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")

    if enrollment_type == "recurring" and sess.batch_id:
        # Find all sessions in this batch that share the SAME weekday AND start_time
        try:
            target_weekday = _dt.strptime(sess.date, "%Y-%m-%d").weekday()
        except Exception:
            target_weekday = None

        batch_sessions = db.query(ClassSession).filter(
            ClassSession.batch_id == sess.batch_id
        ).all()

        matching = [
            s for s in batch_sessions
            if s.start_time == sess.start_time
            and (
                target_weekday is None
                or _dt.strptime(s.date, "%Y-%m-%d").weekday() == target_weekday
            )
        ]

        # Only sessions not already booked actually consume the package.
        to_add = [
            s for s in matching
            if not db.query(Attendance).filter(
                Attendance.session_id == s.id,
                Attendance.student_id == student_id,
            ).first()
        ]
        # Restrict to the active package's validity window so the count we
        # charge matches what sessions_used_for_package will later count.
        sp = get_active_student_package(db, student_id)
        if sp:
            to_add = [
                s for s in to_add
                if s.date >= sp.start_date and (not sp.end_date or s.date <= sp.end_date)
            ]
        # Guard: package must be active, in-window, and have room for all of them.
        assert_can_book(db, student_id, sess, count=len(to_add) or 1)

        for s in to_add:
            db.add(Attendance(
                session_id=s.id,
                student_id=student_id,
                status="present",
                enrollment_type="recurring",
            ))
        db.commit()
        return {"message": f"Enrolled in {len(to_add)} session(s) on the same day/time"}

    # single_session — just this one
    exists = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.student_id == student_id
    ).first()
    if not exists:
        assert_can_book(db, student_id, sess, count=1)
        db.add(Attendance(
            session_id=session_id,
            student_id=student_id,
            status="present",
            enrollment_type="single_session",
        ))
        db.commit()
    return {"message": "Enrolled in this session"}


@app.delete("/sessions/{session_id}/students/{student_id}")
def remove_student_from_session(
    session_id: int, student_id: int,
    scope: str = "this_class",
    db: Session = Depends(get_db)
):
    from datetime import datetime as _dt

    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    if scope == "all_classes" and sess.batch_id:
        # Remove from all sessions in this batch that share the same weekday + time
        try:
            target_weekday = _dt.strptime(sess.date, "%Y-%m-%d").weekday()
        except Exception:
            target_weekday = None

        batch_sessions = db.query(ClassSession).filter(
            ClassSession.batch_id == sess.batch_id
        ).all()
        matching_ids = [
            s.id for s in batch_sessions
            if s.start_time == sess.start_time
            and (target_weekday is None or _dt.strptime(s.date, "%Y-%m-%d").weekday() == target_weekday)
        ]
        if matching_ids:
            db.query(Attendance).filter(
                Attendance.session_id.in_(matching_ids),
                Attendance.student_id == student_id,
                Attendance.enrollment_type == "recurring",
            ).delete(synchronize_session=False)
    else:
        # Remove only this session's attendance
        att = db.query(Attendance).filter(
            Attendance.session_id == session_id,
            Attendance.student_id == student_id
        ).first()
        if att:
            db.delete(att)

    db.commit()
    return {"message": "Removed"}


@app.put("/sessions/{session_id}/attendance/{student_id}")
def mark_attendance(
    session_id: int, student_id: int,
    status: str = "present",
    notes: Optional[str] = None,
    require_feedback: bool = False,
    bypass_package: bool = False,   # True for teacher/admin — skips package quota check
    db: Session = Depends(get_db)
):
    if require_feedback and status == "present" and not notes:
        raise HTTPException(status_code=400, detail="Feedback/notes are required before marking present")
    att = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.student_id == student_id
    ).first()

    # Enforce the package guard only when this mark newly consumes a session
    # (going to 'present' from absent/unmarked). bypass_package=True lets
    # teachers/admins mark anyone present regardless of package state.
    newly_present = status == "present" and (att is None or att.status != "present")
    if newly_present and not bypass_package:
        session = db.query(ClassSession).filter(ClassSession.id == session_id).first()
        assert_can_book(db, student_id, session, count=1)

    if att:
        att.status = status
        att.notes = notes
    else:
        db.add(Attendance(session_id=session_id, student_id=student_id, status=status, notes=notes))
    db.commit()
    return {"message": "Attendance updated"}


@app.post("/student/{student_id}/sessions/{session_id}/cancel")
def student_cancel_session(
    student_id: int, session_id: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Student cancels their own attendance for an occurrence.
    Checks the cancellation window from the student's active package.
    Marks attendance as 'student_cancelled' — does NOT cancel the occurrence itself.
    """
    from datetime import datetime as _dt
    occ = db.query(ClassOccurrence).filter(ClassOccurrence.id == session_id).first()
    if not occ:
        raise HTTPException(status_code=404, detail="Session not found")
    if occ.status == "cancelled":
        raise HTTPException(status_code=400, detail="This session has already been cancelled")

    # Check cancellation window
    sp = get_active_student_package(db, student_id, persist=False)
    cancel_hours = 24
    if sp:
        pkg = db.query(Package).filter(Package.id == sp.package_id).first()
        if pkg and pkg.cancellation_window_hours is not None:
            cancel_hours = pkg.cancellation_window_hours
    if cancel_hours > 0 and occ.date and occ.start_time:
        try:
            session_dt = _dt.strptime(f"{occ.date} {occ.start_time}", "%Y-%m-%d %H:%M")
            hours_until = (session_dt - _dt.now()).total_seconds() / 3600
            if hours_until < cancel_hours:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cancellation window has passed — classes must be cancelled at least {cancel_hours}h in advance."
                )
        except ValueError:
            pass

    att = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.student_id == student_id
    ).first()
    if att:
        att.status = "student_cancelled"
        att.notes = reason or "Cancelled by student"
    else:
        db.add(Attendance(
            session_id=session_id, student_id=student_id,
            status="student_cancelled", notes=reason or "Cancelled by student"
        ))
    db.commit()
    return {"message": "Session cancelled", "session_id": session_id}


@app.put("/sessions/{session_id}/publish")
def toggle_session_publish(session_id: int, db: Session = Depends(get_db)):
    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.is_published = not (sess.is_published if sess.is_published is not None else True)
    db.commit()
    return {"is_published": sess.is_published}


@app.put("/sessions/{session_id}/cancel")
def cancel_session(session_id: int, db: Session = Depends(get_db)):
    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.status = "cancelled"
    db.commit()
    return {"status": "cancelled"}


@app.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    db.query(Attendance).filter(Attendance.session_id == session_id).delete()
    db.delete(sess)
    db.commit()
    return {"message": "Deleted"}


@app.get("/sessions/{session_id}/series")
def get_session_series(session_id: int, db: Session = Depends(get_db)):
    """Return all sessions in the same batch (series) on or after this session's date."""
    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    if not sess.batch_id:
        return [{"id": sess.id, "date": sess.date, "start_time": sess.start_time, "end_time": sess.end_time}]
    sessions = db.query(ClassSession).filter(
        ClassSession.batch_id == sess.batch_id,
        ClassSession.date >= sess.date
    ).order_by(ClassSession.date).all()
    return [{"id": s.id, "date": s.date, "start_time": s.start_time, "end_time": s.end_time} for s in sessions]


@app.put("/sessions/{session_id}/update-future")
async def update_future_sessions(session_id: int, request: Request, db: Session = Depends(get_db)):
    """Update start/end time for all sessions in the same batch on or after this session's date."""
    body = await request.json()
    sess = db.query(ClassSession).filter(ClassSession.id == session_id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    query = db.query(ClassSession).filter(ClassSession.id == session_id)
    if sess.batch_id:
        query = db.query(ClassSession).filter(
            ClassSession.batch_id == sess.batch_id,
            ClassSession.date >= sess.date
        )
    for s in query.all():
        if "start_time" in body:
            s.start_time = body["start_time"]
        if "end_time" in body:
            s.end_time = body["end_time"]
    db.commit()
    return {"message": "Updated", "count": query.count()}


@app.post("/enrollments")
async def enroll_student(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    enrollment = StudentEnrollment(
        student_id=body.get("student_id"),
        batch_id=body.get("batch_id"),
    )
    db.add(enrollment)
    db.commit()
    return {"message": "Enrolled"}


# ════════════════════════ Scheduling v2 (recurring events) ════════════════════════
# Templates → recurrence rules → materialized occurrences, with 3-tier edits
# (this | this_and_future | series), capacity-checked template enrollment, and
# package-gated per-occurrence attendance (reuses assert_can_book).

from datetime import date as _d, timedelta as _td

_EDIT_FIELDS = ("start_time", "end_time", "teacher_id", "room_id")


def _template_color(template_id, start_time=None):
    """Deterministic color per (template, start_time) pair.
    Same class series at same time stays one color across days;
    different time slots get distinct colors even on the same template."""
    import colorsys
    seed = (template_id or 0)
    if start_time:
        try:
            parts = str(start_time).split(":")
            minutes = int(parts[0]) * 60 + int(parts[1])
            seed = seed * 1440 + minutes
        except Exception:
            pass
    hue = ((seed * 137.508) % 360) / 360.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.45, 0.65)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _validate_times(start, end):
    if start and end and end <= start:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")


def _validate_rule(freq, by_weekday, start_date, end_date):
    freq = (freq or "").lower()
    if freq not in ("daily", "weekly", "monthly", "custom"):
        raise HTTPException(status_code=400, detail="freq must be daily|weekly|monthly|custom")
    if freq in ("weekly", "custom") and not by_weekday:
        raise HTTPException(status_code=400, detail="weekly/custom recurrence requires at least one weekday")
    if not start_date:
        raise HTTPException(status_code=400, detail="recurrence start_date is required")
    if end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on/after start_date")


def _template_dict(db, t: ClassTemplate):
    r = t.rule
    active_enroll = db.query(Enrollment).filter(
        Enrollment.template_id == t.id, Enrollment.status == "active"
    ).count()
    occ_count = db.query(ClassOccurrence).filter(ClassOccurrence.template_id == t.id).count()
    teacher = db.query(Staff).filter(Staff.id == t.teacher_id).first() if t.teacher_id else None
    return {
        "id": t.id, "name": t.name, "course": t.course,
        "teacher_id": t.teacher_id, "teacher_name": teacher.name if teacher else None,
        "center_id": t.center_id, "room_id": t.room_id,
        "start_time": t.start_time, "end_time": t.end_time,
        "capacity": t.capacity, "status": t.status,
        "enrolled_count": active_enroll, "occurrence_count": occ_count,
        "parent_template_id": t.parent_template_id,
        "recurrence": {
            "freq": r.freq, "interval": r.interval, "by_weekday": r.by_weekday,
            "by_monthday": r.by_monthday, "start_date": r.start_date, "end_date": r.end_date,
            "rrule": scheduling.to_rrule(r),
        } if r else None,
    }


def _occurrence_dict(db, o: ClassOccurrence):
    teacher = db.query(Staff).filter(Staff.id == o.teacher_id).first() if o.teacher_id else None
    t = o.template
    present = db.query(Attendance).filter(
        Attendance.session_id == o.id, Attendance.status == "present"
    ).count()
    return {
        "id": o.id, "template_id": o.template_id,
        "name": t.name if t else None, "course": t.course if t else None,
        "date": o.date, "start_time": o.start_time, "end_time": o.end_time,
        "teacher_id": o.teacher_id, "teacher_name": teacher.name if teacher else None,
        "room_id": o.room_id, "status": o.status,
        "is_modified": o.is_modified, "is_makeup": o.is_makeup, "original_date": o.original_date,
        "capacity": t.capacity if t else None,
        "present_count": present, "notes": o.notes, "is_published": o.is_published,
    }


def _apply_edits(obj, edits):
    for f in _EDIT_FIELDS:
        if f in edits and edits[f] is not None:
            setattr(obj, f, edits[f])


def _copy_active_enrollments(db, src_template_id, dst_template_id):
    # Only the baseline roster (occurrence_id NULL) carries over to a split
    # template; per-occurrence overrides are tied to specific old occurrences.
    for e in db.query(Enrollment).filter(
        Enrollment.template_id == src_template_id, Enrollment.status == "active",
        Enrollment.occurrence_id.is_(None),
    ).all():
        if not db.query(Enrollment).filter(
            Enrollment.template_id == dst_template_id, Enrollment.student_id == e.student_id,
            Enrollment.occurrence_id.is_(None),
        ).first():
            db.add(Enrollment(template_id=dst_template_id, student_id=e.student_id,
                              status="active", start_date=e.start_date))


# ── Recurrence-aware roster (baseline + per-occurrence include/exclude) ──

def _stream_occurrences(db, occ, scope):
    """Occurrences affected by a scoped roster op, reusing the SAME stream logic
    as delete/edit. 'this' → just this occurrence. 'this_and_future' → same
    weekday stream, date >= this occurrence (never other day/time streams)."""
    if scope == "this":
        return [occ]
    rule = occ.template.rule if occ.template else None
    weekdays = [w.strip() for w in (rule.by_weekday or "").split(",") if w.strip()] if rule else []
    weekly = rule and (rule.freq or "").lower() in ("weekly", "custom") and weekdays
    target_wd = _d.fromisoformat(occ.date).weekday()
    rows = db.query(ClassOccurrence).filter(
        ClassOccurrence.template_id == occ.template_id, ClassOccurrence.date >= occ.date
    ).all()
    return [x for x in rows if (not weekly) or _d.fromisoformat(x.date).weekday() == target_wd]


def _baseline_ids(db, template_id):
    return {e.student_id for e in db.query(Enrollment).filter(
        Enrollment.template_id == template_id, Enrollment.occurrence_id.is_(None),
        Enrollment.status == "active").all()}


def _occurrence_roster_ids(db, occ, base_ids=None):
    """Effective roster for one occurrence: (baseline − excludes) ∪ includes."""
    base = set(base_ids) if base_ids is not None else _baseline_ids(db, occ.template_id)
    inc, exc = set(), set()
    for e in db.query(Enrollment).filter(Enrollment.occurrence_id == occ.id).all():
        (inc if e.kind == "include" else exc).add(e.student_id)
    return (base - exc) | inc


def _set_membership(db, occ, student_id, present, base_ids):
    """Make `student_id` present/absent on a single occurrence's roster via an
    override row, against the template baseline."""
    in_base = student_id in base_ids
    row = db.query(Enrollment).filter(
        Enrollment.occurrence_id == occ.id, Enrollment.student_id == student_id).first()
    if present:
        if in_base:
            if row:
                db.delete(row)              # remove any exclude → baseline applies
        else:
            if row:
                row.kind = "include"; row.status = "active"
            else:
                db.add(Enrollment(template_id=occ.template_id, student_id=student_id,
                                  occurrence_id=occ.id, kind="include", status="active"))
    else:  # remove
        if in_base:
            if row:
                row.kind = "exclude"
            else:
                db.add(Enrollment(template_id=occ.template_id, student_id=student_id,
                                  occurrence_id=occ.id, kind="exclude", status="active"))
        else:
            if row:
                db.delete(row)              # drop the include


def _new_split_template(db, template, target_date, edits, by_weekday, end_date):
    """Create the continuation template (+rule) that a split peels off."""
    t2 = ClassTemplate(
        name=template.name, course=template.course,
        teacher_id=edits.get("teacher_id") or template.teacher_id,
        center_id=template.center_id,
        room_id=edits.get("room_id") if "room_id" in edits else template.room_id,
        start_time=edits.get("start_time") or template.start_time,
        end_time=edits.get("end_time") or template.end_time,
        capacity=template.capacity, status="active",
        parent_template_id=template.id, split_from_date=target_date,
    )
    db.add(t2)
    db.flush()
    db.add(RecurrenceRule(
        template_id=t2.id, freq=template.rule.freq, interval=template.rule.interval,
        by_weekday=by_weekday, by_monthday=template.rule.by_monthday,
        start_date=target_date, end_date=end_date,
    ))
    db.flush()
    return t2


def _split_series(db, template: ClassTemplate, target_date: str, edits: dict) -> ClassTemplate:
    """Split a recurring schedule at target_date for "This & Following".

    Business rule: only the edited day/time STREAM moves forward. For a template
    that recurs on multiple weekdays (e.g. MO,WE), editing a Monday peels ONLY
    the future Mondays into a new template — Wednesdays stay on the original,
    unchanged. For a single-stream template, the whole future splits.
    Past occurrences + attendance always stay on the original template.
    """
    rule = template.rule
    target_wd = _d.fromisoformat(target_date).weekday()
    target_code = scheduling.WEEKDAY_CODES[target_wd]
    weekdays = [w.strip() for w in (rule.by_weekday or "").split(",") if w.strip()] if rule else []
    multi_stream = (
        rule and (rule.freq or "").lower() in ("weekly", "custom")
        and len(weekdays) > 1 and target_code in weekdays
    )

    if not multi_stream:
        # Single stream → split the entire future (original behavior).
        old_end = rule.end_date
        rule.end_date = (_d.fromisoformat(target_date) - _td(days=1)).isoformat()
        t2 = _new_split_template(db, template, target_date, edits, rule.by_weekday, old_end)
        for occ in db.query(ClassOccurrence).filter(
            ClassOccurrence.template_id == template.id, ClassOccurrence.date >= target_date
        ).all():
            occ.template_id = t2.id
            if not occ.is_modified:
                _apply_edits(occ, edits)
        _copy_active_enrollments(db, template.id, t2.id)
        db.flush()
        db.refresh(t2)
        scheduling.generate_for_template(db, t2, from_date=target_date)
        return t2

    # Multi-weekday template → peel ONLY the edited weekday's stream.
    # 1. Original keeps the other weekdays going (drop the edited weekday from it).
    rule.by_weekday = ",".join(w for w in weekdays if w != target_code)
    # 2. New template recurs only on the edited weekday, with the edits.
    t2 = _new_split_template(db, template, target_date, edits, target_code, rule.end_date)
    # 3. Move ONLY future occurrences of the edited weekday onto the new stream.
    for occ in db.query(ClassOccurrence).filter(
        ClassOccurrence.template_id == template.id, ClassOccurrence.date >= target_date
    ).all():
        if _d.fromisoformat(occ.date).weekday() != target_wd:
            continue  # leave Wednesdays (other streams) on the original
        occ.template_id = t2.id
        if not occ.is_modified:
            _apply_edits(occ, edits)
    _copy_active_enrollments(db, template.id, t2.id)
    db.flush()
    db.refresh(t2)
    scheduling.generate_for_template(db, t2, from_date=target_date)
    return t2


# ── Templates ──

@app.post("/scheduling/templates")
async def create_template(request: Request, db: Session = Depends(get_db),
                         current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    _validate_times(body.get("start_time"), body.get("end_time"))

    # Accept recurrence fields either nested {"recurrence": {...}} or flat at top level.
    rec = body.get("recurrence") or {}
    if not rec.get("freq"):
        # Flat style: freq/by_weekday/start_date/end_date at top level
        rec = {
            "freq": body.get("freq"),
            "by_weekday": body.get("by_weekday"),
            "by_monthday": body.get("by_monthday"),
            "start_date": body.get("start_date"),
            "end_date": body.get("end_date"),
            "interval": body.get("interval", 1),
        }

    # Normalise by_weekday: accept list ["MO","WE"] or CSV string "MO,WE"
    bwd = rec.get("by_weekday")
    if isinstance(bwd, list):
        bwd = ",".join(bwd)
    rec["by_weekday"] = bwd

    _validate_rule(rec.get("freq"), rec.get("by_weekday"), rec.get("start_date"), rec.get("end_date"))

    # Auto-assign center for center admins; super_admin may override via body.
    caller = current.get("obj")
    center_id = body.get("center_id")
    if not center_id and caller and getattr(caller, "access_role", None) == "center_admin":
        center_id = getattr(caller, "center_id", None)

    # Accept either "name" or "title" from the frontend.
    name = body.get("name") or body.get("title") or ""
    # Accept either "course" or "subject" from the frontend.
    course = body.get("course") or body.get("subject")

    t = ClassTemplate(
        name=name, course=course,
        teacher_id=body.get("teacher_id"), center_id=center_id,
        room_id=body.get("room_id"),
        start_time=body["start_time"], end_time=body["end_time"],
        capacity=int(body.get("capacity", 10)),
    )
    db.add(t)
    db.flush()
    db.add(RecurrenceRule(
        template_id=t.id, freq=rec["freq"].lower(), interval=int(rec.get("interval") or 1),
        by_weekday=rec.get("by_weekday"), by_monthday=rec.get("by_monthday"),
        start_date=rec["start_date"], end_date=rec.get("end_date"),
    ))
    db.flush()
    db.refresh(t)
    created = scheduling.generate_for_template(db, t)
    db.commit()
    db.refresh(t)
    return {**_template_dict(db, t), "occurrences_created": created}


@app.get("/scheduling/templates")
def list_templates(center_id: Optional[int] = None, page: Optional[int] = None, limit: int = 50,
                  db: Session = Depends(get_db),
                  current = Depends(require_roles("super_admin", "center_admin", "teacher"))):
    """List class templates. Phase 6: Paginated if page param provided, else returns array."""
    q = db.query(ClassTemplate).filter(ClassTemplate.status == "active")
    # Phase 1A: Center admin only sees their center's templates
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(ClassTemplate.center_id == current["obj"].center_id)
    elif center_id:
        q = q.filter(ClassTemplate.center_id == center_id)

    # If no page param, return array (backward compatibility)
    if page is None:
        templates = q.order_by(ClassTemplate.created_at.desc()).all()
        return [_template_dict(db, t) for t in templates]

    # If page param provided, return paginated response
    total = q.count()
    templates = q.order_by(ClassTemplate.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [_template_dict(db, t) for t in templates],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


@app.get("/scheduling/templates/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)):
    t = db.query(ClassTemplate).filter(ClassTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return _template_dict(db, t)


@app.put("/scheduling/templates/{template_id}")
async def update_template(template_id: int, request: Request, db: Session = Depends(get_db),
                         current = Depends(require_roles("super_admin", "center_admin"))):
    """Series-level edit: update template + recurrence, regenerate future occurrences."""
    body = await request.json()
    t = db.query(ClassTemplate).filter(ClassTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    # Center admin may only edit their own center's templates.
    caller_put = current.get("obj")
    if (caller_put and getattr(caller_put, "access_role", None) == "center_admin"
            and t.center_id and t.center_id != getattr(caller_put, "center_id", None)):
        raise HTTPException(status_code=403, detail="You can only edit your own center's templates")

    _validate_times(body.get("start_time", t.start_time), body.get("end_time", t.end_time))
    for f in ("name", "course", "teacher_id", "center_id", "room_id", "start_time", "end_time", "capacity"):
        if f in body:
            setattr(t, f, body[f])
    # Accept "title"/"subject" aliases
    if "title" in body and "name" not in body:
        t.name = body["title"]
    if "subject" in body and "course" not in body:
        t.course = body["subject"]

    # Accept flat recurrence fields OR nested recurrence object
    rec = body.get("recurrence")
    if not rec and any(k in body for k in ("freq", "by_weekday", "start_date")):
        rec = {k: body.get(k) for k in ("freq", "interval", "by_weekday", "by_monthday", "start_date", "end_date")}
    if rec:
        # Normalise by_weekday list → CSV
        bwd = rec.get("by_weekday")
        if isinstance(bwd, list):
            rec["by_weekday"] = ",".join(bwd)
        r = t.rule
        _validate_rule(rec.get("freq", r.freq), rec.get("by_weekday", r.by_weekday),
                       rec.get("start_date", r.start_date), rec.get("end_date", r.end_date))
        for f in ("freq", "interval", "by_weekday", "by_monthday", "start_date", "end_date"):
            if f in rec:
                setattr(r, f, rec[f])
    # Propagate field changes to future non-modified occurrences, then regenerate.
    today = _d.today().isoformat()
    for occ in db.query(ClassOccurrence).filter(
        ClassOccurrence.template_id == t.id, ClassOccurrence.date >= today,
        ClassOccurrence.is_modified == False
    ).all():
        occ.start_time = t.start_time
        occ.end_time = t.end_time
        if "teacher_id" in body:
            occ.teacher_id = t.teacher_id
        if "room_id" in body:
            occ.room_id = t.room_id
    scheduling.generate_for_template(db, t, from_date=today)
    db.commit()
    db.refresh(t)
    return _template_dict(db, t)


@app.post("/scheduling/templates/{template_id}/regenerate")
def regenerate_template(template_id: int, db: Session = Depends(get_db)):
    """Top up future occurrences up to the rolling horizon (for open-ended series)."""
    t = db.query(ClassTemplate).filter(ClassTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    created = scheduling.generate_for_template(db, t, from_date=_d.today().isoformat())
    db.commit()
    return {"occurrences_created": created}


# ── Occurrences + 3-tier edits ──

@app.get("/scheduling/occurrences/{occ_id}")
def get_occurrence(occ_id: int, db: Session = Depends(get_db)):
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")
    return _occurrence_dict(db, o)


@app.put("/scheduling/occurrences/{occ_id}")
async def edit_occurrence(occ_id: int, request: Request, db: Session = Depends(get_db)):
    """3-tier edit. body: {scope, start_time?, end_time?, teacher_id?, room_id?}."""
    body = await request.json()
    scope = body.get("scope", "this")
    if scope not in ("this", "this_and_future", "series"):
        raise HTTPException(status_code=400, detail="scope must be this|this_and_future|series")
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")
    edits = {f: body[f] for f in _EDIT_FIELDS if f in body}
    _validate_times(edits.get("start_time", o.start_time), edits.get("end_time", o.end_time))
    t = o.template

    if scope == "this":
        _apply_edits(o, edits)
        o.is_modified = True
        result_template_id = o.template_id
    elif scope == "this_and_future":
        if not t:
            raise HTTPException(status_code=400, detail="Occurrence has no series to split")
        t2 = _split_series(db, t, o.date, edits)
        result_template_id = t2.id
    else:  # series
        if t:
            _apply_edits(t, edits)
            for occ in db.query(ClassOccurrence).filter(
                ClassOccurrence.template_id == t.id, ClassOccurrence.is_modified == False
            ).all():
                _apply_edits(occ, edits)
        else:
            _apply_edits(o, edits)
        result_template_id = o.template_id
    db.commit()
    return {"message": f"Updated ({scope})", "template_id": result_template_id}


@app.post("/scheduling/occurrences/{occ_id}/cancel")
async def cancel_occurrence(occ_id: int, request: Request, db: Session = Depends(get_db)):
    """Cancel occurrence(s). Attendance is preserved. body: {scope}."""
    body = await request.json() if await request.body() else {}
    scope = (body or {}).get("scope", "this")
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")
    if scope == "this":
        o.status = "cancelled"
        o.is_modified = True
        n = 1
    else:  # this_and_future
        targets = db.query(ClassOccurrence).filter(
            ClassOccurrence.template_id == o.template_id,
            ClassOccurrence.date >= o.date,
            ClassOccurrence.status != "cancelled",
        ).all()
        for occ in targets:
            occ.status = "cancelled"
            occ.is_modified = True
        n = len(targets)
    db.commit()
    return {"message": "Cancelled", "count": n}


@app.delete("/scheduling/occurrences/{occ_id}")
def delete_occurrence(occ_id: int, scope: str = "this", db: Session = Depends(get_db)):
    """Permanently delete occurrence(s), isolated to the selected recurrence
    STREAM (same weekday/time). scope:
       this            → just this occurrence (+ its attendance)
       this_and_future → this + all later occurrences in the SAME stream; the
                         stream stops generating forward (other weekdays untouched)
       series          → all occurrences in the SAME stream; other streams (and
                         their enrollments) are preserved."""
    if scope not in ("this", "this_and_future", "series"):
        raise HTTPException(status_code=400, detail="scope must be this|this_and_future|series")
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")

    template = o.template
    rule = template.rule if template else None
    target_wd = _d.fromisoformat(o.date).weekday()
    target_code = scheduling.WEEKDAY_CODES[target_wd]
    weekdays = [w.strip() for w in (rule.by_weekday or "").split(",") if w.strip()] if rule else []
    weekly = rule and (rule.freq or "").lower() in ("weekly", "custom") and len(weekdays) >= 1
    multi_stream = weekly and len(weekdays) > 1

    def _same_stream(occ):
        # Weekly/custom → stream identified by weekday. Daily/monthly → single stream.
        return (not weekly) or (_d.fromisoformat(occ.date).weekday() == target_wd)

    def _purge(rows):
        ids = [x.id for x in rows]
        if ids:
            db.query(Attendance).filter(Attendance.session_id.in_(ids)).delete(synchronize_session=False)
            db.query(ClassOccurrence).filter(ClassOccurrence.id.in_(ids)).delete(synchronize_session=False)
        return len(ids)

    base = db.query(ClassOccurrence).filter(ClassOccurrence.template_id == o.template_id) if o.template_id \
        else db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id)

    if scope == "this":
        n = _purge([o])

    elif scope == "this_and_future":
        n = _purge([x for x in base.filter(ClassOccurrence.date >= o.date).all() if _same_stream(x)])
        # Stop this stream from regenerating; leave other streams intact.
        if rule:
            if multi_stream:
                rule.by_weekday = ",".join(w for w in weekdays if w != target_code)
                # The dropped weekday no longer matches the rule, so pin the
                # surviving earlier occurrences of this stream to protect them
                # from regeneration cleanup (other weekdays still regenerate).
                for x in base.filter(ClassOccurrence.date < o.date).all():
                    if _same_stream(x):
                        x.is_modified = True
            else:
                rule.end_date = (_d.fromisoformat(o.date) - _td(days=1)).isoformat()

    else:  # series — delete the whole stream (past + future)
        n = _purge([x for x in base.all() if _same_stream(x)])
        if multi_stream:
            # Drop just this weekday; other streams + enrollments survive.
            rule.by_weekday = ",".join(w for w in weekdays if w != target_code)
        elif o.template_id:
            # Single-stream template → remove it entirely.
            tid = o.template_id
            db.query(Enrollment).filter(Enrollment.template_id == tid).delete(synchronize_session=False)
            db.query(RecurrenceRule).filter(RecurrenceRule.template_id == tid).delete(synchronize_session=False)
            db.query(ClassTemplate).filter(ClassTemplate.id == tid).delete(synchronize_session=False)
    db.commit()
    return {"message": "Deleted", "count": n}


# ── Enrollment (template-level, capacity-checked) ──

@app.post("/scheduling/templates/{template_id}/enroll")
async def enroll_in_template(template_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    student_id = body.get("student_id")
    t = db.query(ClassTemplate).filter(ClassTemplate.id == template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if not db.query(Student).filter(Student.id == student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")
    existing = db.query(Enrollment).filter(
        Enrollment.template_id == template_id, Enrollment.student_id == student_id,
        Enrollment.occurrence_id.is_(None)
    ).first()
    if existing and existing.status == "active":
        return {"message": "Already enrolled"}
    active = db.query(Enrollment).filter(
        Enrollment.template_id == template_id, Enrollment.status == "active",
        Enrollment.occurrence_id.is_(None)
    ).count()
    if t.capacity and active >= t.capacity:
        raise HTTPException(status_code=400, detail=f"Class is full ({active}/{t.capacity})")
    if existing:
        existing.status = "active"
    else:
        db.add(Enrollment(template_id=template_id, student_id=student_id,
                          status="active", start_date=body.get("start_date") or _d.today().isoformat()))
    db.commit()
    return {"message": "Enrolled"}


@app.post("/scheduling/occurrences/{occ_id}/add-student")
async def add_student_scoped(occ_id: int, request: Request, db: Session = Depends(get_db)):
    """Recurrence-aware add. body: {student_id, scope: this|this_and_future}.
    Adds to the selected occurrence (and, for this_and_future, all later
    occurrences in the SAME day/time stream — never other streams)."""
    body = await request.json()
    student_id = body.get("student_id")
    scope = body.get("scope", "this")
    if scope not in ("this", "this_and_future"):
        raise HTTPException(status_code=400, detail="scope must be this|this_and_future")
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")
    if not db.query(Student).filter(Student.id == student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")
    base = _baseline_ids(db, o.template_id)
    cap = o.template.capacity if o.template else None
    targets = _stream_occurrences(db, o, scope)
    added = 0
    for occ in targets:
        roster = _occurrence_roster_ids(db, occ, base)
        if student_id in roster:
            continue
        if cap and len(roster) >= cap:
            raise HTTPException(status_code=400, detail=f"Class on {occ.date} is full ({len(roster)}/{cap})")
        _set_membership(db, occ, student_id, present=True, base_ids=base)
        added += 1
    db.commit()
    return {"message": "Added", "occurrences_affected": added, "scope": scope}


@app.post("/scheduling/occurrences/{occ_id}/remove-student")
async def remove_student_scoped(occ_id: int, request: Request, db: Session = Depends(get_db)):
    """Recurrence-aware remove. body: {student_id, scope: this|this_and_future}.
    Removes from the selected occurrence (and, for this_and_future, all later
    occurrences in the SAME stream). Past occurrences / other streams untouched."""
    body = await request.json()
    student_id = body.get("student_id")
    scope = body.get("scope", "this")
    if scope not in ("this", "this_and_future"):
        raise HTTPException(status_code=400, detail="scope must be this|this_and_future")
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")
    base = _baseline_ids(db, o.template_id)
    removed = 0
    for occ in _stream_occurrences(db, o, scope):
        if student_id not in _occurrence_roster_ids(db, occ, base):
            continue
        _set_membership(db, occ, student_id, present=False, base_ids=base)
        removed += 1
    db.commit()
    return {"message": "Removed", "occurrences_affected": removed, "scope": scope}


@app.delete("/scheduling/templates/{template_id}/enroll/{student_id}")
def unenroll_from_template(template_id: int, student_id: int, db: Session = Depends(get_db)):
    e = db.query(Enrollment).filter(
        Enrollment.template_id == template_id, Enrollment.student_id == student_id
    ).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    e.status = "cancelled"
    e.end_date = _d.today().isoformat()
    db.commit()
    return {"message": "Unenrolled"}


@app.get("/scheduling/templates/{template_id}/roster")
def template_roster(template_id: int, db: Session = Depends(get_db)):
    enrolls = db.query(Enrollment).filter(
        Enrollment.template_id == template_id, Enrollment.status == "active",
        Enrollment.occurrence_id.is_(None)
    ).all()
    out = []
    for e in enrolls:
        s = db.query(Student).filter(Student.id == e.student_id).first()
        if s:
            out.append({"student_id": s.id, "first_name": s.first_name,
                        "last_name": s.last_name, "email": s.email, "start_date": e.start_date})
    return out


# ── Attendance (per occurrence, package-gated) ──

@app.get("/scheduling/occurrences/{occ_id}/attendance")
def occurrence_attendance(occ_id: int, db: Session = Depends(get_db)):
    """Enrolled roster for this occurrence's template + each student's attendance
    status/notes for THIS occurrence — powers the detail dialog."""
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")
    att_map = {a.student_id: a for a in db.query(Attendance).filter(Attendance.session_id == occ_id).all()}
    rows = []
    roster_ids = _occurrence_roster_ids(db, o) if o.template_id else set()
    seen = set()
    for sid in roster_ids:
        s = db.query(Student).filter(Student.id == sid).first()
        if not s:
            continue
        seen.add(s.id)
        a = att_map.get(s.id)
        rows.append({"student_id": s.id, "first_name": s.first_name, "last_name": s.last_name,
                     "status": a.status if a else None, "notes": a.notes if a else None})
    # Include any ad-hoc attendees not in the active roster (e.g. makeups).
    for sid, a in att_map.items():
        if sid in seen:
            continue
        s = db.query(Student).filter(Student.id == sid).first()
        if s:
            rows.append({"student_id": s.id, "first_name": s.first_name, "last_name": s.last_name,
                         "status": a.status, "notes": a.notes})
    return rows


@app.put("/scheduling/occurrences/{occ_id}/attendance/{student_id}")
async def mark_occurrence_attendance(occ_id: int, student_id: int, request: Request,
                                     status: str = "present", notes: Optional[str] = None,
                                     require_feedback: bool = False,
                                     bypass_package: bool = False,
                                     db: Session = Depends(get_db)):
    o = db.query(ClassOccurrence).filter(ClassOccurrence.id == occ_id).first()
    if not o:
        raise HTTPException(status_code=404, detail="Occurrence not found")
    if require_feedback and status == "present" and not notes:
        raise HTTPException(status_code=400, detail="Feedback/notes are required before marking present")
    att = db.query(Attendance).filter(
        Attendance.session_id == occ_id, Attendance.student_id == student_id
    ).first()
    # Package gate: only when newly consuming a 'present' slot.
    # bypass_package=True lets teachers/admins mark any student present regardless of package.
    newly_present = status == "present" and (att is None or att.status != "present")
    if newly_present and not bypass_package:
        assert_can_book(db, student_id, o, count=1, is_makeup=bool(o.is_makeup))
    if att:
        att.status = status
        att.notes = notes
    else:
        db.add(Attendance(session_id=occ_id, student_id=student_id, status=status, notes=notes))
    db.commit()
    # Non-blocking advisories for the admin UI (overdue invoice, low sessions…).
    return {"message": "Attendance recorded", "warnings": student_warnings(db, student_id)}


# ── Optimized calendar (single query, pre-fetched maps to avoid N+1) ──

@app.get("/scheduling/calendar")
def scheduling_calendar(
    start: Optional[str] = None, end: Optional[str] = None, view: str = "week",
    teacher_id: Optional[int] = None, center_id: Optional[int] = None,
    room_id: Optional[int] = None, student_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current = Depends(require_roles("super_admin", "center_admin", "teacher", "student")),
):
    # Auto-scope center admins to their center; teachers to their center.
    caller_cal = current.get("obj")
    if caller_cal and getattr(caller_cal, "access_role", None) in ("center_admin", "teacher"):
        if not center_id and getattr(caller_cal, "center_id", None):
            center_id = caller_cal.center_id

    q = db.query(ClassOccurrence)
    if start:
        q = q.filter(ClassOccurrence.date >= start)
    if end:
        q = q.filter(ClassOccurrence.date <= end)
    if teacher_id:
        q = q.filter(ClassOccurrence.teacher_id == teacher_id)
    if room_id:
        q = q.filter(ClassOccurrence.room_id == room_id)

    # Restrict by center/student via the parent template (pre-resolve template ids).
    if center_id is not None or student_id is not None:
        tq = db.query(ClassTemplate.id)
        if center_id is not None:
            tq = tq.filter(ClassTemplate.center_id == center_id)
        if student_id is not None:
            enrolled = [e.template_id for e in db.query(Enrollment).filter(
                Enrollment.student_id == student_id, Enrollment.status == "active"
            ).all()]
            tq = tq.filter(ClassTemplate.id.in_(enrolled or [-1]))
        allowed = {row[0] for row in tq.all()}
        q = q.filter(ClassOccurrence.template_id.in_(allowed or [-1]))

    occs = q.order_by(ClassOccurrence.date, ClassOccurrence.start_time).all()

    # Pre-fetch maps (avoid N+1)
    template_ids = {o.template_id for o in occs if o.template_id}
    teacher_ids = {o.teacher_id for o in occs if o.teacher_id}
    occ_ids = [o.id for o in occs]
    templates = {t.id: t for t in db.query(ClassTemplate).filter(ClassTemplate.id.in_(template_ids or [-1])).all()}
    teachers = {s.id: s for s in db.query(Staff).filter(Staff.id.in_(teacher_ids or [-1])).all()}
    rooms = {r.id: r for r in db.query(Room).all()}
    # Per-occurrence roster = (template baseline − excludes) ∪ includes.
    # Prefetch baselines (per template) + overrides (per occurrence) — no N+1.
    baseline_by_template = {}   # tid -> set(student_id)
    overrides_by_occ = {}       # occ_id -> {"inc": set, "exc": set}
    stu_ids = set()
    if template_ids:
        for e in db.query(Enrollment).filter(
            Enrollment.template_id.in_(template_ids), Enrollment.status == "active"
        ).all():
            stu_ids.add(e.student_id)
            if e.occurrence_id is None:
                baseline_by_template.setdefault(e.template_id, set()).add(e.student_id)
            else:
                d = overrides_by_occ.setdefault(e.occurrence_id, {"inc": set(), "exc": set()})
                d["inc" if e.kind == "include" else "exc"].add(e.student_id)
    stu_map = {s.id: s for s in db.query(Student).filter(Student.id.in_(stu_ids or [-1])).all()}

    # Per-template occurrence counts → "is this a recurring class?" (>1 occurrence).
    from sqlalchemy import func as _func
    occ_count_by_tmpl = dict(
        db.query(ClassOccurrence.template_id, _func.count())
        .filter(ClassOccurrence.template_id.in_(template_ids or [-1]))
        .group_by(ClassOccurrence.template_id).all()
    ) if template_ids else {}

    def _roster_for(o):
        base = set(baseline_by_template.get(o.template_id, set()))
        ov = overrides_by_occ.get(o.id)
        ids = ((base - ov["exc"]) | ov["inc"]) if ov else base
        return [{"id": s.id, "first_name": s.first_name, "last_name": s.last_name}
                for sid in ids for s in [stu_map.get(sid)] if s]

    present_counts = {}
    if occ_ids:
        for a in db.query(Attendance).filter(
            Attendance.session_id.in_(occ_ids), Attendance.status == "present"
        ).all():
            present_counts[a.session_id] = present_counts.get(a.session_id, 0) + 1

    result = []
    for o in occs:
        t = templates.get(o.template_id)
        teacher = teachers.get(o.teacher_id)
        room = rooms.get(o.room_id)
        students = _roster_for(o)
        result.append({
            "id": o.id, "template_id": o.template_id,
            "name": t.name if t else None, "course": t.course if t else None,
            "date": o.date, "start_time": o.start_time, "end_time": o.end_time,
            "teacher_id": o.teacher_id, "teacher_name": teacher.name if teacher else None,
            "room_id": o.room_id, "room_name": room.name if room else None,
            "center_id": t.center_id if t else None,
            "status": o.status, "is_makeup": o.is_makeup, "is_modified": o.is_modified,
            "is_published": o.is_published,
            "is_recurring": occ_count_by_tmpl.get(o.template_id, 0) > 1,
            "capacity": t.capacity if t else None,
            "enrolled_count": len(students),
            "enrollment_count": len(students),
            "enrolled_students": students,
            "present_count": present_counts.get(o.id, 0),
            # Compatibility shape so the existing calendar cards/pills render unchanged.
            "batch": {
                "id": o.template_id, "name": t.name if t else None,
                "subject": t.course if t else None,
                "teacher_id": o.teacher_id,
                "teacher": {"name": teacher.name} if teacher else None,
                "capacity": t.capacity if t else None,
                "color_tag": _template_color(o.template_id, o.start_time),
            } if t else None,
        })
    return {"view": view, "count": len(result), "occurrences": result}


# ── Rooms + Holidays (support entities) ──

@app.get("/scheduling/rooms")
def list_rooms(center_id: Optional[int] = None, db: Session = Depends(get_db),
              current = Depends(require_roles("super_admin", "center_admin"))):
    q = db.query(Room).filter(Room.is_active == True)
    # Phase 1A: Center admin only sees their center's rooms
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(Room.center_id == current["obj"].center_id)
    elif center_id:
        q = q.filter(Room.center_id == center_id)
    return [{"id": r.id, "name": r.name, "center_id": r.center_id, "capacity": r.capacity} for r in q.all()]


@app.post("/scheduling/rooms")
async def create_room(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    r = Room(name=body["name"], center_id=body.get("center_id"), capacity=body.get("capacity"))
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"id": r.id, "name": r.name}


@app.get("/scheduling/holidays")
def list_holidays(center_id: Optional[int] = None, db: Session = Depends(get_db)):
    rows = db.query(Holiday).order_by(Holiday.date).all()
    return [{"id": h.id, "date": h.date, "name": h.name, "center_id": h.center_id}
            for h in rows if center_id is None or h.center_id is None or h.center_id == center_id]


@app.post("/scheduling/holidays")
async def create_holiday(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    h = Holiday(date=body["date"], name=body.get("name"), center_id=body.get("center_id"))
    db.add(h)
    db.commit()
    db.refresh(h)
    return {"id": h.id, "date": h.date}


# ==================== PAYMENT — Packages ====================

@app.get("/admin/packages")
def list_packages(db: Session = Depends(get_db),
                 current = Depends(require_roles("super_admin", "center_admin"))):
    import json as _json
    pkgs = db.query(Package).filter(Package.is_archived == False).all()
    result = []
    for p in pkgs:
        try:
            grades = _json.loads(p.applicable_grades) if isinstance(p.applicable_grades, str) else (p.applicable_grades or [])
        except Exception:
            grades = []
        try:
            courses = _json.loads(p.applicable_courses) if isinstance(p.applicable_courses, str) else (p.applicable_courses or [])
        except Exception:
            courses = []
        per_session = round((p.price or 0) / p.total_sessions, 2) if p.total_sessions else 0
        result.append({
            "id": p.id,
            "name": p.name,
            "applicable_grades": grades,
            "applicable_courses": courses,
            "validity_days": p.validity_days,
            "total_sessions": p.total_sessions,
            "session_duration_minutes": p.session_duration_minutes or 60,
            "makeup_sessions": p.makeup_sessions or 0,
            "makeup_validity_days": p.makeup_validity_days,
            "cancellation_window_hours": p.cancellation_window_hours if p.cancellation_window_hours is not None else 24,
            "prorate_enabled": p.prorate_enabled,
            "price": p.price,
            "per_session_fee": per_session,
            "tax_percentage": p.tax_percentage,
            "is_published": p.is_published,
            "is_archived": p.is_archived,
            "description": p.description or "",
        })
    return result


def _validate_package_fields(total_sessions, validity_days):
    """A package must define a positive class count and validity period."""
    if total_sessions is None or int(total_sessions) < 1:
        raise HTTPException(status_code=400, detail="total_sessions must be at least 1")
    if validity_days is None or int(validity_days) < 1:
        raise HTTPException(status_code=400, detail="validity_days must be at least 1")


@app.post("/admin/packages")
async def create_package(request: Request, db: Session = Depends(get_db),
                        current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    import json
    _validate_package_fields(body.get("total_sessions", 8), body.get("validity_days", 30))
    pkg = Package(
        name=body["name"],
        applicable_grades=json.dumps(body.get("applicable_grades", [])),
        applicable_courses=json.dumps(body.get("applicable_courses", [])),
        validity_days=body.get("validity_days", 30),
        total_sessions=body.get("total_sessions", 8),
        session_duration_minutes=body.get("session_duration_minutes", 60),
        makeup_sessions=body.get("makeup_sessions", 0),
        makeup_validity_days=body.get("makeup_validity_days"),
        cancellation_window_hours=body.get("cancellation_window_hours", 24),
        prorate_enabled=body.get("prorate_enabled", False),
        price=float(body["price"]),
        tax_percentage=float(body.get("tax_percentage", 18)),
        is_published=body.get("is_published", False),
        description=body.get("description"),
    )
    db.add(pkg)
    db.commit()
    # Phase 4A: Audit package.created
    audit(db, "package.created", subject=("staff", current["id"]), request=request,
          detail={"package_id": pkg.id, "name": pkg.name, "price": float(pkg.price)})
    db.refresh(pkg)
    return {"id": pkg.id, "message": "Package created"}


@app.put("/admin/packages/{pkg_id}")
async def update_package(pkg_id: int, request: Request, db: Session = Depends(get_db),
                        current = Depends(require_roles("super_admin", "center_admin"))):
    import json
    body = await request.json()
    pkg = db.query(Package).filter(Package.id == pkg_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found")
    _validate_package_fields(
        body.get("total_sessions", pkg.total_sessions),
        body.get("validity_days", pkg.validity_days),
    )
    for field in ["name", "validity_days", "total_sessions", "session_duration_minutes",
                  "makeup_sessions", "makeup_validity_days", "cancellation_window_hours",
                  "prorate_enabled", "price", "tax_percentage", "is_published", "is_archived", "description"]:
        if field in body:
            setattr(pkg, field, body[field])
    if "applicable_grades" in body:
        pkg.applicable_grades = json.dumps(body["applicable_grades"])
    if "applicable_courses" in body:
        pkg.applicable_courses = json.dumps(body["applicable_courses"])
    db.commit()
    # Phase 4A: Audit package.updated
    audit(db, "package.updated", subject=("staff", current["id"]), request=request,
          detail={"package_id": pkg.id, "name": pkg.name})
    return {"message": "Updated"}


# ==================== Student Package Lifecycle (Admin) ====================

@app.get("/admin/student/{student_id}/packages")
def admin_list_student_packages(student_id: int, db: Session = Depends(get_db)):
    """All packages assigned to a student with live used/remaining/status."""
    rows = (
        db.query(StudentPackage)
        .filter(StudentPackage.student_id == student_id)
        .order_by(StudentPackage.created_at.desc())
        .all()
    )
    return [resolve_package_state(db, sp) for sp in rows]


@app.post("/admin/student/{student_id}/assign-package")
async def admin_assign_package(student_id: int, request: Request, db: Session = Depends(get_db),
                              current = Depends(require_roles("super_admin", "center_admin"))):
    """Assign a package to a student directly (no payment). Supersedes any
    existing active package so there is only ever one active per student."""
    from datetime import date, timedelta
    body = await request.json()
    package_id = body.get("package_id")
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    # Phase 2B: Center admin can only assign packages to students in their center
    if current.get("obj").access_role == "center_admin" and student.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Cannot assign packages to students outside your center")
    pkg = db.query(Package).filter(Package.id == package_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found")

    start = body.get("start_date") or str(date.today())
    if body.get("end_date"):
        end = body["end_date"]
    else:
        from datetime import datetime as _dt
        start_d = _dt.strptime(start, "%Y-%m-%d").date()
        end = str(start_d + timedelta(days=pkg.validity_days or 30))

    for old in db.query(StudentPackage).filter(
        StudentPackage.student_id == student_id,
        StudentPackage.status == "active",
    ).all():
        old.status = "cancelled"

    sp = StudentPackage(
        student_id=student_id,
        package_id=package_id,
        start_date=start,
        end_date=end,
        sessions_used=0,
        makeup_used=0,
        status="active",
    )
    db.add(sp)
    db.commit()
    # Phase 4A: Audit package.assigned
    audit(db, "package.assigned", subject=("staff", current["id"]), request=request,
          detail={"package_id": package_id, "student_id": student_id, "center_id": student.center_id})
    db.refresh(sp)
    return resolve_package_state(db, sp)


@app.post("/admin/student-packages/{sp_id}/extend")
async def admin_extend_package(sp_id: int, request: Request, db: Session = Depends(get_db)):
    """Extend validity by N days (`extra_days`) or set an explicit `end_date`.
    Re-activates an expired package if the new end date is in the future."""
    from datetime import datetime as _dt, timedelta, date as _date
    body = await request.json()
    sp = db.query(StudentPackage).filter(StudentPackage.id == sp_id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Student package not found")

    if body.get("end_date"):
        sp.end_date = body["end_date"]
    elif body.get("extra_days"):
        base = sp.end_date or str(_date.today())
        new_end = _dt.strptime(base, "%Y-%m-%d").date() + timedelta(days=int(body["extra_days"]))
        sp.end_date = str(new_end)
    else:
        raise HTTPException(status_code=400, detail="Provide extra_days or end_date")

    # Re-activate if it had lapsed but is now valid again and not exhausted.
    if sp.status in ("expired", "exhausted"):
        state = resolve_package_state(db, sp)
        if state["effective_status"] == "active":
            sp.status = "active"
    db.commit()
    db.refresh(sp)
    return resolve_package_state(db, sp)



@app.post("/admin/student-packages/{sp_id}/cancel")
def admin_cancel_package(sp_id: int, db: Session = Depends(get_db)):
    sp = db.query(StudentPackage).filter(StudentPackage.id == sp_id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Student package not found")
    sp.status = "cancelled"
    db.commit()
    return {"message": "Package cancelled"}


# ==================== PAYMENT — Invoices ====================

@app.get("/admin/invoices")
def list_invoices(status: Optional[str] = None, center_id: Optional[int] = None, page: Optional[int] = None, limit: int = 50,
                  db: Session = Depends(get_db),
                  current = Depends(require_roles("super_admin", "center_admin"))):
    """List invoices. Phase 6: Paginated if page param provided, else returns array."""
    q = db.query(Invoice)
    if status and status != "all":
        q = q.filter(Invoice.status == status)
    # Phase 2B: Use direct center_id column instead of subquery
    # Center admin only sees their center's invoices
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(Invoice.center_id == current["obj"].center_id)
    elif center_id:
        q = q.filter(Invoice.center_id == center_id)

    students_map = {s.id: s for s in db.query(Student).all()}

    def format_invoice(inv):
        student = students_map.get(inv.student_id)
        return {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "student_id": inv.student_id,
            "student_name": f"{student.first_name} {student.last_name}" if student else "Unknown",
            "grade": student.current_grade if student else None,
            "course": student.desired_course if student else None,
            "amount": inv.amount,
            "tax_amount": inv.tax_amount,
            "discount_amount": inv.discount_amount,
            "total_amount": inv.total_amount,
            "paid_amount": inv.paid_amount,
            "status": inv.status,
            "payment_type": inv.payment_type,
            "payment_mode": inv.payment_mode,
            "description": inv.description,
            "due_date": inv.due_date,
            "issue_date": inv.issue_date,
            "paid_date": inv.paid_date,
            "sessions_count": inv.sessions_count,
            "attendance_sessions": inv.attendance_sessions,
        }

    # If no page param, return array (backward compatibility)
    if page is None:
        invoices = q.order_by(Invoice.created_at.desc()).all()
        return [format_invoice(inv) for inv in invoices]

    # If page param provided, return paginated response
    total = q.count()
    invoices = q.order_by(Invoice.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [format_invoice(inv) for inv in invoices],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


# ── Invoicing helpers ──

def _money(v):
    return f"₹{float(v or 0):,.2f}"


def _invoice_detail(db, inv):
    student = db.query(Student).filter(Student.id == inv.student_id).first()
    items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == inv.id).all()
    insts = db.query(InvoiceInstallment).filter(InvoiceInstallment.invoice_id == inv.id).order_by(InvoiceInstallment.seq).all()
    pays = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == inv.id).order_by(InvoicePayment.id).all()
    return {
        "id": inv.id, "invoice_number": inv.invoice_number,
        "student_id": inv.student_id,
        "student_name": f"{student.first_name} {student.last_name}" if student else "Unknown",
        "student_email": student.email if student else None,
        "student_phone": student.primary_phone_number if student else None,
        "student_address": student.address if student else None,
        "amount": inv.amount, "tax_amount": inv.tax_amount, "discount_amount": inv.discount_amount,
        "total_amount": inv.total_amount, "paid_amount": inv.paid_amount,
        "balance": round((inv.total_amount or 0) - (inv.paid_amount or 0), 2),
        "status": inv.status, "has_installments": inv.has_installments,
        "discount_percentage": inv.discount_percentage, "template_id": inv.template_id,
        "issue_date": inv.issue_date, "due_date": inv.due_date, "paid_date": inv.paid_date,
        "notes": inv.notes, "internal_notes": inv.internal_notes, "description": inv.description,
        "items": [{"id": i.id, "package_id": i.package_id, "label": i.label, "description": i.description,
                   "quantity": i.quantity, "unit_price": i.unit_price, "valid_till": i.valid_till,
                   "amount": i.amount} for i in items],
        "installments": [{"id": x.id, "seq": x.seq, "due_date": x.due_date, "amount": x.amount,
                          "paid_amount": x.paid_amount, "status": x.status} for x in insts],
        "payments": [{"id": p.id, "amount": p.amount, "method": p.method, "reference": p.reference,
                      "paid_date": p.paid_date, "installment_id": p.installment_id} for p in pays],
    }


def _org_settings(db):
    rows = {s.key: s.value for s in db.query(AppSetting).filter(AppSetting.key.like("org.%")).all()}
    return {k: rows.get(f"org.{k}", "") for k in _ORG_KEYS}


def _org_settings_for_center(db, center_id=None):
    """Return org settings merged with per-center overrides (center values win)."""
    base = _org_settings(db)
    if not center_id:
        return base
    prefix = f"center_{center_id}_org."
    rows = {s.key[len(prefix):]: s.value
            for s in db.query(AppSetting).filter(AppSetting.key.like(f"{prefix}%")).all()}
    return {k: (rows[k] if rows.get(k) not in (None, "") else base.get(k, "")) for k in _ORG_KEYS}


def _invoice_html(detail, kind="invoice", org=None):
    """Render the academy invoice in the reference layout (logo, BILL FROM/TO,
    GSTIN, line-item table with Discount/Tax, Validity, totals, Notes)."""
    org = org or {}
    academy = org.get("academy_name") or "Vama Academy for Music & Performing Arts"
    logo = (f"<img src='{org['logo_url']}' alt='logo' style='max-height:64px'>"
            if org.get("logo_url") else
            f"<div style='font-size:26px;font-weight:900;letter-spacing:2px;color:#c0392b'>VAMA</div>"
            f"<div style='font-size:9px;letter-spacing:1px;color:#555'>ACADEMY FOR MUSIC &amp; PERFORMING ARTS</div>")

    def _line(i):
        v = f"<div style='font-size:12px'><b>Validity</b> {i['valid_till']}</div>" if i.get("valid_till") else ""
        desc = f"<div style='color:#555;font-size:12px'>{i['description']}</div>" if i.get("description") else ""
        return (f"<tr style='border-bottom:1px solid #eee'>"
                f"<td style='padding:10px 6px;vertical-align:top'><b>{i['label']}</b>{desc}{v}</td>"
                f"<td style='padding:10px 6px;text-align:right;vertical-align:top'>{i['quantity']}</td>"
                f"<td style='padding:10px 6px;text-align:right;vertical-align:top'>INR {i['unit_price']:.2f}</td>"
                f"<td style='padding:10px 6px;text-align:right;vertical-align:top'>- INR {(0.0):.2f}</td>"
                f"<td style='padding:10px 6px;text-align:right;vertical-align:top'>INR {(0.0):.2f}</td>"
                f"<td style='padding:10px 6px;text-align:right;vertical-align:top'>INR {i['amount']:,.2f}</td></tr>")
    rows = "".join(_line(i) for i in detail["items"])

    inst = ""
    if detail.get("installments"):
        inst = ("<div style='margin-top:14px'><b style='font-size:12px'>Installment plan</b>"
                "<table style='width:100%;border-collapse:collapse;font-size:12px;margin-top:6px'>"
                + "".join(f"<tr><td style='padding:4px 0'>#{x['seq']} · due {x['due_date']}</td>"
                          f"<td style='text-align:right'>INR {x['amount']:,.2f} ({x['status']})</td></tr>"
                          for x in detail["installments"]) + "</table></div>")

    notes_text = detail.get("notes") or org.get("invoice_notes") or ""
    notes = (f"<div style='margin-top:26px'><div style='font-size:12px;color:#333'><b>Notes:</b></div>"
             f"<div style='color:#475569;font-size:12px;white-space:pre-wrap;margin-top:6px'>{notes_text}</div></div>") if notes_text else ""

    title = {"invoice": "Invoice", "reminder": "Payment Reminder", "receipt": "Payment Receipt"}[kind]
    # "Pay online" link (Razorpay) — shown only when there is a balance and we're
    # not rendering a receipt. Links to the public payment page.
    pay_button = ""
    if kind != "receipt" and (detail.get("balance") or 0) > 0:
        import os as _os
        pay_url = f"{_os.getenv('FRONTEND_URL', 'http://localhost:5173').rstrip('/')}/pay/{detail['id']}"
        pay_button = (f"<div style='margin-top:18px'><a href='{pay_url}' "
                      f"style='display:inline-block;background:#463a7a;color:#fff;text-decoration:none;"
                      f"font-weight:700;padding:12px 28px;border-radius:8px;font-size:14px'>Pay online</a></div>")
    paid_stamp = ("<div style='display:inline-block;margin-top:8px;border:2px solid #16a34a;color:#16a34a;"
                  "font-weight:800;font-size:12px;letter-spacing:2px;padding:3px 10px;border-radius:6px'>PAID</div>"
                  if (detail.get("balance") or 0) <= 0 and (detail.get("paid_amount") or 0) > 0 else "")
    gst = f"<div>GST {org['gst_number']}</div>" if org.get("gst_number") else ""
    website = f"<div>{org['website']}</div>" if org.get("website") else ""
    bill_to = (f"<b>{detail['student_name']}</b><br>{detail.get('student_address','') or ''}"
               f"{('<br>'+detail['student_phone']) if detail.get('student_phone') else ''}"
               f"{('<br>'+detail['student_email']) if detail.get('student_email') else ''}")

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:auto;color:#1a1a1a;padding:24px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div><div style="font-size:32px;font-weight:800">{title}</div>{paid_stamp}</div>
        <div style="text-align:right">{logo}</div>
      </div>
      <table style="margin-top:24px;font-size:13px"><tr><td style="color:#777;padding-right:24px">Invoice number</td><td><b>{detail['invoice_number']}</b></td></tr>
        <tr><td style="color:#777">Date of issue</td><td><b>{detail['issue_date']}</b></td></tr>
        <tr><td style="color:#777">Due date</td><td><b>{detail['due_date']}</b></td></tr></table>

      <div style="display:flex;gap:40px;margin-top:28px;font-size:12px">
        <div style="flex:1"><div style="color:#888;letter-spacing:1px;margin-bottom:6px">BILL FROM</div>
          <b>{academy}</b><br>{org.get('address','').replace(chr(10),'<br>')}<br>{org.get('phone','')}<br>{org.get('email','')}<br>{website}{gst}</div>
        <div style="flex:1"><div style="color:#888;letter-spacing:1px;margin-bottom:6px">BILL TO</div>{bill_to}</div>
      </div>

      <table style="width:100%;border-collapse:collapse;margin-top:30px;font-size:12px">
        <tr style="border-bottom:1px solid #ccc;color:#888"><th style="text-align:left;padding:6px">DESCRIPTION</th>
          <th style="text-align:right;padding:6px">QTY</th><th style="text-align:right;padding:6px">UNIT PRICE</th>
          <th style="text-align:right;padding:6px">DISCOUNT</th><th style="text-align:right;padding:6px">TAX</th>
          <th style="text-align:right;padding:6px">AMOUNT</th></tr>
        {rows}
      </table>

      <table style="width:50%;margin-left:50%;margin-top:18px;font-size:13px;border-collapse:collapse">
        <tr><td style="padding:4px 0;color:#555">Subtotal</td><td style="text-align:right">INR {detail['amount']:,.2f}</td></tr>
        <tr><td style="padding:4px 0;color:#555">Tax</td><td style="text-align:right">INR {detail['tax_amount']:,.2f}</td></tr>
        <tr><td style="padding:4px 0;color:#555">Discount</td><td style="text-align:right">- INR {detail['discount_amount']:,.2f}</td></tr>
        <tr><td style="padding:4px 0;color:#555">Rounded off</td><td style="text-align:right">INR 0.00</td></tr>
        <tr style="border-top:1px solid #ccc"><td style="padding:8px 0;font-weight:800">Total</td><td style="text-align:right;font-weight:800">INR {detail['total_amount']:,.2f}</td></tr>
        <tr><td style="padding:4px 0;color:#555">Paid</td><td style="text-align:right">- INR {detail['paid_amount']:,.2f}</td></tr>
        <tr style="border-top:1px solid #ccc"><td style="padding:8px 0;font-weight:800">Amount due</td><td style="text-align:right;font-weight:800">INR {detail['balance']:,.2f}</td></tr>
      </table>
      {pay_button}
      {inst}
      {notes}
    </div>"""


def _maybe_email_invoice(db, inv, kind, to_email=None, do_send=True):
    if not do_send:
        return False
    detail = _invoice_detail(db, inv)
    to = to_email or detail["student_email"]
    if not to:
        return False
    org = _org_settings_for_center(db, getattr(inv, "center_id", None))
    academy = org.get("academy_name") or "Vama Academy"
    subj = {"invoice": f"Invoice {inv.invoice_number} — {academy}",
            "reminder": f"Payment Reminder: {inv.invoice_number} — ₹{int(detail.get('balance', 0)):,} due — {academy}",
            "receipt": f"Payment Receipt {inv.invoice_number} — {academy}"}[kind]
    html = _reminder_html(detail, org) if kind == "reminder" else _invoice_html(detail, kind, org)
    return send_email(to, subj, html)


def _recompute_invoice_status(inv):
    from datetime import date as _dd
    paid = round(inv.paid_amount or 0, 2)
    total = round(inv.total_amount or 0, 2)
    if paid >= total and total > 0:
        inv.status = "paid"
        inv.paid_date = inv.paid_date or str(_dd.today())
    elif paid > 0:
        inv.status = "partial"
    else:
        today = str(_dd.today())
        if inv.due_date and inv.due_date < today:
            inv.status = "overdue"
        else:
            inv.status = "pending"


def _reminder_html(detail, org=None):
    """Standalone payment-reminder email — distinct urgent design, not the full invoice layout."""
    org = org or {}
    academy = org.get("academy_name") or "Vama Academy for Music & Performing Arts"
    logo_name = academy.split()[0].upper()
    inv_num = detail["invoice_number"]
    student = detail["student_name"]
    amount_due = detail["balance"]
    due_date = detail.get("due_date") or "—"
    pay_url = ""
    if amount_due > 0:
        import os as _os
        base = _os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
        pay_url = f"{base}/pay/{detail['id']}"

    items_rows = "".join(
        f"<tr><td style='padding:10px 0;border-bottom:1px solid #f1f5f9;font-size:13px;color:#475569'>{i['label']}"
        f"{'<br><span style=font-size:11px;color:#94a3b8>' + i['description'] + '</span>' if i.get('description') else ''}</td>"
        f"<td style='padding:10px 0;border-bottom:1px solid #f1f5f9;text-align:right;font-size:13px;font-weight:700;color:#1e293b'>₹{i['amount']:,.0f}</td></tr>"
        for i in detail["items"]
    )
    pay_block = (
        f"<div style='margin-top:28px;text-align:center'>"
        f"<a href='{pay_url}' style='display:inline-block;background:#dc2626;color:#fff;text-decoration:none;"
        f"font-weight:800;padding:14px 36px;border-radius:10px;font-size:15px;letter-spacing:0.3px'>Pay ₹{amount_due:,.0f} Now</a>"
        f"<p style='margin-top:10px;font-size:11px;color:#94a3b8'>Secure online payment via our payment portal</p></div>"
    ) if pay_url else ""

    return f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;max-width:600px;margin:0 auto;background:#fff">
  <!-- Header -->
  <div style="background:linear-gradient(135deg,#7f1d1d,#dc2626);padding:36px 40px;border-radius:16px 16px 0 0">
    <div style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <div style="font-size:11px;letter-spacing:2px;color:#fca5a5;font-weight:700;text-transform:uppercase">Payment Reminder</div>
        <div style="font-size:28px;font-weight:900;color:#fff;margin-top:4px;letter-spacing:-0.5px">{logo_name}</div>
      </div>
      <div style="background:rgba(255,255,255,0.15);border-radius:12px;padding:10px 18px;text-align:center">
        <div style="font-size:10px;color:#fca5a5;font-weight:700;letter-spacing:1px;text-transform:uppercase">Amount Due</div>
        <div style="font-size:26px;font-weight:900;color:#fff;margin-top:2px">₹{amount_due:,.0f}</div>
      </div>
    </div>
  </div>
  <!-- Body -->
  <div style="padding:36px 40px;background:#fff;border:1px solid #f1f5f9;border-top:none">
    <p style="font-size:15px;color:#374151;line-height:1.7;margin:0 0 20px">
      Dear <strong>{student}</strong>,
    </p>
    <p style="font-size:14px;color:#6b7280;line-height:1.7;margin:0 0 24px">
      This is a friendly reminder that payment for invoice <strong style="color:#1e293b">{inv_num}</strong>
      is outstanding. Please settle the balance at your earliest convenience to avoid any disruption to your classes.
    </p>
    <!-- Info box -->
    <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:20px 24px;margin-bottom:28px">
      <table style="width:100%;border-collapse:collapse">
        <tr>
          <td style="font-size:12px;color:#6b7280;padding:4px 0">Invoice Number</td>
          <td style="font-size:13px;font-weight:700;color:#1e293b;text-align:right;padding:4px 0">{inv_num}</td>
        </tr>
        <tr>
          <td style="font-size:12px;color:#6b7280;padding:4px 0">Due Date</td>
          <td style="font-size:13px;font-weight:700;color:#dc2626;text-align:right;padding:4px 0">{due_date}</td>
        </tr>
        <tr>
          <td style="font-size:12px;color:#6b7280;padding:4px 0">Total Amount</td>
          <td style="font-size:13px;font-weight:700;color:#1e293b;text-align:right;padding:4px 0">₹{detail['total_amount']:,.0f}</td>
        </tr>
        <tr>
          <td style="font-size:12px;color:#6b7280;padding:4px 0">Amount Paid</td>
          <td style="font-size:13px;font-weight:700;color:#16a34a;text-align:right;padding:4px 0">₹{detail['paid_amount']:,.0f}</td>
        </tr>
        <tr style="border-top:1px solid #fecaca">
          <td style="font-size:13px;font-weight:800;color:#dc2626;padding:10px 0 4px">Balance Due</td>
          <td style="font-size:16px;font-weight:900;color:#dc2626;text-align:right;padding:10px 0 4px">₹{amount_due:,.0f}</td>
        </tr>
      </table>
    </div>
    <!-- Line items -->
    {"<div style='margin-bottom:28px'><div style='font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#94a3b8;margin-bottom:8px'>Invoice Details</div><table style='width:100%;border-collapse:collapse'>" + items_rows + "</table></div>" if items_rows else ""}
    {pay_block}
    <p style="font-size:13px;color:#6b7280;line-height:1.7;margin:28px 0 0">
      If you have already made this payment, please disregard this reminder.
      For any queries, please contact us and we will be happy to assist you.
    </p>
  </div>
  <!-- Footer -->
  <div style="background:#f8fafc;padding:24px 40px;border-radius:0 0 16px 16px;border:1px solid #f1f5f9;border-top:none;text-align:center">
    <p style="font-size:12px;color:#94a3b8;margin:0">{academy}</p>
    {"<p style='font-size:11px;color:#94a3b8;margin:4px 0 0'>" + org.get("email","") + " · " + org.get("phone","") + "</p>" if org.get("email") or org.get("phone") else ""}
    <p style="font-size:10px;color:#cbd5e1;margin:8px 0 0">You received this reminder because you have an outstanding balance.</p>
  </div>
</div>"""


# ── Invoice CREATION (line items from packages) ──

@app.post("/admin/invoices")
async def create_invoice(request: Request, db: Session = Depends(get_db),
                        current = Depends(require_roles("super_admin", "center_admin"))):
    """Create an invoice with line items. body:
       { student_id, issue_date, due_date, items:[{package_id?,label,description,quantity,unit_price,valid_till}],
         discount_amount?, tax_amount? | tax_percentage?, coupon_code?, notes?,
         installments:[{due_date, amount}]?, send_email? }"""
    import random, string
    from datetime import date as _dd
    body = await request.json()
    items = body.get("items") or []
    if not body.get("student_id") or not items:
        raise HTTPException(status_code=400, detail="student_id and at least one item are required")

    # Phase 2B: Fetch student and validate center access
    student = db.query(Student).filter(Student.id == int(body["student_id"])).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if current.get("obj").access_role == "center_admin" and student.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Cannot create invoice for students outside your center")

    subtotal = 0.0
    for it in items:
        qty = int(it.get("quantity", 1) or 1)
        price = float(it.get("unit_price", 0) or 0)
        it["_amount"] = round(qty * price, 2)
        subtotal += it["_amount"]
    # Discount is percentage-based (coupons removed). Fall back to amount if given.
    disc_pct = float(body.get("discount_percentage", 0) or 0)
    if disc_pct > 0:
        discount = round(subtotal * disc_pct / 100, 2)
    else:
        discount = float(body.get("discount_amount", 0) or 0)
    if "tax_amount" in body and body["tax_amount"] not in (None, ""):
        tax = float(body["tax_amount"])
    else:
        tax = round((subtotal - discount) * float(body.get("tax_percentage", 0) or 0) / 100, 2)
    total = round(subtotal + tax - discount, 2)
    inv_num = body.get("invoice_number") or ("INV-" + "".join(random.choices(string.digits, k=5)))

    # Notes default to the academy's standard Welcome + T&C (single default
    # template — no per-invoice template selection).
    template_id = body.get("template_id")
    notes = body.get("notes") or _org_settings(db).get("invoice_notes")

    inst_list = body.get("installments") or []
    first_pkg_id = items[0].get("package_id") if items else None
    inv = Invoice(
        invoice_number=inv_num, student_id=int(body["student_id"]),
        package_id=int(first_pkg_id) if first_pkg_id else None,
        amount=round(subtotal, 2), tax_amount=tax, discount_amount=discount, total_amount=total,
        status="pending", payment_type=(items[0].get("label") if items else None),
        description=notes or (items[0].get("description") if items else None),
        due_date=body.get("due_date") or str(_dd.today()),
        issue_date=body.get("issue_date") or str(_dd.today()),
        discount_percentage=disc_pct, template_id=template_id,
        has_installments=bool(inst_list), notes=notes, paid_amount=0.0,
        center_id=student.center_id,
    )
    db.add(inv)
    db.flush()
    for it in items:
        db.add(InvoiceItem(
            invoice_id=inv.id, package_id=it.get("package_id"), label=it.get("label") or "Item",
            description=it.get("description"), quantity=int(it.get("quantity", 1) or 1),
            unit_price=float(it.get("unit_price", 0) or 0), valid_till=it.get("valid_till"),
            amount=it["_amount"]))
    for i, ins in enumerate(inst_list, start=1):
        db.add(InvoiceInstallment(invoice_id=inv.id, seq=i, due_date=ins.get("due_date"),
                                  amount=float(ins.get("amount", 0) or 0)))
    db.commit()
    # Phase 4A: Audit invoice.created
    audit(db, "invoice.created", subject=("staff", current["id"]), request=request,
          detail={"invoice_id": inv.id, "student_id": inv.student_id, "center_id": student.center_id, "amount": inv.total_amount})
    db.refresh(inv)
    emailed = _maybe_email_invoice(db, inv, "invoice", body.get("to_email"), bool(body.get("send_email")))
    return {"id": inv.id, "invoice_number": inv_num, "emailed": emailed}


@app.get("/admin/invoices/{inv_id}")
def get_invoice(inv_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return _invoice_detail(db, inv)


@app.get("/admin/invoices/{inv_id}/html", response_class=HTMLResponse)
def invoice_html(inv_id: int, db: Session = Depends(get_db)):
    """Full printable invoice (the default academy layout) — open + Ctrl/Cmd-P
    to save as PDF. This is the single invoice format; there is no choice."""
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    body = _invoice_html(_invoice_detail(db, inv), "invoice", _org_settings_for_center(db, inv.center_id))
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Invoice {inv.invoice_number}</title></head><body>{body}</body></html>"


@app.get("/student/invoices/{inv_id}/html", response_class=HTMLResponse)
def student_invoice_html(inv_id: int, db: Session = Depends(get_db)):
    """Same printable invoice for the student portal — open in browser, Ctrl/Cmd-P to save PDF."""
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    body = _invoice_html(_invoice_detail(db, inv), "invoice", _org_settings_for_center(db, inv.center_id))
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Invoice {inv.invoice_number}</title></head><body>{body}</body></html>"


@app.patch("/admin/invoices/{inv_id}")
async def update_invoice(inv_id: int, request: Request, db: Session = Depends(get_db),
                        current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    # Phase 2B: Center admin can only update invoices for their center
    if current.get("obj").access_role == "center_admin" and inv.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Cannot update invoices outside your center")
    for field in ["status", "paid_date", "paid_amount", "notes", "internal_notes", "due_date", "issue_date"]:
        if field in body:
            setattr(inv, field, body[field])
    if body.get("status") == "paid":
        inv.paid_amount = inv.total_amount
    db.commit()
    # Phase 4A: Audit invoice.updated
    audit(db, "invoice.updated", subject=("staff", current["id"]), request=request,
          detail={"invoice_id": inv.id, "center_id": inv.center_id})
    return {"message": "Updated"}


@app.put("/admin/invoices/{inv_id}")
async def replace_invoice(inv_id: int, request: Request, db: Session = Depends(get_db),
                         current = Depends(require_roles("super_admin", "center_admin"))):
    """Full edit — replace line items, discount, tax, dates and notes, then
    recompute totals. Recorded payments are preserved and status is recomputed."""
    body = await request.json()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    # Phase 2B: Center admin can only update invoices for their center
    if current.get("obj").access_role == "center_admin" and inv.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Cannot update invoices outside your center")

    items = body.get("items")
    if items is not None:
        if not items:
            raise HTTPException(status_code=400, detail="At least one item is required")
        db.query(InvoiceItem).filter(InvoiceItem.invoice_id == inv.id).delete()
        subtotal = 0.0
        for it in items:
            qty = int(it.get("quantity", 1) or 1)
            price = float(it.get("unit_price", 0) or 0)
            amt = round(qty * price, 2)
            subtotal += amt
            db.add(InvoiceItem(invoice_id=inv.id, package_id=it.get("package_id"),
                               label=it.get("label") or "Item", description=it.get("description"),
                               quantity=qty, unit_price=price, valid_till=it.get("valid_till"), amount=amt))
        disc_pct = float(body.get("discount_percentage", inv.discount_percentage or 0) or 0)
        discount = round(subtotal * disc_pct / 100, 2) if disc_pct else float(body.get("discount_amount", 0) or 0)
        tax = round((subtotal - discount) * float(body.get("tax_percentage", 0) or 0) / 100, 2) if "tax_percentage" in body else (inv.tax_amount or 0)
        inv.amount = round(subtotal, 2)
        inv.discount_percentage = disc_pct
        inv.discount_amount = discount
        inv.tax_amount = tax
        inv.total_amount = round(subtotal + tax - discount, 2)

    for field in ["due_date", "issue_date", "notes", "internal_notes"]:
        if field in body:
            setattr(inv, field, body[field])
    _recompute_invoice_status(inv)
    db.commit()
    # Phase 4A: Audit invoice.updated
    audit(db, "invoice.updated", subject=("staff", current["id"]), request=request,
          detail={"invoice_id": inv.id, "center_id": inv.center_id})
    db.refresh(inv)
    return _invoice_detail(db, inv)


@app.delete("/admin/invoices/{inv_id}")
def delete_invoice(inv_id: int, request: Request, db: Session = Depends(get_db),
                  current = Depends(require_roles("super_admin", "center_admin"))):
    """Permanently delete an invoice and its items, installments and payments."""
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    # Phase 2B: Center admin can only delete invoices for their center
    if current.get("obj").access_role == "center_admin" and inv.center_id != current["obj"].center_id:
        raise HTTPException(status_code=403, detail="Cannot delete invoices outside your center")
    inv_center_id = inv.center_id
    db.delete(inv)   # cascades to items / installments / payments
    db.commit()
    # Phase 4A: Audit invoice.deleted
    audit(db, "invoice.deleted", subject=("staff", current["id"]), request=request,
          detail={"invoice_id": inv_id, "center_id": inv_center_id})
    return {"message": "Invoice deleted"}


@app.delete("/admin/invoices/{inv_id}/payments/{pay_id}")
def delete_payment(inv_id: int, pay_id: int, db: Session = Depends(get_db)):
    """Delete a recorded payment and roll back the invoice balance/status."""
    p = db.query(InvoicePayment).filter(InvoicePayment.id == pay_id, InvoicePayment.invoice_id == inv_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    inv.paid_amount = max(0.0, round((inv.paid_amount or 0) - p.amount, 2))
    if p.installment_id:
        ins = db.query(InvoiceInstallment).filter(InvoiceInstallment.id == p.installment_id).first()
        if ins:
            ins.paid_amount = max(0.0, round((ins.paid_amount or 0) - p.amount, 2))
            ins.status = "paid" if ins.paid_amount >= ins.amount else ("partial" if ins.paid_amount > 0 else "pending")
    db.delete(p)
    _recompute_invoice_status(inv)
    db.commit()
    db.refresh(inv)
    return {"message": "Payment deleted", "status": inv.status,
            "paid_amount": inv.paid_amount, "balance": round(inv.total_amount - inv.paid_amount, 2)}


# ── Payment RECORDING (separate from creation) ──

@app.post("/admin/invoices/{inv_id}/record-payment")
async def record_payment(inv_id: int, request: Request, db: Session = Depends(get_db)):
    """Record a payment. body: { amount, method, reference?, paid_date?, installment_id?, send_receipt? }"""
    from datetime import date as _dd
    body = await request.json()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    amount = float(body.get("amount") or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")
    pay = InvoicePayment(
        invoice_id=inv.id, installment_id=body.get("installment_id"), amount=amount,
        method=body.get("method"), reference=body.get("reference"),
        paid_date=body.get("paid_date") or str(_dd.today()), notes=body.get("notes"))
    db.add(pay)
    inv.paid_amount = round((inv.paid_amount or 0) + amount, 2)
    inv.payment_mode = body.get("method") or inv.payment_mode
    if body.get("installment_id"):
        ins = db.query(InvoiceInstallment).filter(InvoiceInstallment.id == body["installment_id"]).first()
        if ins:
            ins.paid_amount = round((ins.paid_amount or 0) + amount, 2)
            ins.status = "paid" if ins.paid_amount >= ins.amount else "partial"
    _recompute_invoice_status(inv)
    db.commit()
    db.refresh(inv)
    emailed = _maybe_email_invoice(db, inv, "receipt", body.get("to_email"), bool(body.get("send_receipt")))
    return {"message": "Payment recorded", "status": inv.status,
            "paid_amount": inv.paid_amount, "balance": round(inv.total_amount - inv.paid_amount, 2),
            "receipt_emailed": emailed}


@app.post("/admin/invoices/{inv_id}/send")
async def send_invoice(inv_id: int, request: Request, db: Session = Depends(get_db)):
    """Email an invoice or reminder. body: { kind: invoice|reminder, to_email? }"""
    body = await request.json()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    kind = body.get("kind", "invoice")
    if kind not in ("invoice", "reminder", "receipt"):
        kind = "invoice"
    ok = _maybe_email_invoice(db, inv, kind, body.get("to_email"), True)
    if not ok:
        raise HTTPException(status_code=400, detail="No email address, or email is not configured.")
    return {"success": True, "message": "Email sent"}


# Backward-compat alias for the old send-email endpoint.
@app.post("/admin/invoices/{inv_id}/send-email")
async def send_invoice_email(inv_id: int, request: Request, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    body = await request.json()
    ok = _maybe_email_invoice(db, inv, "invoice", body.get("to_email"), True)
    if not ok:
        raise HTTPException(status_code=400, detail="No email address, or email is not configured.")
    return {"success": True, "message": "Invoice sent"}


# ── Payment Modes (dynamic, admin-managed) ──

@app.get("/admin/payment-modes")
def list_payment_modes(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(PaymentMode)
    if active_only:
        q = q.filter(PaymentMode.is_active == True)
    return [{"id": m.id, "name": m.name, "is_active": m.is_active, "sort_order": m.sort_order}
            for m in q.order_by(PaymentMode.sort_order, PaymentMode.id).all()]


@app.post("/admin/payment-modes")
async def create_payment_mode(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if db.query(PaymentMode).filter(PaymentMode.name.ilike(name)).first():
        raise HTTPException(status_code=400, detail="That payment mode already exists")
    nxt = (db.query(func.max(PaymentMode.sort_order)).scalar() or 0) + 1
    m = PaymentMode(name=name, is_active=body.get("is_active", True), sort_order=nxt)
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"id": m.id, "name": m.name}


@app.patch("/admin/payment-modes/{mode_id}")
async def update_payment_mode(mode_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    m = db.query(PaymentMode).filter(PaymentMode.id == mode_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Payment mode not found")
    for f in ("name", "is_active", "sort_order"):
        if f in body:
            setattr(m, f, body[f])
    db.commit()
    return {"message": "Updated"}


@app.delete("/admin/payment-modes/{mode_id}")
def delete_payment_mode(mode_id: int, db: Session = Depends(get_db)):
    m = db.query(PaymentMode).filter(PaymentMode.id == mode_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Payment mode not found")
    db.delete(m)
    db.commit()
    return {"message": "Deleted"}


@app.post("/admin/payment-modes/reorder")
async def reorder_payment_modes(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    for i, mid in enumerate(body.get("order", [])):
        m = db.query(PaymentMode).filter(PaymentMode.id == mid).first()
        if m:
            m.sort_order = i
    db.commit()
    return {"message": "Reordered"}


# ── Invoice Templates (notes/terms blocks) ──

@app.get("/admin/invoice-templates")
def list_invoice_templates(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "category": t.category, "content": t.content,
             "is_default": t.is_default}
            for t in db.query(InvoiceTemplate).order_by(InvoiceTemplate.name).all()]


@app.post("/admin/invoice-templates")
async def create_invoice_template(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    if not (body.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if body.get("is_default"):
        db.query(InvoiceTemplate).update({InvoiceTemplate.is_default: False})
    t = InvoiceTemplate(name=body["name"].strip(), category=body.get("category"),
                        content=body.get("content"), is_default=bool(body.get("is_default")))
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id}


@app.patch("/admin/invoice-templates/{tpl_id}")
async def update_invoice_template(tpl_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    t = db.query(InvoiceTemplate).filter(InvoiceTemplate.id == tpl_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if body.get("is_default"):
        db.query(InvoiceTemplate).update({InvoiceTemplate.is_default: False})
    for f in ("name", "category", "content", "is_default"):
        if f in body:
            setattr(t, f, body[f])
    db.commit()
    return {"message": "Updated"}


@app.delete("/admin/invoice-templates/{tpl_id}")
def delete_invoice_template(tpl_id: int, db: Session = Depends(get_db)):
    t = db.query(InvoiceTemplate).filter(InvoiceTemplate.id == tpl_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(t)
    db.commit()
    return {"message": "Deleted"}


# ── Organisation / GST settings (key-value in app_settings) ──

_ORG_KEYS = ["academy_name", "gst_number", "address", "phone", "email", "website",
             "logo_url", "bank_details", "upi_id", "invoice_footer", "invoice_notes"]


@app.get("/admin/org-settings")
def get_org_settings(db: Session = Depends(get_db),
                     current = Depends(require_roles("super_admin"))):
    rows = {s.key: s.value for s in db.query(AppSetting).filter(AppSetting.key.in_([f"org.{k}" for k in _ORG_KEYS])).all()}
    return {k: rows.get(f"org.{k}", "") for k in _ORG_KEYS}


@app.put("/admin/org-settings")
async def put_org_settings(request: Request, db: Session = Depends(get_db),
                           current = Depends(require_roles("super_admin"))):
    body = await request.json()
    for k in _ORG_KEYS:
        if k in body:
            key = f"org.{k}"
            row = db.query(AppSetting).filter(AppSetting.key == key).first()
            if row:
                row.value = body[k]
            else:
                db.add(AppSetting(key=key, value=body[k]))
    db.commit()
    return {"message": "Saved"}


@app.get("/admin/center-billing-settings")
def get_center_billing_settings(db: Session = Depends(get_db),
                                current = Depends(require_roles("super_admin", "center_admin"))):
    """Returns this center's billing settings (merged with global defaults).
    Center admins see their own center; super admins see global defaults."""
    obj = current.get("obj")
    if obj.access_role == "center_admin":
        return _org_settings_for_center(db, obj.center_id)
    return _org_settings(db)


@app.put("/admin/center-billing-settings")
async def put_center_billing_settings(request: Request, db: Session = Depends(get_db),
                                      current = Depends(require_roles("super_admin", "center_admin"))):
    """Save billing settings. Center admins save center-specific overrides;
    super admins save global defaults."""
    body = await request.json()
    obj = current.get("obj")
    if obj.access_role == "center_admin":
        prefix = f"center_{obj.center_id}_org."
        for k in _ORG_KEYS:
            if k in body and k != "logo_url":
                key = f"{prefix}{k}"
                row = db.query(AppSetting).filter(AppSetting.key == key).first()
                if row:
                    row.value = body[k]
                else:
                    db.add(AppSetting(key=key, value=body[k]))
        db.commit()
        return {"message": "Saved"}
    # super_admin → save global
    for k in _ORG_KEYS:
        if k in body:
            key = f"org.{k}"
            row = db.query(AppSetting).filter(AppSetting.key == key).first()
            if row:
                row.value = body[k]
            else:
                db.add(AppSetting(key=key, value=body[k]))
    db.commit()
    return {"message": "Saved"}


@app.post("/admin/upload-logo")
async def upload_logo(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload an academy logo; stores it and saves the absolute URL to org settings."""
    ext = (file.filename or "logo.png").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "webp", "svg"):
        ext = "png"
    path = f"static/logo.{ext}"
    with open(path, "wb") as f:
        f.write(await file.read())
    url = str(request.base_url).rstrip("/") + "/" + path
    row = db.query(AppSetting).filter(AppSetting.key == "org.logo_url").first()
    if row:
        row.value = url
    else:
        db.add(AppSetting(key="org.logo_url", value=url))
    db.commit()
    return {"logo_url": url}


# ── Public invoice payment (Razorpay) ──

@app.get("/pay/{inv_id}")
def public_invoice(inv_id: int, db: Session = Depends(get_db)):
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    det = _invoice_detail(db, inv)
    org = _org_settings_for_center(db, inv.center_id)
    return {
        "invoice_number": det["invoice_number"], "academy": org.get("academy_name"),
        "logo_url": org.get("logo_url"), "student_name": det["student_name"],
        "items": det["items"], "total": det["total_amount"], "paid": det["paid_amount"],
        "balance": det["balance"], "status": det["status"],
        "razorpay_key": _osmod.getenv("RAZORPAY_KEY_ID", ""),
    }


@app.post("/pay/{inv_id}/order")
def public_invoice_order(inv_id: int, db: Session = Depends(get_db)):
    import razorpay
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    balance = round((inv.total_amount or 0) - (inv.paid_amount or 0), 2)
    if balance <= 0:
        raise HTTPException(status_code=400, detail="Invoice is already fully paid")
    key, secret = _osmod.getenv("RAZORPAY_KEY_ID"), _osmod.getenv("RAZORPAY_KEY_SECRET")
    if not key or not secret:
        raise HTTPException(status_code=500, detail="Razorpay is not configured (RAZORPAY_KEY_ID/SECRET).")
    client = razorpay.Client(auth=(key, secret))
    order = client.order.create({"amount": int(round(balance * 100)), "currency": "INR",
                                 "receipt": inv.invoice_number, "payment_capture": 1})
    return {"order_id": order["id"], "amount": order["amount"], "key": key, "balance": balance}


@app.post("/pay/{inv_id}/verify")
async def public_invoice_verify(inv_id: int, request: Request, db: Session = Depends(get_db)):
    """Verify a Razorpay payment, record it against the invoice, and email the
    receipt immediately."""
    import hmac, hashlib
    from datetime import date as _dd
    body = await request.json()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    oid = body.get("razorpay_order_id")
    pid = body.get("razorpay_payment_id")
    sig = body.get("razorpay_signature")
    secret = _osmod.getenv("RAZORPAY_KEY_SECRET", "")
    if not body.get("test_mode"):
        gen = hmac.new(secret.encode(), f"{oid}|{pid}".encode(), hashlib.sha256).hexdigest()
        if gen != sig:
            raise HTTPException(status_code=400, detail="Payment verification failed")
    balance = round((inv.total_amount or 0) - (inv.paid_amount or 0), 2)
    if balance <= 0:
        return {"success": True, "message": "Already paid"}
    db.add(InvoicePayment(invoice_id=inv.id, amount=balance, method="Razorpay (online)",
                          reference=pid, paid_date=str(_dd.today())))
    inv.paid_amount = round((inv.paid_amount or 0) + balance, 2)
    inv.payment_mode = "Razorpay (online)"
    _recompute_invoice_status(inv)
    db.commit()
    _maybe_email_invoice(db, inv, "receipt", None, True)   # instant receipt
    return {"success": True, "status": inv.status}


# ── Admin dashboard alerts ──

@app.get("/admin/dashboard-alerts")
def dashboard_alerts(center_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Aggregated alerts: overdue invoices, expiring packages, low sessions,
    installments due soon, makeup violations."""
    from datetime import date as _date, timedelta
    today = _date.today()
    today_s = str(today)
    soon_s = str(today + timedelta(days=7))

    sq = db.query(Student)
    if center_id:
        sq = sq.filter(Student.center_id == center_id)
    students = sq.all()
    sids = [s.id for s in students] or [-1]
    name = {s.id: f"{s.first_name} {s.last_name}" for s in students}

    # Overdue invoices
    overdue = db.query(Invoice).filter(
        Invoice.student_id.in_(sids), Invoice.status.in_(["pending", "partial", "overdue"]),
        Invoice.due_date < today_s).all()
    overdue_invoices = [{"id": i.id, "invoice_number": i.invoice_number, "student": name.get(i.student_id),
                         "balance": round((i.total_amount or 0) - (i.paid_amount or 0), 2),
                         "due_date": i.due_date} for i in overdue]

    # Installments due within 7 days (unpaid)
    inv_by_student = {i.id: i.student_id for i in db.query(Invoice).filter(Invoice.student_id.in_(sids)).all()}
    inst = db.query(InvoiceInstallment).filter(
        InvoiceInstallment.invoice_id.in_(list(inv_by_student.keys()) or [-1]),
        InvoiceInstallment.status != "paid",
        InvoiceInstallment.due_date >= today_s, InvoiceInstallment.due_date <= soon_s).all()
    installments_due = [{"id": x.id, "student": name.get(inv_by_student.get(x.invoice_id)),
                         "amount": x.amount, "due_date": x.due_date} for x in inst]

    # Active packages → expiring / low / exhausted / makeup violations
    expiring, low_sessions, makeup_viol = [], [], []
    for sp in db.query(StudentPackage).filter(StudentPackage.student_id.in_(sids), StudentPackage.status == "active").all():
        st = resolve_package_state(db, sp)
        nm = name.get(sp.student_id)
        if sp.end_date and today_s <= sp.end_date <= soon_s and not st["is_expired"]:
            expiring.append({"student": nm, "package": st["package_name"], "end_date": sp.end_date})
        if st["sessions_total"] > 0 and 0 < st["sessions_remaining"] <= 2:
            low_sessions.append({"student": nm, "package": st["package_name"], "remaining": st["sessions_remaining"]})
        if st["makeup_allowed"] > 0 and st["makeup_used"] > st["makeup_allowed"]:
            makeup_viol.append({"student": nm, "used": st["makeup_used"], "allowed": st["makeup_allowed"]})

    return {
        "counts": {"overdue_invoices": len(overdue_invoices), "expiring_packages": len(expiring),
                   "low_sessions": len(low_sessions), "installments_due": len(installments_due),
                   "makeup_violations": len(makeup_viol)},
        "overdue_invoices": overdue_invoices, "expiring_packages": expiring,
        "low_sessions": low_sessions, "installments_due": installments_due,
        "makeup_violations": makeup_viol,
    }


# ── Installment reminders (call from a daily scheduler/cron) ──

_REMINDER_BUCKETS = {7, 3, 0, -1, -3, -7}  # days-until-due that trigger an email


def _installment_reminder_targets(db):
    from datetime import date as _date
    today = _date.today()
    out = []
    for ins in db.query(InvoiceInstallment).filter(
            InvoiceInstallment.status != "paid", InvoiceInstallment.due_date.isnot(None)).all():
        try:
            days = (_date.fromisoformat(ins.due_date) - today).days
        except Exception:
            continue
        if days in _REMINDER_BUCKETS:
            out.append((ins, days))
    return out


@app.post("/admin/run-installment-reminders")
def run_installment_reminders(db: Session = Depends(get_db)):
    """Send installment reminders for due in 7/3/0 days and overdue. Designed to
    be invoked once daily by an external scheduler (cron/Cloud Scheduler)."""
    sent = 0
    for ins, days in _installment_reminder_targets(db):
        inv = db.query(Invoice).filter(Invoice.id == ins.invoice_id).first()
        if not inv:
            continue
        if _maybe_email_invoice(db, inv, "reminder", None, True):
            sent += 1
    return {"reminders_sent": sent, "scanned": len(_installment_reminder_targets(db))}


@app.post("/admin/run-overdue")
def run_overdue(db: Session = Depends(get_db)):
    """Mark all unpaid invoices past their due_date as 'overdue'. Call daily."""
    from datetime import date as _dd
    today = str(_dd.today())
    updated = (
        db.query(Invoice)
        .filter(Invoice.status.in_(["pending", "partial"]), Invoice.due_date < today)
        .all()
    )
    for inv in updated:
        inv.status = "overdue"
    db.commit()
    return {"marked_overdue": len(updated)}


# ==================== PAYMENT — Subscriptions ====================

@app.get("/admin/subscriptions")
def list_subscriptions(db: Session = Depends(get_db),
                      current = Depends(require_roles("super_admin", "center_admin"))):
    # Phase 1A: Center admin only sees subscriptions for their center's students
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        center_students = db.query(Student).filter(Student.center_id == current["obj"].center_id).all()
        student_ids = [s.id for s in center_students]
        subs = db.query(Subscription).filter(Subscription.student_id.in_(student_ids)).all()
    else:
        subs = db.query(Subscription).all()
    result = []
    for sub in subs:
        student = db.query(Student).filter(Student.id == sub.student_id).first()
        result.append({
            "id": sub.id,
            "student_id": sub.student_id,
            "student_name": f"{student.first_name} {student.last_name}" if student else "Unknown",
            "grade": student.current_grade if student else None,
            "course": student.desired_course if student else None,
            "plan_name": sub.plan_name,
            "billing_cycle": sub.billing_cycle,
            "amount": sub.amount,
            "sessions_total": sub.sessions_total,
            "sessions_used": sub.sessions_used,
            "start_date": sub.start_date,
            "renewal_date": sub.renewal_date,
            "status": sub.status,
            "auto_renew": sub.auto_renew,
            "create_offset_days": sub.create_offset_days or 0,
            "due_offset_days": sub.due_offset_days or 7,
            "first_invoice_date": sub.first_invoice_date,
            "next_invoice_date": sub.next_invoice_date,
            "end_type": sub.end_type, "end_date": sub.end_date,
            "timezone": sub.timezone, "auto_email": sub.auto_email,
            "student_email": student.email if student else None,
        })
    return result


_CYCLE_MONTHS = {"monthly": 1, "quarterly": 3, "half-yearly": 6, "half_yearly": 6, "yearly": 12}


def _advance_cycle(date_str, cycle):
    return scheduling._fmt(scheduling._add_months(scheduling._parse(date_str), _CYCLE_MONTHS.get(cycle, 1)))


@app.post("/admin/subscriptions")
async def create_subscription(request: Request, db: Session = Depends(get_db),
                             current = Depends(require_roles("super_admin", "center_admin"))):
    from datetime import date as _dd
    body = await request.json()
    first = body.get("first_invoice_date") or body.get("start_date") or str(_dd.today())
    sub = Subscription(
        student_id=int(body["student_id"]),
        package_id=body.get("package_id"),
        plan_name=body["plan_name"],
        billing_cycle=body["billing_cycle"],
        amount=float(body["amount"]),
        sessions_total=body.get("sessions_total"),
        start_date=body.get("start_date") or first,
        first_invoice_date=first, next_invoice_date=first,
        create_offset_days=int(body.get("create_offset_days", 0) or 0),
        due_offset_days=int(body.get("due_offset_days", 7) or 7),
        end_type=body.get("end_type", "never"), end_date=body.get("end_date"),
        timezone=body.get("timezone", "Asia/Calcutta"),
        auto_email=body.get("auto_email", True), template_id=body.get("template_id"),
        renewal_date=first, auto_renew=body.get("auto_renew", True), status="active",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"id": sub.id, "next_invoice_date": sub.next_invoice_date}


@app.put("/admin/subscriptions/{sub_id}")
async def update_subscription(sub_id: int, request: Request, db: Session = Depends(get_db),
                             current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Not found")
    for field in ["plan_name", "billing_cycle", "amount", "sessions_total", "start_date",
                  "renewal_date", "status", "auto_renew", "create_offset_days", "due_offset_days",
                  "first_invoice_date", "next_invoice_date", "end_type", "end_date",
                  "timezone", "auto_email", "template_id"]:
        if field in body:
            setattr(sub, field, body[field])
    db.commit()
    return {"message": "Updated"}


@app.post("/admin/run-subscriptions")
def run_subscriptions(db: Session = Depends(get_db)):
    """Generate due recurring invoices from active subscriptions and email the
    enrolled student. Call once daily from an external scheduler."""
    import random, string
    from datetime import date as _dd, timedelta
    today = _dd.today()
    today_s = str(today)
    created = []
    for sub in db.query(Subscription).filter(Subscription.status == "active").all():
        nd = sub.next_invoice_date or sub.first_invoice_date or sub.start_date
        if not nd:
            continue
        try:
            due = _dd.fromisoformat(nd)
        except Exception:
            continue
        if sub.end_type == "on_date" and sub.end_date and today_s > sub.end_date:
            sub.status = "expired"
            db.commit()
            continue
        # Invoice is created `create_offset_days` before the due date.
        if today < (due - timedelta(days=sub.create_offset_days or 0)):
            continue
        pkg = db.query(Package).filter(Package.id == sub.package_id).first() if sub.package_id else None
        qty = (pkg.total_sessions if pkg and pkg.total_sessions else 1)
        per = round(sub.amount / qty, 2) if qty else sub.amount
        valid_till = None
        if pkg and pkg.validity_days:
            valid_till = str(due + timedelta(days=pkg.validity_days))
        notes = None
        if sub.template_id:
            t = db.query(InvoiceTemplate).filter(InvoiceTemplate.id == sub.template_id).first()
            notes = t.content if t else None
        payment_due = due + timedelta(days=int(sub.due_offset_days or 7))
        student_obj = db.query(Student).filter(Student.id == sub.student_id).first()
        inv = Invoice(
            invoice_number="INV-" + "".join(random.choices(string.digits, k=5)),
            student_id=sub.student_id, package_id=sub.package_id,
            amount=sub.amount, tax_amount=0, discount_amount=0, total_amount=sub.amount,
            status="pending", payment_type=sub.plan_name, description=notes, notes=notes,
            issue_date=today_s, due_date=str(payment_due), paid_amount=0,
            center_id=student_obj.center_id if student_obj else None,
        )
        db.add(inv)
        db.flush()
        db.add(InvoiceItem(invoice_id=inv.id, package_id=sub.package_id, label=sub.plan_name,
                           quantity=qty, unit_price=per, amount=sub.amount, valid_till=valid_till))
        db.commit()
        if sub.auto_email:
            _maybe_email_invoice(db, inv, "invoice", None, True)
        sub.next_invoice_date = _advance_cycle(str(due), sub.billing_cycle)
        sub.renewal_date = sub.next_invoice_date
        db.commit()
        created.append(inv.invoice_number)
    return {"invoices_created": len(created), "invoices": created}


# ==================== PAYMENT — Dashboard ====================

@app.get("/admin/reports")
def admin_reports(period: str = "month", center_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Comprehensive business intelligence — sales, students, teachers, attendance, operations."""
    from datetime import datetime as _dt, timedelta as _td
    from collections import defaultdict

    now = _dt.utcnow()
    days_map = {"week": 7, "month": 30, "quarter": 90, "year": 365}
    span = days_map.get(period, 30)
    start_dt = now - _td(days=span)
    start_str = start_dt.strftime("%Y-%m-%d")

    def in_period(date_str):
        if not date_str:
            return False
        return str(date_str)[:10] >= start_str

    today_str = now.strftime("%Y-%m-%d")

    # ── Load data (center-scoped) ──────────────────────────────────────────────
    students = db.query(Student)
    if center_id:
        students = students.filter(Student.center_id == center_id)
    students = students.all()
    student_ids = {s.id for s in students}
    students_map = {s.id: s for s in students}

    staff = db.query(Staff)
    if center_id:
        staff = staff.filter(Staff.center_id == center_id)
    staff = staff.all()

    batches = db.query(Batch)
    if center_id:
        batches = batches.filter(Batch.center_id == center_id)
    batches = batches.all()
    batch_ids = {b.id for b in batches}
    batch_map = {b.id: b for b in batches}

    all_invoices = db.query(Invoice).all()
    invoices = [i for i in all_invoices if (not center_id or i.student_id in student_ids)]

    all_sessions = db.query(ClassSession).all()
    sessions = [s for s in all_sessions if (not center_id or s.batch_id in batch_ids)]

    all_attendance = db.query(Attendance).all()
    session_ids = {s.id for s in sessions}
    attendance = [a for a in all_attendance if (not center_id or a.session_id in session_ids)]

    staff_map = {s.id: s for s in staff}

    # ════════════════════ SALES / REVENUE ════════════════════
    paid_invoices = [i for i in invoices if i.status == "paid"]
    period_paid = [i for i in paid_invoices if in_period(i.paid_date or i.issue_date)]

    total_revenue = sum(i.paid_amount or i.total_amount or 0 for i in period_paid)
    total_billed = sum(i.total_amount or 0 for i in invoices if in_period(i.issue_date))
    total_collected = sum(i.paid_amount or 0 for i in invoices)
    total_outstanding = sum((i.total_amount or 0) - (i.paid_amount or 0)
                            for i in invoices if i.status in ("pending", "partial", "overdue"))
    collection_rate = round((total_collected / sum(i.total_amount or 0 for i in invoices) * 100), 1) if invoices else 0

    # Revenue by month (last 6 months)
    rev_by_month = defaultdict(float)
    for i in paid_invoices:
        d = i.paid_date or i.issue_date
        if d:
            rev_by_month[str(d)[:7]] += (i.paid_amount or i.total_amount or 0)
    revenue_trend = [{"month": k, "revenue": round(v)} for k, v in sorted(rev_by_month.items())][-6:]

    # By payment mode
    mode_rev = defaultdict(float)
    for i in period_paid:
        mode_rev[i.payment_mode or "Other"] += (i.paid_amount or i.total_amount or 0)
    revenue_by_mode = [{"mode": k, "amount": round(v)} for k, v in sorted(mode_rev.items(), key=lambda x: -x[1])]

    # By package / type
    pkg_rev = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for i in period_paid:
        key = i.payment_type or "Other"
        pkg_rev[key]["amount"] += (i.paid_amount or i.total_amount or 0)
        pkg_rev[key]["count"] += 1
    revenue_by_package = [{"name": k, "amount": round(v["amount"]), "count": v["count"]}
                          for k, v in sorted(pkg_rev.items(), key=lambda x: -x[1]["amount"])][:8]

    # Invoice status breakdown
    status_counts = defaultdict(int)
    for i in invoices:
        status_counts[i.status or "pending"] += 1

    # ════════════════════ STUDENTS ════════════════════
    new_students = [s for s in students if in_period(s.created_at.strftime("%Y-%m-%d") if s.created_at else None)]

    # Enrollment trend by month
    enroll_by_month = defaultdict(int)
    for s in students:
        if s.created_at:
            enroll_by_month[s.created_at.strftime("%Y-%m")] += 1
    enrollment_trend = [{"month": k, "count": v} for k, v in sorted(enroll_by_month.items())][-6:]

    # By grade
    grade_counts = defaultdict(int)
    for s in students:
        grade_counts[s.current_grade or "Unassigned"] += 1
    students_by_grade = [{"grade": k, "count": v} for k, v in sorted(grade_counts.items(), key=lambda x: -x[1])]

    # By course / instrument
    course_counts = defaultdict(int)
    for s in students:
        course_counts[s.desired_course or s.instrument or "Unassigned"] += 1
    students_by_course = [{"course": k, "count": v} for k, v in sorted(course_counts.items(), key=lambda x: -x[1])][:10]

    # By center (only when viewing all)
    center_counts = defaultdict(int)
    centers_lookup = {c.id: c.name for c in db.query(Center).all()}
    for s in students:
        center_counts[centers_lookup.get(s.center_id, "Unassigned")] += 1
    students_by_center = [{"center": k, "count": v} for k, v in sorted(center_counts.items(), key=lambda x: -x[1])]

    # Exam students
    exam_students = sum(1 for s in students if s.is_exam_student)

    # ════════════════════ TEACHERS ════════════════════
    teachers = [s for s in staff if (s.takes_classes or (s.role or "").lower() in ("teacher", "instructor"))]
    sessions_by_teacher = defaultdict(int)
    period_sessions_by_teacher = defaultdict(int)
    for sess in sessions:
        tid = sess.teacher_id or (batch_map.get(sess.batch_id).teacher_id if sess.batch_id in batch_map else None)
        if tid:
            sessions_by_teacher[tid] += 1
            if in_period(sess.date):
                period_sessions_by_teacher[tid] += 1

    # students per teacher (via batch enrollments)
    enrollments = db.query(StudentEnrollment).all()
    students_per_teacher = defaultdict(set)
    for e in enrollments:
        b = batch_map.get(e.batch_id)
        if b and b.teacher_id and e.student_id in student_ids:
            students_per_teacher[b.teacher_id].add(e.student_id)

    teacher_report = []
    for t in teachers:
        teacher_report.append({
            "id": t.id,
            "name": t.name,
            "total_sessions": sessions_by_teacher.get(t.id, 0),
            "period_sessions": period_sessions_by_teacher.get(t.id, 0),
            "students": len(students_per_teacher.get(t.id, set())),
        })
    teacher_report.sort(key=lambda x: -x["period_sessions"])

    # ════════════════════ ATTENDANCE ════════════════════
    period_attendance = [a for a in attendance if a.session_id in {s.id for s in sessions if in_period(s.date)}]
    att_present = sum(1 for a in period_attendance if a.status == "present")
    att_absent = sum(1 for a in period_attendance if a.status == "absent")
    att_total = att_present + att_absent
    attendance_rate = round((att_present / att_total * 100), 1) if att_total else 0

    # Attendance trend by week
    att_by_week = defaultdict(lambda: {"present": 0, "absent": 0})
    sess_date = {s.id: s.date for s in sessions}
    for a in attendance:
        d = sess_date.get(a.session_id)
        if d and in_period(d):
            try:
                wk = _dt.strptime(str(d)[:10], "%Y-%m-%d").strftime("%Y-W%U")
                if a.status in ("present", "absent"):
                    att_by_week[wk][a.status] += 1
            except Exception:
                pass
    attendance_trend = [{"week": k, **v} for k, v in sorted(att_by_week.items())][-8:]

    # ════════════════════ OPERATIONS ════════════════════
    period_sessions = [s for s in sessions if in_period(s.date)]
    sessions_completed = sum(1 for s in period_sessions if (s.status or "scheduled") != "cancelled" and str(s.date)[:10] <= today_str)
    sessions_cancelled = sum(1 for s in period_sessions if s.status == "cancelled")
    sessions_upcoming = sum(1 for s in period_sessions if str(s.date)[:10] > today_str)

    return {
        "period": period,
        "center_id": center_id,
        "generated_at": now.isoformat(),
        "sales": {
            "total_revenue": round(total_revenue),
            "total_billed": round(total_billed),
            "total_collected": round(total_collected),
            "total_outstanding": round(total_outstanding),
            "collection_rate": collection_rate,
            "paid_invoice_count": len(period_paid),
            "avg_invoice": round(total_revenue / len(period_paid)) if period_paid else 0,
            "revenue_trend": revenue_trend,
            "revenue_by_mode": revenue_by_mode,
            "revenue_by_package": revenue_by_package,
            "status_breakdown": [{"status": k, "count": v} for k, v in status_counts.items()],
        },
        "students": {
            "total": len(students),
            "new_this_period": len(new_students),
            "exam_students": exam_students,
            "enrollment_trend": enrollment_trend,
            "by_grade": students_by_grade,
            "by_course": students_by_course,
            "by_center": students_by_center,
        },
        "teachers": {
            "total": len(teachers),
            "report": teacher_report,
            "total_sessions_period": sum(period_sessions_by_teacher.values()),
        },
        "attendance": {
            "rate": attendance_rate,
            "present": att_present,
            "absent": att_absent,
            "total_marked": att_total,
            "trend": attendance_trend,
        },
        "operations": {
            "total_sessions": len(period_sessions),
            "completed": sessions_completed,
            "cancelled": sessions_cancelled,
            "upcoming": sessions_upcoming,
            "active_batches": len(batches),
            "cancellation_rate": round((sessions_cancelled / len(period_sessions) * 100), 1) if period_sessions else 0,
        },
    }


@app.get("/admin/payment-dashboard")
def payment_dashboard(period: str = "month", db: Session = Depends(get_db)):
    from datetime import datetime as _dt, timedelta as _td

    now = _dt.utcnow()

    # ── time-range start date string (YYYY-MM-DD) ──────────────────────────────
    if period == "today":
        start_str = now.strftime("%Y-%m-%d")
    elif period == "week":
        start_str = (now - _td(days=7)).strftime("%Y-%m-%d")
    elif period == "month":
        start_str = now.replace(day=1).strftime("%Y-%m-%d")
    elif period == "quarter":
        start_str = (now - _td(days=90)).strftime("%Y-%m-%d")
    elif period == "half":
        start_str = (now - _td(days=180)).strftime("%Y-%m-%d")
    elif period == "year":
        start_str = now.replace(month=1, day=1).strftime("%Y-%m-%d")
    else:
        start_str = None

    # ── pre-fetch everything once ──────────────────────────────────────────────
    all_invoices   = db.query(Invoice).order_by(Invoice.id.desc()).all()
    all_subs       = db.query(Subscription).all()
    all_packages   = db.query(Package).filter(Package.is_archived == False).all()
    students_map   = {s.id: s for s in db.query(Student).all()}

    # ── KPI slice (time-filtered) ──────────────────────────────────────────────
    kpi_invs = (
        [i for i in all_invoices if i.issue_date and i.issue_date >= start_str]
        if start_str else all_invoices
    )
    total_invoiced = sum(i.total_amount or 0 for i in kpi_invs)
    total_received = sum(i.paid_amount  or 0 for i in kpi_invs)
    total_due      = sum(
        max(0, (i.total_amount or 0) - (i.paid_amount or 0))
        for i in kpi_invs if i.status not in ("paid", "cancelled")
    )
    overdue_all    = [i for i in all_invoices if i.status == "overdue"]
    active_subs    = [s for s in all_subs if s.status == "active"]

    # ── upcoming renewals (next 30 days) ──────────────────────────────────────
    upcoming_renewals = []
    for s in active_subs:
        if not s.renewal_date:
            continue
        try:
            rd = _dt.fromisoformat(s.renewal_date[:10])
        except Exception:
            continue
        days_left = (rd - now).days
        if 0 <= days_left <= 30:
            stu = students_map.get(s.student_id)
            upcoming_renewals.append({
                "id": s.id,
                "student_name": f"{stu.first_name} {stu.last_name}" if stu else "Unknown",
                "plan_name":    s.plan_name or "",
                "amount":       float(s.amount or 0),
                "renewal_date": s.renewal_date,
                "status":       s.status,
                "auto_renew":   bool(s.auto_renew),
            })

    # ── monthly revenue — always last 6 calendar months ───────────────────────
    monthly_revenue = []
    for months_back in range(5, -1, -1):
        yr, mo = now.year, now.month - months_back
        while mo <= 0:
            mo += 12; yr -= 1
        prefix = f"{yr}-{mo:02d}"
        label  = _dt(yr, mo, 1).strftime("%b")
        m_invs = [i for i in all_invoices if i.issue_date and i.issue_date[:7] == prefix]
        monthly_revenue.append({
            "month":     label,
            "revenue":   round(sum(i.total_amount or 0 for i in m_invs), 2),
            "collected": round(sum(i.paid_amount  or 0 for i in m_invs), 2),
        })

    # ── grade-wise dues (all outstanding invoices) ────────────────────────────
    grade_map = {}
    for inv in all_invoices:
        if inv.status not in ("pending", "overdue", "partial"):
            continue
        stu   = students_map.get(inv.student_id)
        grade = (stu.current_grade if stu else None) or "Unknown"
        if grade not in grade_map:
            grade_map[grade] = {"due": 0.0, "ids": set()}
        grade_map[grade]["due"] += max(0, (inv.total_amount or 0) - (inv.paid_amount or 0))
        grade_map[grade]["ids"].add(inv.student_id)
    grades_due = sorted(
        [{"grade": k, "due": round(v["due"], 2), "students": len(v["ids"])} for k, v in grade_map.items() if v["due"] > 0],
        key=lambda x: x["due"], reverse=True
    )

    # ── package performance ────────────────────────────────────────────────────
    pkg_map = {}
    for inv in all_invoices:
        if not inv.package_id:
            continue
        pkg = next((p for p in all_packages if p.id == inv.package_id), None)
        name = pkg.name if pkg else "Other"
        if name not in pkg_map:
            pkg_map[name] = {"rev": 0.0, "ids": set(), "sessions": pkg.total_sessions if pkg else 0}
        pkg_map[name]["rev"] += float(inv.paid_amount or 0)
        pkg_map[name]["ids"].add(inv.student_id)
    package_perf = sorted(
        [{"name": k, "revenue": round(v["rev"], 2), "students": len(v["ids"]), "sessions": v["sessions"]} for k, v in pkg_map.items()],
        key=lambda x: x["revenue"], reverse=True
    )
    # Fall back to listing packages with 0 revenue when no invoices exist yet
    if not package_perf:
        package_perf = [
            {"name": p.name, "revenue": 0, "students": 0, "sessions": p.total_sessions or 0}
            for p in all_packages[:4]
        ]

    # ── recent invoices (8 latest) ────────────────────────────────────────────
    recent_invoices = []
    for inv in all_invoices[:8]:
        stu = students_map.get(inv.student_id)
        recent_invoices.append({
            "id":             inv.id,
            "invoice_number": inv.invoice_number or "",
            "student_name":   f"{stu.first_name} {stu.last_name}" if stu else "Unknown",
            "grade":          (stu.current_grade if stu else None) or "",
            "amount":         float(inv.total_amount or 0),
            "paid_amount":    float(inv.paid_amount or 0),
            "status":         inv.status or "pending",
            "payment_type":   inv.payment_type or "",
            "payment_mode":   inv.payment_mode or "",
            "issue_date":     inv.issue_date or "",
            "due_date":       inv.due_date or "",
        })

    # ── defaulters (overdue, up to 8) ─────────────────────────────────────────
    defaulters = []
    for inv in overdue_all[:8]:
        stu = students_map.get(inv.student_id)
        defaulters.append({
            "id":             inv.id,
            "invoice_number": inv.invoice_number or "",
            "student_name":   f"{stu.first_name} {stu.last_name}" if stu else "Unknown",
            "grade":          (stu.current_grade if stu else None) or "",
            "amount":         float(inv.total_amount or 0),
            "due_date":       inv.due_date or "",
            "status":         inv.status,
        })

    return {
        "kpi": {
            "totalInvoiced":       round(total_invoiced, 2),
            "totalReceived":       round(total_received, 2),
            "totalDue":            round(total_due, 2),
            "overdue":             round(sum(i.total_amount or 0 for i in overdue_all), 2),
            "overdueCount":        len(overdue_all),
            "activeSubscriptions": len(active_subs),
            "upcomingRenewals":    len(upcoming_renewals),
            "sessionsTotal":       sum(s.sessions_total or 0 for s in active_subs),
            "sessionsUsed":        sum(s.sessions_used  or 0 for s in active_subs),
            "collectionRate":      round(total_received / total_invoiced * 100, 1) if total_invoiced > 0 else 0,
        },
        "allInvoicesCount":  len(all_invoices),
        "invoices":          recent_invoices,
        "allInvoices":       recent_invoices,
        "subscriptions":     [{"id": s.id, "status": s.status, "plan_name": s.plan_name, "amount": float(s.amount or 0)} for s in all_subs],
        "upcomingRenewals":  upcoming_renewals,
        "packages":          package_perf,
        "monthlyRevenue":    monthly_revenue,
        "gradesDue":         grades_due,
        "defaulters":        defaulters,
    }


# ==================== PAYMENT — Student packages (grade/course filtered) ====================

@app.get("/student/packages")
def student_packages(student_id: int, db: Session = Depends(get_db)):
    """Return only packages applicable to the student's grade and course."""
    import json as _json
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    grade = student.current_grade or "Debut"
    course = student.instrument or student.desired_course or ""

    all_pkgs = db.query(Package).filter(
        Package.is_archived == False,
        Package.is_published == True
    ).all()

    result = []
    for pkg in all_pkgs:
        try:
            grades = _json.loads(pkg.applicable_grades or "[]")
        except Exception:
            grades = []
        try:
            courses = _json.loads(pkg.applicable_courses or "[]")
        except Exception:
            courses = []

        grade_ok = len(grades) == 0 or grade in grades
        course_ok = len(courses) == 0 or course in courses

        if grade_ok and course_ok:
            result.append({
                "id": pkg.id,
                "name": pkg.name,
                "applicable_grades": grades,
                "applicable_courses": courses,
                "validity_days": pkg.validity_days,
                "total_sessions": pkg.total_sessions,
                "session_duration_minutes": pkg.session_duration_minutes or 60,
                "makeup_sessions": pkg.makeup_sessions or 0,
                "makeup_validity_days": pkg.makeup_validity_days,
                "cancellation_window_hours": pkg.cancellation_window_hours if pkg.cancellation_window_hours is not None else 24,
                "prorate_enabled": pkg.prorate_enabled,
                "price": pkg.price,
                "per_session_fee": round((pkg.price or 0) / pkg.total_sessions, 2) if pkg.total_sessions else 0,
                "tax_percentage": pkg.tax_percentage,
                "total_with_tax": round(pkg.price * (1 + pkg.tax_percentage / 100), 2),
                "description": pkg.description,
            })
    return result


# ==================== PAYMENT — Razorpay create order ====================

@app.post("/student/payments/create-order")
async def create_razorpay_order(request: Request, db: Session = Depends(get_db)):
    import razorpay
    body = await request.json()
    package_id = body.get("package_id")
    student_id = body.get("student_id")

    pkg = db.query(Package).filter(Package.id == package_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found")

    amount_paise = int(round(pkg.price * (1 + pkg.tax_percentage / 100) * 100))

    student = db.query(Student).filter(Student.id == student_id).first()
    rzp = _razorpay_for_center(db, student.center_id if student else None)
    key_id     = rzp["key_id"]     or "rzp_test_placeholder"
    key_secret = rzp["key_secret"] or "placeholder_secret"

    try:
        client = razorpay.Client(auth=(key_id, key_secret))
        order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"vama_pkg_{package_id}_stu_{student_id}",
            "notes": {
                "package_id": str(package_id),
                "student_id": str(student_id),
                "package_name": pkg.name,
            }
        })
        return {
            "order_id": order["id"],
            "amount": amount_paise,
            "currency": "INR",
            "key_id": key_id,
            "package_name": pkg.name,
            "description": pkg.description or f"{pkg.name} - {pkg.total_sessions} sessions",
        }
    except Exception as e:
        # Return a test order when Razorpay keys are not configured
        return {
            "order_id": f"order_test_{package_id}_{student_id}",
            "amount": amount_paise,
            "currency": "INR",
            "key_id": key_id,
            "package_name": pkg.name,
            "description": pkg.description or f"{pkg.name} - {pkg.total_sessions} sessions",
            "test_mode": True,
        }


# ==================== PAYMENT — Razorpay verify & create invoice ====================

@app.post("/student/payments/verify")
async def verify_razorpay_payment(request: Request, db: Session = Depends(get_db)):
    import hashlib, hmac, random, string
    body = await request.json()

    razorpay_order_id = body.get("razorpay_order_id")
    razorpay_payment_id = body.get("razorpay_payment_id")
    razorpay_signature = body.get("razorpay_signature")
    package_id = body.get("package_id")
    student_id = body.get("student_id")
    test_mode = body.get("test_mode", False)

    student = db.query(Student).filter(Student.id == student_id).first()
    rzp = _razorpay_for_center(db, student.center_id if student else None)
    key_secret = rzp["key_secret"] or "placeholder_secret"

    # Verify signature (skip in test mode)
    if not test_mode and razorpay_signature:
        generated = hmac.new(
            key_secret.encode(),
            f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
            hashlib.sha256
        ).hexdigest()
        if generated != razorpay_signature:
            raise HTTPException(status_code=400, detail="Payment verification failed")

    # Fetch package details
    pkg = db.query(Package).filter(Package.id == package_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found")

    student = db.query(Student).filter(Student.id == student_id).first()

    # Create invoice
    amount = pkg.price
    tax = round(amount * pkg.tax_percentage / 100, 2)
    total = round(amount + tax, 2)
    inv_num = "INV-" + "".join(random.choices(string.digits, k=5))

    from datetime import date, timedelta
    today = date.today()
    due = today + timedelta(days=1)

    invoice = Invoice(
        invoice_number=inv_num,
        student_id=student_id,
        package_id=package_id,
        amount=amount,
        tax_amount=tax,
        discount_amount=0,
        total_amount=total,
        paid_amount=total,
        status="paid",
        payment_type="Package",
        description=f"Payment for {pkg.name}",
        issue_date=str(today),
        due_date=str(due),
        paid_date=str(today),
        notes=f"Razorpay: {razorpay_payment_id or 'test'}",
        sessions_count=pkg.total_sessions,
    )
    db.add(invoice)

    # Activate / queue StudentPackage
    from datetime import date, timedelta
    today_d = date.today()

    # Check if there's an active package with sessions remaining — queue behind it.
    active_sp = db.query(StudentPackage).filter(
        StudentPackage.student_id == student_id,
        StudentPackage.status == "active",
    ).order_by(StudentPackage.created_at.desc()).first()

    if active_sp:
        state = resolve_package_state(db, active_sp)
        if state and state["sessions_remaining"] > 0 and not state["is_expired"]:
            # Queue: start after the current package ends
            queue_start = active_sp.end_date or str(today_d)
            queue_end = str((date.fromisoformat(queue_start) + timedelta(days=pkg.validity_days or 30)))
            new_status = "queued"
            start = queue_start
            end = queue_end
        else:
            # Current package exhausted/expired — cancel it and activate new one
            active_sp.status = "expired" if state and state["is_expired"] else "exhausted"
            start = str(today_d)
            end = str(today_d + timedelta(days=pkg.validity_days or 30))
            new_status = "active"
    else:
        start = str(today_d)
        end = str(today_d + timedelta(days=pkg.validity_days or 30))
        new_status = "active"

    # Cancel any existing queued packages (replaced by new purchase)
    for old_q in db.query(StudentPackage).filter(
        StudentPackage.student_id == student_id,
        StudentPackage.status == "queued",
    ).all():
        old_q.status = "cancelled"

    new_sp = StudentPackage(
        student_id=student_id,
        package_id=package_id,
        start_date=str(start),
        end_date=str(end),
        sessions_used=0,
        makeup_used=0,
        status=new_status,
    )
    db.add(new_sp)
    db.commit()

    return {
        "success": True,
        "invoice_number": inv_num,
        "package_name": pkg.name,
        "sessions": pkg.total_sessions,
        "valid_until": str(end),
        "amount_paid": total,
        "queued": new_status == "queued",
    }


# ==================== PAYMENT — Student view ====================

@app.get("/student/{student_id}/payments")
def student_payments(student_id: int, db: Session = Depends(get_db)):
    invoices = db.query(Invoice).filter(Invoice.student_id == student_id).order_by(Invoice.created_at.desc()).all()
    sub = db.query(Subscription).filter(Subscription.student_id == student_id, Subscription.status == "active").first()
    sp = (
        db.query(StudentPackage)
        .filter(StudentPackage.student_id == student_id, StudentPackage.status == "active")
        .order_by(StudentPackage.created_at.desc())
        .first()
    )
    pkg = db.query(Package).filter(Package.id == sp.package_id).first() if sp else None

    # Queued package (next in line)
    queued_sp = (
        db.query(StudentPackage)
        .filter(StudentPackage.student_id == student_id, StudentPackage.status == "queued")
        .order_by(StudentPackage.created_at.asc())
        .first()
    )
    queued_pkg = db.query(Package).filter(Package.id == queued_sp.package_id).first() if queued_sp else None

    active_package = None
    if pkg and sp:
        state = resolve_package_state(db, sp)
        active_package = {
            "name": pkg.name,
            "sessions_total": state["sessions_total"],
            "sessions_used": state["sessions_used"],
            "sessions_remaining": state["sessions_remaining"],
            "makeup_sessions": state["makeup_allowed"],
            "makeup_used": state["makeup_used"],
            "makeup_remaining": state["makeup_remaining"],
            "per_session_fee": round((pkg.price or 0) / pkg.total_sessions, 2) if pkg.total_sessions else 0,
            "session_duration_minutes": pkg.session_duration_minutes or 60,
            "validity_until": sp.end_date,
            "start_date": sp.start_date,
            "is_expired": state["is_expired"],
            "status": state["effective_status"],
            "price": pkg.price,
        }

    # Fee packages assigned per enrollment (grade/subject matched)
    try:
        from models import LearningEnrollment as _LE
        enrollments = db.query(_LE).filter(
            _LE.student_id == student_id, _LE.status == "active"
        ).all()
        enrollment_packages = []
        for e in enrollments:
            ep = db.query(Package).filter(Package.id == e.fee_package_id).first() if e.fee_package_id else None
            enrollment_packages.append({
                "subject": e.subject,
                "grade": e.grade,
                "syllabus_type": e.syllabus_type,
                "fee_package_id": e.fee_package_id,
                "fee_package_name": ep.name if ep else None,
                "fee_package_price": ep.price if ep else None,
                "fee_package_sessions": ep.total_sessions if ep else None,
                "fee_package_validity_days": ep.validity_days if ep else None,
            })
    except Exception:
        enrollment_packages = []

    inv_ids = [i.id for i in invoices]
    payments = db.query(InvoicePayment).filter(InvoicePayment.invoice_id.in_(inv_ids or [-1])).order_by(InvoicePayment.id.desc()).all()
    inv_num = {i.id: i.invoice_number for i in invoices}
    upcoming_inst = db.query(InvoiceInstallment).filter(
        InvoiceInstallment.invoice_id.in_(inv_ids or [-1]),
        InvoiceInstallment.status != "paid",
    ).order_by(InvoiceInstallment.due_date).all()

    queued_package = None
    if queued_sp and queued_pkg:
        queued_package = {
            "name": queued_pkg.name,
            "sessions_total": queued_pkg.total_sessions,
            "start_date": queued_sp.start_date,
            "end_date": queued_sp.end_date,
            "price": queued_pkg.price,
            "validity_days": queued_pkg.validity_days,
        }

    return {
        "active_package": active_package,
        "queued_package": queued_package,
        "enrollment_packages": enrollment_packages,
        "invoices": [{"id": i.id, "invoice_number": i.invoice_number, "amount": i.amount, "tax_amount": i.tax_amount, "discount_amount": i.discount_amount, "total_amount": i.total_amount, "paid_amount": i.paid_amount, "balance": round((i.total_amount or 0) - (i.paid_amount or 0), 2), "status": i.status, "payment_type": i.payment_type, "issue_date": i.issue_date, "due_date": i.due_date, "paid_date": i.paid_date} for i in invoices],
        "payments": [{"id": p.id, "invoice_number": inv_num.get(p.invoice_id), "amount": p.amount, "method": p.method, "reference": p.reference, "paid_date": p.paid_date} for p in payments],
        "upcoming_installments": [{"id": x.id, "invoice_number": inv_num.get(x.invoice_id), "seq": x.seq, "amount": x.amount, "paid_amount": x.paid_amount, "due_date": x.due_date, "status": x.status} for x in upcoming_inst],
        "attendance_timeline": [],
        "upcoming_renewals": [{"plan": sub.plan_name, "amount": sub.amount, "due_date": sub.renewal_date, "sessions": sub.sessions_total}] if sub else [],
    }


# ==================== Centers ====================

@app.get("/centers")
def list_centers(db: Session = Depends(get_db)):
    return [
        {"id": c.id, "name": c.name, "address": c.address, "phone": c.phone,
         "email": c.email, "is_active": c.is_active}
        for c in db.query(Center).filter(Center.is_active == True).order_by(Center.name).all()
    ]


@app.post("/centers")
async def create_center(request: Request, db: Session = Depends(get_db),
                       current = Depends(require_roles("super_admin"))):
    body = await request.json()
    c = Center(name=body["name"], address=body.get("address"), phone=body.get("phone"), email=body.get("email"))
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "name": c.name}


# ── Phase 3A: Center Onboarding (full transactional flow) ──
@app.post("/centers/onboard")
async def onboard_center(request: Request, db: Session = Depends(get_db),
                        current = Depends(require_roles("super_admin"))):
    """Complete center onboarding: creates center + admin account + seeds settings.

    Body: { center_name, center_address?, center_phone, center_email }

    The center itself is the admin — center email and phone are used as the admin account.

    Returns: { center, admin_staff, activation_link_sent }
    """
    body = await request.json()

    # Validate required fields
    center_name = (body.get("center_name") or "").strip()
    center_email = (body.get("center_email") or "").strip().lower()
    center_phone = (body.get("center_phone") or "").strip()

    if not center_name or not center_email or not center_phone:
        raise HTTPException(status_code=400, detail="center_name, center_email, and center_phone are required")

    # Check if email already exists
    if email_exists(db, center_email):
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    # 1. Create Center
    center = Center(
        name=center_name,
        address=body.get("center_address"),
        phone=center_phone,
        email=center_email,
        is_active=True,
    )
    db.add(center)
    db.flush()  # Get center.id without committing

    # 2. Create Center Admin Staff Account using center details
    admin_staff = Staff(
        name=center_name,
        first_name=center_name.split()[0] if center_name else "",
        last_name=" ".join(center_name.split()[1:]) if len(center_name.split()) > 1 else "",
        role="Center Admin",
        access_role="center_admin",
        center_id=center.id,
        phone=center_phone,
        email=center_email,
        calendar=False,
        takes_classes=False,
    )
    db.add(admin_staff)
    db.flush()

    # 3. Provision account (sends activation email)
    activation_token = provision_account(db, "staff", admin_staff, request=request)

    # 4. Seed per-center AppSettings (working hours, etc.)
    # For now, just mark this center as seeded; can expand with more settings later
    db.add(AppSetting(key=f"center_{center.id}_seeded", value="1"))

    # 5. Commit everything
    db.commit()
    db.refresh(center)
    db.refresh(admin_staff)

    # 6. Audit log
    audit(db, "center.created", subject=("staff", current["id"]), request=request,
          detail={"center_id": center.id, "center_admin_staff_id": admin_staff.id})
    db.commit()

    # 7. Build activation link for dev mode
    activation_link = None
    if activation_token:
        frontend_url = _osmod.getenv("FRONTEND_URL", "http://localhost:5173")
        activation_link = f"{frontend_url}/activate?token={activation_token}"

    return {
        "center": {
            "id": center.id,
            "name": center.name,
            "address": center.address,
            "phone": center.phone,
            "email": center.email,
            "is_active": center.is_active,
            "created_at": center.created_at.isoformat() if center.created_at else None,
        },
        "admin_staff": {
            "id": admin_staff.id,
            "name": admin_staff.name,
            "email": admin_staff.email,
            "phone": admin_staff.phone,
            "access_role": admin_staff.access_role,
            "account_status": admin_staff.account_status,
        },
        "message": "Center created successfully. Activation email sent to center admin.",
        "activation_link": activation_link,
    }


@app.put("/centers/{center_id}")
async def update_center(center_id: int, request: Request, db: Session = Depends(get_db),
                       current = Depends(require_roles("super_admin"))):
    body = await request.json()
    c = db.query(Center).filter(Center.id == center_id).first()
    if not c: raise HTTPException(status_code=404, detail="Center not found")
    for f in ["name", "address", "phone", "email", "is_active"]:
        if f in body: setattr(c, f, body[f])
    db.commit()
    return {"message": "Updated"}


@app.put("/admin/staff/{staff_id}/access")
async def update_staff_access(staff_id: int, request: Request, db: Session = Depends(get_db),
                             current = Depends(require_roles("super_admin"))):
    """Update a staff member's access_role and center assignment."""
    body = await request.json()
    s = db.query(Staff).filter(Staff.id == staff_id).first()
    if not s: raise HTTPException(status_code=404, detail="Staff not found")
    if "access_role" in body: s.access_role = body["access_role"]
    if "center_id" in body:   s.center_id   = body["center_id"] or None
    db.commit()
    return {"message": "Access updated", "access_role": s.access_role, "center_id": s.center_id}


@app.put("/admin/students/{student_id}/center")
async def assign_student_center(student_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s: raise HTTPException(status_code=404, detail="Student not found")
    s.center_id = body.get("center_id")
    db.commit()
    return {"message": "Center assigned"}


# ==================== Admin Login ====================

@app.post("/admin/login")
async def admin_login(request: Request, db: Session = Depends(get_db)):
    import asyncio
    body = await request.json()
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")

    staff = db.query(Staff).filter(Staff.email.ilike(email)).first()
    if not staff:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # Offload Argon2 (CPU-bound) to thread pool so the event loop stays free
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, verify_credentials, db, staff, password)
    if not ok:
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if (staff.account_status or "active") != "active":
        raise HTTPException(status_code=403, detail="Your account is not active. Please activate it via the link sent to your email.")

    access_role = staff.access_role or "teacher"
    if access_role not in ("super_admin", "center_admin"):
        raise HTTPException(status_code=403, detail="This account does not have admin access")

    roles = roles_for("staff", staff)
    sub = f"staff:{staff.id}"
    access_token = security.create_access_token(sub, roles)
    refresh_token = security.create_refresh_token(sub)

    center = None
    if staff.center_id:
        c = db.query(Center).filter(Center.id == staff.center_id).first()
        if c: center = {"id": c.id, "name": c.name}

    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "admin": {
            "id":          staff.id,
            "name":        staff.name,
            "email":       staff.email,
            "access_role": access_role,
            "center_id":   staff.center_id,
            "center":      center,
        }
    }


# ==================== Credentials / Password Management ====================

def _admin_force_set_password(db, subject_type, obj, new_pass, request):
    """Admin force-sets a password: hashed, policy-checked, account activated."""
    err = security.validate_password_strength(new_pass)
    if err:
        raise HTTPException(status_code=400, detail=err)
    obj.password_hash = security.hash_password(new_pass)
    obj.password = None              # drop any legacy plaintext
    obj.account_status = "active"    # an admin-set password activates the account
    obj.failed_login_count = 0
    audit(db, "password.changed", subject=(subject_type, obj.id), request=request,
          detail={"via": "admin_set"})
    db.commit()


@app.put("/admin/students/{student_id}/set-password")
async def set_student_password(student_id: int, request: Request, db: Session = Depends(get_db),
                               current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    new_pass = body.get("password", "").strip()
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    _admin_force_set_password(db, "student", student, new_pass, request)
    return {"message": "Password updated", "student_id": student_id}


@app.put("/admin/staff/{staff_id}/set-password")
async def set_staff_password(staff_id: int, request: Request, db: Session = Depends(get_db),
                            current = Depends(require_roles("super_admin", "center_admin"))):
    body = await request.json()
    new_pass = body.get("password", "").strip()
    staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    _admin_force_set_password(db, "staff", staff, new_pass, request)
    return {"message": "Password updated", "staff_id": staff_id}


@app.post("/admin/bulk-resend-activations")
async def bulk_resend_activations(request: Request, db: Session = Depends(get_db)):
    """Re-send activation emails to every account still pending activation.
    Replaces the old default-password bulk tool — no passwords are generated."""
    sent = 0
    for subject_type, model in (("student", Student), ("staff", Staff)):
        for obj in db.query(model).filter(model.account_status == "pending_activation").all():
            if not obj.email:
                continue
            raw = issue_auth_token(db, subject_type, obj.id, "activation")
            _send_activation_email(obj.email, display_name(subject_type, obj), raw)
            audit(db, "activation.resent", subject=(subject_type, obj.id), request=request)
            sent += 1
    db.commit()
    return {"activation_emails_sent": sent}


# ── Phase 5A: SuperAdmin Cross-Center Stats ──
@app.get("/admin/super-admin/stats")
def super_admin_stats(db: Session = Depends(get_db),
                     current = Depends(require_roles("super_admin"))):
    """Cross-center KPIs for super admin dashboard. Only super_admin can access."""
    # Global stats
    total_centers = db.query(Center).filter(Center.is_active == True).count()
    total_students = db.query(Student).count()
    total_staff = db.query(Staff).count()
    total_revenue = db.query(func.sum(Invoice.total_amount)).filter(Invoice.status == "paid").scalar() or 0

    # Per-center stats
    centers = db.query(Center).filter(Center.is_active == True).order_by(Center.created_at.desc()).all()
    center_stats = []

    for center in centers:
        center_students = db.query(Student).filter(Student.center_id == center.id).count()
        center_staff = db.query(Staff).filter(Staff.center_id == center.id).count()
        center_revenue = (
            db.query(func.sum(Invoice.total_amount))
            .join(Student)
            .filter(Invoice.status == "paid", Student.center_id == center.id)
            .scalar() or 0
        )
        center_outstanding = (
            db.query(func.sum(Invoice.total_amount))
            .join(Student)
            .filter(Invoice.status.in_(["pending", "overdue"]), Student.center_id == center.id)
            .scalar() or 0
        )

        center_stats.append({
            "center_id": center.id,
            "center_name": center.name,
            "students": center_students,
            "staff": center_staff,
            "revenue": float(center_revenue),
            "outstanding": float(center_outstanding),
            "created_at": center.created_at.isoformat() if center.created_at else None,
        })

    return {
        "global": {
            "total_centers": total_centers,
            "total_students": total_students,
            "total_staff": total_staff,
            "total_revenue": float(total_revenue),
        },
        "centers": center_stats,
    }


# ── Phase 4B: Audit Log Viewer ──
@app.get("/admin/audit-logs")
def get_audit_logs(
    page: int = 1, limit: int = 50, action: Optional[str] = None,
    from_date: Optional[str] = None, to_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current = Depends(require_roles("super_admin", "center_admin")),
):
    """Get audit logs with pagination. Center admin sees only their center's actions."""
    q = db.query(AuditLog)

    # Phase 4B: Center admin only sees audit logs for their center
    if current.get("obj").access_role == "center_admin" and current.get("obj").center_id:
        q = q.filter(AuditLog.center_id == current["obj"].center_id)

    # Filter by action (e.g., "student.created", "payment.recorded")
    if action:
        q = q.filter(AuditLog.action == action)

    # Filter by date range
    if from_date:
        q = q.filter(AuditLog.created_at >= from_date)
    if to_date:
        q = q.filter(AuditLog.created_at <= to_date)

    total = q.count()
    logs = q.order_by(AuditLog.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    return {
        "logs": [
            {
                "id": log.id,
                "action": log.action,
                "actor": f"{log.actor_type}:{log.actor_id}",
                "subject": f"{log.subject_type}:{log.subject_id}" if log.subject_type else None,
                "center_id": log.center_id,
                "ip_address": log.ip_address,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


@app.get("/admin/credentials")
def get_credentials(db: Session = Depends(get_db),
                   current = Depends(require_roles("super_admin"))):
    """Account/activation status overview for admin reference.
    Passwords are never exposed — only whether each account is set up."""
    students = db.query(Student).order_by(Student.first_name).all()
    staff    = db.query(Staff).order_by(Staff.name).all()
    return {
        "students": [
            {
                "id": s.id,
                "name": f"{s.first_name} {s.last_name}",
                "email": s.email,
                "account_status": s.account_status or "active",
                "has_password": bool(s.password_hash),
                "last_login_at": s.last_login_at.isoformat() if s.last_login_at else None,
                "login_url": "/student-login",
            }
            for s in students
        ],
        "staff": [
            {
                "id": t.id,
                "name": t.name,
                "role": t.role,
                "email": t.email,
                "account_status": t.account_status or "active",
                "has_password": bool(t.password_hash),
                "last_login_at": t.last_login_at.isoformat() if t.last_login_at else None,
                "login_url": "/teacher-login",
            }
            for t in staff
        ],
    }


# ==================== App Settings ====================

# Sensitive keys — values are masked in GET responses
_MASKED_KEYS = {"smtp_pass", "admin_password", "razorpay_key_secret"}

# Default values seeded on first load
_DEFAULTS = {
    "academy_name":         "Vama Academy",
    "academy_tagline":      "School of Music & Arts",
    "academy_email":        "techatvama@gmail.com",
    "academy_phone":        "",
    "academy_address":      "",
    "academy_website":      "",
    "academy_gst":          "",
    "academy_pan":          "",
    "branches":             '["Vama - Gunjur","Vama - Varthur","Vama - Kadubeesnahali"]',
    "smtp_host":            "smtp.gmail.com",
    "smtp_port":            "587",
    "smtp_user":            "",
    "smtp_pass":            "",
    "smtp_sender_name":     "Vama Academy",
    "default_tax_pct":      "18",
    "invoice_prefix":       "INV",
    "invoice_due_days":     "30",
    "currency_symbol":      "₹",
    "primary_color":        "#463a7a",
    "attendance_feedback":  "required_for_present",
    "session_start_hour":   "8",
    "session_end_hour":     "21",
    "razorpay_key_id":      "",
    "razorpay_key_secret":  "",
    "razorpay_enabled":     "false",
}


def _get_all_settings(db: Session) -> dict:
    rows = db.query(AppSetting).all()
    stored = {r.key: r.value for r in rows}
    result = {**_DEFAULTS, **stored}
    for k in _MASKED_KEYS:
        if k in result and result[k]:
            result[k] = "••••••••"
    return result


def _razorpay_for_center(db: Session, center_id) -> dict:
    """Return Razorpay keys for a center; falls back to global settings then env vars."""
    import os as _os
    result: dict = {}
    if center_id:
        prefix = f"center_{center_id}_razorpay."
        rows = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like(f"{prefix}%")).all()}
        result = {
            "key_id":     rows.get(f"{prefix}key_id", ""),
            "key_secret": rows.get(f"{prefix}key_secret", ""),
            "enabled":    rows.get(f"{prefix}enabled", ""),
        }
    # Fall back to global settings
    if not result.get("key_id"):
        global_row = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.in_(["razorpay_key_id", "razorpay_key_secret", "razorpay_enabled"])).all()}
        result["key_id"]     = global_row.get("razorpay_key_id", "")     or _os.getenv("RAZORPAY_KEY_ID", "")
        result["key_secret"] = global_row.get("razorpay_key_secret", "") or _os.getenv("RAZORPAY_KEY_SECRET", "")
        result["enabled"]    = global_row.get("razorpay_enabled", "false")
    return result


@app.get("/admin/razorpay-settings")
def get_razorpay_settings(center_id: int = None, db: Session = Depends(get_db)):
    """Get Razorpay settings for a specific center (or global if no center_id)."""
    if center_id:
        prefix = f"center_{center_id}_razorpay."
        rows = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.like(f"{prefix}%")).all()}
        return {
            "center_id":  center_id,
            "key_id":     rows.get(f"{prefix}key_id", ""),
            "key_secret": "••••••••" if rows.get(f"{prefix}key_secret") else "",
            "enabled":    rows.get(f"{prefix}enabled", "false"),
        }
    # Global fallback
    global_row = {r.key: r.value for r in db.query(AppSetting).filter(AppSetting.key.in_(["razorpay_key_id", "razorpay_key_secret", "razorpay_enabled"])).all()}
    return {
        "center_id":  None,
        "key_id":     global_row.get("razorpay_key_id", ""),
        "key_secret": "••••••••" if global_row.get("razorpay_key_secret") else "",
        "enabled":    global_row.get("razorpay_enabled", "false"),
    }


@app.put("/admin/razorpay-settings")
async def put_razorpay_settings(request: Request, db: Session = Depends(get_db)):
    """Save Razorpay keys for a center or globally. Body: {center_id?, key_id, key_secret, enabled}."""
    body = await request.json()
    center_id = body.get("center_id")

    def _upsert(key: str, value: str):
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))

    if center_id:
        prefix = f"center_{center_id}_razorpay."
        _upsert(f"{prefix}key_id", body.get("key_id", ""))
        if body.get("key_secret") and body["key_secret"] != "••••••••":
            _upsert(f"{prefix}key_secret", body["key_secret"])
        _upsert(f"{prefix}enabled", str(body.get("enabled", "false")).lower())
    else:
        _upsert("razorpay_key_id", body.get("key_id", ""))
        if body.get("key_secret") and body["key_secret"] != "••••••••":
            _upsert("razorpay_key_secret", body["key_secret"])
        _upsert("razorpay_enabled", str(body.get("enabled", "false")).lower())

    db.commit()
    return get_razorpay_settings(center_id=center_id, db=db)


@app.get("/admin/settings")
def get_settings(db: Session = Depends(get_db)):
    return _get_all_settings(db)


@app.put("/admin/settings")
async def update_settings(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    for key, value in body.items():
        if key in _MASKED_KEYS and value == "••••••••":
            continue  # Don't overwrite with the masked placeholder
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = str(value) if value is not None else None
        else:
            db.add(AppSetting(key=key, value=str(value) if value is not None else None))
    db.commit()
    return _get_all_settings(db)


@app.post("/admin/settings/test-email")
async def test_email(request: Request, db: Session = Depends(get_db)):
    import smtplib, os
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    body = await request.json()
    to_email = body.get("to_email", "")
    if not to_email:
        raise HTTPException(status_code=400, detail="to_email is required")

    # Read SMTP from settings DB (fall back to env vars)
    rows = {r.key: r.value for r in db.query(AppSetting).all()}
    smtp_host = rows.get("smtp_host") or os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(rows.get("smtp_port") or os.getenv("SMTP_PORT", "587"))
    smtp_user = rows.get("smtp_user") or os.getenv("SMTP_USER", "")
    smtp_pass = rows.get("smtp_pass") or os.getenv("SMTP_PASS", "")
    sender    = rows.get("smtp_sender_name") or rows.get("academy_name") or "Vama Academy"

    if not smtp_user or not smtp_pass:
        raise HTTPException(status_code=500, detail="SMTP credentials not configured. Set smtp_user and smtp_pass in Settings.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "✅ Vama Academy – Email test successful"
    msg["From"]    = f"{sender} <{smtp_user}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(
        f"<p>Hello!<br><br>This is a test email from <strong>Vama Academy</strong>.<br>"
        f"If you received this, your SMTP settings are configured correctly.</p>",
        "html"
    ))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        return {"success": True, "message": f"Test email sent to {to_email}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SMTP error: {str(e)}")


# ==================== Health ====================

@app.get("/")
def health():
    return {"status": "ok", "service": "Vama Optimus API"}
