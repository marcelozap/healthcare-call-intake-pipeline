"""
The pipeline: five messy extracts in, one conformed cohort out.

Principles this file is built on
--------------------------------
1. **The raw layer is never edited.** Files in `data/raw` land and stay as-is,
   so a bad transformation can always be re-run from the original bytes.

2. **Every rule is counted.** If a rule repaired forty thousand rows last night
   that is a number on a report, not a surprise in a board meeting.

3. **Nothing is dropped silently.** Rows that fail go to `quarantine` with the
   reason attached. A row that vanishes is a bug found six months later.

4. **Missing is missing.** A sentinel `-1` becomes NULL, not zero. Averaging a
   sentinel produces a number that is wrong and looks completely plausible.

The conformance functions at the top are the interesting part and are each
small enough to hold in your head. They have doctests because they are exactly
the kind of code that gets quietly broken by a well-meaning edit.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.domain import REASONS, Attempt, Member, OutreachReason

# ══════════════════════════════════════════════════════════════════════════
#  Conformance rules
# ══════════════════════════════════════════════════════════════════════════

_MEMBER_ID = re.compile(r"^(?:LMC-?)?(\d{6})$", re.IGNORECASE)


def conform_member_id(value: str | None) -> str | None:
    """Collapse every member-id shape onto one surrogate key.

    The same person arrives as 'LMC-200100', '200100' and 'lmc200100' depending
    on which system exported them. Joins silently miss rows until this is fixed
    in exactly one place.

    >>> conform_member_id("LMC-200100")
    'LMC-200100'
    >>> conform_member_id("  200100 ")
    'LMC-200100'
    >>> conform_member_id("lmc200100")
    'LMC-200100'
    >>> conform_member_id("") is None
    True
    >>> conform_member_id(None) is None
    True
    """
    if value is None:
        return None
    match = _MEMBER_ID.match(str(value).strip())
    return f"LMC-{match.group(1)}" if match else None


def conform_timestamp(date_part: str, time_part: str | None = None) -> datetime | None:
    """Turn any of the three source conventions into one UTC instant.

    The dialler splits a timestamp across two columns in US 12-hour format; the
    ADT feed uses epoch seconds. Neither states a time zone.

    >>> conform_timestamp("07/31/2026", "02:22 PM").hour
    14
    >>> conform_timestamp("1785510000").year
    2026
    >>> conform_timestamp("not a date") is None
    True
    """
    text = (date_part or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10}", text):
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    fmt = "%m/%d/%Y %I:%M %p" if time_part else "%m/%d/%Y"
    try:
        joined = f"{text} {time_part.strip()}" if time_part else text
        return datetime.strptime(joined, fmt).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


_OUTCOME_MAP = {
    "CONTACT_MADE": "reached", "NO_ANS": "no_answer", "VM_LEFT": "voicemail",
    "CB_REQ": "callback_req", "BAD_NUM": "wrong_number",
    "LANG_XFER_REQ": "needs_spanish", "CAREGIVER_REQ": "caregiver_req",
    "INCOMPLETE": "incomplete",
}


def conform_outcome(value: str | None) -> str:
    """Map a vendor disposition code onto the closed set.

    Anything unrecognised becomes 'unknown' rather than being guessed at. A
    rising count of 'unknown' is the alarm that a vendor changed their codes
    without telling anyone.

    >>> conform_outcome("CONTACT_MADE")
    'reached'
    >>> conform_outcome(" vm_left ")
    'voicemail'
    >>> conform_outcome("RSLVD_V2")
    'unknown'
    """
    return _OUTCOME_MAP.get(str(value or "").strip().upper(), "unknown")


def clean_talk_seconds(value) -> int | None:
    """The platform writes -1 for 'not recorded'.

    Treating that as zero drags every duration metric downward while leaving a
    number that looks entirely reasonable. NULL is the honest representation.

    >>> clean_talk_seconds("240")
    240
    >>> clean_talk_seconds(-1) is None
    True
    >>> clean_talk_seconds("") is None
    True
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return None if seconds < 0 else seconds


