from sqlalchemy.orm import Session
from models import Staff, Student
from schemas import StaffCreate, StudentCreate, StudentUpdate
from typing import List, Optional

# Staff CRUD Operations
def get_all_staff(db: Session) -> List[Staff]:
    """Fetch all staff members"""
    return db.query(Staff).order_by(Staff.created_at.desc()).all()

def get_staff_by_email(db: Session, email: str) -> Optional[Staff]:
    """Get staff member by email"""
    return db.query(Staff).filter(Staff.email == email).first()

def create_staff(db: Session, staff: StaffCreate) -> Staff:
    """Create a new staff member"""
    db_staff = Staff(
        name=staff.name,
        first_name=staff.firstName,
        last_name=staff.lastName,
        role=staff.role,
        phone=staff.phone,
        email=staff.email,
        calendar=staff.calendar,
        takes_classes=staff.takesClasses if staff.takesClasses is not None else True
    )
    db.add(db_staff)
    db.commit()
    db.refresh(db_staff)
    return db_staff

def update_staff(db: Session, staff_id: int, staff: StaffCreate) -> Optional[Staff]:
    """Update a staff member"""
    db_staff = db.query(Staff).filter(Staff.id == staff_id).first()
    if not db_staff:
        return None
    
    # Update fields
    for key, value in staff.dict(exclude_unset=True).items():
        if key == "firstName":
            db_staff.first_name = value
        elif key == "lastName":
            db_staff.last_name = value
        elif key == "takesClasses":
            db_staff.takes_classes = value
        elif hasattr(db_staff, key):
             setattr(db_staff, key, value)
    
    db.commit()
    db.refresh(db_staff)
    return db_staff


# Student CRUD Operations
def get_all_students(db: Session) -> List[Student]:
    """Fetch all students"""
    return db.query(Student).order_by(Student.created_at.desc()).all()

def get_student_by_email(db: Session, email: str) -> Optional[Student]:
    """Get student by email"""
    return db.query(Student).filter(Student.email == email).first()

def create_student(db: Session, student: StudentCreate) -> Student:
    """Create a new student"""
    db_student = Student(
        first_name=student.First_Name,
        last_name=student.Last_Name,
        email=student.Email,
        primary_phone_number=student.Primary_Phone_Number,
        date_of_birth=student.Date_of_Birth,
        gender=student.Gender,
        address=student.Address,
        desired_course=student.Desired_Course,
        nearest_vama_center=student.Nearest_Vama_Center,
        preferred_mode_of_contact=student.Preferred_Mode_of_Contact
    )
    db.add(db_student)
    db.commit()
    db.refresh(db_student)
    return db_student

def update_student(db: Session, student_id: int, student: StudentUpdate) -> Optional[Student]:

    """Update a student"""
    db_student = db.query(Student).filter(Student.id == student_id).first()
    if not db_student:
        return None
    
    # Update fields
    for key, value in student.dict(exclude_unset=True).items():
        if key == "First_Name":
            db_student.first_name = value
        elif key == "Last_Name":
            db_student.last_name = value
        elif key == "Email":
            db_student.email = value
        elif key == "Primary_Phone_Number":
            db_student.primary_phone_number = value
        elif key == "Date_of_Birth":
            db_student.date_of_birth = value
        elif key == "Gender":
            db_student.gender = value
        elif key == "Address":
            db_student.address = value
        elif key == "Desired_Course":
            db_student.desired_course = value
        elif key == "Nearest_Vama_Center":
            db_student.nearest_vama_center = value
        elif key == "Preferred_Mode_of_Contact":
            db_student.preferred_mode_of_contact = value
            
    db.commit()
    db.refresh(db_student)
    return db_student

 