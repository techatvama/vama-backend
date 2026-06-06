from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float,
    ForeignKey, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Staff(Base):
    __tablename__ = "staff"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    role = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    calendar = Column(Boolean, default=True)
    takes_classes = Column(Boolean, default=True)
    password = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    batches = relationship("Batch", back_populates="teacher")
    sessions = relationship("ClassSession", back_populates="teacher")


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    primary_phone_number = Column(String, nullable=True)
    date_of_birth = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    address = Column(String, nullable=True)
    desired_course = Column(String, nullable=True)
    nearest_vama_center = Column(String, nullable=True)
    preferred_mode_of_contact = Column(String, nullable=True)
    password = Column(String, nullable=True)
    current_grade = Column(String, nullable=True, default='Debut')
    syllabus_type = Column(String, nullable=True, default='Trinity')
    is_exam_student = Column(Boolean, nullable=True, default=False)
    exam_date = Column(String, nullable=True)
    instrument = Column(String, nullable=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    teacher = relationship("Staff", foreign_keys=[teacher_id])
    progress_records = relationship("StudentProgress", back_populates="student")
    enrollments = relationship("StudentEnrollment", back_populates="student")
    materials = relationship("Material", back_populates="student")


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


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    teacher_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
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
    makeup_sessions = Column(Integer, default=0)
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
    sessions_used = Column(Integer, default=0)
    makeup_used = Column(Integer, default=0)
    status = Column(String, default="active")  # active | expired | cancelled | paused
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    student = relationship("Student")
    package = relationship("Package", back_populates="invoices")


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

