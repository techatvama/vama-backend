"""Enrollment module — single source of truth for student learning journeys.

A student may hold multiple LearningEnrollments, one per subject.
Each enrollment carries its own teacher, syllabus, grade, and auto-mapped
fee package; changes propagate automatically to all consuming modules.

Router prefix: /admin/enrollments  (admin CRUD)
               /teacher/enrollments (teacher grade/syllabus updates)
               /student/{id}/enrollments (student portal read)
               /admin/analytics/enrollments (analytics)
"""
import json
import logging
from datetime import date as _date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import (
    LearningEnrollment, Student, Staff, Package,
    StudentInstructor, Center,
)
from auth import require_roles, audit

logger = logging.getLogger("enrollment")

router = APIRouter()


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _auto_package(db: Session, subject: str, grade: str) -> Optional[int]:
    """Return the best matching published Package id for (subject, grade), or None."""
    pkgs = db.query(Package).filter(
        Package.is_published == True,
        Package.is_archived == False,
    ).all()
    for pkg in pkgs:
        try:
            grades = json.loads(pkg.applicable_grades or "[]")
            courses = json.loads(pkg.applicable_courses or "[]")
        except (ValueError, TypeError):
            continue
        if grade in grades and subject in courses:
            return pkg.id
    return None


def _sync_student_instructor(db: Session, enrollment: LearningEnrollment):
    """Keep student_instructors in sync so existing teacher-portal views work."""
    existing = db.query(StudentInstructor).filter(
        StudentInstructor.student_id == enrollment.student_id,
        StudentInstructor.instrument == enrollment.subject,
    ).first()

    if enrollment.status == "active":
        if existing:
            existing.teacher_id = enrollment.teacher_id
        else:
            # Avoid unique-constraint violation (student+teacher+instrument).
            dup = db.query(StudentInstructor).filter(
                StudentInstructor.student_id == enrollment.student_id,
                StudentInstructor.teacher_id == enrollment.teacher_id,
                StudentInstructor.instrument == enrollment.subject,
            ).first()
            if not dup:
                db.add(StudentInstructor(
                    student_id=enrollment.student_id,
                    teacher_id=enrollment.teacher_id,
                    instrument=enrollment.subject,
                ))
    else:
        # Paused/completed: remove the instructor link so teacher portal
        # doesn't show this student as active.
        if existing and existing.teacher_id == enrollment.teacher_id:
            db.delete(existing)


def _sync_student_primary(db: Session, student_id: int):
    """Mirror the primary (oldest active) enrollment onto Student.*  fields.

    Existing code reads Student.teacher_id / instrument / current_grade /
    syllabus_type; keep them current so nothing breaks.
    """
    primary = (
        db.query(LearningEnrollment)
        .filter(
            LearningEnrollment.student_id == student_id,
            LearningEnrollment.status == "active",
        )
        .order_by(LearningEnrollment.id)
        .first()
    )
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        return
    if primary:
        student.teacher_id = primary.teacher_id
        student.instrument = primary.subject
        student.current_grade = primary.grade
        student.syllabus_type = primary.syllabus_type
    # If no active enrollment keep existing values — don't wipe them.


