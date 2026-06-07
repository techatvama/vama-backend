from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional
import gspread

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== DB Setup ====================
from database import engine, get_db, Base
from models import (
    Center, Staff, Student, Grade, Subject, ExamSession,
    Syllabus, SyllabusModule, SyllabusContent, StudentProgress,
    Batch, ClassSession, StudentEnrollment, Attendance, Material,
    Package, StudentPackage, Invoice, Subscription, AppSetting
)
import crud
from schemas import StaffCreate, StaffResponse


@app.on_event("startup")
async def startup_event():
    try:
        Base.metadata.create_all(bind=engine)
        _run_migrations()
        _seed_defaults()
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
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
            except Exception:
                pass
        conn.commit()


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


# ==================== Google Sheets (Legacy) ====================
from sheets_service import get_sheets_service


class AddRowRequest(BaseModel):
    timestamp: str
    center: str
    email: str
    first_name: str
    last_name: str
    gender: str
    course: str
    class_frequency: str
    parent_name: str
    complete_address: str
    city: str
    state: str
    state_code: str
    primary_phone: str
    emergency_contact: str
    blood_group: str
    allergies: str
    refferer: str
    acknowledgement: str


@app.get("/read-sheet")
def read_sheet():
    service = get_sheets_service()
    data = service.get_all_students()
    return {"data": data}


@app.post("/add-student")
async def add_student(request: Request):
    body = await request.json()
    service = get_sheets_service()
    success = service.add_student(body)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to add student to Google Sheets")
    return {"message": "Student added successfully"}


