"""Comprehensive tests for the Enrollment module.

Run:
    JWT_SECRET=test python3 test_enrollment.py

Uses an in-memory SQLite database — never touches the real DB.

Scenarios covered:
  1.  Create a single enrollment
  2.  Create multiple enrollments for the same student (different subjects)
  3.  Assign different teachers per subject
  4.  Assign different subjects
  5.  Assign different syllabuses
  6.  Assign different grades
  7.  Verify automatic fee package mapping (subject + grade)
  8.  Teacher updates grade → fee package auto-syncs
  9.  Teacher updates syllabus → Student record syncs
  10. Student sees only their enrolled subject's data
  11. Changing one enrollment does NOT affect another enrollment
  12. Analytics update after every enrollment change
  13. No existing modules broken (GET /students, GET /staff still work)
"""
import os, sys, json
os.environ.setdefault("JWT_SECRET", "test")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database, models, auth, main
from fastapi.testclient import TestClient

# ── In-memory SQLite ──────────────────────────────────────────────────────────
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(bind=engine)
models.Base.metadata.create_all(bind=engine)

# Disable email sending during tests.
auth.send_email = lambda *a, **k: None


def _override():
    db = Session()
    try:
        yield db
    finally:
        db.close()


main.app.dependency_overrides[database.get_db] = _override
c = TestClient(main.app, raise_server_exceptions=True)

# ── Result tracking ───────────────────────────────────────────────────────────
RESULTS = []


def check(name: str, cond: bool):
    RESULTS.append((name, bool(cond)))
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _db():
    return Session()


