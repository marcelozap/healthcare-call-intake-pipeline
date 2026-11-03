"""
Generate the synthetic outreach cohort.

This is a faithful port of the browser prototype's generator, calling the same
seeded RNG in the same order, so the two produce identical members. `verify.py`
checks that with a hash rather than asking anyone to take it on trust.

Nothing here is real. No member, name, phone number or clinical detail
corresponds to a real person. The clinic names are Leon's; everything attached
to them is invented.

Why the data is shaped the way it is
------------------------------------
Synthetic data is only useful if its problems are realistic. Three things are
deliberate:

  * Language misrouting is a **property of the member**, not of each call.
    Being on the wrong language queue is a configuration mistake, so it affects
    every attempt for that member. Modelling it per-call washes the effect out
    to nothing and hides the finding.

  * A minority of members generate most of the contact effort, because that is
    how outreach actually behaves.

  * Missing data is missing, not zero. A member with no phone has `has_phone`
    False and no dialler attempts at all — they fall out of the campaign
    silently, which is the failure the safety rules exist to catch.
"""

from __future__ import annotations

from app.config import settings
from app.domain import (CLINICS, CLINIC_WEIGHTS, FIRST_NAMES, LAST_NAMES,
                        REASON_WEIGHTS, REASONS, Attempt, ContactMethod,
                        Language, Member, Outcome)
from app.rng import Random


def generate_cohort(seed: int | None = None, size: int | None = None) -> list[Member]:
    """Build the campaign cohort. Deterministic for a given seed.

    >>> members = generate_cohort()
    >>> len(members)
    420
    >>> members[0].member_id
    'LMC-200100'
    """
    rnd = Random(seed if seed is not None else settings.seed)
    n = size if size is not None else settings.cohort_size
    members: list[Member] = []

    for i in range(n):
        age = max(64, min(99, round(rnd.gauss(75, 9))))
        language = rnd.weighted([Language.SPANISH, Language.ENGLISH], [76, 24])
        reason = rnd.weighted(REASONS, REASON_WEIGHTS)

        # Post-discharge members are on the list *because* they were discharged;
        # a few others happen to have been recently, which is what makes the
        # discharge-with-no-appointment rule non-trivial to evaluate.
        if reason.id == "post_discharge":
            discharge_days: int | None = rnd.between(2, 26)
        else:
            discharge_days = rnd.between(3, 29) if rnd.chance(0.06) else None

        member = Member(
            member_id=f"LMC-{200100 + i * 3}",
            name=f"{rnd.pick(FIRST_NAMES)} {rnd.pick(LAST_NAMES)}",
            age=age,
            clinic=rnd.weighted(CLINICS, CLINIC_WEIGHTS),
            language=str(language),
            contact_method=str(rnd.weighted(
                [ContactMethod.PHONE, ContactMethod.SMS,
                 ContactMethod.CAREGIVER, ContactMethod.PORTAL], [71, 11, 11, 7])),
            caregiver_known=rnd.chance(0.31),
            needs_interpreter=language == Language.SPANISH and rnd.chance(0.22),
            transport_needed=rnd.chance(0.15),
            transport_booked=False,
            has_phone=rnd.chance(0.972),
            reason=reason,
            discharge_days=discharge_days,
            has_appointment=rnd.chance(0.70) if discharge_days is not None else rnd.chance(0.55),
            medication_question=rnd.chance(0.08),
            misrouted=False,
        )
        # Order matters: these two draws must happen here to stay in step with
        # the browser. Moving them changes every member that follows.
        member.misrouted = language == Language.SPANISH and rnd.chance(0.16)
        if member.transport_needed:
            member.transport_booked = rnd.chance(0.62)

        _simulate_attempts(member, rnd)
        members.append(member)

    return members


def _simulate_attempts(member: Member, rnd: Random) -> None:
    """Walk the campaign's contact attempts for one member.

    The loop stops early once somebody has been reached — most of the time.
    Occasionally it keeps going, because in a real campaign a member gets called
    again after a successful contact, and a model that never does that produces
    suspiciously tidy data.
    """
    planned = rnd.weighted([1, 2, 3, 4, 5, 6], [14, 19, 21, 19, 16, 11])

    for seq in range(planned):
        if member.reached and rnd.chance(0.72):
            break

        # Was the call taken by a Spanish-speaking coordinator?
        if member.misrouted:
            agent_spanish = False
        else:
            agent_spanish = ((member.language != Language.SPANISH)
                             if rnd.chance(0.05)
                             else (member.language == Language.SPANISH))

        if not member.has_phone:
            # No number on file: nothing can be dialled, and the record shows
            # why rather than simply being absent.
            outcome = str(Outcome.INCOMPLETE)
        elif member.language == Language.SPANISH and not agent_spanish:
            outcome = str(rnd.weighted(
                [Outcome.NEEDS_SPANISH, Outcome.NO_ANSWER, Outcome.VOICEMAIL],
                [62, 20, 18]))
        elif member.contact_method == ContactMethod.CAREGIVER:
            outcome = str(rnd.weighted(
                [Outcome.CAREGIVER_REQUESTED, Outcome.REACHED,
                 Outcome.NO_ANSWER, Outcome.VOICEMAIL], [38, 26, 20, 16]))
        else:
            outcome = str(rnd.weighted(
                [Outcome.REACHED, Outcome.NO_ANSWER, Outcome.VOICEMAIL,
                 Outcome.CALLBACK_REQUESTED, Outcome.WRONG_NUMBER, Outcome.INCOMPLETE],
                [38, 27, 19, 7, 3, 4]))

        member.attempts.append(Attempt(
            seq=seq,
            outcome=outcome,
            agent_spanish=agent_spanish,
            day_offset=rnd.between(1, 27),
        ))
