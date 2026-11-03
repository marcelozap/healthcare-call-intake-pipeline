"""
Plain-English questions over the conformed cohort.

How a question becomes an answer
--------------------------------
    question -> query plan -> validated plan -> parameterised SQL -> rows

The model, when one is configured, produces the *plan* — a small JSON object
naming a metric, an optional dimension and some filters. It never writes SQL and
never sees a row. The plan is then checked against an allowlist here, and only
the surviving parts are compiled into SQL by this module.

That ordering is the whole security argument. Because the model cannot emit SQL,
there is no prompt-injection path to arbitrary SQL: the worst a hostile input can
do is name a metric that does not exist, which the validator refuses.

The SQL is built from allowlisted identifiers and parameterised values. No user
string is ever concatenated into a statement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.db import connect
from app.domain import CLINICS
from app.llm import LLMUnavailable, complete, parse_json


# ══════════════════════════════════════════════════════════════════════════
#  The semantic layer — every metric defined exactly once
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    sql: str
    description: str
    unit: str = "count"        # count | percent | rate


METRICS: dict[str, Metric] = {
    "members": Metric(
        "members", "Members", "COUNT(*)",
        "Members on the campaign."),
    "needs_review": Metric(
        "needs_review", "Need a person", "SUM(needs_review)",
        "Members with at least one safety rule fired."),
    "review_rate": Metric(
        "review_rate", "Review rate", "100.0 * SUM(needs_review) / COUNT(*)",
        "Share of members routed to a human.", unit="percent"),
    "transport_needed": Metric(
        "transport_needed", "Need transportation", "SUM(transport_needed)",
        "Members who need a ride to their appointment."),
    "transport_gap": Metric(
        "transport_gap", "Ride not booked", "SUM(transport_gap)",
        "Transport needed and no ride arranged."),
    "discharge_gap": Metric(
        "discharge_gap", "Discharged, no follow-up", "SUM(discharge_gap)",
        "Out of hospital within 30 days with nothing on the calendar."),
    "medication_question": Metric(
        "medication_question", "Medication questions", "SUM(medication_question)",
        "Members who raised a medication question."),
    "no_caregiver": Metric(
        "no_caregiver", "No caregiver on file", "SUM(1 - caregiver_known)",
        "No caregiver contact recorded."),
    "needs_interpreter": Metric(
        "needs_interpreter", "Interpreter needed", "SUM(needs_interpreter)",
        "Documented interpreter requirement."),
    "contact_rate": Metric(
        "contact_rate", "Contact rate", "100.0 * SUM(reached) / COUNT(*)",
        "Share reached at least once.", unit="percent"),
    "unreached": Metric(
        "unreached", "Never reached", "SUM(1 - reached)",
        "No successful contact on any attempt."),
    "call_volume": Metric(
        "call_volume", "Calls placed", "SUM(attempt_count)",
        "Total outreach attempts."),
    "calls_per_member": Metric(
        "calls_per_member", "Calls per member", "AVG(attempt_count)",
        "Mean attempts per member.", unit="rate"),
}

#: column name -> human label. Only these can be grouped on.
DIMENSIONS: dict[str, str] = {
    "clinic": "Centre",
    "language": "Preferred language",
    "contact_method": "Contact preference",
    "outreach_reason": "Outreach reason",
    "age_band": "Age band",
}

#: column -> the values a filter may take. Anything else is refused.
FILTERS: dict[str, tuple[str, ...]] = {
    "clinic": tuple(CLINICS),
    "language": ("Spanish", "English"),
    "contact_method": ("phone", "sms", "caregiver", "portal"),
    "age_band": ("65-74", "75-84", "85+"),
}


@dataclass
class QueryPlan:
    metric: str
    dimension: str | None = None
    filters: list[tuple[str, str]] = field(default_factory=list)
    limit: int = 20
    source: str = "rules"                     # rules | model
    rejected: list[str] = field(default_factory=list)


@dataclass
class Answer:
    plan: QueryPlan
    sql: str
    params: list
    total: float
    rows: list[dict]
    row_count: int


# ══════════════════════════════════════════════════════════════════════════
#  Validation — the gate the model's plan must pass
# ══════════════════════════════════════════════════════════════════════════

def validate_plan(raw: dict) -> QueryPlan:
    """Reduce an untrusted plan to one that is safe to compile.

    Never raises. Anything unrecognised is dropped with a reason and the query
    still runs on what survived, because a partial answer with a visible
    caveat beats an error page.

    >>> p = validate_plan({"metric": "mortality_risk", "groupBy": "ssn"})
    >>> p.metric, p.dimension, len(p.rejected)
    ('members', None, 2)
    """
    rejected: list[str] = []

    metric = str(raw.get("metric", ""))
    if metric not in METRICS:
        rejected.append(f"unknown metric '{metric}' — rejected")
        metric = "members"

    dimension = raw.get("groupBy") or raw.get("dimension")
    dimension = str(dimension) if dimension and str(dimension) != "null" else None
    if dimension and dimension not in DIMENSIONS:
        rejected.append(f"unknown dimension '{dimension}' — dropped")
        dimension = None

    filters: list[tuple[str, str]] = []
    for item in raw.get("filters") or []:
        if not isinstance(item, dict):
            rejected.append("filter was not an object — dropped")
            continue
        column = str(item.get("col") or item.get("column") or "")
        value = str(item.get("val") or item.get("value") or "")
        allowed = FILTERS.get(column)
        if allowed is None:
            rejected.append(f"filter column '{column}' is not in the allowlist — dropped")
            continue
        match = next((a for a in allowed if a.lower() == value.lower()), None)
        if match is None:
            rejected.append(f"value '{value}' is not permitted for '{column}' — dropped")
            continue
        filters.append((column, match))

    try:
        limit = int(raw.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(settings.max_row_limit, limit))

    return QueryPlan(metric=metric, dimension=dimension, filters=filters,
                     limit=limit, source="model", rejected=rejected)


# ══════════════════════════════════════════════════════════════════════════
#  Compile and run
# ══════════════════════════════════════════════════════════════════════════

def compile_sql(plan: QueryPlan) -> tuple[str, list]:
    """Build parameterised SQL from an already-validated plan.

    Identifiers come from the allowlists above and are never taken from input;
    values are bound as parameters. Both halves matter.
    """
    metric = METRICS[plan.metric]
    select = [f"{metric.sql} AS {plan.metric}"]
    params: list = []

    if plan.dimension:
        select.insert(0, plan.dimension)
        select.append("COUNT(*) AS n")

    sql = f"SELECT {', '.join(select)}\n  FROM v_outreach_member"

    if plan.filters:
        sql += "\n WHERE " + "\n   AND ".join(f"{col} = ?" for col, _ in plan.filters)
        params.extend(value for _, value in plan.filters)

    if plan.dimension:
        sql += (f"\n GROUP BY {plan.dimension}"
                f"\nHAVING COUNT(*) >= {settings.min_group_size}"
                f"\n ORDER BY {plan.metric} DESC"
                f"\n LIMIT {plan.limit}")
    return sql + ";", params


def execute(plan: QueryPlan, db_path: Path | None = None) -> Answer:
    """Run the plan against the database, read-only."""
    sql, params = compile_sql(plan)
    total_sql, total_params = compile_sql(
        QueryPlan(metric=plan.metric, dimension=None, filters=plan.filters))

    with connect(db_path, readonly=True) as conn:
        total = conn.execute(total_sql, total_params).fetchone()[plan.metric]
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()] if plan.dimension else []

    return Answer(plan=plan, sql=sql, params=params,
                  total=float(total or 0), rows=rows, row_count=len(rows))


# ══════════════════════════════════════════════════════════════════════════
#  Turning a question into a plan
# ══════════════════════════════════════════════════════════════════════════

#: Longest matching phrase wins, so a specific phrase beats a generic one.
_METRIC_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("discharge_gap", ("discharged", "discharge", "follow-up", "follow up", "readmission")),
    ("transport_gap", ("ride not booked", "not arranged", "transport gap")),
    ("transport_needed", ("transportation", "transport", "ride")),
    ("medication_question", ("medication", "pharmacy", "prescription")),
    ("no_caregiver", ("no caregiver", "caregiver", "family contact")),
    ("needs_interpreter", ("interpreter",)),
    ("review_rate", ("review rate", "share needing", "proportion needing")),
    ("needs_review", ("need a person", "need a human", "review queue", "flagged")),
    ("contact_rate", ("contact rate", "reach rate", "did we reach", "reached")),
    ("unreached", ("never reached", "not reached", "unreachable", "no contact")),
    ("call_volume", ("calls placed", "call volume", "how many calls", "total calls")),
    ("calls_per_member", ("calls per member", "attempts per")),
    ("members", ("members", "patients", "how many", "count")),
]
_DIMENSION_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("clinic", ("centre", "center", "clinic", "location")),
    ("language", ("language", "spanish", "english")),
    ("contact_method", ("contact preference", "contact method", "channel")),
    ("outreach_reason", ("outreach reason", "reason")),
    ("age_band", ("age band", "age", "elderly", "older")),
]


def plan_from_rules(question: str) -> QueryPlan:
    """Deterministic question parsing. No network, always available.

    >>> plan_from_rules("transport gap by centre").metric
    'transport_gap'
    >>> plan_from_rules("contact rate by language").dimension
    'language'
    """
    text = f" {question.lower().strip()} "
    # Everything after " by " names the grain, not the measure.
    measure_text = text.split(" by ")[0]

    metric, best = "members", 0
    for name, words in _METRIC_WORDS:
        for word in words:
            if word in measure_text and len(word) > best:
                metric, best = name, len(word)
    if not best:
        for name, words in _METRIC_WORDS:
            for word in words:
                if word in text and len(word) > best:
                    metric, best = name, len(word)

    dimension, best_dim = None, 0
    for name, words in _DIMENSION_WORDS:
        for word in words:
            if word in text and len(word) > best_dim:
                dimension, best_dim = name, len(word)

    filters: list[tuple[str, str]] = []
    if "spanish" in text:
        filters.append(("language", "Spanish"))
    elif "english" in text:
        filters.append(("language", "English"))
    for clinic in CLINICS:
        if clinic.lower() in text:
            filters.append(("clinic", clinic))

    # Naming a value is a filter, not a grain: "contact rate for Spanish members"
    # should return one number, not a one-row breakdown by language.
    if dimension and any(col == dimension for col, _ in filters):
        dimension = None

    return QueryPlan(metric=metric, dimension=dimension, filters=filters, source="rules")


def _plan_prompt() -> str:
    metrics = "\n".join(f"  {m.name:<20}{m.description}" for m in METRICS.values())
    dims = "\n".join(f"  {k:<20}{v}" for k, v in DIMENSIONS.items())
    filters = "\n".join(f"  {k:<20}{', '.join(v)}" for k, v in FILTERS.items())
    return f"""You turn an operational question into a JSON query plan over a member
outreach dataset. You never see member data — only these names.

METRICS (choose exactly one)
{metrics}

DIMENSIONS (choose zero or one)
{dims}

FILTER COLUMNS AND THEIR ALLOWED VALUES
{filters}

Respond with ONLY:
{{"metric":"...","groupBy":"...|null","limit":20,"filters":[{{"col":"language","val":"Spanish"}}]}}"""


def plan_from_question(question: str, *, prefer_model: bool = True) -> QueryPlan:
    """Model when configured and working, deterministic parser otherwise."""
    if prefer_model and settings.llm_available:
        try:
            response = complete(_plan_prompt(), question, max_tokens=300)
            return validate_plan(parse_json(response.text))
        except LLMUnavailable:
            pass
    return plan_from_rules(question)


def ask(question: str, *, prefer_model: bool = True, db_path: Path | None = None) -> Answer:
    """Question in, answer out."""
    return execute(plan_from_question(question, prefer_model=prefer_model), db_path)
