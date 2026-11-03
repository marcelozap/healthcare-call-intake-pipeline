"""
The vocabulary of the system.

Every controlled list lives here. If a value is not in this file it does not
exist as far as the rest of the platform is concerned — which is what stops
"Resolved", "RESOLVED" and "resolved" turning into three different things six
months from now.

The clinic list is Leon's real one. The rest is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StrEnum(str, Enum):
    """`enum.StrEnum` only exists in Python 3.11+. This is the same behaviour
    (members compare and format as plain strings) on 3.9 and up, so the project
    runs on whatever Python happens to be installed."""

    def __str__(self) -> str:  # noqa: D105
        return str(self.value)


# ── Leon's actual centres ────────────────────────────────────────────────
CLINICS: list[str] = [
    "Bird Road", "East Hialeah", "Flagler", "Homestead",
    "Kendall", "Miami", "West Hialeah", "Westchester",
]
# Rough relative panel sizes. Synthetic, but not uniform — uniform data hides
# exactly the kind of variation the system exists to surface.
CLINIC_WEIGHTS: list[int] = [13, 16, 12, 8, 13, 12, 12, 14]


class Language(StrEnum):
    SPANISH = "Spanish"
    ENGLISH = "English"


class ContactMethod(StrEnum):
    PHONE = "phone"
    SMS = "sms"
    CAREGIVER = "caregiver"
    PORTAL = "portal"


class Outcome(StrEnum):
    """What happened on a single outreach attempt.

    Eight values, not two. Most diallers record connected/not-connected, which
    collapses six situations that each need a different response: a voicemail is
    not a wrong number, a caregiver asking you to call back is not a refusal,
    and "needs a Spanish callback" is not a failed contact at all — it is a
    successful contact that was routed to the wrong queue.
    """
    REACHED = "reached"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    CALLBACK_REQUESTED = "callback_req"
    WRONG_NUMBER = "wrong_number"
    NEEDS_SPANISH = "needs_spanish"
    CAREGIVER_REQUESTED = "caregiver_req"
    INCOMPLETE = "incomplete"


#: Outcomes that mean we did not speak to the member on that attempt.
NO_CONTACT: frozenset[str] = frozenset(
    {Outcome.NO_ANSWER, Outcome.VOICEMAIL, Outcome.NEEDS_SPANISH,
     Outcome.WRONG_NUMBER, Outcome.INCOMPLETE}
)


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"


@dataclass(frozen=True)
class OutreachReason:
    id: str
    label: str
    needs_appointment: bool


REASONS: list[OutreachReason] = [
    OutreachReason("post_discharge", "Post-discharge follow-up", True),
    OutreachReason("medication_review", "Medication review", False),
    OutreachReason("annual_wellness", "Annual wellness visit", True),
    OutreachReason("care_gap", "Care gap closure", True),
    OutreachReason("transportation", "Transportation arrangement", False),
]
REASON_WEIGHTS: list[int] = [18, 22, 26, 24, 10]

FIRST_NAMES = ["Ana", "Jose", "Maria", "Carlos", "Luis", "Rosa", "Miguel", "Elena",
               "Pedro", "Carmen", "Jorge", "Isabel", "Rafael", "Marta", "Ramon",
               "Gloria", "Hector", "Julia", "Ernesto", "Silvia", "Orlando", "Teresa",
               "Armando", "Lucia", "Alberto", "Yolanda", "Mercedes", "Fernando",
               "Aida", "Caridad"]
LAST_NAMES = ["Garcia", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
              "Perez", "Sanchez", "Ramirez", "Torres", "Rivera", "Diaz", "Fernandez",
              "Alvarez", "Ruiz", "Castillo", "Vargas", "Morales", "Suarez", "Acosta",
              "Cruz", "Reyes", "Padron", "Valdes", "Quintana"]


# ── records ──────────────────────────────────────────────────────────────

@dataclass
class Attempt:
    """One outreach attempt against one member."""
    seq: int
    outcome: str
    agent_spanish: bool
    day_offset: int          # days before "today"


@dataclass
class Member:
    """A member on the outreach campaign, with the access profile attached.

    The access fields — language, contact method, caregiver, interpreter,
    transport — are the ones that decide *how* somebody should be contacted.
    Holding them next to the outreach history is what makes it possible to ask
    whether the two ever disagreed.
    """
    member_id: str
    name: str
    age: int
    clinic: str
    language: str
    contact_method: str
    caregiver_known: bool
    needs_interpreter: bool
    transport_needed: bool
    transport_booked: bool
    has_phone: bool
    reason: OutreachReason
    discharge_days: int | None      # days since hospital discharge, if any
    has_appointment: bool
    medication_question: bool
    misrouted: bool                 # assigned to the wrong language queue
    attempts: list[Attempt] = field(default_factory=list)

    # ---- derived, and deliberately not stored twice --------------------

    @property
    def reached(self) -> bool:
        return any(a.outcome == Outcome.REACHED for a in self.attempts)

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def age_band(self) -> str:
        return "85+" if self.age >= 85 else "75-84" if self.age >= 75 else "65-74"

    @property
    def transport_gap(self) -> bool:
        """Needs a ride and nobody has booked one."""
        return self.transport_needed and not self.transport_booked

    @property
    def discharge_gap(self) -> bool:
        """Out of hospital in the last 30 days with nothing on the calendar."""
        return (self.discharge_days is not None
                and self.discharge_days <= 30
                and not self.has_appointment)

    def count(self, *outcomes: str) -> int:
        return sum(1 for a in self.attempts if a.outcome in outcomes)
