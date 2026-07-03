"""Recurrence engine + occurrence generation for Scheduling v2.

Pure stdlib date math (no dateutil). Structured recurrence columns expand to a
list of dates; `generate_for_template` materializes `class_occurrences` without
ever touching the past, manually-edited occurrences, or ones with attendance.
"""
import os
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import (
    ClassTemplate, RecurrenceRule, ClassOccurrence, Enrollment, Holiday, Attendance,
)

# Weekday code <-> Python weekday() (Mon=0 .. Sun=6)
WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
CODE_TO_IDX = {c: i for i, c in enumerate(WEEKDAY_CODES)}

HORIZON_DAYS = int(os.getenv("OCCURRENCE_HORIZON_DAYS", "365"))


# ──────────────────────────── date helpers ────────────────────────────

def _parse(d: str) -> date:
    return date.fromisoformat(d)


def _fmt(d: date) -> str:
    return d.isoformat()


def parse_weekdays(csv: Optional[str]) -> list[int]:
    if not csv:
        return []
    return [CODE_TO_IDX[c.strip().upper()] for c in csv.split(",") if c.strip().upper() in CODE_TO_IDX]


def _add_months(d: date, months: int) -> date:
    """Add calendar months, clamping the day to the month's length."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    # clamp day
    import calendar
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


# ──────────────────────────── expansion ────────────────────────────

def horizon_end(rule: RecurrenceRule, today: Optional[date] = None) -> date:
    """The last date to materialize: rule.end_date if set, else a rolling cap."""
    today = today or date.today()
    cap = today + timedelta(days=HORIZON_DAYS)
    if rule.end_date:
        return min(_parse(rule.end_date), cap)
    return cap


def expand_occurrences(rule: RecurrenceRule, *, until: Optional[date] = None,
                       from_date: Optional[date] = None,
                       holiday_dates: Optional[set[str]] = None,
                       today: Optional[date] = None) -> list[date]:
    """Return the list of dates this rule produces, bounded by [from_date, until].

    `until` defaults to horizon_end(rule). Holiday dates are skipped.
    """
    start = _parse(rule.start_date)
    if from_date and from_date > start:
        start = from_date
    end = until or horizon_end(rule, today)
    if end < start:
        return []
    holiday_dates = holiday_dates or set()
    interval = max(1, rule.interval or 1)
    freq = (rule.freq or "weekly").lower()
    dates: list[date] = []

    if freq == "daily":
        d = start
        # align: daily interval counted from rule.start_date
        base = _parse(rule.start_date)
        offset = (start - base).days % interval
        if offset:
            d = start + timedelta(days=(interval - offset))
        while d <= end:
            dates.append(d)
            d += timedelta(days=interval)

    elif freq in ("weekly", "custom"):
        weekdays = parse_weekdays(rule.by_weekday) or [_parse(rule.start_date).weekday()]
        # custom == weekly with interval forced to 1
        wk_interval = 1 if freq == "custom" else interval
        base_week_monday = _parse(rule.start_date) - timedelta(days=_parse(rule.start_date).weekday())
        d = start
        while d <= end:
            week_monday = d - timedelta(days=d.weekday())
            weeks_since = (week_monday - base_week_monday).days // 7
            if weeks_since % wk_interval == 0 and d.weekday() in weekdays:
                dates.append(d)
            d += timedelta(days=1)

    elif freq == "monthly":
        monthday = rule.by_monthday or _parse(rule.start_date).day
        base = _parse(rule.start_date)
        # iterate month by month from start month
        cursor = date(base.year, base.month, 1)
        month_step = 0
        while True:
            occ_month = _add_months(cursor, month_step)
            import calendar
            last = calendar.monthrange(occ_month.year, occ_month.month)[1]
            d = date(occ_month.year, occ_month.month, min(monthday, last))
            if d > end:
                break
            if d >= start and (month_step % interval == 0):
                dates.append(d)
            month_step += 1
            if month_step > 1200:  # safety
                break

    # Skip holidays
    return [d for d in dates if _fmt(d) not in holiday_dates]


def holiday_dates_for(db: Session, center_id: Optional[int]) -> set[str]:
    """Holidays affecting a center (center-specific + global)."""
    q = db.query(Holiday)
    rows = q.all()
    return {h.date for h in rows if h.center_id is None or h.center_id == center_id}


# ──────────────────────────── generation ────────────────────────────

def generate_for_template(db: Session, template: ClassTemplate, *,
                          from_date: Optional[str] = None, flush: bool = True) -> int:
    """Materialize occurrences for a template. Idempotent.

    Never modifies: past occurrences, is_modified ones, or those with attendance.
    `from_date` limits (re)generation to dates >= from_date (used by series edits
    so only the future is regenerated). Returns count of NEW occurrences created.
    """
    rule = template.rule
    if not rule:
        return 0
    fd = _parse(from_date) if from_date else None
    holidays = holiday_dates_for(db, template.center_id)
    wanted = expand_occurrences(rule, from_date=fd, holiday_dates=holidays)
    wanted_set = {_fmt(d) for d in wanted}

    existing = db.query(ClassOccurrence).filter(
        ClassOccurrence.template_id == template.id
    ).all()
    existing_by_date = {o.date: o for o in existing}

    # Occurrences that have attendance must be preserved regardless.
    occ_ids = [o.id for o in existing]
    att_occ_ids = set()
    if occ_ids:
        att_occ_ids = {
            a.session_id for a in db.query(Attendance).filter(
                Attendance.session_id.in_(occ_ids)
            ).all()
        }

    created = 0
    for d in wanted_set:
        if d in existing_by_date:
            continue  # keep existing (incl. modified/with-attendance)
        db.add(ClassOccurrence(
            template_id=template.id,
            date=d,
            start_time=template.start_time,
            end_time=template.end_time,
            teacher_id=template.teacher_id,
            room_id=template.room_id,
            status="scheduled",
            is_published=True,
        ))
        created += 1

    # Remove now-unwanted FUTURE occurrences that are safe to drop
    today = _fmt(date.today())
    for o in existing:
        if o.date in wanted_set:
            continue
        if o.date < today:
            continue                       # never touch the past
        if o.is_modified or o.is_makeup:
            continue                       # protected exceptions
        if o.id in att_occ_ids:
            continue                       # has attendance
        if fd and o.date < from_date:
            continue                       # outside regen window
        db.delete(o)

    if flush:
        db.flush()
    return created


# ──────────────────────────── RRULE export ────────────────────────────

def to_rrule(rule: RecurrenceRule) -> str:
    """Serialize to an RFC-5545 RRULE string (for interop/export)."""
    freq_map = {"daily": "DAILY", "weekly": "WEEKLY", "custom": "WEEKLY", "monthly": "MONTHLY"}
    parts = [f"FREQ={freq_map.get((rule.freq or 'weekly').lower(), 'WEEKLY')}"]
    if rule.interval and rule.interval != 1:
        parts.append(f"INTERVAL={rule.interval}")
    if rule.by_weekday:
        parts.append(f"BYDAY={rule.by_weekday.replace(' ', '')}")
    if rule.by_monthday:
        parts.append(f"BYMONTHDAY={rule.by_monthday}")
    if rule.end_date:
        parts.append(f"UNTIL={rule.end_date.replace('-', '')}")
    return ";".join(parts)
