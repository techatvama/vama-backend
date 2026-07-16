from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float,
    ForeignKey, Text, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Center(Base):
    __tablename__ = "centers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="center")


class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    role = Column(String, nullable=False)           # job title: Teacher, Admin, etc.
    access_role = Column(String, default="teacher") # system role: super_admin | center_admin | teacher
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    phone = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    calendar = Column(Boolean, default=True)
    takes_classes = Column(Boolean, default=True)
    password = Column(String, nullable=True)            # legacy plaintext — deprecated, do not use
    password_hash = Column(String, nullable=True)        # Argon2id hash
    account_status = Column(String, default="pending_activation")  # pending_activation | active | suspended | disabled
    failed_login_count = Column(Integer, default=0)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    center = relationship("Center", back_populates="staff")

    # Relationships
    batches = relationship("Batch", back_populates="teacher")
    sessions = relationship("ClassSession", back_populates="teacher")


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    guardian_email = Column(String, nullable=True, index=True)  # links siblings under one parent/guardian
    primary_phone_number = Column(String, nullable=True)
    date_of_birth = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    address = Column(String, nullable=True)
    desired_course = Column(String, nullable=True)
    nearest_vama_center = Column(String, nullable=True)
    preferred_mode_of_contact = Column(String, nullable=True)
    password = Column(String, nullable=True)            # legacy plaintext — deprecated, do not use
    password_hash = Column(String, nullable=True)        # Argon2id hash
    account_status = Column(String, default="pending_activation")  # pending_activation | active | suspended | disabled
    failed_login_count = Column(Integer, default=0)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    current_grade = Column(String, nullable=True, default='Debut')
    syllabus_type = Column(String, nullable=True, default='Trinity')
    is_exam_student = Column(Boolean, nullable=True, default=False)
    exam_date = Column(String, nullable=True)
    instrument = Column(String, nullable=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    teacher = relationship("Staff", foreign_keys=[teacher_id])
    progress_records = relationship("StudentProgress", back_populates="student")
    enrollments = relationship("StudentEnrollment", back_populates="student")
    materials = relationship("Material", back_populates="student")


class StudentApplication(Base):
    """A submission from the public enrollment form, awaiting admin review.

    Captures everything collected on intake; only the fields relevant to the
    Student model are copied over once an admin approves it.
    """
    __tablename__ = "student_applications"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)
    guardian_email = Column(String, nullable=True)
    primary_phone_number = Column(String, nullable=True)
    emergency_contact = Column(String, nullable=True)
    date_of_birth = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    parent_name = Column(String, nullable=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    state_code = Column(String, nullable=True)
    desired_course = Column(String, nullable=True)
    class_frequency = Column(String, nullable=True)
    nearest_vama_center = Column(String, nullable=True)
    preferred_mode_of_contact = Column(String, nullable=True)
    blood_group = Column(String, nullable=True)
    allergies = Column(String, nullable=True)
    referrer = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending | approved | rejected
    rejection_reason = Column(String, nullable=True)
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("staff.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", foreign_keys=[student_id])
    reviewer = relationship("Staff", foreign_keys=[reviewed_by])


class Grade(Base):
    __tablename__ = "grades"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    exam_board = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Syllabus(Base):
    __tablename__ = "syllabi"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    grade_name = Column(String, nullable=True)
    syllabus_type = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    modules = relationship("SyllabusModule", back_populates="syllabus", order_by="SyllabusModule.order")


class SyllabusModule(Base):
    __tablename__ = "syllabus_modules"

    id = Column(Integer, primary_key=True, index=True)
    syllabus_id = Column(Integer, ForeignKey("syllabi.id"), nullable=False)
    order = Column(Integer, default=1)
    name = Column(String, nullable=False)
    weight = Column(Float, default=1.0)

    syllabus = relationship("Syllabus", back_populates="modules")
    contents = relationship("SyllabusContent", back_populates="module", order_by="SyllabusContent.id")


class SyllabusContent(Base):
    __tablename__ = "syllabus_contents"

    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("syllabus_modules.id"), nullable=False)
    name = Column(String, nullable=False)
    content_type = Column(String, default="exercise")
    weight = Column(Float, default=1.0)

    module = relationship("SyllabusModule", back_populates="contents")
    progress_records = relationship("StudentProgress", back_populates="content")


