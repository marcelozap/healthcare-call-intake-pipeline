"""
Database access and the load step.

Deliberately thin. No ORM, because the interesting part of this system is the
SQL in `schema.sql` and hiding it behind an object layer would make it harder to
explain, not easier.

The load is a full rebuild every time. That is a choice: the cohort is small and
deterministic, so a rebuild is both faster to reason about and impossible to get
into a half-updated state. A production version against real feeds would do
incremental upserts keyed on `member_id`, and the shape of the code would barely
change.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import settings
from app.domain import Member
from app.rules import evaluate

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@contextmanager
def connect(db_path: Path | None = None, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a connection with rows that behave like dicts.

    `readonly` is used by the API so a query layer bug can never write.
    """
    path = db_path or settings.db_path
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def build(members: list[Member], db_path: Path | None = None) -> dict[str, int]:
    """Create the database from scratch and load the cohort into it."""
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    with connect(path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

        # Columns named explicitly rather than positionally: a positional insert
        # silently writes to the wrong field the moment anyone reorders the schema.
        conn.executemany(
            """INSERT INTO outreach_member (
                   member_id, member_name, age, clinic, language, contact_method,
                   caregiver_known, needs_interpreter, transport_needed,
                   transport_booked, has_phone, outreach_reason, discharge_days,
                   has_appointment, medication_question, misrouted,
                   attempt_count, reached
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(m.member_id, m.name, m.age, m.clinic, m.language, m.contact_method,
              int(m.caregiver_known), int(m.needs_interpreter), int(m.transport_needed),
              int(m.transport_booked), int(m.has_phone), m.reason.label,
              m.discharge_days, int(m.has_appointment), int(m.medication_question),
              int(m.misrouted), m.attempt_count, int(m.reached))
             for m in members])

        conn.executemany(
            "INSERT INTO outreach_attempt (member_id, seq, outcome, agent_spanish, day_offset) "
            "VALUES (?,?,?,?,?)",
            [(m.member_id, a.seq, a.outcome, int(a.agent_spanish), a.day_offset)
             for m in members for a in m.attempts])

        flags = [(m.member_id, f.rule.id, f.severity, f.detail)
                 for m in members for f in evaluate(m)]
        conn.executemany("INSERT INTO outreach_flag VALUES (?,?,?,?)", flags)
        conn.commit()

        counts = {
            "members": len(members),
            "attempts": sum(m.attempt_count for m in members),
            "flags": len(flags),
            "needs_review": conn.execute(
                "SELECT COUNT(*) FROM v_outreach_member WHERE needs_review = 1"
            ).fetchone()[0],
        }
    return counts


# ── data-quality checks ──────────────────────────────────────────────────
# These run after every load. A pipeline that cannot say whether it succeeded
# has not finished.

def run_quality_checks(db_path: Path | None = None) -> list[dict]:
    """Assertions about the loaded data. Recorded in `dq_result`, not just printed."""
    checks: list[dict] = []

    def record(conn, name: str, passed: bool, observed, detail: str) -> None:
        checks.append({"check": name, "passed": passed,
                       "observed": str(observed), "detail": detail})
        conn.execute(
            "INSERT INTO dq_result (check_name, passed, observed, detail) VALUES (?,?,?,?)",
            (name, int(passed), str(observed), detail))

    with connect(db_path) as conn:
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731

        total = q("SELECT COUNT(*) FROM outreach_member")
        record(conn, "cohort size is 420", total == settings.cohort_size, total,
               "The generator is seeded, so this is fixed. A change here means "
               "the seed or the generator moved.")

        orphans = q("SELECT COUNT(*) FROM outreach_attempt a "
                    "LEFT JOIN outreach_member m ON m.member_id = a.member_id "
                    "WHERE m.member_id IS NULL")
        record(conn, "no orphan attempts", orphans == 0, orphans,
               "Every attempt must belong to a member on the campaign.")

        drift = q("SELECT COUNT(*) FROM outreach_member m WHERE m.attempt_count <> "
                  "(SELECT COUNT(*) FROM outreach_attempt a WHERE a.member_id = m.member_id)")
        record(conn, "attempt_count matches attempts", drift == 0, drift,
               "A denormalised count that disagrees with its source is how "
               "dashboards start lying.")

        bad_reached = q("SELECT COUNT(*) FROM outreach_member m WHERE m.reached = 1 "
                        "AND NOT EXISTS (SELECT 1 FROM outreach_attempt a "
                        "WHERE a.member_id = m.member_id AND a.outcome = 'reached')")
        record(conn, "reached implies a reached attempt", bad_reached == 0, bad_reached,
               "A member cannot be marked reached without an attempt that reached them.")

        phoneless = q("SELECT COUNT(*) FROM outreach_member m WHERE m.has_phone = 0 "
                      "AND EXISTS (SELECT 1 FROM outreach_attempt a "
                      "WHERE a.member_id = m.member_id AND a.outcome <> 'incomplete')")
        record(conn, "no dialled attempts without a phone", phoneless == 0, phoneless,
               "Members with no number on file cannot have been dialled.")

        unflagged_gap = q("SELECT COUNT(*) FROM v_outreach_member v WHERE v.discharge_gap = 1 "
                          "AND NOT EXISTS (SELECT 1 FROM outreach_flag f "
                          "WHERE f.member_id = v.member_id AND f.rule_id = 'discharge_no_followup')")
        record(conn, "every discharge gap is flagged", unflagged_gap == 0, unflagged_gap,
               "The view and the rule engine must agree. If they drift, one of "
               "them is wrong and the queue is incomplete.")

        conn.commit()
    return checks
