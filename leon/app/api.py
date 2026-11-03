"""
The HTTP service.

Endpoints
---------
    GET  /api/health          what is wired up right now
    GET  /api/members         the review queue, filterable
    GET  /api/members/{id}    one member with their attempts and flags
    GET  /api/rules           the safety rules and how often each fires
    POST /api/extract         transcript -> structured fields
    POST /api/ask             plain-English question -> validated SQL -> answer
    POST /api/transcribe      audio -> transcript (optional, needs whisper)
    GET  /api/pipeline        the last run's data-quality report

Two conventions worth noticing
------------------------------
**The database is opened read-only** for everything the API serves. A query-layer
bug cannot write, no matter what it is asked to do.

**Every response says how it was produced.** `engine`, `fallback_reason` and
`rejected` are on the payload, so the caller can always tell whether a model was
involved and what the validator refused. A system that hides that is one you have
to trust; a system that reports it is one you can check.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.ask import DIMENSIONS, FILTERS, METRICS, ask, plan_from_question, execute
from app.config import WEB_DIR, settings
from app.db import connect
from app.extraction import extract
from app.rules import RULES_BY_ID

log = logging.getLogger(__name__)

app = FastAPI(
    title="Leon Care Outreach",
    description="Capture, check and ask over a Medicare Advantage outreach campaign. "
                "Synthetic data only — no PHI.",
    version="1.0.0",
)


# ══════════════════════════════════════════════════════════════════════════
#  Request models
# ══════════════════════════════════════════════════════════════════════════

class ExtractRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=8000)
    use_model: bool = True


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    use_model: bool = True


# ══════════════════════════════════════════════════════════════════════════
#  Health and metadata
# ══════════════════════════════════════════════════════════════════════════

@app.get("/api/health")
def health() -> dict[str, Any]:
    """What is actually connected. The frontend shows this so "is it real?"
    is answered on screen rather than in conversation."""
    db_ready, members = False, 0
    try:
        with connect(readonly=True) as conn:
            members = conn.execute("SELECT COUNT(*) FROM outreach_member").fetchone()[0]
            db_ready = True
    except Exception as exc:                       # noqa: BLE001 — reported, not raised
        log.warning("database not ready: %s", exc)

    return {
        "status": "ok" if db_ready else "database not built",
        "database": {"connected": db_ready, "path": str(settings.db_path),
                     "members": members},
        "model": {"configured": settings.llm_available,
                  "provider": settings.llm_provider if settings.llm_available else None},
        "transcription": {"available": _whisper_available()},
        "data": "synthetic — no PHI",
    }


@app.get("/api/schema")
def schema() -> dict[str, Any]:
    """The semantic layer, as data. This is exactly what a model is shown when
    it writes a query plan — nothing else."""
    return {
        "metrics": {k: {"label": m.label, "description": m.description, "unit": m.unit}
                    for k, m in METRICS.items()},
        "dimensions": DIMENSIONS,
        "filters": {k: list(v) for k, v in FILTERS.items()},
    }


# ══════════════════════════════════════════════════════════════════════════
#  Members and the review queue
# ══════════════════════════════════════════════════════════════════════════

@app.get("/api/members")
def members(
    needs_review: bool | None = None,
    clinic: str | None = None,
    rule: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """The review queue. Filters are allowlisted, values are bound."""
    where, params = [], []
    if needs_review is not None:
        where.append("needs_review = ?")
        params.append(int(needs_review))
    if clinic:
        if clinic not in FILTERS["clinic"]:
            raise HTTPException(400, f"unknown clinic '{clinic}'")
        where.append("clinic = ?")
        params.append(clinic)
    if rule:
        if rule not in RULES_BY_ID:
            raise HTTPException(400, f"unknown rule '{rule}'")
        where.append("member_id IN (SELECT member_id FROM outreach_flag WHERE rule_id = ?)")
        params.append(rule)

    sql = "SELECT * FROM v_outreach_member"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY high_flags DESC, flag_count DESC, member_id LIMIT ?"
    params.append(limit)

    with connect(readonly=True) as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
        for row in rows:
            row["flags"] = [dict(f) for f in conn.execute(
                "SELECT rule_id, severity, detail FROM outreach_flag WHERE member_id = ?",
                (row["member_id"],))]
    return {"count": len(rows), "members": rows}


@app.get("/api/members/{member_id}")
def member_detail(member_id: str) -> dict[str, Any]:
    with connect(readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM v_outreach_member WHERE member_id = ?", (member_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"no member {member_id}")
        member = dict(row)
        member["attempts"] = [dict(a) for a in conn.execute(
            "SELECT seq, outcome, agent_spanish, day_offset FROM outreach_attempt "
            "WHERE member_id = ? ORDER BY seq", (member_id,))]
        member["flags"] = [
            dict(f) | {"title": RULES_BY_ID[f["rule_id"]].title,
                       "action": RULES_BY_ID[f["rule_id"]].action}
            for f in conn.execute(
                "SELECT rule_id, severity, detail FROM outreach_flag WHERE member_id = ?",
                (member_id,))
            if f["rule_id"] in RULES_BY_ID]
    return member


@app.get("/api/rules")
def rules() -> dict[str, Any]:
    """Every rule with its firing count.

    A rule that never fires is wrong or unnecessary; a rule that fires on most
    of the cohort is a default, not a safety control. Both are visible here.
    """
    with connect(readonly=True) as conn:
        total = conn.execute("SELECT COUNT(*) FROM outreach_member").fetchone()[0]
        counts = {r["rule_id"]: r["n"] for r in conn.execute(
            "SELECT rule_id, COUNT(*) AS n FROM outreach_flag GROUP BY rule_id")}
    return {
        "cohort_size": total,
        "rules": [{
            "id": r.id, "severity": r.severity, "title": r.title,
            "rationale": r.rationale, "action": r.action,
            "fired": counts.get(r.id, 0),
            "share_pct": round(100 * counts.get(r.id, 0) / total, 1) if total else 0.0,
        } for r in RULES_BY_ID.values()],
    }


# ══════════════════════════════════════════════════════════════════════════
#  Capture and ask
# ══════════════════════════════════════════════════════════════════════════

@app.post("/api/extract")
def extract_endpoint(request: ExtractRequest) -> dict[str, Any]:
    """Transcript in, structured fields out.

    `rejected` lists whatever the validator refused. It is part of the response
    rather than a log line because refusals are the interesting part.
    """
    result = extract(request.transcript, prefer_model=request.use_model)
    return {
        "engine": result.engine,
        "fallback_reason": result.fallback_reason,
        "confidence": round(result.confidence, 3),
        "threshold": settings.confidence_threshold,
        "held_for_review": result.held_for_review,
        "rejected": result.rejected,
        "written_to_record_of_truth": False,      # a person decides, always
        "fields": [{
            "name": f.name, "value": f.value,
            "confidence": f.confidence, "source_text": f.source_text,
            "basis": f.basis, "below_threshold": f.below_threshold,
        } for f in result.fields],
    }


@app.post("/api/ask")
def ask_endpoint(request: AskRequest) -> dict[str, Any]:
    """Plain-English question over the conformed cohort.

    The generated SQL is returned with the answer. If a number cannot be traced
    to the query that produced it, it is not evidence.
    """
    plan = plan_from_question(request.question, prefer_model=request.use_model)
    answer = execute(plan)
    metric = METRICS[plan.metric]
    return {
        "question": request.question,
        "plan": {"metric": plan.metric, "dimension": plan.dimension,
                 "filters": [{"col": c, "val": v} for c, v in plan.filters],
                 "source": plan.source, "rejected": plan.rejected},
        "metric": {"label": metric.label, "unit": metric.unit,
                   "description": metric.description},
        "total": answer.total,
        "rows": answer.rows,
        "sql": answer.sql,
        "params": answer.params,
    }


# ══════════════════════════════════════════════════════════════════════════
#  Transcription (optional)
# ══════════════════════════════════════════════════════════════════════════

def _whisper_available() -> bool:
    try:
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _multipart_available() -> bool:
    """FastAPI needs python-multipart to accept a file upload at all."""
    try:
        import multipart  # noqa: F401
        return True
    except ImportError:
        return False


# The upload route is registered only when its dependency is present.
# Declaring an `UploadFile` parameter without python-multipart installed raises
# at import time and takes the entire service down — an optional feature must
# never be able to do that. Either way /api/transcribe exists and explains itself.
if _multipart_available():

    @app.post("/api/transcribe")
    async def transcribe(file: UploadFile) -> dict[str, Any]:
        """Audio in, transcript out. Needs `pip install openai-whisper`.

        Runs locally, so the audio never leaves the machine. That is the
        difference between something demonstrable and something deployable in a
        HIPAA setting: a browser speech API posts audio to a third party, and a
        local model does not.
        """
        if not _whisper_available():
            raise HTTPException(
                503, "transcription model not installed — run: pip install openai-whisper")

        import tempfile
        import whisper

        suffix = Path(file.filename or "audio.wav").suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            temp_path = tmp.name

        try:
            model = whisper.load_model(settings.whisper_model)
            result = model.transcribe(temp_path, fp16=False)
            return {"transcript": result["text"].strip(),
                    "language": result.get("language"),
                    "model": settings.whisper_model,
                    "processed": "locally — audio did not leave this machine"}
        finally:
            Path(temp_path).unlink(missing_ok=True)

else:

    @app.post("/api/transcribe")
    def transcribe_unavailable() -> dict[str, Any]:
        raise HTTPException(
            503, "file upload not available — run: pip install python-multipart openai-whisper")


# ══════════════════════════════════════════════════════════════════════════
#  Static frontend
# ══════════════════════════════════════════════════════════════════════════

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