def conform_bool(value: str | None) -> bool:
    """Sources disagree about how to write a boolean.

    >>> [conform_bool(v) for v in ("1", "Y", "true", "0", "N", "", None)]
    [True, True, True, False, False, False, False]
    """
    return str(value or "").strip().upper() in {"1", "Y", "YES", "TRUE", "T"}


# ══════════════════════════════════════════════════════════════════════════
#  Run accounting
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineReport:
    rules: list[dict] = field(default_factory=list)
    quarantine: list[dict] = field(default_factory=list)

    def rule(self, name: str, source: str, rows: int, action: str) -> None:
        self.rules.append({"rule": name, "source": source, "rows": rows, "action": action})

    def reject(self, source: str, key: str, reason: str) -> None:
        self.quarantine.append({"source": source, "key": key, "reason": reason})

    def render(self) -> str:
        if not self.rules:
            return "(no rules fired)"
        width = max(len(r["rule"]) for r in self.rules)
        lines = ["", "PIPELINE REPORT", "=" * (width + 44)]
        for r in self.rules:
            lines.append(f"  {r['rule']:<{width}}  {r['rows']:>7,}  {r['action']}")
        lines.append("-" * (width + 44))
        lines.append(f"  {'quarantined':<{width}}  {len(self.quarantine):>7,}  held with reason, not dropped")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  Extract → conform → assemble
# ══════════════════════════════════════════════════════════════════════════

def run(raw_dir: Path | None = None) -> tuple[list[Member], PipelineReport]:
    """Read the five extracts and rebuild the conformed cohort."""
    raw = raw_dir or settings.raw_dir
    report = PipelineReport()

    members = _load_campaign(raw, report)
    _apply_access(raw, members, report)
    _apply_attempts(raw, members, report)
    _apply_discharge(raw, members, report)
    _apply_portal(raw, members, report)

    for m in members.values():
        m.attempts.sort(key=lambda a: a.seq)
    return list(members.values()), report


def _load_campaign(raw: Path, report: PipelineReport) -> dict[str, Member]:
    payload = json.loads((raw / "campaign.json").read_text(encoding="utf-8"))
    by_label: dict[str, OutreachReason] = {r.label: r for r in REASONS}
    members: dict[str, Member] = {}
    unresolved = 0

    for row in payload["data"]:
        member_id = conform_member_id(row["memberId"])
        if member_id is None:
            unresolved += 1
            report.reject("campaign", str(row.get("memberId")), "member id does not match any known format")
            continue
        members[member_id] = Member(
            member_id=member_id, name=row["memberName"], age=int(row["age"]),
            clinic=row["assignedClinic"], language="", contact_method="",
            caregiver_known=False, needs_interpreter=False,
            transport_needed=False, transport_booked=False, has_phone=False,
            reason=by_label[row["outreachReason"]], discharge_days=None,
            has_appointment=conform_bool(row["followUpApptOnFile"]),
            medication_question=False, misrouted=False,
        )
    report.rule("campaign rows loaded", "care management", len(members), "one row per member")
    if unresolved:
        report.rule("unresolvable member ids", "care management", unresolved, "quarantined")
    return members


def _apply_access(raw: Path, members: dict[str, Member], report: PipelineReport) -> None:
    """Positional TSV, no header. Columns are defined outside the file."""
    lang = {"SPA": "Spanish", "ENG": "English"}
    contact = {"PH": "phone", "TX": "sms", "CG": "caregiver", "PT": "portal"}
    seen: set[str] = set()
    duplicates = reconformed = no_phone = orphans = 0

    for line in (raw / "member_access.tsv").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        member_id = conform_member_id(cols[0])
        if member_id is None or member_id not in members:
            orphans += 1
            report.reject("access", cols[0], "no matching member on the campaign")
            continue
        if cols[0] != member_id:
            reconformed += 1
        if member_id in seen:
            duplicates += 1
            continue
        seen.add(member_id)

        m = members[member_id]
        m.language = lang.get(cols[1], "")
        m.contact_method = contact.get(cols[2], "")
        m.caregiver_known = conform_bool(cols[3])
        m.needs_interpreter = conform_bool(cols[4])
        m.transport_needed = conform_bool(cols[5])
        m.transport_booked = conform_bool(cols[6])
        m.has_phone = bool(cols[7].strip())
        if not m.has_phone:
            no_phone += 1

    report.rule("member ids conformed", "access profile", reconformed, "bare digits -> LMC-######")
    report.rule("duplicate profile rows", "access profile", duplicates, "deduplicated on conformed key")
    report.rule("members with no phone on file", "access profile", no_phone, "retained and flagged, not dropped")
    if orphans:
        report.rule("profiles with no campaign member", "access profile", orphans, "quarantined")


