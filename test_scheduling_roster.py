"""Automated tests for recurrence-aware Add/Remove student (roster scope).

Run:  JWT_SECRET=test python3 test_scheduling_roster.py
In-memory SQLite; never touches the real DB.

Business rule: add/remove with scope (this | this_and_future) must affect only
the selected day/time STREAM and never unrelated recurrence schedules.
"""
import os
import sys
from datetime import date, timedelta

os.environ.setdefault("JWT_SECRET", "test")
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import database, models, auth, main
from fastapi.testclient import TestClient

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TS = sessionmaker(bind=engine)
models.Base.metadata.create_all(bind=engine)
auth.send_email = lambda *a, **k: None


def _ov():
    d = TS()
    try:
        yield d
    finally:
        d.close()


main.app.dependency_overrides[database.get_db] = _ov
c = TestClient(main.app)

_T0 = date.today()
NM = _T0 + timedelta(days=(7 - _T0.weekday()) % 7 or 7)
END = NM + timedelta(weeks=8)
RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def seed_students(n):
    d = TS()
    ids = []
    base = d.query(models.Student).count()
    for i in range(n):
        s = models.Student(first_name=f"S{base+i}", last_name="X", email=f"s{base+i}_{base}@x.com")
        d.add(s); d.flush(); ids.append(s.id)
    d.commit(); d.close()
    return ids


def make_template(by_weekday, cap=20):
    r = c.post("/scheduling/templates", json={
        "name": "C", "course": "Guitar", "start_time": "10:00", "end_time": "11:00", "capacity": cap,
        "recurrence": {"freq": "weekly", "by_weekday": by_weekday,
                       "start_date": NM.isoformat(), "end_date": END.isoformat()}})
    return r.json()["id"]


def occ_of(tid, weekday=None):
    rows = [o for o in c.get("/scheduling/calendar", params={"start": NM.isoformat(), "end": END.isoformat()}).json()["occurrences"]
            if o["template_id"] == tid]
    if weekday:
        rows = [o for o in rows if date.fromisoformat(o["date"]).strftime("%a") == weekday]
    return sorted(rows, key=lambda o: o["date"])


def roster(occ_id):
    return {r["student_id"] for r in c.get(f"/scheduling/occurrences/{occ_id}/attendance").json()}


def add(occ_id, sid, scope):
    return c.post(f"/scheduling/occurrences/{occ_id}/add-student", json={"student_id": sid, "scope": scope})


def remove(occ_id, sid, scope):
    return c.post(f"/scheduling/occurrences/{occ_id}/remove-student", json={"student_id": sid, "scope": scope})


# ════════════ ADD ════════════
def test_add_this_class():
    print("ADD 1 — This Class")
    [s] = seed_students(1); tid = make_template("MO")
    occ = occ_of(tid); w3 = occ[2]
    r = add(w3["id"], s, "this")
    check("api persistence (200, affected=1)", r.status_code == 200 and r.json()["occurrences_affected"] == 1)
    check("added to W3 only", s in roster(w3["id"]))
    check("not in W1,W2,W4,W5", all(s not in roster(o["id"]) for o in occ if o["id"] != w3["id"]))


def test_add_this_and_following():
    print("ADD 2 — This & Following (single stream)")
    [s] = seed_students(1); tid = make_template("MO")
    occ = occ_of(tid); w3 = occ[2]
    add(w3["id"], s, "this_and_future")
    fut = [o for o in occ if o["date"] >= w3["date"]]
    past = [o for o in occ if o["date"] < w3["date"]]
    check("in W3+ (selected+future)", all(s in roster(o["id"]) for o in fut))
    check("NOT in past W1,W2", all(s not in roster(o["id"]) for o in past))


def test_add_multi_stream():
    print("ADD 3 — Multiple streams (MO/WE/FR), This & Following on Monday")
    [s] = seed_students(1); tid = make_template("MO,WE,FR")
    mon = occ_of(tid, "Mon"); w3 = mon[2]
    add(w3["id"], s, "this_and_future")
    check("future Mondays include student", all(s in roster(o["id"]) for o in mon if o["date"] >= w3["date"]))
    check("Wednesdays untouched", all(s not in roster(o["id"]) for o in occ_of(tid, "Wed")))
    check("Fridays untouched", all(s not in roster(o["id"]) for o in occ_of(tid, "Fri")))