def seed_admin() -> dict:
    """Create a super_admin staff and return a valid auth token header."""
    db = _db()
    import security
    staff = models.Staff(
        name="Test Admin",
        first_name="Test",
        last_name="Admin",
        role="Admin",
        access_role="super_admin",
        phone="0000000000",
        email="testadmin@enroll.test",
        password_hash=security.hash_password("Admin@1234"),
        account_status="active",
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    db.close()

    r = c.post("/admin/login", json={"email": "testadmin@enroll.test", "password": "Admin@1234"})
    assert r.status_code == 200, f"Admin login failed: {r.text}"
    token = r.json().get("token") or r.json().get("access_token")
    return {"Authorization": f"Bearer {token}"}


def seed_teacher(name: str, email: str) -> int:
    db = _db()
    import security
    t = models.Staff(
        name=name, first_name=name.split()[0], last_name=name.split()[-1],
        role="Teacher", access_role="teacher",
        phone="1111111111", email=email,
        password_hash=security.hash_password("Teacher@1234"),
        account_status="active",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    tid = t.id
    db.close()
    return tid


def seed_student(first: str, last: str, email: str) -> int:
    db = _db()
    s = models.Student(first_name=first, last_name=last, email=email)
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()
    return sid


def seed_package(name: str, subject: str, grade: str, price: float = 1000.0) -> int:
    db = _db()
    p = models.Package(
        name=name,
        applicable_courses=json.dumps([subject]),
        applicable_grades=json.dumps([grade]),
        price=price,
        is_published=True,
        is_archived=False,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    pid = p.id
    db.close()
    return pid


# ─────────────────────────────────────────────────────────────────────────────
# Test suite
# ─────────────────────────────────────────────────────────────────────────────

def run_tests():
    print("\n" + "=" * 60)
    print("Enrollment Module — Test Suite")
    print("=" * 60)

    H = seed_admin()
    teacher_a = seed_teacher("Teacher Alpha", "alpha@enroll.test")
    teacher_b = seed_teacher("Teacher Beta",  "beta@enroll.test")
    john = seed_student("John", "Doe", "john.doe@enroll.test")
    jane = seed_student("Jane", "Doe", "jane.doe@enroll.test")

    # Pre-seed packages for auto-mapping tests
    pkg_guitar_g2 = seed_package("Guitar Grade 2 Package", "Guitar", "Grade 2", 1500.0)
    pkg_drums_g1  = seed_package("Drums Grade 1 Package",  "Drums",  "Grade 1", 1200.0)
    pkg_guitar_g3 = seed_package("Guitar Grade 3 Package", "Guitar", "Grade 3", 1800.0)
    pkg_piano_g1  = seed_package("Piano Grade 1 Package",  "Piano",  "Grade 1", 1100.0)

    print("\n── Scenario 1: Create a single enrollment ──")
    r = c.post("/admin/enrollments", headers=H, json={
        "student_id": john,
        "teacher_id": teacher_a,
        "subject": "Guitar",
        "grade": "Grade 2",
        "syllabus_type": "Trinity",
        "start_date": "2026-07-01",
    })
    check("POST /admin/enrollments returns 200", r.status_code == 200)
    enroll_guitar = r.json()
    check("Enrollment has correct student_id", enroll_guitar.get("student_id") == john)
    check("Enrollment has correct subject",    enroll_guitar.get("subject") == "Guitar")
    check("Enrollment has correct grade",      enroll_guitar.get("grade") == "Grade 2")
    check("Enrollment has correct teacher",    enroll_guitar.get("teacher_id") == teacher_a)
    check("Enrollment status is active",       enroll_guitar.get("status") == "active")
    guitar_id = enroll_guitar["id"]

    print("\n── Scenario 7: Automatic fee package mapping ──")
    check(
        "Auto-mapped fee_package_id for Guitar/Grade 2",
        enroll_guitar.get("fee_package_id") == pkg_guitar_g2,
    )
    check("Fee package name present", bool(enroll_guitar.get("fee_package_name")))

    print("\n── Scenario 2 & 3: Multiple enrollments, different teachers ──")
    r2 = c.post("/admin/enrollments", headers=H, json={
        "student_id": john,
        "teacher_id": teacher_b,
        "subject": "Drums",
        "grade": "Grade 1",
        "syllabus_type": "RSL",
        "start_date": "2026-07-01",
    })
    check("Create second enrollment (Drums) returns 200", r2.status_code == 200)
    enroll_drums = r2.json()
    drums_id = enroll_drums["id"]
    check("Drums enrollment has different teacher (B)", enroll_drums.get("teacher_id") == teacher_b)
    check("Drums auto-mapped fee package",              enroll_drums.get("fee_package_id") == pkg_drums_g1)

    print("\n── Scenario 4 & 5: Different subjects, different syllabuses ──")
    check("Guitar enrollment syllabus = Trinity", enroll_guitar.get("syllabus_type") == "Trinity")
    check("Drums enrollment syllabus = RSL",      enroll_drums.get("syllabus_type") == "RSL")
    check("Guitar subject != Drums subject",      enroll_guitar["subject"] != enroll_drums["subject"])

    print("\n── Scenario 6: Different grades ──")
    check("Guitar grade = Grade 2", enroll_guitar.get("grade") == "Grade 2")
    check("Drums grade = Grade 1",  enroll_drums.get("grade") == "Grade 1")

    print("\n── GET /admin/students/{id}/enrollments ──")
    r3 = c.get(f"/admin/students/{john}/enrollments", headers=H)
    check("GET student enrollments returns 200", r3.status_code == 200)
    johns_enrollments = r3.json()
    check("John has 2 enrollments",            len(johns_enrollments) == 2)
    subjects_found = {e["subject"] for e in johns_enrollments}
    check("Both Guitar and Drums present",     subjects_found == {"Guitar", "Drums"})

    print("\n── Student portal: GET /student/{id}/learning-enrollments ──")
    r4 = c.get(f"/student/{john}/learning-enrollments")
    check("Student portal returns 200",         r4.status_code == 200)
    portal = r4.json()
    check("Portal shows 2 active enrollments",  len(portal) == 2)

    print("\n── Scenario 10: Subject-visibility — student only sees own subjects ──")
    subjects_portal = {e["subject"] for e in portal}
    check("Student sees Guitar",  "Guitar" in subjects_portal)
    check("Student sees Drums",   "Drums" in subjects_portal)
    check("Student does NOT see Piano", "Piano" not in subjects_portal)

    print("\n── Scenario 8: Teacher updates grade → fee package auto-syncs ──")
    r5 = c.patch(f"/teacher/enrollments/{guitar_id}/grade", headers=H, json={"grade": "Grade 3"})
    check("PATCH grade returns 200", r5.status_code == 200)
    updated = r5.json()
    check("Grade updated to Grade 3",        updated.get("grade") == "Grade 3")
    check("Fee package auto-synced to G3",   updated.get("fee_package_id") == pkg_guitar_g3)

    # Verify Student record synced
    db = _db()
    student = db.query(models.Student).filter(models.Student.id == john).first()
    check("Student.current_grade synced to Grade 3", student.current_grade == "Grade 3")
    check("Student.instrument synced to Guitar",     student.instrument == "Guitar")
    check("Student.teacher_id synced to Teacher A",  student.teacher_id == teacher_a)
    db.close()

    print("\n── Scenario 9: Teacher updates syllabus → Student record syncs ──")
    r6 = c.patch(f"/teacher/enrollments/{guitar_id}/syllabus", headers=H, json={"syllabus_type": "RSL"})
    check("PATCH syllabus returns 200", r6.status_code == 200)
    check("Syllabus updated to RSL",    r6.json().get("syllabus_type") == "RSL")
    db = _db()
    student = db.query(models.Student).filter(models.Student.id == john).first()
    check("Student.syllabus_type synced to RSL", student.syllabus_type == "RSL")
    db.close()

    print("\n── Scenario 11: Changing one enrollment does NOT affect another ──")
    r7 = c.get(f"/admin/enrollments/{drums_id}", headers=H)
    drums_after = r7.json()
    check("Drums grade still Grade 1 after Guitar update", drums_after.get("grade") == "Grade 1")
    check("Drums teacher still Teacher B",                 drums_after.get("teacher_id") == teacher_b)
    check("Drums syllabus still RSL",                      drums_after.get("syllabus_type") == "RSL")
    check("Drums fee package unchanged",                   drums_after.get("fee_package_id") == pkg_drums_g1)

    print("\n── PUT /admin/enrollments/{id}: full update ──")
    r8 = c.put(f"/admin/enrollments/{drums_id}", headers=H, json={
        "grade": "Grade 2",
        "status": "paused",
    })
    check("PUT enrollment returns 200",        r8.status_code == 200)
    check("Status updated to paused",          r8.json().get("status") == "paused")
    check("Drums grade updated to Grade 2",    r8.json().get("grade") == "Grade 2")

    print("\n── Duplicate subject rejected ──")
    r9 = c.post("/admin/enrollments", headers=H, json={
        "student_id": john,
        "teacher_id": teacher_a,
        "subject": "Guitar",   # already enrolled
        "grade": "Grade 1",
        "syllabus_type": "Trinity",
    })
    check("Duplicate subject returns 409",     r9.status_code == 409)

    print("\n── Second student's enrollments are independent ──")
    r10 = c.post("/admin/enrollments", headers=H, json={
        "student_id": jane,
        "teacher_id": teacher_a,
        "subject": "Piano",
        "grade": "Grade 1",
        "syllabus_type": "Trinity",
    })
    check("Jane's Piano enrollment created",   r10.status_code == 200)
    check("Jane auto-mapped Piano package",    r10.json().get("fee_package_id") == pkg_piano_g1)
    jane_piano_id = r10.json()["id"]

    # John's enrollments unaffected by Jane's creation
    r11 = c.get(f"/admin/students/{john}/enrollments", headers=H)
    check("John still has exactly 2 enrollments", len(r11.json()) == 2)

    print("\n── Scenario 12: Analytics update after changes ──")
    r12 = c.get("/admin/analytics/enrollments", headers=H)
    check("Analytics returns 200", r12.status_code == 200)
    analytics = r12.json()
    check("summary key present",           "summary" in analytics)
    check("by_subject key present",        "by_subject" in analytics)
    check("by_teacher key present",        "by_teacher" in analytics)
    check("by_grade key present",          "by_grade" in analytics)
    check("by_syllabus key present",       "by_syllabus" in analytics)
    check("by_fee_package key present",    "by_fee_package" in analytics)
    check("students_per_teacher present",  "students_per_teacher" in analytics)
    check("students_per_subject present",  "students_per_subject" in analytics)
    check("monthly_trend present",         "monthly_trend" in analytics)
    check("status_summary present",        "status_summary" in analytics)
    check("teacher_workload present",      "teacher_workload" in analytics)
    check("subject_popularity present",    "subject_popularity" in analytics)

    # Guitar is John's active enrollment; Drums is paused; Jane has Piano
    summary = analytics["summary"]
    check("total_enrollments >= 3",  summary.get("total_enrollments", 0) >= 3)
    check("active_enrollments >= 2", summary.get("active_enrollments", 0) >= 2)
    check("paused_enrollments >= 1", summary.get("paused_enrollments", 0) >= 1)

    # Check Guitar appears in by_subject
    check("Guitar in by_subject", analytics["by_subject"].get("Guitar", 0) >= 1)
    check("Piano in by_subject",  analytics["by_subject"].get("Piano", 0) >= 1)

    print("\n── Scenario 13: Existing modules unbroken ──")
    r_s = c.get("/students", headers=H)
    check("GET /students still works", r_s.status_code == 200)
    check("Students list is a list",   isinstance(r_s.json(), list))

    r_st = c.get("/staff", headers=H)
    check("GET /staff still works",    r_st.status_code == 200)
    check("Staff list is a list",      isinstance(r_st.json(), list))

    print("\n── DELETE enrollment (soft-complete) ──")
    r13 = c.delete(f"/admin/enrollments/{jane_piano_id}", headers=H)
    check("DELETE returns 200",                        r13.status_code == 200)
    check("Response contains enrollment id",           r13.json().get("id") == jane_piano_id)
    r14 = c.get(f"/admin/enrollments/{jane_piano_id}", headers=H)
    check("Enrollment status is now completed",        r14.json().get("status") == "completed")

    # Jane's portal should no longer show Piano
    r15 = c.get(f"/student/{jane}/learning-enrollments")
    jane_portal = r15.json()
    check("Jane's portal shows 0 active enrollments", len(jane_portal) == 0)

    print("\n── Teacher portal: GET /teacher/{id}/learning-enrollments ──")
    r16 = c.get(f"/teacher/{teacher_a}/learning-enrollments", headers=H)
    check("Teacher portal returns 200", r16.status_code == 200)
    teacher_a_students = r16.json()
    teacher_a_subjects = {e["subject"] for e in teacher_a_students}
    check("Teacher A sees Guitar", "Guitar" in teacher_a_subjects)
    check("Teacher A does NOT see Drums (owned by B)", "Drums" not in teacher_a_subjects)

    r17 = c.get(f"/teacher/{teacher_b}/learning-enrollments", headers=H)
    check("Teacher B portal returns 200", r17.status_code == 200)

    print("\n── Pagination ──")
    r18 = c.get("/admin/enrollments?page=1&limit=2", headers=H)
    check("Paginated list returns 200", r18.status_code == 200)
    paged = r18.json()
    check("Paginated response has items key",  "items" in paged)
    check("Paginated response has total key",  "total" in paged)
    check("items count <= limit",              len(paged["items"]) <= 2)

    print("\n── Invalid grade PATCH (missing grade field) ──")
    r19 = c.patch(f"/teacher/enrollments/{guitar_id}/grade", headers=H, json={})
    check("Missing grade returns 400", r19.status_code == 400)

    print("\n── Invalid syllabus value ──")
    r20 = c.patch(f"/teacher/enrollments/{guitar_id}/syllabus", headers=H,
                  json={"syllabus_type": "ABRSM"})
    check("Invalid syllabus returns 400", r20.status_code == 400)

    print("\n── GET /admin/enrollments filters ──")
    r21 = c.get(f"/admin/enrollments?subject=Guitar", headers=H)
    check("Filter by subject returns 200", r21.status_code == 200)
    guitar_only = r21.json()
    check("All results are Guitar",
          all(e["subject"] == "Guitar" for e in guitar_only))

    r22 = c.get(f"/admin/enrollments?teacher_id={teacher_a}", headers=H)
    check("Filter by teacher_id returns 200", r22.status_code == 200)
    check("All results belong to teacher A",
          all(e["teacher_id"] == teacher_a for e in r22.json()))

    r23 = c.get(f"/admin/enrollments?status=active", headers=H)
    check("Filter by status=active returns 200", r23.status_code == 200)
    check("All results are active",
          all(e["status"] == "active" for e in r23.json()))

    # ── Summary ───────────────────────────────────────────────────────────────
    total = len(RESULTS)
    passed = sum(1 for _, ok in RESULTS if ok)
    failed = total - passed

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed  ({failed} failed)")
    print("=" * 60)

    if failed:
        print("\nFailed checks:")
        for name, ok in RESULTS:
            if not ok:
                print(f"  ✗ {name}")
        sys.exit(1)
    else:
        print("All tests passed ✓")


if __name__ == "__main__":
    run_tests()