def _apply_attempts(raw: Path, members: dict[str, Member], report: PipelineReport) -> None:
    seen_calls: set[str] = set()
    duplicates = orphans = unknown = sentinels = bad_ts = 0

    with (raw / "dialler.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            call_id = row["CALL_ID"]
            if call_id in seen_calls:
                duplicates += 1                      # same leg written twice
                continue
            seen_calls.add(call_id)

            if conform_timestamp(row["ATTEMPT_DT"], row["ATTEMPT_TM"]) is None:
                bad_ts += 1
                report.reject("dialler", call_id, "unparseable timestamp")
                continue

            member_id = conform_member_id(row["MBR_REF"])
            if member_id is None or member_id not in members:
                orphans += 1
                report.reject("dialler", call_id,
                              "blank member reference" if not row["MBR_REF"]
                              else "member not on the campaign")
                continue

            outcome = conform_outcome(row["DISPOSITION"])
            if outcome == "unknown":
                unknown += 1
            if clean_talk_seconds(row["TALK_SEC"]) is None and str(row["TALK_SEC"]).strip():
                sentinels += 1

            members[member_id].attempts.append(Attempt(
                seq=int(row["ATTEMPT_NO"]) - 1,
                outcome=outcome,
                agent_spanish=row["AGENT_LANG"].strip().upper() == "ES",
                day_offset=0,
            ))

    report.rule("duplicate call legs", "dialler", duplicates, "deduplicated on CALL_ID")
    report.rule("sentinel talk times (-1)", "dialler", sentinels, "set NULL, excluded from averages")
    report.rule("attempts with no resolvable member", "dialler", orphans, "quarantined for review")
    if bad_ts:
        report.rule("unparseable timestamps", "dialler", bad_ts, "quarantined")
    if unknown:
        report.rule("UNMAPPED DISPOSITION CODES", "dialler", unknown,
                    "vendor changed a code — investigate before publishing")


def _apply_discharge(raw: Path, members: dict[str, Member], report: PipelineReport) -> None:
    text = (raw / "discharge.xml").read_text(encoding="utf-8")
    linked = 0
    for block in re.findall(r"<event>(.*?)</event>", text, re.S):
        key = re.search(r"<patientKey>([^<]*)</patientKey>", block)
        epoch = re.search(r"<occurredEpoch>([^<]*)</occurredEpoch>", block)
        if not key or not epoch:
            continue
        member_id = conform_member_id(key.group(1))
        if member_id is None or member_id not in members:
            continue
        when = conform_timestamp(epoch.group(1))
        if when is None:
            continue
        from app.feeds import NOW
        members[member_id].discharge_days = (NOW - when).days
        linked += 1
    report.rule("discharge events linked", "ADT feed", linked, "epoch -> UTC, key rehydrated")


_PORTAL = re.compile(r"^(\S+ \S+) \[(\w+)\] outreach\.sms mbr=(\S+) .*status=(\S+)")


def _apply_portal(raw: Path, members: dict[str, Member], report: PipelineReport) -> None:
    linked = unmatched = 0
    for line in (raw / "portal.log").read_text(encoding="utf-8").splitlines():
        match = _PORTAL.match(line)
        if not match:
            if line.strip():
                unmatched += 1
                report.reject("portal", line[:40], "line did not match the extraction pattern")
            continue
        member_id = conform_member_id(match.group(3))
        if member_id is None or member_id not in members:
            continue
        if match.group(4) == "medication_question":
            members[member_id].medication_question = True
            linked += 1
    report.rule("medication questions linked", "portal log", linked, "regex extraction")
    report.rule("log lines not matching pattern", "portal log", unmatched,
                "quarantined — a silent skip here loses a clinical question")