def test_add_capacity_and_persist():
    print("ADD 4/5 — capacity guard + persistence")
    ids = seed_students(2); tid = make_template("MO", cap=1)
    occ = occ_of(tid)
    r1 = add(occ[0]["id"], ids[0], "this")
    r2 = add(occ[0]["id"], ids[1], "this")  # over capacity
    check("first add ok", r1.status_code == 200)
    check("second add blocked (capacity)", r2.status_code == 400)
    check("persisted across refetch", ids[0] in roster(occ[0]["id"]))


# ════════════ REMOVE ════════════
def _template_with_enrolled(by_weekday, n=1):
    """Template with n baseline-enrolled students (in every occurrence)."""
    ids = seed_students(n); tid = make_template(by_weekday, cap=50)
    for sid in ids:
        c.post(f"/scheduling/templates/{tid}/enroll", json={"student_id": sid})
    return tid, ids


def test_remove_this_class():
    print("REMOVE 1 — This Class (baseline student)")
    tid, [s] = _template_with_enrolled("MO")
    occ = occ_of(tid); w3 = occ[2]
    check("baseline in all before", all(s in roster(o["id"]) for o in occ))
    r = remove(w3["id"], s, "this")
    check("api persistence (affected=1)", r.status_code == 200 and r.json()["occurrences_affected"] == 1)
    check("removed from W3 only", s not in roster(w3["id"]))
    check("still in others", all(s in roster(o["id"]) for o in occ if o["id"] != w3["id"]))


def test_remove_this_and_following():
    print("REMOVE 2 — This & Following")
    tid, [s] = _template_with_enrolled("MO")
    occ = occ_of(tid); w3 = occ[2]
    remove(w3["id"], s, "this_and_future")
    check("removed from W3+", all(s not in roster(o["id"]) for o in occ if o["date"] >= w3["date"]))
    check("kept in past W1,W2", all(s in roster(o["id"]) for o in occ if o["date"] < w3["date"]))


def test_remove_multi_stream():
    print("REMOVE 3 — Multiple streams, This & Following on Monday")
    tid, [s] = _template_with_enrolled("MO,WE,FR")
    mon = occ_of(tid, "Mon"); w3 = mon[2]
    remove(w3["id"], s, "this_and_future")
    check("future Mondays removed", all(s not in roster(o["id"]) for o in mon if o["date"] >= w3["date"]))
    check("Wednesdays untouched (still in)", all(s in roster(o["id"]) for o in occ_of(tid, "Wed")))
    check("Fridays untouched (still in)", all(s in roster(o["id"]) for o in occ_of(tid, "Fri")))


def test_remove_persist_roster():
    print("REMOVE 4/5 — roster validation + persistence after regenerate")
    tid, [s] = _template_with_enrolled("MO,WE")
    mon = occ_of(tid, "Mon"); w2 = mon[1]
    remove(w2["id"], s, "this_and_future")
    c.post(f"/scheduling/templates/{tid}/regenerate")
    mon2 = occ_of(tid, "Mon")
    check("removal persists after regenerate", all(s not in roster(o["id"]) for o in mon2 if o["date"] >= w2["date"]))
    check("Wednesdays still have student after regen", all(s in roster(o["id"]) for o in occ_of(tid, "Wed")))


def run():
    for fn in (test_add_this_class, test_add_this_and_following, test_add_multi_stream,
               test_add_capacity_and_persist,
               test_remove_this_class, test_remove_this_and_following, test_remove_multi_stream,
               test_remove_persist_roster):
        fn()
    failed = [n for n, ok in RESULTS if not ok]
    print("\n" + "=" * 52)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
    print("ALL ADD/REMOVE STREAM-SCOPE TESTS PASSED ✅")


if __name__ == "__main__":
    run()