class StudentProgress(Base):
    __tablename__ = "student_progress"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    content_id = Column(Integer, ForeignKey("syllabus_contents.id"), nullable=False)
    status = Column(String, default="not-yet")  # not-yet | in-progress | done
    notes = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    student = relationship("Student", back_populates="progress_records")
    content = relationship("SyllabusContent", back_populates="progress_records")


class StudentGradeHistory(Base):
    __tablename__ = "student_grade_history"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    from_grade = Column(String, nullable=True)   # None for the very first assignment
    to_grade = Column(String, nullable=False)
    change_type = Column(String, default="manual")  # "manual" | "auto_promote"
    changed_by = Column(String, nullable=True)       # name of teacher / admin
    notes = Column(Text, nullable=True)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student")


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    teacher = relationship("Staff", back_populates="batches")
    sessions = relationship("ClassSession", back_populates="batch")
    enrollments = relationship("StudentEnrollment", back_populates="batch")


class ClassSession(Base):
    __tablename__ = "class_sessions"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    date = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    is_published = Column(Boolean, default=True)
    status = Column(String, default="scheduled")  # scheduled | cancelled | completed
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    batch = relationship("Batch", back_populates="sessions")
    teacher = relationship("Staff", back_populates="sessions")
    attendances = relationship("Attendance", back_populates="session")


class StudentEnrollment(Base):
    __tablename__ = "student_enrollments"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=False)
    enrolled_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="enrollments")
    batch = relationship("Batch", back_populates="enrollments")


class Attendance(Base):
    __tablename__ = "attendances"
    __table_args__ = (
        Index("ix_attendance_session", "session_id"),
        Index("ix_attendance_student", "student_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("class_sessions.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    status = Column(String, default="present")  # present | absent | late
    notes = Column(Text, nullable=True)
    enrollment_type = Column(String, default="single_session")  # single_session | recurring
    marked_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ClassSession", back_populates="attendances")


class Material(Base):
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    file_type = Column(String, nullable=True)
    url = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)
    uploaded_by = Column(Integer, ForeignKey("staff.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="materials")


# ==================== PAYMENT MODELS ====================

class Package(Base):
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    applicable_grades = Column(Text, nullable=True)   # JSON: ["Debut","Grade 1"]
    applicable_courses = Column(Text, nullable=True)  # JSON: ["Piano","Guitar"]
    validity_days = Column(Integer, nullable=True)    # e.g. 30, 90, 180, 365
    total_sessions = Column(Integer, nullable=True)
    session_duration_minutes = Column(Integer, default=60)   # e.g. 30 / 45 / 60
    makeup_sessions = Column(Integer, default=0)             # allowed make-up sessions
    makeup_validity_days = Column(Integer, nullable=True)    # make-ups expire N days after class
    cancellation_window_hours = Column(Integer, default=24)  # min notice to cancel/reschedule
    prorate_enabled = Column(Boolean, default=False)
    price = Column(Float, nullable=False, default=0.0)
    tax_percentage = Column(Float, default=0.0)
    is_published = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    student_packages = relationship("StudentPackage", back_populates="package")
    invoices = relationship("Invoice", back_populates="package")
    subscriptions = relationship("Subscription", back_populates="package")


class StudentPackage(Base):
    """Tracks which package a student is on and session consumption."""
    __tablename__ = "student_packages"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=False)
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=True)
    sessions_used = Column(Integer, default=0)  # legacy/denormalized — used-count is computed live from attendance
    makeup_used = Column(Integer, default=0)
    status = Column(String, default="active")  # active | expired | exhausted | cancelled | paused
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    student = relationship("Student")
    package = relationship("Package", back_populates="student_packages")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String, unique=True, nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    amount = Column(Float, nullable=False)
    tax_amount = Column(Float, default=0.0)
    discount_amount = Column(Float, default=0.0)
    total_amount = Column(Float, nullable=False)
    status = Column(String, default="pending")  # paid | pending | overdue | partial | cancelled
    payment_type = Column(String, nullable=True)   # package name or fee label
    payment_mode = Column(String, nullable=True)   # Cash | UPI | Card
    description = Column(Text, nullable=True)
    due_date = Column(String, nullable=False)
    issue_date = Column(String, nullable=False)
    paid_date = Column(String, nullable=True)
    paid_amount = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)
    sessions_count = Column(Integer, nullable=True)
    attendance_sessions = Column(Integer, nullable=True)
    coupon_code = Column(String, nullable=True)       # deprecated — replaced by discount_percentage
    internal_notes = Column(Text, nullable=True)      # admin-only, never shown to student
    discount_percentage = Column(Float, default=0.0)
    template_id = Column(Integer, ForeignKey("invoice_templates.id"), nullable=True)
    has_installments = Column(Boolean, default=False)
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    student = relationship("Student")
    package = relationship("Package", back_populates="invoices")
    items = relationship("InvoiceItem", cascade="all, delete-orphan")
    installments = relationship("InvoiceInstallment", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    plan_name = Column(String, nullable=False)
    billing_cycle = Column(String, nullable=False)  # monthly | quarterly | half-yearly | yearly
    amount = Column(Float, nullable=False)
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=True)
    renewal_date = Column(String, nullable=True)
    status = Column(String, default="active")  # active | paused | cancelled | expired
    auto_renew = Column(Boolean, default=True)
    sessions_total = Column(Integer, nullable=True)
    sessions_used = Column(Integer, default=0)
    # Recurring-invoice settings (image-1 features)
    create_offset_days = Column(Integer, default=0)      # invoice created N days before due (0/1/2/7)
    due_offset_days = Column(Integer, default=7)         # invoice due N days after issue date
    first_invoice_date = Column(String, nullable=True)
    next_invoice_date = Column(String, nullable=True)    # when the next invoice fires
    end_type = Column(String, default="never")           # never | on_date
    timezone = Column(String, default="Asia/Calcutta")
    auto_email = Column(Boolean, default=True)
    template_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    student = relationship("Student")
    package = relationship("Package", back_populates="subscriptions")