def _enrollment_dict(e: LearningEnrollment, db: Session) -> dict:
    student = db.query(Student).filter(Student.id == e.student_id).first()
    teacher = db.query(Staff).filter(Staff.id == e.teacher_id).first()
    pkg = db.query(Package).filter(Package.id == e.fee_package_id).first() if e.fee_package_id else None
    center = db.query(Center).filter(Center.id == e.center_id).first() if e.center_id else None
    return {
        "id": e.id,
        "student_id": e.student_id,
        "student_name": f"{student.first_name} {student.last_name}" if student else None,
        "teacher_id": e.teacher_id,
        "teacher_name": teacher.name if teacher else None,
        "subject": e.subject,
        "syllabus_type": e.syllabus_type,
        "grade": e.grade,
        "fee_package_id": e.fee_package_id,
        "fee_package_name": pkg.name if pkg else None,
        "fee_package_price": pkg.price if pkg else None,
        "center_id": e.center_id,
        "center_name": center.name if center else None,
        "status": e.status,
        "start_date": e.start_date,
        "end_date": e.end_date,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


# ─────────────────────────────────────────────
# Admin CRUD
# ─────────────────────────────────────────────

@router.post("/admin/enrollments")
async def create_enrollment(
    request: Request,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin")),
):
    """Create a new learning enrollment for a student in one subject."""
    body = await request.json()

    student_id = body.get("student_id")
    teacher_id = body.get("teacher_id")
    subject = (body.get("subject") or "").strip()

    if not student_id:
        raise HTTPException(status_code=400, detail="student_id is required")
    if not teacher_id:
        raise HTTPException(status_code=400, detail="teacher_id is required")
    if not subject:
        raise HTTPException(status_code=400, detail="subject is required")

    if not db.query(Student).filter(Student.id == student_id).first():
        raise HTTPException(status_code=404, detail="Student not found")
    if not db.query(Staff).filter(Staff.id == teacher_id).first():
        raise HTTPException(status_code=404, detail="Teacher not found")

    existing = db.query(LearningEnrollment).filter(
        LearningEnrollment.student_id == student_id,
        LearningEnrollment.subject == subject,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Student already has an enrollment for '{subject}'. Update the existing one instead.",
        )

    grade = body.get("grade", "Debut")
    syllabus_type = body.get("syllabus_type", "Trinity")
    center_id = body.get("center_id")

    # Auto-assign center from caller if not supplied.
    caller = current.get("obj")
    if not center_id and caller and getattr(caller, "access_role", None) == "center_admin":
        center_id = getattr(caller, "center_id", None)

    fee_package_id = body.get("fee_package_id") or _auto_package(db, subject, grade)

    enrollment = LearningEnrollment(
        student_id=student_id,
        teacher_id=teacher_id,
        subject=subject,
        syllabus_type=syllabus_type,
        grade=grade,
        fee_package_id=fee_package_id,
        center_id=center_id,
        status=body.get("status", "active"),
        start_date=body.get("start_date") or str(_date.today()),
        end_date=body.get("end_date"),
    )
    db.add(enrollment)
    db.flush()  # get id before sync

    _sync_student_instructor(db, enrollment)
    _sync_student_primary(db, student_id)
    db.commit()
    db.refresh(enrollment)

    audit(db, "enrollment.created", subject=("staff", current["id"]), request=request,
          detail={"enrollment_id": enrollment.id, "student_id": student_id, "subject": subject})
    db.commit()

    return _enrollment_dict(enrollment, db)


@router.get("/admin/enrollments")
def list_enrollments(
    student_id: Optional[int] = None,
    teacher_id: Optional[int] = None,
    subject: Optional[str] = None,
    status: Optional[str] = None,
    center_id: Optional[int] = None,
    page: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin")),
):
    """List enrollments with optional filters."""
    q = db.query(LearningEnrollment)

    caller = current.get("obj")
    if caller and getattr(caller, "access_role", None) == "center_admin" and getattr(caller, "center_id", None):
        q = q.filter(LearningEnrollment.center_id == caller.center_id)
    elif center_id:
        q = q.filter(LearningEnrollment.center_id == center_id)

    if student_id:
        q = q.filter(LearningEnrollment.student_id == student_id)
    if teacher_id:
        q = q.filter(LearningEnrollment.teacher_id == teacher_id)
    if subject:
        q = q.filter(LearningEnrollment.subject == subject)
    if status:
        q = q.filter(LearningEnrollment.status == status)

    q = q.order_by(LearningEnrollment.created_at.desc())

    if page is not None:
        total = q.count()
        rows = q.offset((page - 1) * limit).limit(limit).all()
        return {
            "items": [_enrollment_dict(e, db) for e in rows],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit,
        }

    return [_enrollment_dict(e, db) for e in q.all()]


@router.get("/admin/enrollments/{enrollment_id}")
def get_enrollment(
    enrollment_id: int,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin")),
):
    e = db.query(LearningEnrollment).filter(LearningEnrollment.id == enrollment_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    return _enrollment_dict(e, db)


@router.put("/admin/enrollments/{enrollment_id}")
async def update_enrollment(
    enrollment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin")),
):
    """Update any enrollment field. Grade or syllabus changes auto-resync fee package."""
    e = db.query(LearningEnrollment).filter(LearningEnrollment.id == enrollment_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    body = await request.json()
    grade_changed = False
    teacher_changed = False

    if "teacher_id" in body:
        new_teacher = db.query(Staff).filter(Staff.id == body["teacher_id"]).first()
        if not new_teacher:
            raise HTTPException(status_code=404, detail="Teacher not found")
        e.teacher_id = body["teacher_id"]
        teacher_changed = True

    if "subject" in body:
        new_subject = (body["subject"] or "").strip()
        if new_subject != e.subject:
            # Check uniqueness for new subject
            conflict = db.query(LearningEnrollment).filter(
                LearningEnrollment.student_id == e.student_id,
                LearningEnrollment.subject == new_subject,
                LearningEnrollment.id != enrollment_id,
            ).first()
            if conflict:
                raise HTTPException(status_code=409, detail=f"Student already enrolled in '{new_subject}'")
            e.subject = new_subject
            grade_changed = True  # re-map package on subject change too

    if "grade" in body:
        e.grade = body["grade"]
        grade_changed = True

    if "syllabus_type" in body:
        e.syllabus_type = body["syllabus_type"]

    if "status" in body:
        e.status = body["status"]

    if "start_date" in body:
        e.start_date = body["start_date"]

    if "end_date" in body:
        e.end_date = body["end_date"]

    if "center_id" in body:
        e.center_id = body["center_id"]

    # Auto-remap package when grade or subject changed, unless caller forced one.
    if "fee_package_id" in body:
        e.fee_package_id = body["fee_package_id"]
    elif grade_changed:
        mapped = _auto_package(db, e.subject, e.grade)
        if mapped:
            e.fee_package_id = mapped

    _sync_student_instructor(db, e)
    _sync_student_primary(db, e.student_id)
    db.commit()
    db.refresh(e)

    audit(db, "enrollment.updated", subject=("staff", current["id"]), request=request,
          detail={"enrollment_id": e.id, "student_id": e.student_id, "subject": e.subject})
    db.commit()

    return _enrollment_dict(e, db)


@router.delete("/admin/enrollments/{enrollment_id}")
async def delete_enrollment(
    enrollment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin")),
):
    e = db.query(LearningEnrollment).filter(LearningEnrollment.id == enrollment_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    student_id = e.student_id
    e.status = "completed"
    _sync_student_instructor(db, e)
    _sync_student_primary(db, student_id)
    db.commit()

    audit(db, "enrollment.deleted", subject=("staff", current["id"]), request=request,
          detail={"enrollment_id": enrollment_id, "student_id": student_id})
    db.commit()

    return {"message": "Enrollment marked completed", "id": enrollment_id}


@router.get("/admin/students/{student_id}/enrollments")
def student_enrollments(
    student_id: int,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin", "teacher")),
):
    """All enrollments for a specific student."""
    rows = (
        db.query(LearningEnrollment)
        .filter(LearningEnrollment.student_id == student_id)
        .order_by(LearningEnrollment.created_at)
        .all()
    )
    return [_enrollment_dict(e, db) for e in rows]


# ─────────────────────────────────────────────
# Teacher portal — grade / syllabus updates
# ─────────────────────────────────────────────

@router.get("/teacher/{teacher_id}/learning-enrollments")
def teacher_learning_enrollments(
    teacher_id: int,
    status: Optional[str] = "active",
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin", "teacher")),
):
    """All enrollments assigned to a teacher, for the teacher portal."""
    q = db.query(LearningEnrollment).filter(
        LearningEnrollment.teacher_id == teacher_id
    )
    if status:
        q = q.filter(LearningEnrollment.status == status)
    rows = q.order_by(LearningEnrollment.subject, LearningEnrollment.grade).all()
    return [_enrollment_dict(e, db) for e in rows]


@router.patch("/teacher/enrollments/{enrollment_id}/grade")
async def teacher_update_grade(
    enrollment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin", "teacher")),
):
    """Teacher updates a student's grade. Auto-syncs fee package, Student record,
    and StudentInstructor — consistent data across all modules."""
    body = await request.json()
    grade = (body.get("grade") or "").strip()
    if not grade:
        raise HTTPException(status_code=400, detail="grade is required")

    e = db.query(LearningEnrollment).filter(LearningEnrollment.id == enrollment_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Teachers may only update enrollments assigned to them.
    caller = current.get("obj")
    if caller and getattr(caller, "access_role", None) == "teacher":
        if e.teacher_id != caller.id:
            raise HTTPException(status_code=403, detail="You can only update your own students")

    e.grade = grade
    mapped = _auto_package(db, e.subject, grade)
    if mapped:
        e.fee_package_id = mapped

    _sync_student_primary(db, e.student_id)
    db.commit()
    db.refresh(e)

    audit(db, "enrollment.grade_updated", subject=("staff", current["id"]), request=request,
          detail={"enrollment_id": e.id, "student_id": e.student_id, "grade": grade,
                  "fee_package_id": e.fee_package_id})
    db.commit()

    return _enrollment_dict(e, db)


@router.patch("/teacher/enrollments/{enrollment_id}/syllabus")
async def teacher_update_syllabus(
    enrollment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin", "teacher")),
):
    """Teacher updates a student's syllabus. Syncs Student record immediately."""
    body = await request.json()
    syllabus_type = (body.get("syllabus_type") or "").strip()
    if not syllabus_type:
        raise HTTPException(status_code=400, detail="syllabus_type is required")
    if syllabus_type not in ("RSL", "Trinity"):
        raise HTTPException(status_code=400, detail="syllabus_type must be RSL or Trinity")

    e = db.query(LearningEnrollment).filter(LearningEnrollment.id == enrollment_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    caller = current.get("obj")
    if caller and getattr(caller, "access_role", None) == "teacher":
        if e.teacher_id != caller.id:
            raise HTTPException(status_code=403, detail="You can only update your own students")

    e.syllabus_type = syllabus_type
    _sync_student_primary(db, e.student_id)
    db.commit()
    db.refresh(e)

    audit(db, "enrollment.syllabus_updated", subject=("staff", current["id"]), request=request,
          detail={"enrollment_id": e.id, "student_id": e.student_id, "syllabus_type": syllabus_type})
    db.commit()

    return _enrollment_dict(e, db)


# ─────────────────────────────────────────────
# Student portal — read-only
# ─────────────────────────────────────────────

@router.get("/student/{student_id}/learning-enrollments")
def student_learning_enrollments(
    student_id: int,
    db: Session = Depends(get_db),
):
    """Return a student's active enrollments for the student portal.

    Each entry tells the portal exactly which subject, teacher, grade,
    syllabus, and fee package apply — and nothing more. Existing portal
    endpoints that need to filter by subject should join on this data.
    """
    rows = (
        db.query(LearningEnrollment)
        .filter(
            LearningEnrollment.student_id == student_id,
            LearningEnrollment.status == "active",
        )
        .order_by(LearningEnrollment.id)
        .all()
    )
    return [_enrollment_dict(e, db) for e in rows]


# ─────────────────────────────────────────────
# Admin analytics
# ─────────────────────────────────────────────

@router.get("/admin/analytics/enrollments")
def enrollment_analytics(
    center_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current=Depends(require_roles("super_admin", "center_admin")),
):
    """Full enrollment analytics for admin dashboards.

    Returns all key metrics derived from LearningEnrollment as the master record.
    No UI change required — these endpoints feed future dashboards.
    """
    caller = current.get("obj")
    effective_center = center_id
    if caller and getattr(caller, "access_role", None) == "center_admin" and getattr(caller, "center_id", None):
        effective_center = caller.center_id

    base = db.query(LearningEnrollment)
    if effective_center:
        base = base.filter(LearningEnrollment.center_id == effective_center)

    all_rows: List[LearningEnrollment] = base.all()

    # ── Counts by status ──────────────────────────────────────────────────────
    active   = [e for e in all_rows if e.status == "active"]
    paused   = [e for e in all_rows if e.status == "paused"]
    completed = [e for e in all_rows if e.status == "completed"]

    # ── By subject ────────────────────────────────────────────────────────────
    by_subject: dict = {}
    for e in active:
        by_subject[e.subject] = by_subject.get(e.subject, 0) + 1

    # ── By teacher ────────────────────────────────────────────────────────────
    teacher_map = {s.id: s.name for s in db.query(Staff).all()}
    by_teacher: dict = {}
    students_per_teacher: dict = {}
    for e in active:
        name = teacher_map.get(e.teacher_id, f"teacher_{e.teacher_id}")
        by_teacher[name] = by_teacher.get(name, 0) + 1
        students_per_teacher.setdefault(name, set()).add(e.student_id)
    students_per_teacher = {k: len(v) for k, v in students_per_teacher.items()}

    # ── By center / branch ────────────────────────────────────────────────────
    center_map = {c.id: c.name for c in db.query(Center).all()}
    by_center: dict = {}
    for e in active:
        name = center_map.get(e.center_id, "Unassigned")
        by_center[name] = by_center.get(name, 0) + 1

    # ── By grade ──────────────────────────────────────────────────────────────
    by_grade: dict = {}
    for e in active:
        by_grade[e.grade] = by_grade.get(e.grade, 0) + 1

    # ── By syllabus ───────────────────────────────────────────────────────────
    by_syllabus: dict = {}
    for e in active:
        by_syllabus[e.syllabus_type] = by_syllabus.get(e.syllabus_type, 0) + 1

    # ── Fee package distribution ──────────────────────────────────────────────
    pkg_map = {p.id: p.name for p in db.query(Package).all()}
    by_package: dict = {}
    for e in active:
        name = pkg_map.get(e.fee_package_id, "No Package")
        by_package[name] = by_package.get(name, 0) + 1

    # ── Students per subject ──────────────────────────────────────────────────
    students_per_subject: dict = {}
    for e in active:
        students_per_subject.setdefault(e.subject, set()).add(e.student_id)
    students_per_subject = {k: len(v) for k, v in students_per_subject.items()}

    # ── Teacher workload (active enrollments per teacher) ─────────────────────
    teacher_workload: dict = {}
    for e in active:
        name = teacher_map.get(e.teacher_id, f"teacher_{e.teacher_id}")
        teacher_workload[name] = teacher_workload.get(name, 0) + 1

    # ── Monthly enrollment trends (created_at month) ──────────────────────────
    monthly: dict = {}
    for e in all_rows:
        if e.created_at:
            key = e.created_at.strftime("%Y-%m")
            monthly[key] = monthly.get(key, 0) + 1
    monthly_trend = [{"month": k, "count": v} for k, v in sorted(monthly.items())]

    return {
        "summary": {
            "total_enrollments": len(all_rows),
            "active_enrollments": len(active),
            "paused_enrollments": len(paused),
            "completed_enrollments": len(completed),
        },
        "by_subject": by_subject,
        "by_teacher": by_teacher,
        "by_center": by_center,
        "by_grade": by_grade,
        "by_syllabus": by_syllabus,
        "by_fee_package": by_package,
        "students_per_teacher": students_per_teacher,
        "students_per_subject": students_per_subject,
        "teacher_workload": teacher_workload,
        "subject_popularity": sorted(
            [{"subject": k, "count": v} for k, v in by_subject.items()],
            key=lambda x: -x["count"],
        ),
        "grade_distribution": by_grade,
        "status_summary": {
            "active": len(active),
            "paused": len(paused),
            "completed": len(completed),
        },
        "monthly_trend": monthly_trend,
    }
