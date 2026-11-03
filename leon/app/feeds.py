"""
Write the cohort out as five source extracts, in the shapes real systems emit.

Why bother making the data messy on purpose
-------------------------------------------
A pipeline that only ever sees clean input proves nothing. These writers
deliberately reintroduce the problems that actually occur when data crosses a
system boundary, so the conformance rules in `pipeline.py` have something real
to do and can be tested against a known answer:

  * five formats — JSON, CSV, TSV, XML, application log
  * four member-id shapes for the same person
  * three date conventions, one of them split across two columns
  * booleans as 0/1, Y/N, true/false depending on the source
  * a status vocabulary per system, sharing nothing
  * duplicate rows, blank keys, and a sentinel -1

The round trip is the test: cohort -> messy files -> pipeline -> database must
reproduce the identical fingerprint. If a conformance rule is wrong, the hash
moves and `tests/test_roundtrip.py` fails.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.domain import Member
from app.rng import Random

#: Fixed "today" so the extracts are reproducible.
NOW = datetime(2026, 7, 31, 17, 0, tzinfo=timezone.utc)

# Each source names the same field differently. Mapping these back is the job.
STATUS_BY_SOURCE = {
    "reached": "CONTACT_MADE", "no_answer": "NO_ANS", "voicemail": "VM_LEFT",
    "callback_req": "CB_REQ", "wrong_number": "BAD_NUM",
    "needs_spanish": "LANG_XFER_REQ", "caregiver_req": "CAREGIVER_REQ",
    "incomplete": "INCOMPLETE",
}
LANG_CODE = {"Spanish": "SPA", "English": "ENG"}
CONTACT_CODE = {"phone": "PH", "sms": "TX", "caregiver": "CG", "portal": "PT"}


def write_all(members: list[Member], raw_dir: Path | None = None) -> dict[str, int]:
    """Emit all five extracts. Returns row counts per file."""
    out = raw_dir or settings.raw_dir
    out.mkdir(parents=True, exist_ok=True)
    rnd = Random(settings.seed + 1)          # separate stream: messiness is not the data

    counts = {
        "campaign.json": _campaign(members, out),
        "dialler.csv": _dialler(members, out, rnd),
        "member_access.tsv": _access(members, out, rnd),
        "discharge.xml": _discharge(members, out),
        "portal.log": _portal(members, out),
    }
    return counts


def _campaign(members: list[Member], out: Path) -> int:
    """The call list, from the care-management system. The best-behaved feed."""
    payload = {
        "campaignId": "CMP-2026-07",
        "totalCount": len(members),
        "_links": {"self": "/api/v2/campaigns/CMP-2026-07"},
        "data": [
            {
                "memberId": m.member_id,               # already canonical here
                "memberName": m.name,
                "assignedClinic": m.clinic,
                "outreachReason": m.reason.label,
                "followUpApptOnFile": "Y" if m.has_appointment else "N",
                "age": m.age,
            }
            for m in members
        ],
    }
    (out / "campaign.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return len(members)


def _dialler(members: list[Member], out: Path, rnd: Random) -> int:
    """Telephony export. Two columns for one timestamp, coded dispositions,
    and the member reference drifting between prefixed and bare."""
    rows = []
    for m in members:
        for a in m.attempts:
            ts = NOW - timedelta(days=a.day_offset)
            ts = ts.replace(hour=9 + (a.seq * 3) % 9, minute=(a.seq * 17) % 60)
            rows.append({
                "CALL_ID": f"D{40000 + len(rows)}",
                # same person, two shapes, decided per row
                "MBR_REF": m.member_id if rnd.chance(0.45) else m.member_id.removeprefix("LMC-"),
                "ATTEMPT_DT": ts.strftime("%m/%d/%Y"),
                "ATTEMPT_TM": ts.strftime("%I:%M %p"),
                "DISPOSITION": STATUS_BY_SOURCE[a.outcome],
                "AGENT_LANG": "ES" if a.agent_spanish else "EN",
                "ATTEMPT_NO": a.seq + 1,
                # -1 is the platform's "not recorded". Averaging it silently
                # produces a wrong number that looks entirely plausible.
                "TALK_SEC": -1 if rnd.chance(0.03) else (rnd.between(60, 540)
                                                         if a.outcome == "reached" else 0),
            })

    # A real export contains rows that cannot be resolved. They are kept here so
    # the pipeline has to decide what to do with them rather than never seeing them.
    rows.append({"CALL_ID": f"D{40000 + len(rows)}", "MBR_REF": "",
                 "ATTEMPT_DT": "07/29/2026", "ATTEMPT_TM": "10:14 AM",
                 "DISPOSITION": "NO_ANS", "AGENT_LANG": "ES", "ATTEMPT_NO": 1, "TALK_SEC": 0})
    rows.append({"CALL_ID": f"D{40000 + len(rows)}", "MBR_REF": "LMC-999999",
                 "ATTEMPT_DT": "07/30/2026", "ATTEMPT_TM": "02:05 PM",
                 "DISPOSITION": "CB_REQ", "AGENT_LANG": "EN", "ATTEMPT_NO": 1, "TALK_SEC": 60})
    # duplicate call leg — the same event written twice by the switch
    if len(rows) > 5:
        rows.append(dict(rows[4]))

    with (out / "dialler.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _access(members: list[Member], out: Path, rnd: Random) -> int:
    """Patient access profile. Tab separated, positional, no header row —
    the column meanings live in an email somewhere."""
    lines = []
    for m in members:
        lines.append("\t".join([
            m.member_id if rnd.chance(0.6) else m.member_id.removeprefix("LMC-"),
            LANG_CODE[m.language],
            CONTACT_CODE[m.contact_method],
            "1" if m.caregiver_known else "0",
            "1" if m.needs_interpreter else "0",
            "1" if m.transport_needed else "0",
            "1" if m.transport_booked else "0",
            f"305-555-{2000 + (int(m.member_id.removeprefix('LMC-')) % 8000):04d}"
            if m.has_phone else "",            # blank, not "unknown"
            str(m.age),
        ]))
    # duplicate profile row: a full nightly re-export accumulates these
    lines.append(lines[7])
    (out / "member_access.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def _discharge(members: list[Member], out: Path) -> int:
    """ADT feed. XML, epoch seconds, no prefix on the key."""
    events = []
    for m in members:
        if m.discharge_days is None:
            continue
        when = NOW - timedelta(days=m.discharge_days)
        events.append(
            "  <event>\n"
            f"    <eventId>ADT{9000 + len(events)}</eventId>\n"
            f"    <patientKey>{m.member_id.removeprefix('LMC-')}</patientKey>\n"
            "    <eventType>DISCHARGE</eventType>\n"
            f"    <occurredEpoch>{int(when.timestamp())}</occurredEpoch>\n"
            "  </event>"
        )
    (out / "discharge.xml").write_text(
        "<events>\n" + "\n".join(events) + "\n</events>\n", encoding="utf-8")
    return len(events)


def _portal(members: list[Member], out: Path) -> int:
    """SMS/portal traffic. Not a dataset at all — log lines that happen to
    contain the only record of a member raising a medication question."""
    lines = []
    for m in members:
        if not m.medication_question:
            continue
        when = NOW - timedelta(days=(m.age % 20) + 1)
        lines.append(
            f"{when.strftime('%Y-%m-%d %H:%M:%S')} [INFO] outreach.sms "
            f"mbr=lmc{m.member_id.removeprefix('LMC-')} channel=sms "
            f"status=medication_question lang={LANG_CODE[m.language].lower()}"
        )
    # lines the extraction pattern will not match, kept so they can be counted
    lines.append("#### log rotated 2026-07-28 ####")
    lines.append("2026-07-27T14:88:01 outreach.sms TRUNCATED WRITE mbr=")
    (out / "portal.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)