class AppSetting(Base):
    """Key-value store for application-wide settings."""
    __tablename__ = "app_settings"

    key   = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ════════════════════════ AUTH / IDENTITY ════════════════════════
# Credentials live on the existing `staff` and `students` tables — there is no
# separate parent identity. A student account is the login used by the
# parent/guardian on the child's behalf. Siblings are linked via
# `students.guardian_email`, letting one login view/switch between children.
# A "subject" across the auth system is (subject_type, subject_id) where
# subject_type ∈ {staff, student}.

class AuthToken(Base):
    """Single-use, time-limited token for activation or password reset.
    Only the SHA-256 hash of the token is stored."""
    __tablename__ = "auth_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String, nullable=False, index=True)
    purpose = Column(String, nullable=False)        # activation | password_reset
    subject_type = Column(String, nullable=False)   # staff | student | parent
    subject_id = Column(Integer, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Immutable trail of security-relevant events."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String, nullable=False, index=True)  # user.created, account.activated, ...
    actor_type = Column(String, nullable=True)           # who did it
    actor_id = Column(Integer, nullable=True)
    subject_type = Column(String, nullable=True)         # who/what it was done to
    subject_id = Column(Integer, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    detail = Column(Text, nullable=True)                 # JSON blob of extra context
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class LoginAttempt(Base):
    """Login history — successes and failures — for security monitoring."""
    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=True, index=True)
    subject_type = Column(String, nullable=True)
    subject_id = Column(Integer, nullable=True)
    success = Column(Boolean, default=False)
    reason = Column(String, nullable=True)               # bad_password | not_active | ok | unknown_email
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ════════════════════ SCHEDULING v2 (recurring events) ════════════════════
# ClassTemplate (the recurring class) ──1:1── RecurrenceRule (expands to dates)
#       │
#       ├──1:N── ClassOccurrence (materialized instances; attendance attaches here)
#       ├──1:N── Enrollment       (student↔template; auto-applies to future occurrences)
#       └──1:N── TeacherAssignment(substitution / change history)
# Room + Holiday are support entities (Room View, holiday-skip).
# Series identity == ClassTemplate; "this & future" edits split into a new
# template chained via parent_template_id so history stays immutable.

class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    capacity = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Holiday(Base):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("date", "center_id", name="uq_holiday_date_center"),)

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False, index=True)     # YYYY-MM-DD
    name = Column(String, nullable=True)
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)  # null = all branches
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ClassTemplate(Base):
    """The recurring class definition (the 'series')."""
    __tablename__ = "class_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    course = Column(String, nullable=True)                # program/course (maps from Batch.subject)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    start_time = Column(String, nullable=False)           # HH:MM
    end_time = Column(String, nullable=False)
    capacity = Column(Integer, default=10)
    status = Column(String, default="active")             # active | archived
    parent_template_id = Column(Integer, ForeignKey("class_templates.id"), nullable=True)
    split_from_date = Column(String, nullable=True)       # set when this template was split off
    legacy_batch_id = Column(Integer, nullable=True)      # migration provenance
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    rule = relationship("RecurrenceRule", back_populates="template", uselist=False,
                        cascade="all, delete-orphan")
    occurrences = relationship("ClassOccurrence", back_populates="template")
    enrollments = relationship("Enrollment", back_populates="template")


