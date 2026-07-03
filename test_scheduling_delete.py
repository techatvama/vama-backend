"""Automated tests for recurring-class DELETE — stream isolation.

Run:  JWT_SECRET=test python3 test_scheduling_delete.py
Exits non-zero if any test fails. Uses an in-memory SQLite DB; never touches
the real database.

Business rule under test: a template may recur on multiple weekdays (streams);
delete (this / this_and_future / series) must only affect the SELECTED stream
and never unrelated day/time schedules.
"""
import os
import sys
from datetime import date, timedelta

os.environ.setdefault("JWT_SECRET", "test")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database, models, auth
import main
from fastapi.testclient import TestClient

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TS = sessionmaker(bind=engine)
models.Base.metadata.create_all(bind=engine)
auth.send_email = lambda *a, **k: None


def _override():
    d = TS()
    try:
        yield d
    finally:
        d.close()


main.app.dependency_overrides[database.get_db] = _override
client = TestClient(main.app)

# Anchor everything to the future so "past" guards don't interfere.
_T0 = date.today()
NEXT_MON = _T0 + timedelta(days=(7 - _T0.weekday()) % 7 or 7)
END = NEXT_MON + timedelta(weeks=8)


def wd(occ):
    return date.fromisoformat(occ["date"]).strftime("%a")


def make_template(by_weekday, start="10:00", end="11:00", name="C", course="Guitar"):
    r = client.post("/scheduling/templates", json={
        "name": name, "course": course, "start_time": start, "end_time": end, "capacity": 9,
        "recurrence": {"freq": "weekly", "by_weekday": by_weekday,
                       "start_date": NEXT_MON.isoformat(), "end_date": END.isoformat()},
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def occ_for(template_id, weekday=None):
    rows = client.get("/scheduling/calendar", params={
        "start": NEXT_MON.isoformat(), "end": END.isoformat()}).json()["occurrences"]
    rows = [o for o in rows if o["template_id"] == template_id]
    if weekday:
        rows = [o for o in rows if wd(o) == weekday]
    return sorted(rows, key=lambda o: o["date"])


def delete(occ_id, scope):
    return client.request("DELETE", f"/scheduling/occurrences/{occ_id}", params={"scope": scope})


def regenerate(tid):
    client.post(f"/scheduling/templates/{tid}/regenerate")


RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


# ── Test 1: This Occurrence Only ───────────────────────────────────────────
def test1_this_only():
    print("Test 1 — This Occurrence Only")
    tid = make_template("MO")
    occ = occ_for(tid)            # 5+ Mondays
    w3 = occ[2]
    delete(w3["id"], "this")
    after = occ_for(tid)
    dates = {o["date"] for o in after}
    check("W3 deleted", w3["date"] not in dates)
    check("others remain", len(after) == len(occ) - 1)


# ── Test 2: This & Following (single stream) ───────────────────────────────
def test2_this_and_following():
    print("Test 2 — This & Following (single Monday stream)")
    tid = make_template("MO")
    occ = occ_for(tid)
    w3 = occ[2]
    delete(w3["id"], "this_and_future")
    after = occ_for(tid)
    kept = [o for o in after if o["date"] < w3["date"]]
    gone = [o for o in after if o["date"] >= w3["date"]]
    check("W1,W2 remain", len(kept) == 2)
    check("W3+ deleted", len(gone) == 0)
    regenerate(tid)
    check("does not resurrect after regenerate", len(occ_for(tid)) == 2)


# ── Test 3: Entire Series (single stream) ──────────────────────────────────
def test3_entire_series():
    print("Test 3 — Entire Series (single stream)")
    tid = make_template("MO")
    occ = occ_for(tid)
    delete(occ[0]["id"], "series")
    check("all Mondays removed", len(occ_for(tid)) == 0)
    check("template removed", client.get(f"/scheduling/templates/{tid}").status_code == 404)


# ── Test 4: Mixed streams, This & Following on Monday ──────────────────────
def test4_mixed_this_and_following():
    print("Test 4 — Mixed MO/WE/FR, This & Following on Monday W3")
    tid = make_template("MO,WE,FR")
    mons = occ_for(tid, "Mon")
    wed_before = len(occ_for(tid, "Wed"))
    fri_before = len(occ_for(tid, "Fri"))
    w3 = mons[2]
    delete(w3["id"], "this_and_future")
    mon_after = occ_for(tid, "Mon")
    check("future Mondays deleted", all(o["date"] < w3["date"] for o in mon_after))
    check("past Mondays kept (2)", len(mon_after) == 2)
    check("Wednesdays untouched", len(occ_for(tid, "Wed")) == wed_before)
    check("Fridays untouched", len(occ_for(tid, "Fri")) == fri_before)
    regenerate(tid)
    check("Mondays do not resurrect", len(occ_for(tid, "Mon")) == 2)
    check("Wed still intact after regen", len(occ_for(tid, "Wed")) == wed_before)


# ── Test 5: Mixed streams, Entire Series on Monday ─────────────────────────
def test5_mixed_entire_series():
    print("Test 5 — Mixed MO/WE/FR, Entire Series on Monday")
    tid = make_template("MO,WE,FR")
    wed_before = len(occ_for(tid, "Wed"))
    fri_before = len(occ_for(tid, "Fri"))
    delete(occ_for(tid, "Mon")[0]["id"], "series")
    check("all Mondays removed", len(occ_for(tid, "Mon")) == 0)
    check("Wednesdays remain", len(occ_for(tid, "Wed")) == wed_before)
    check("Fridays remain", len(occ_for(tid, "Fri")) == fri_before)
    regenerate(tid)
    check("Mondays stay gone after regen", len(occ_for(tid, "Mon")) == 0)


# ── Edge cases ─────────────────────────────────────────────────────────────
def test_edge_first_last_modified():
    print("Edge — first / last / edited occurrence")
    # First occurrence + this_and_future on a multi-stream → whole Monday stream gone
    tid = make_template("MO,WE")
    mons = occ_for(tid, "Mon")
    delete(mons[0]["id"], "this_and_future")
    check("delete-first removes all Mondays", len(occ_for(tid, "Mon")) == 0)
    check("Wednesdays survive delete-first", len(occ_for(tid, "Wed")) > 0)

    # Last occurrence, this only
    tid2 = make_template("MO")
    occ = occ_for(tid2)
    delete(occ[-1]["id"], "this")
    check("delete-last removes only last", len(occ_for(tid2)) == len(occ) - 1)

    # Edited (is_modified) occurrence is still deleted by stream delete
    tid3 = make_template("MO,WE")
    mons = occ_for(tid3, "Mon")
    client.put(f"/scheduling/occurrences/{mons[1]['id']}", json={"scope": "this", "start_time": "12:00", "end_time": "13:00"})
    delete(occ_for(tid3, "Mon")[0]["id"], "series")
    remaining_mon = occ_for(tid3, "Mon")
    check("series delete also removes edited occurrence", len(remaining_mon) == 0)
    check("Wednesdays untouched by edited-Monday series delete", len(occ_for(tid3, "Wed")) > 0)


def main_run():
    for fn in (test1_this_only, test2_this_and_following, test3_entire_series,
               test4_mixed_this_and_following, test5_mixed_entire_series,
               test_edge_first_last_modified):
        fn()
    failed = [n for n, ok in RESULTS if not ok]
    print("\n" + "=" * 50)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL DELETE STREAM-ISOLATION TESTS PASSED ✅")


if __name__ == "__main__":
    main_run()
