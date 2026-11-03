-- ═══════════════════════════════════════════════════════════════════════════
--  Care outreach — analytics schema
--
--  Three tables and one view. The view is the important part: every question
--  the API answers reads from `v_outreach_member` and never from the base
--  tables, so a metric is defined exactly once and two screens cannot disagree
--  about what "needs review" means.
--
--  Written for SQLite so the project runs with no server to install. On
--  Postgres or Snowflake the differences are narrow: CLUSTER BY in place of
--  these indexes, and native boolean instead of INTEGER 0/1.
-- ═══════════════════════════════════════════════════════════════════════════

DROP VIEW  IF EXISTS v_outreach_member;
DROP TABLE IF EXISTS outreach_flag;
DROP TABLE IF EXISTS outreach_attempt;
DROP TABLE IF EXISTS outreach_member;
DROP TABLE IF EXISTS dq_result;

-- Grain: one row per member on the campaign.
CREATE TABLE outreach_member (
    member_id           TEXT PRIMARY KEY,
    member_name         TEXT    NOT NULL,
    age                 INTEGER NOT NULL CHECK (age BETWEEN 0 AND 120),
    clinic              TEXT    NOT NULL,
    language            TEXT    NOT NULL CHECK (language IN ('Spanish','English')),
    contact_method      TEXT    NOT NULL CHECK (contact_method IN ('phone','sms','caregiver','portal')),
    caregiver_known     INTEGER NOT NULL CHECK (caregiver_known IN (0,1)),
    needs_interpreter   INTEGER NOT NULL CHECK (needs_interpreter IN (0,1)),
    transport_needed    INTEGER NOT NULL CHECK (transport_needed IN (0,1)),
    transport_booked    INTEGER NOT NULL CHECK (transport_booked IN (0,1)),
    has_phone           INTEGER NOT NULL CHECK (has_phone IN (0,1)),
    outreach_reason     TEXT    NOT NULL,
    -- NULL means "never discharged", not "discharged zero days ago".
    -- Collapsing those two is the same class of error as averaging a sentinel.
    discharge_days      INTEGER,
    has_appointment     INTEGER NOT NULL CHECK (has_appointment IN (0,1)),
    medication_question INTEGER NOT NULL CHECK (medication_question IN (0,1)),
    misrouted           INTEGER NOT NULL CHECK (misrouted IN (0,1)),
    attempt_count       INTEGER NOT NULL,
    reached             INTEGER NOT NULL CHECK (reached IN (0,1))
);

-- Grain: one row per outreach attempt.
CREATE TABLE outreach_attempt (
    attempt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id     TEXT    NOT NULL REFERENCES outreach_member(member_id),
    seq           INTEGER NOT NULL,
    outcome       TEXT    NOT NULL,
    agent_spanish INTEGER NOT NULL CHECK (agent_spanish IN (0,1)),
    day_offset    INTEGER NOT NULL,
    UNIQUE (member_id, seq)
);

-- Grain: one row per fired rule per member. Storing the explanation alongside
-- the flag means the queue can say *why* without recomputing anything.
CREATE TABLE outreach_flag (
    member_id TEXT NOT NULL REFERENCES outreach_member(member_id),
    rule_id   TEXT NOT NULL,
    severity  TEXT NOT NULL CHECK (severity IN ('high','medium')),
    detail    TEXT NOT NULL,
    PRIMARY KEY (member_id, rule_id)
);

-- The pipeline's own audit trail. If leadership does not trust the numbers,
-- this is the table you show them.
CREATE TABLE dq_result (
    run_ts        TEXT NOT NULL DEFAULT (datetime('now')),
    check_name    TEXT NOT NULL,
    passed        INTEGER NOT NULL CHECK (passed IN (0,1)),
    observed      TEXT,
    detail        TEXT
);

CREATE INDEX idx_member_clinic ON outreach_member (clinic);
CREATE INDEX idx_member_lang   ON outreach_member (language);
CREATE INDEX idx_attempt_member ON outreach_attempt (member_id);
CREATE INDEX idx_flag_rule     ON outreach_flag (rule_id);


-- ═══════════════════════════════════════════════════════════════════════════
--  SEMANTIC LAYER
--
--  Derived columns live here, once, rather than being re-expressed in every
--  query that needs them. `transport_gap` means one thing in this system.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE VIEW v_outreach_member AS
SELECT
    m.*,
    CASE WHEN m.age >= 85 THEN '85+'
         WHEN m.age >= 75 THEN '75-84'
         ELSE '65-74' END                                          AS age_band,
    CASE WHEN m.transport_needed = 1 AND m.transport_booked = 0
         THEN 1 ELSE 0 END                                         AS transport_gap,
    CASE WHEN m.discharge_days IS NOT NULL
          AND m.discharge_days <= 30
          AND m.has_appointment = 0 THEN 1 ELSE 0 END              AS discharge_gap,
    CASE WHEN EXISTS (SELECT 1 FROM outreach_flag f
                       WHERE f.member_id = m.member_id)
         THEN 1 ELSE 0 END                                         AS needs_review,
    (SELECT COUNT(*) FROM outreach_flag f
      WHERE f.member_id = m.member_id AND f.severity = 'high')     AS high_flags,
    (SELECT COUNT(*) FROM outreach_flag f
      WHERE f.member_id = m.member_id)                             AS flag_count
FROM outreach_member m;
