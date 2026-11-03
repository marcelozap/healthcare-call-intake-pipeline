"""
Safety rules — the cases automation must hand to a person.

Each rule is one small function that answers a single question: is *this* member
in a situation an automated campaign should not be handling? It returns either
None or a sentence explaining itself in language a care manager can read and
disagree with. That readability is the point: a rule only engineering can audit
is not a safety control.

Two design decisions worth defending
------------------------------------
1. Rules return an explanation, not a boolean. A queue that says "flagged"
   without saying why gets ignored within a fortnight.

2. There is no rule for "member speaks Spanish". Roughly three quarters of
   Leon's members do, so that is not a finding — it is the population. What
   *is* a finding is a member who prefers Spanish being called by an
   English-speaking coordinator, which is the `language_mismatch` rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.domain import Member, Outcome, Severity


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    title: str
    rationale: str          # why this rule exists at all
    action: str             # what the person picking it up should do
    test: Callable[[Member], str | None]


@dataclass(frozen=True)
class Flag:
    rule: Rule
    detail: str

    @property
    def severity(self) -> str:
        return self.rule.severity


# ── the rules ────────────────────────────────────────────────────────────

def _discharge_no_followup(m: Member) -> str | None:
    if not m.discharge_gap:
        return None
    return (f"Discharged {m.discharge_days} days ago with no follow-up "
            f"appointment on file.")


def _language_mismatch(m: Member) -> str | None:
    """Prefers Spanish, but the calls went out in English.

    This is the one that hides best. The attempt *happened*, so it counts
    toward the contact rate — while being unable to accomplish anything.
    """
    if m.language != "Spanish":
        return None
    wrong_agent = sum(1 for a in m.attempts if not a.agent_spanish)
    asked = m.count(Outcome.NEEDS_SPANISH)
    if not wrong_agent and not asked:
        return None
    detail = f"{wrong_agent} call(s) placed by an English-speaking coordinator"
    if asked:
        detail += f"; {asked} explicitly asked for a Spanish callback"
    return detail + "."


def _elderly_repeated_no_contact(m: Member) -> str | None:
    """Three unanswered calls to an 80-year-old is a signal to change channel,
    not a reason to keep dialling the same number."""
    misses = m.count(Outcome.NO_ANSWER, Outcome.VOICEMAIL)
    if not (m.age >= 75 and misses >= 3 and not m.reached):
        return None
    tail = (" Caregiver contact is on file and has not been used."
            if m.caregiver_known else " No caregiver contact on file.")
    return f"Age {m.age}. {misses} attempts, never reached.{tail}"


def _missing_contact_data(m: Member) -> str | None:
    """A blank field removes a member from an automated campaign silently.
    Nobody gets an alert, because from the system's point of view nothing
    happened."""
    if m.has_phone:
        return None
    return "No phone number on file — this member cannot be dialled at all."


def _wrong_number(m: Member) -> str | None:
    n = m.count(Outcome.WRONG_NUMBER)
    if not n:
        return None
    return (f"{n} attempt(s) reached a wrong number. Continued dialling cannot "
            f"succeed, but each try still increments the attempt counter.")


def _transport_unarranged(m: Member) -> str | None:
    if not m.transport_gap:
        return None
    tail = (" An appointment is already booked." if m.has_appointment else "")
    return f"Transportation needed and no ride booked.{tail}"


def _callback_owed(m: Member) -> str | None:
    if not m.count(Outcome.CALLBACK_REQUESTED) or m.reached:
        return None
    return "Member asked us to call back and we have not."


def _caregiver_requested(m: Member) -> str | None:
    n = m.count(Outcome.CAREGIVER_REQUESTED)
    if not n:
        return None
    tail = ("; caregiver contact is on file." if m.caregiver_known
            else "; no caregiver contact recorded — capture it before proceeding.")
    return f"{n} attempt(s) ended with a caregiver asking to handle it{tail}"


def _medication_question(m: Member) -> str | None:
    if not m.medication_question:
        return None
    return ("Member raised a medication question. Clinical, and outside what an "
            "outreach coordinator or an automated workflow should answer.")


def _interpreter_unmet(m: Member) -> str | None:
    if not m.needs_interpreter:
        return None
    completed = [a for a in m.attempts if a.agent_spanish and a.outcome == Outcome.REACHED]
    if completed:
        return None
    attempted = sum(1 for a in m.attempts if a.agent_spanish)
    got = (f"{attempted} Spanish-language attempt(s), none completed"
           if attempted else "no Spanish-language attempt recorded")
    return f"Interpreter required. {got}."


RULES: list[Rule] = [
    Rule("discharge_no_followup", Severity.HIGH,
         "Recent discharge, no follow-up booked",
         "A member out of hospital in the last 30 days with nothing on the calendar "
         "is the highest-consequence gap here. Readmission risk concentrates in it.",
         "Book the follow-up on this call. Do not leave a voicemail and move on.",
         _discharge_no_followup),

    Rule("language_mismatch", Severity.HIGH,
         "Spanish preferred, English call placed",
         "The attempt counts toward the contact rate while being unable to "
         "accomplish anything, so the failure is invisible in the headline number.",
         "Route to a Spanish-speaking coordinator. Do not count the prior attempts "
         "against the persistence limit.",
         _language_mismatch),

    Rule("elderly_no_contact", Severity.HIGH,
         "Elderly member, 3+ attempts, no contact",
         "An automated dialler will call the same number indefinitely and record "
         "it as diligence.",
         "Try the caregiver contact if one exists; otherwise flag for a home visit "
         "or mail follow-up.",
         _elderly_repeated_no_contact),

    Rule("missing_contact_data", Severity.HIGH,
         "Missing phone number",
         "A blank field silently removes somebody from the campaign, and nothing "
         "raises an alert because nothing happened.",
         "Complete intake from the chart or at the next visit before the campaign closes.",
         _missing_contact_data),

    Rule("wrong_number", Severity.HIGH,
         "Number on file is not the member",
         "Every subsequent automated attempt is guaranteed to fail and still counts "
         "toward marking the member unreachable.",
         "Suppress dialling on this number and source a correct one before re-queuing.",
         _wrong_number),

    Rule("transport_unarranged", Severity.HIGH,
         "Transportation needed, not arranged",
         "For this population transport is frequently the entire reason a visit is "
         "missed. Booking the appointment without the ride produces a no-show.",
         "Arrange transport at the same time as the appointment, not afterwards.",
         _transport_unarranged),

    Rule("callback_owed", Severity.MEDIUM,
         "Callback requested, not returned",
         "The member did their part. An unreturned callback is the most damaging "
         "and most easily detected failure in an outreach programme.",
         "Return the call within one business day.",
         _callback_owed),

    Rule("caregiver_route", Severity.MEDIUM,
         "Caregiver asked to handle it",
         "Common and legitimate in an elderly population. It needs a documented "
         "caregiver contact and consent, not another attempt on the member's line.",
         "Confirm caregiver details and consent, then continue through them.",
         _caregiver_requested),

    Rule("medication_question", Severity.MEDIUM,
         "Medication question raised",
         "Clinical in nature and out of scope for outreach.",
         "Route to the pharmacy line or care team, and close the loop with the member.",
         _medication_question),

    Rule("interpreter_unmet", Severity.MEDIUM,
         "Interpreter needed, no completed Spanish contact",
         "A documented interpreter requirement with no completed Spanish conversation "
         "means it was either ignored or never captured.",
         "Confirm interpreter arrangements, then attempt again with a Spanish speaker.",
         _interpreter_unmet),
]

RULES_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}


def evaluate(member: Member) -> list[Flag]:
    """Run every rule against one member, highest severity first."""
    flags = [Flag(rule, detail)
             for rule in RULES
             if (detail := rule.test(member)) is not None]
    flags.sort(key=lambda f: 0 if f.severity == Severity.HIGH else 1)
    return flags


def needs_review(member: Member) -> bool:
    return any(rule.test(member) is not None for rule in RULES)