class RecurrenceRule(Base):
    """How a template repeats. Structured columns; serializes to RFC-5545 RRULE."""
    __tablename__ = "recurrence_rules"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("class_templates.id"), unique=True, nullable=False)
    freq = Column(String, nullable=False)                 # daily | weekly | monthly | custom
    interval = Column(Integer, default=1)
    by_weekday = Column(String, nullable=True)            # CSV "MO,WE,FR"
    by_monthday = Column(Integer, nullable=True)          # for monthly
    start_date = Column(String, nullable=False)           # YYYY-MM-DD
    end_date = Column(String, nullable=True)              # null = open-ended (horizon-capped)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    template = relationship("ClassTemplate", back_populates="rule")


class ClassOccurrence(Base):
    """A single materialized class instance. Attendance attaches here.
    On migration, ids mirror class_sessions.id so attendances stay valid."""
    __tablename__ = "class_occurrences"
    __table_args__ = (
        Index("ix_occurrences_date", "date"),
        Index("ix_occurrences_template_date", "template_id", "date"),
        Index("ix_occurrences_teacher_date", "teacher_id", "date"),
        Index("ix_occurrences_room_date", "room_id", "date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("class_templates.id"), nullable=True)
    date = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=True)   # effective teacher
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    status = Column(String, default="scheduled")          # scheduled|completed|cancelled|rescheduled|holiday
    is_modified = Column(Boolean, default=False)          # this-occurrence-only edit → protected from regen
    is_makeup = Column(Boolean, default=False)
    original_date = Column(String, nullable=True)         # set on reschedule
    makeup_for_occurrence_id = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    is_published = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    template = relationship("ClassTemplate", back_populates="occurrences")


class Enrollment(Base):
    """Roster membership for a recurring class.

    Two row types:
      • Template baseline (occurrence_id NULL, kind 'include') — student is in the
        whole recurring class; auto-applies to all (incl. future) occurrences.
      • Per-occurrence override (occurrence_id set):
          kind='include' → add to this single occurrence (on top of baseline)
          kind='exclude' → remove from this single occurrence (despite baseline)
    Roster(occurrence) = (baseline − excludes) ∪ includes.
    Table is `class_enrollments` — the legacy `enrollments` table is a separate
    batch-based feature and must not be clobbered."""
    __tablename__ = "class_enrollments"
    __table_args__ = (
        UniqueConstraint("template_id", "student_id", "occurrence_id",
                         name="uq_enrollment_template_student_occ"),
        Index("ix_enrollment_template_occ_status", "template_id", "occurrence_id", "status"),
        Index("ix_enrollment_occurrence", "occurrence_id"),
        Index("ix_enrollment_student_occ", "student_id", "occurrence_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("class_templates.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    occurrence_id = Column(Integer, ForeignKey("class_occurrences.id"), nullable=True)  # NULL = baseline
    kind = Column(String, default="include")              # include | exclude (override rows)
    status = Column(String, default="active")             # active | paused | cancelled
    start_date = Column(String, nullable=True)            # effective date — gates counted occurrences
    end_date = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    template = relationship("ClassTemplate", back_populates="enrollments")


class TeacherAssignment(Base):
    """Append-only history of teacher substitutions / permanent changes."""
    __tablename__ = "teacher_assignments"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("class_templates.id"), nullable=True)
    occurrence_id = Column(Integer, ForeignKey("class_occurrences.id"), nullable=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    assignment_type = Column(String, nullable=False)      # substitute_single | change_future | original
    effective_from = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class StudentInstructor(Base):
    """A student's enrollment in one instrument with one instructor. A student
    may have several (e.g. Guitar with teacher A + Vocals with teacher B).
    `Student.teacher_id`/`Student.instrument` mirror the first/primary track for
    backward compatibility with the portal and progress views."""
    __tablename__ = "student_instructors"
    __table_args__ = (UniqueConstraint("student_id", "teacher_id", "instrument", name="uq_student_instrument_teacher"),)

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    instrument = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LearningEnrollment(Base):
    """Master record for one student's learning journey in one subject.

    A student can have multiple LearningEnrollments (one per subject).
    This is the single source of truth consumed by all other modules:
    - Subject-specific teacher assignment
    - Grade and syllabus per subject
    - Auto-mapped fee package (subject + grade → package)
    - Student portal content filtering
    """
    __tablename__ = "learning_enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "subject", name="uq_learning_enrollment_student_subject"),
    )

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    subject = Column(String, nullable=False)           # Guitar, Drums, Piano, Vocals, etc.
    syllabus_type = Column(String, nullable=False, default="Trinity")  # RSL | Trinity
    grade = Column(String, nullable=False, default="Debut")
    fee_package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)  # auto-mapped
    center_id = Column(Integer, ForeignKey("centers.id"), nullable=True)
    status = Column(String, nullable=False, default="active")  # active | paused | completed
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    student = relationship("Student", foreign_keys=[student_id])
    teacher = relationship("Staff", foreign_keys=[teacher_id])
    fee_package = relationship("Package", foreign_keys=[fee_package_id])
    center = relationship("Center", foreign_keys=[center_id])


# ════════════════════════ INVOICING v2 ════════════════════════
# Invoice (header) ──1:N── InvoiceItem (line items, sourced from packages)
#                    ├──1:N── InvoiceInstallment (optional payment plan)
#                    └──1:N── InvoicePayment   (recorded payments)

class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    package_id = Column(Integer, ForeignKey("packages.id"), nullable=True)
    label = Column(String, nullable=False)            # e.g. "BEGINNER - GRADE 2 | Guitar 1 Class/Week"
    description = Column(Text, nullable=True)
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, default=0.0)
    valid_till = Column(String, nullable=True)        # YYYY-MM-DD
    amount = Column(Float, default=0.0)               # quantity * unit_price
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InvoiceInstallment(Base):
    __tablename__ = "invoice_installments"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, default=1)
    due_date = Column(String, nullable=True)
    amount = Column(Float, default=0.0)
    paid_amount = Column(Float, default=0.0)
    status = Column(String, default="pending")        # pending | paid | partial
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PaymentMode(Base):
    """Dynamic payment methods — admin-managed, no code changes for new ones."""
    __tablename__ = "payment_modes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InvoiceTemplate(Base):
    """Reusable invoice notes/terms blocks (Welcome, T&C, Bank details, UPI…)."""
    __tablename__ = "invoice_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)          # welcome | terms | refund | payment | bank | upi | custom
    content = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class InvoicePayment(Base):
    __tablename__ = "invoice_payments"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    installment_id = Column(Integer, ForeignKey("invoice_installments.id"), nullable=True)
    amount = Column(Float, nullable=False)
    method = Column(String, nullable=True)            # Cash | UPI | Card | Bank Transfer | Cheque
    reference = Column(String, nullable=True)         # txn id / cheque no
    paid_date = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