@app.put("/update-cell")
def update_cell(row: int, col: int, value: str):
    service = get_sheets_service()
    service.worksheet.update_cell(row, col, value)
    return {"message": f"Updated cell {row}, {col} with '{value}'"}


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
        raise HTTPException(status_code=401, detail="No account found with this email")

    # If no password set yet, allow login (first-time access)
    if student.password is not None and student.password != password:
        raise HTTPException(status_code=401, detail="Incorrect password")

    return {
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


@app.post("/teacher/login")
async def teacher_login(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    teacher = db.query(Staff).filter(
        Staff.email.ilike(email)
    ).first()

    if not teacher:
        raise HTTPException(status_code=401, detail="No account found with this email")

    if teacher.password is not None and teacher.password != password:
        raise HTTPException(status_code=401, detail="Incorrect password")

    return {
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
def get_students(center_id: Optional[int] = None, db: Session = Depends(get_db)):
    """List all students, optionally filtered by center."""
    q = db.query(Student)
    if center_id:
        q = q.filter(Student.center_id == center_id)
    students = q.order_by(Student.first_name).all()
    return [
        {
            "id": s.id,
            "first_name": s.first_name,
            "last_name": s.last_name,
            "email": s.email,
            "primary_phone_number": s.primary_phone_number or "",
            "desired_course": s.desired_course or "",
            "instrument": s.instrument or s.desired_course or "",
            "nearest_vama_center": s.nearest_vama_center or "",
            "current_grade": s.current_grade or "Debut",
            "syllabus_type": s.syllabus_type or "Trinity",
            "is_exam_student": s.is_exam_student or False,
            "exam_date": s.exam_date,
            "teacher_id": s.teacher_id,
            "created_at": s.created_at.isoformat() if s.created_at else "",
        }
        for s in students
    ]


@app.post("/students")
async def create_student(request: Request, db: Session = Depends(get_db)):
    """Create a new student in the database."""
    body = await request.json()
    existing = db.query(Student).filter(Student.email == body.get("email")).first()
    if existing:
        raise HTTPException(status_code=400, detail="Student with this email already exists")

    student = Student(
        first_name=body.get("first_name", ""),
        last_name=body.get("last_name", ""),
        email=body.get("email", ""),
        primary_phone_number=body.get("primary_phone_number", ""),
        gender=body.get("gender"),
        address=body.get("address"),
        desired_course=body.get("desired_course"),
        nearest_vama_center=body.get("nearest_vama_center"),
        password=body.get("password"),
        current_grade=body.get("current_grade", "Debut"),
        syllabus_type=body.get("syllabus_type", "Trinity"),
        instrument=body.get("instrument") or body.get("desired_course"),
        teacher_id=body.get("teacher_id"),
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return {"id": student.id, "first_name": student.first_name, "last_name": student.last_name}


@app.put("/students/{student_id}")
async def update_student(student_id: int, request: Request, db: Session = Depends(get_db)):
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
        if "password" in body:
            student.password = body["password"]
        # General fields
        if "first_name" in body or "First_Name" in body:
            student.first_name = body.get("first_name") or body.get("First_Name")
        if "last_name" in body or "Last_Name" in body:
            student.last_name = body.get("last_name") or body.get("Last_Name")
        if "email" in body or "Email" in body:
            student.email = body.get("email") or body.get("Email")
        if "primary_phone_number" in body or "Primary_Phone_Number" in body:
            student.primary_phone_number = body.get("primary_phone_number") or body.get("Primary_Phone_Number")
        if "desired_course" in body or "Desired_Course" in body:
            student.desired_course = body.get("desired_course") or body.get("Desired_Course")
        if "nearest_vama_center" in body or "Nearest_Vama_Center" in body:
            student.nearest_vama_center = body.get("nearest_vama_center") or body.get("Nearest_Vama_Center")

        db.commit()
        db.refresh(student)
        return {"message": "Student updated", "id": student.id}

    # Fallback to Google Sheets if student not in DB
    try:
        service = get_sheets_service()
        success = service.update_student_by_row(student_id, body)
        if success:
            return {"message": "Student updated in Google Sheets"}
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="Student not found")


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

    # ── Active package ──────────────────────────────────────
    active_sp = db.query(StudentPackage).filter(
        StudentPackage.student_id == student_id,
        StudentPackage.status == "active"
    ).order_by(StudentPackage.created_at.desc()).first()
    package_info = None
    if active_sp:
        pkg = db.query(Package).filter(Package.id == active_sp.package_id).first()
        if pkg:
            package_info = {
                "id": pkg.id,
                "name": pkg.name,
                "sessions_total": pkg.total_sessions,
                "sessions_used": active_sp.sessions_used,
                "start_date": active_sp.start_date,
                "end_date": active_sp.end_date,
                "status": active_sp.status,
            }

    # ── Progress summary ────────────────────────────────────
    progress_records = db.query(StudentProgress).filter(StudentProgress.student_id == student_id).all()
    done_count   = sum(1 for p in progress_records if p.status == "done")
    inprog_count = sum(1 for p in progress_records if p.status == "in-progress")

    att_pct = round((grand_total_attended / grand_total_classes) * 100) if grand_total_classes else 0

    return {
        "id": student.id,
        "first_name": student.first_name,
        "last_name": student.last_name,
        "email": student.email,
        "primary_phone_number": student.primary_phone_number or "",
        "address": student.address or "",
        "date_of_birth": student.date_of_birth,
        "enrollment_date": student.created_at.isoformat() if student.created_at else None,
        "status": "active",
        "current_grade": student.current_grade or "Debut",
        "desired_course": student.desired_course or "",
        "instrument": student.instrument or student.desired_course or "",
        "syllabus_type": student.syllabus_type or "Trinity",
        "teacher": {"id": teacher.id, "name": teacher.name} if teacher else None,
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


@app.get("/teacher/{teacher_id}/sessions")
def get_teacher_sessions(
    teacher_id: int,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(ClassSession).filter(ClassSession.teacher_id == teacher_id)
    if start:
        query = query.filter(ClassSession.date >= start)
    if end:
        query = query.filter(ClassSession.date <= end)
    sessions = query.order_by(ClassSession.date, ClassSession.start_time).all()
    return [_session_to_dict(s, db) for s in sessions]


@app.get("/student/{student_id}/sessions")
def get_student_sessions(
    student_id: int,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db: Session = Depends(get_db)
):
    # Get all batch IDs the student is enrolled in
    enrollments = db.query(StudentEnrollment).filter(
        StudentEnrollment.student_id == student_id
    ).all()
    batch_ids = [e.batch_id for e in enrollments]

    if not batch_ids:
        return []

    query = db.query(ClassSession).filter(ClassSession.batch_id.in_(batch_ids))
    if start:
        query = query.filter(ClassSession.date >= start)
    if end:
        query = query.filter(ClassSession.date <= end)

    sessions = query.order_by(ClassSession.date, ClassSession.start_time).all()
    return [_session_to_dict(s, db) for s in sessions]


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
    students = db.query(Student).filter(Student.teacher_id == teacher_id).all()
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
    return [{"id": g.id, "name": g.name} for g in grades]


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


# ==================== Syllabus Admin ====================

@app.get("/admin/syllabi")
def list_syllabi(db: Session = Depends(get_db)):
    return [
        {"id": s.id, "name": s.name, "subject": s.subject, "grade_name": s.grade_name, "syllabus_type": s.syllabus_type}
        for s in db.query(Syllabus).all()
    ]


@app.post("/admin/syllabi")
async def create_syllabus(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    syllabus = Syllabus(
        name=body.get("name", ""),
        subject=body.get("subject"),
        grade_name=body.get("grade_name"),
        syllabus_type=body.get("syllabus_type"),
    )
    db.add(syllabus)
    db.commit()
    db.refresh(syllabus)

    # Create modules if provided
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
            content = SyllabusContent(
                module_id=module.id,
                name=content_data.get("name", ""),
                content_type=content_data.get("content_type", "exercise"),
                weight=content_data.get("weight", 1.0),
            )
            db.add(content)

    db.commit()
    return {"id": syllabus.id, "name": syllabus.name}


# ==================== Staff ====================

@app.get("/staff")
def get_staff(center_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Staff)
    if center_id:
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
async def add_staff(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    staff_data = StaffCreate(
        name=body.get("name", f"{body.get('firstName', '')} {body.get('lastName', '')}"),
        role=body.get("role", ""),
        phone=body.get("phone", ""),
        email=body.get("email", ""),
        calendar=body.get("calendar", True),
        firstName=body.get("firstName", ""),
        lastName=body.get("lastName", ""),
        takesClasses=body.get("takesClasses", True)
    )
    new_staff = crud.create_staff(db, staff_data)
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
async def update_staff(staff_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    db_staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not db_staff:
        raise HTTPException(status_code=404, detail="Staff not found")

    for field, col in [
        ("name", "name"), ("role", "role"), ("phone", "phone"),
        ("email", "email"), ("calendar", "calendar"),
        ("password", "password"),
    ]:
        if field in body:
            setattr(db_staff, col, body[field])
    if "firstName" in body:
        db_staff.first_name = body["firstName"]
    if "lastName" in body:
        db_staff.last_name = body["lastName"]
    if "takesClasses" in body:
        db_staff.takes_classes = body["takesClasses"]

    db.commit()
    db.refresh(db_staff)
    return {
        "id": db_staff.id, "name": db_staff.name, "role": db_staff.role,
        "email": db_staff.email, "phone": db_staff.phone, "calendar": db_staff.calendar,
        "firstName": db_staff.first_name, "lastName": db_staff.last_name,
        "takesClasses": db_staff.takes_classes
    }


# ==================== Batches & Class Sessions ====================

@app.get("/batches")
def get_batches(center_id: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Batch)
    if center_id:
        q = q.filter(Batch.center_id == center_id)
    return [
        {"id": b.id, "name": b.name, "subject": b.subject, "teacher_id": b.teacher_id, "center_id": b.center_id}
        for b in q.all()
    ]


@app.post("/batches")
async def create_batch(request: Request, db: Session = Depends(get_db)):
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
async def create_session(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
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
    return {"id": session.id}


@app.get("/sessions/{session_id}/students")
def get_session_students(session_id: int, db: Session = Depends(get_db)):
    """Return all students who have an attendance record for this session."""
    if not db.query(ClassSession).filter(ClassSession.id == session_id).first():
        raise HTTPException(status_code=404, detail="Session not found")

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

        for s in matching:
            exists = db.query(Attendance).filter(
                Attendance.session_id == s.id,
                Attendance.student_id == student_id
            ).first()
            if not exists:
                db.add(Attendance(
                    session_id=s.id,
                    student_id=student_id,
                    status="present",
                    enrollment_type="recurring",
                ))
        db.commit()
        return {"message": f"Enrolled in {len(matching)} session(s) on the same day/time"}

    # single_session — just this one
    exists = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.student_id == student_id
    ).first()
    if not exists:
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
    db: Session = Depends(get_db)
):
    if require_feedback and not notes:
        raise HTTPException(status_code=400, detail="Feedback/notes are required before marking attendance")
    att = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.student_id == student_id
    ).first()
    if att:
        att.status = status
        att.notes = notes
    else:
        db.add(Attendance(session_id=session_id, student_id=student_id, status=status, notes=notes))
    db.commit()
    return {"message": "Attendance updated"}


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


# ==================== PAYMENT — Packages ====================

@app.get("/admin/packages")
def list_packages(db: Session = Depends(get_db)):
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
        result.append({
            "id": p.id,
            "name": p.name,
            "applicable_grades": grades,
            "applicable_courses": courses,
            "validity_days": p.validity_days,
            "total_sessions": p.total_sessions,
            "makeup_sessions": p.makeup_sessions,
            "prorate_enabled": p.prorate_enabled,
            "price": p.price,
            "tax_percentage": p.tax_percentage,
            "is_published": p.is_published,
            "is_archived": p.is_archived,
            "description": p.description or "",
        })
    return result


@app.post("/admin/packages")
async def create_package(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    import json
    pkg = Package(
        name=body["name"],
        applicable_grades=json.dumps(body.get("applicable_grades", [])),
        applicable_courses=json.dumps(body.get("applicable_courses", [])),
        validity_days=body.get("validity_days", 30),
        total_sessions=body.get("total_sessions", 8),
        makeup_sessions=body.get("makeup_sessions", 0),
        prorate_enabled=body.get("prorate_enabled", False),
        price=float(body["price"]),
        tax_percentage=float(body.get("tax_percentage", 18)),
        is_published=body.get("is_published", False),
        description=body.get("description"),
    )
    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    return {"id": pkg.id, "message": "Package created"}


@app.put("/admin/packages/{pkg_id}")
async def update_package(pkg_id: int, request: Request, db: Session = Depends(get_db)):
    import json
    body = await request.json()
    pkg = db.query(Package).filter(Package.id == pkg_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found")
    for field in ["name", "validity_days", "total_sessions", "makeup_sessions", "prorate_enabled", "price", "tax_percentage", "is_published", "is_archived", "description"]:
        if field in body:
            setattr(pkg, field, body[field])
    if "applicable_grades" in body:
        pkg.applicable_grades = json.dumps(body["applicable_grades"])
    if "applicable_courses" in body:
        pkg.applicable_courses = json.dumps(body["applicable_courses"])
    db.commit()
    return {"message": "Updated"}


# ==================== PAYMENT — Invoices ====================

@app.get("/admin/invoices")
def list_invoices(status: Optional[str] = None, center_id: Optional[int] = None, db: Session = Depends(get_db)):
    import json
    q = db.query(Invoice)
    if status and status != "all":
        q = q.filter(Invoice.status == status)
    if center_id:
        center_student_ids = [s.id for s in db.query(Student).filter(Student.center_id == center_id).all()]
        q = q.filter(Invoice.student_id.in_(center_student_ids))
    invoices = q.order_by(Invoice.created_at.desc()).all()
    students_map = {s.id: s for s in db.query(Student).all()}
    result = []
    for inv in invoices:
        student = students_map.get(inv.student_id)
        result.append({
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
        })
    return result


@app.post("/admin/invoices")
async def create_invoice(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    import random, string
    inv_num = body.get("invoice_number") or ("INV-" + "".join(random.choices(string.digits, k=5)))
    amount = float(body["amount"])
    tax = float(body.get("tax_amount", amount * 0.18))
    discount = float(body.get("discount_amount", 0))
    total = amount + tax - discount
    inv = Invoice(
        invoice_number=inv_num,
        student_id=int(body["student_id"]),
        package_id=body.get("package_id"),
        amount=amount,
        tax_amount=tax,
        discount_amount=discount,
        total_amount=total,
        status=body.get("status", "pending"),
        payment_type=body.get("payment_type"),
        payment_mode=body.get("payment_mode"),
        description=body.get("description"),
        due_date=body["due_date"],
        issue_date=body.get("issue_date", str(body.get("issue_date", ""))),
        sessions_count=body.get("sessions_count"),
        notes=body.get("notes"),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return {"id": inv.id, "invoice_number": inv_num}


@app.patch("/admin/invoices/{inv_id}")
async def update_invoice(inv_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    for field in ["status", "paid_date", "paid_amount", "notes"]:
        if field in body:
            setattr(inv, field, body[field])
    if body.get("status") == "paid":
        inv.paid_amount = inv.total_amount
    db.commit()
    return {"message": "Updated"}


@app.post("/admin/invoices/{inv_id}/send-email")
async def send_invoice_email(inv_id: int, request: Request, db: Session = Depends(get_db)):
    import smtplib, os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    body = await request.json()
    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    student = db.query(Student).filter(Student.id == inv.student_id).first()
    to_email = body.get("to_email") or (student.email if student else None)
    if not to_email:
        raise HTTPException(status_code=400, detail="No email address provided")

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        raise HTTPException(
            status_code=500,
            detail="Email not configured. Set SMTP_USER and SMTP_PASS in backend .env file."
        )

    subject = body.get("subject", f"Invoice {inv.invoice_number} – Vama Academy")
    text_body = body.get("body", f"Please find your invoice {inv.invoice_number} attached.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {str(e)}")

    return {"success": True, "message": f"Invoice sent to {to_email}"}


# ==================== PAYMENT — Subscriptions ====================

@app.get("/admin/subscriptions")
def list_subscriptions(db: Session = Depends(get_db)):
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
        })
    return result


@app.post("/admin/subscriptions")
async def create_subscription(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    sub = Subscription(
        student_id=int(body["student_id"]),
        package_id=body.get("package_id"),
        plan_name=body["plan_name"],
        billing_cycle=body["billing_cycle"],
        amount=float(body["amount"]),
        sessions_total=body.get("sessions_total"),
        start_date=body.get("start_date", ""),
        renewal_date=body.get("renewal_date"),
        auto_renew=body.get("auto_renew", True),
        status="active",
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"id": sub.id}


@app.put("/admin/subscriptions/{sub_id}")
async def update_subscription(sub_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    sub = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Not found")
    for field in ["plan_name", "billing_cycle", "amount", "sessions_total", "start_date", "renewal_date", "status", "auto_renew"]:
        if field in body:
            setattr(sub, field, body[field])
    db.commit()
    return {"message": "Updated"}


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
                "makeup_sessions": pkg.makeup_sessions,
                "prorate_enabled": pkg.prorate_enabled,
                "price": pkg.price,
                "tax_percentage": pkg.tax_percentage,
                "total_with_tax": round(pkg.price * (1 + pkg.tax_percentage / 100), 2),
                "description": pkg.description,
            })
    return result


# ==================== PAYMENT — Razorpay create order ====================

@app.post("/student/payments/create-order")
async def create_razorpay_order(request: Request, db: Session = Depends(get_db)):
    import razorpay, os, json as _json
    body = await request.json()
    package_id = body.get("package_id")
    student_id = body.get("student_id")

    pkg = db.query(Package).filter(Package.id == package_id).first()
    if not pkg:
        raise HTTPException(status_code=404, detail="Package not found")

    amount_paise = int(round(pkg.price * (1 + pkg.tax_percentage / 100) * 100))

    key_id = os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "placeholder_secret")

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
    import razorpay, os, hashlib, hmac, random, string
    body = await request.json()

    razorpay_order_id = body.get("razorpay_order_id")
    razorpay_payment_id = body.get("razorpay_payment_id")
    razorpay_signature = body.get("razorpay_signature")
    package_id = body.get("package_id")
    student_id = body.get("student_id")
    test_mode = body.get("test_mode", False)

    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "placeholder_secret")

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

    # Activate / create StudentPackage
    from datetime import date, timedelta
    start = date.today()
    end = start + timedelta(days=pkg.validity_days or 30)

    sp = db.query(StudentPackage).filter(
        StudentPackage.student_id == student_id,
        StudentPackage.status == "active"
    ).first()
    if sp:
        sp.status = "cancelled"

    new_sp = StudentPackage(
        student_id=student_id,
        package_id=package_id,
        start_date=str(start),
        end_date=str(end),
        sessions_used=0,
        makeup_used=0,
        status="active",
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
    }


# ==================== PAYMENT — Student view ====================

@app.get("/student/{student_id}/payments")
def student_payments(student_id: int, db: Session = Depends(get_db)):
    invoices = db.query(Invoice).filter(Invoice.student_id == student_id).order_by(Invoice.created_at.desc()).all()
    sub = db.query(Subscription).filter(Subscription.student_id == student_id, Subscription.status == "active").first()
    sp = db.query(StudentPackage).filter(StudentPackage.student_id == student_id, StudentPackage.status == "active").first()
    pkg = db.query(Package).filter(Package.id == sp.package_id).first() if sp else None

    active_package = None
    if pkg and sp:
        active_package = {
            "name": pkg.name,
            "sessions_total": pkg.total_sessions,
            "sessions_used": sp.sessions_used,
            "makeup_sessions": pkg.makeup_sessions,
            "makeup_used": sp.makeup_used,
            "validity_until": sp.end_date,
            "start_date": sp.start_date,
            "price": pkg.price,
        }

    return {
        "active_package": active_package,
        "invoices": [{"id": i.id, "invoice_number": i.invoice_number, "amount": i.amount, "tax_amount": i.tax_amount, "discount_amount": i.discount_amount, "total_amount": i.total_amount, "status": i.status, "payment_type": i.payment_type, "issue_date": i.issue_date, "due_date": i.due_date, "paid_date": i.paid_date} for i in invoices],
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
async def create_center(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    c = Center(name=body["name"], address=body.get("address"), phone=body.get("phone"), email=body.get("email"))
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "name": c.name}


@app.put("/centers/{center_id}")
async def update_center(center_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    c = db.query(Center).filter(Center.id == center_id).first()
    if not c: raise HTTPException(status_code=404, detail="Center not found")
    for f in ["name", "address", "phone", "email", "is_active"]:
        if f in body: setattr(c, f, body[f])
    db.commit()
    return {"message": "Updated"}


@app.put("/admin/staff/{staff_id}/access")
async def update_staff_access(staff_id: int, request: Request, db: Session = Depends(get_db)):
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
    body = await request.json()
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")

    staff = db.query(Staff).filter(Staff.email.ilike(email)).first()
    if not staff:
        raise HTTPException(status_code=401, detail="No account found with this email")
    if staff.password is not None and staff.password != password:
        raise HTTPException(status_code=401, detail="Incorrect password")

    access_role = staff.access_role or "teacher"
    if access_role not in ("super_admin", "center_admin"):
        raise HTTPException(status_code=403, detail="This account does not have admin access")

    center = None
    if staff.center_id:
        c = db.query(Center).filter(Center.id == staff.center_id).first()
        if c: center = {"id": c.id, "name": c.name}

    return {
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

@app.put("/admin/students/{student_id}/set-password")
async def set_student_password(student_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    new_pass = body.get("password", "").strip()
    if not new_pass:
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    student.password = new_pass
    db.commit()
    return {"message": "Password updated", "student_id": student_id}


@app.put("/admin/staff/{staff_id}/set-password")
async def set_staff_password(staff_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    new_pass = body.get("password", "").strip()
    if not new_pass:
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    staff.password = new_pass
    db.commit()
    return {"message": "Password updated", "staff_id": staff_id}


@app.post("/admin/bulk-set-default-passwords")
async def bulk_set_default_passwords(request: Request, db: Session = Depends(get_db)):
    """Set default password for all students/staff who have no password.
    Default: vama@<phone_last4> or vama@1234 if no phone."""
    body = await request.json()
    default_pass = body.get("default_password", "vama@1234")
    override_all = body.get("override_all", False)  # if True, reset everyone

    students = db.query(Student).all()
    staff    = db.query(Staff).all()

    updated_students, updated_staff = [], []

    for s in students:
        if override_all or not s.password:
            s.password = default_pass
            updated_students.append({"id": s.id, "name": f"{s.first_name} {s.last_name}", "email": s.email, "password": default_pass})

    for t in staff:
        if override_all or not t.password:
            t.password = default_pass
            updated_staff.append({"id": t.id, "name": t.name, "email": t.email, "password": default_pass})

    db.commit()
    return {
        "updated_students": len(updated_students),
        "updated_staff": len(updated_staff),
        "students": updated_students,
        "staff": updated_staff,
    }


@app.get("/admin/credentials")
def get_credentials(db: Session = Depends(get_db)):
    """Return all login credentials for admin reference."""
    students = db.query(Student).order_by(Student.first_name).all()
    staff    = db.query(Staff).order_by(Staff.name).all()
    return {
        "students": [
            {
                "id": s.id,
                "name": f"{s.first_name} {s.last_name}",
                "email": s.email,
                "has_password": bool(s.password),
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
                "has_password": bool(t.password),
                "login_url": "/teacher-login",
            }
            for t in staff
        ],
    }


# ==================== App Settings ====================

# Sensitive keys — values are masked in GET responses
_MASKED_KEYS = {"smtp_pass", "admin_password"}

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
}


def _get_all_settings(db: Session) -> dict:
    rows = db.query(AppSetting).all()
    stored = {r.key: r.value for r in rows}
    # Merge defaults for any missing key
    result = {**_DEFAULTS, **stored}
    # Mask sensitive keys
    for k in _MASKED_KEYS:
        if k in result and result[k]:
            result[k] = "••••••••"
    return result


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
