"""
Turn a call transcript into structured intake fields.

Two engines, one output shape
------------------------------
`extract_rules()` is deterministic pattern matching. It always works, needs no
network, and produces identical output for identical input.

`extract_model()` sends the transcript to a language model. Its output is then
put through `validation.py` before anything downstream can see it.

`extract()` picks: model if one is configured, rules otherwise, and rules again
if the model call fails. The caller gets the same object either way, so nothing
downstream knows or cares which ran.

Why keep the deterministic engine at all
----------------------------------------
Because it is the control. When the model and the rules disagree, that is
information — and because the rules are reproducible, a demo can never fail for
reasons outside the room.

Not diagnosis, not medical advice. This extracts operational facts a coordinator
needs in order to route a call.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import settings
from app.llm import LLMUnavailable, complete, parse_json


# ══════════════════════════════════════════════════════════════════════════
#  The field contract — shared by both engines and by the validator
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str                      # "string" | "boolean" | "enum"
    values: tuple[str, ...] = ()
    note: str = ""


FIELDS: dict[str, FieldSpec] = {
    "member_name":         FieldSpec("member_name", "string"),
    "language":            FieldSpec("language", "enum", ("Spanish", "English")),
    "contact_method":      FieldSpec("contact_method", "enum",
                                     ("phone", "sms", "caregiver", "portal")),
    "needs_interpreter":   FieldSpec("needs_interpreter", "boolean"),
    "transport_needed":    FieldSpec("transport_needed", "boolean"),
    "medication_question": FieldSpec("medication_question", "boolean"),
    "medication_name":     FieldSpec("medication_name", "string"),
    "medication_timing":   FieldSpec("medication_timing", "string"),
    "caregiver_contact":   FieldSpec("caregiver_contact", "boolean"),
    "recent_discharge":    FieldSpec("recent_discharge", "boolean"),
    "has_appointment":     FieldSpec("has_appointment", "boolean"),
}


@dataclass
class Extracted:
    """One field pulled out of a transcript.

    `source_text` is the part that matters. A value you cannot trace back to
    what the member actually said is not auditable, and in a clinical setting
    that makes it unusable regardless of how confident it looks.
    """
    name: str
    value: Any
    confidence: float | None
    source_text: str | None
    basis: str                     # stated | inferred | explicitly unknown | not discussed

    @property
    def below_threshold(self) -> bool:
        return self.confidence is not None and self.confidence < settings.confidence_threshold


@dataclass
class ExtractionResult:
    engine: str                    # "rules" | "model"
    transcript: str
    fields: list[Extracted]
    rejected: list[dict] = field(default_factory=list)
    fallback_reason: str | None = None

    @property
    def found(self) -> list[Extracted]:
        return [f for f in self.fields if f.value is not None]

    @property
    def confidence(self) -> float:
        scored = [f.confidence for f in self.fields if f.confidence is not None]
        return sum(scored) / len(scored) if scored else 0.0

    @property
    def held_for_review(self) -> list[str]:
        """Fields extracted below the write-through threshold."""
        return [f.name for f in self.fields if f.below_threshold]

    def get(self, name: str) -> Any:
        for f in self.fields:
            if f.name == name:
                return f.value
        return None


# ══════════════════════════════════════════════════════════════════════════
#  Deterministic engine
# ══════════════════════════════════════════════════════════════════════════

def fold_accents(text: str) -> str:
    """Strip accents while preserving length, so match offsets still line up.

    Speech-to-text drops accents inconsistently — "Tambien" for "También" — so
    every pattern has to match accent-insensitively. Getting this wrong loses
    fields silently, which is the worst kind of wrong.

    >>> fold_accents("español")
    'espanol'
    >>> len(fold_accents("También")) == len("También")
    True
    """
    return "".join(
        "n" if c == "ñ" else "N" if c == "Ñ"
        else unicodedata.normalize("NFD", c)[0]
        for c in text
    )


@dataclass(frozen=True)
class Pattern:
    field: str
    regex: re.Pattern
    to_value: Callable[[re.Match], Any]
    confidence: float | None
    basis: str = "stated"


PATTERNS: list[Pattern] = [
    Pattern("member_name",
            re.compile(r"(?:soy|habla|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"),
            lambda m: m.group(1), 0.90),
    Pattern("language",
            re.compile(r"prefiero que me llamen en espanol|hablo espanol|english is fine",
                       re.I),
            lambda m: "English" if "english" in m.group(0).lower() else "Spanish", 0.96),
    Pattern("contact_method",
            re.compile(r"que me llamen|calling to confirm|por telefono", re.I),
            lambda m: "phone", 0.86),
    # Inferred, not stated: she said she prefers Spanish, not that she needs an
    # interpreter. Scored below threshold on purpose so it is held, not written.
    Pattern("needs_interpreter",
            re.compile(r"prefiero que me llamen en espanol", re.I),
            lambda m: True, 0.72, basis="inferred"),
    Pattern("transport_needed",
            re.compile(r"necesito transporte[^.]*|do not need transportation", re.I),
            lambda m: "do not need" not in m.group(0).lower(), 0.94),
    Pattern("medication_question",
            re.compile(r"pregunta sobre mi medicamento|question about my medication", re.I),
            lambda m: True, 0.92),
    Pattern("medication_name",
            re.compile(r"\b(lisinopril|metformina|atorvastatina|furosemida|albuterol)\b", re.I),
            lambda m: m.group(1).capitalize(), 0.86),
    # The member said outright that she does not know. That is a fact worth
    # storing, and it is different from the field simply being blank.
    Pattern("medication_timing",
            re.compile(r"no recuerdo la hora exacta[^.]*", re.I),
            lambda m: None, None, basis="explicitly unknown"),
    Pattern("recent_discharge",
            re.compile(r"me dieron de alta[^.]*|discharged[^.]*|del hospital[^.]*", re.I),
            lambda m: True, 0.93),
    Pattern("has_appointment",
            re.compile(r"no tengo cita[^.]*|confirm my appointment[^.]*|"
                       r"already have it on the calendar", re.I),
            lambda m: not m.group(0).lower().startswith("no tengo"), 0.91),
    Pattern("caregiver_contact",
            re.compile(r"mi hija[^.]*|mi hijo[^.]*|my daughter[^.]*|my son[^.]*", re.I),
            lambda m: True, 0.84),
]


def extract_rules(transcript: str) -> ExtractionResult:
    """Pattern-match the transcript. Deterministic and offline.

    >>> r = extract_rules("Hola, soy Maria Alvarez. Necesito transporte para mi cita.")
    >>> r.get("member_name")
    'Maria Alvarez'
    >>> r.get("transport_needed")
    True
    >>> r.get("caregiver_contact") is None
    True
    """
    folded = fold_accents(transcript)
    by_name: dict[str, Extracted] = {}

    for pattern in PATTERNS:
        match = pattern.regex.search(folded)
        if match is None:
            continue
        by_name[pattern.field] = Extracted(
            name=pattern.field,
            value=pattern.to_value(match),
            confidence=pattern.confidence,
            source_text=transcript[match.start():match.end()],
            basis=pattern.basis,
        )

    fields = [
        by_name.get(name, Extracted(name, None, None, None, "not discussed"))
        for name in FIELDS
    ]
    return ExtractionResult(engine="rules", transcript=transcript, fields=fields)


# ══════════════════════════════════════════════════════════════════════════
#  Model engine
# ══════════════════════════════════════════════════════════════════════════

def _prompt() -> str:
    lines = "\n".join(
        f"  {s.name:<22}{s.kind}" + (f" ({'|'.join(s.values)})" if s.values else "")
        for s in FIELDS.values())
    return f"""You extract structured intake fields from a transcript of a call between a
Medicare Advantage care coordinator and a member. The call may be in Spanish or English.

Return ONLY JSON:
{{"fields":[{{"field":"...","value":...,"confidence":0.0,"source_span":"exact substring of the transcript"}}]}}

Allowed fields:
{lines}

Rules:
- source_span MUST be copied verbatim from the transcript. Never paraphrase it.
- Omit any field the transcript does not support. Do not guess.
- If the member states something is unknown to them, return value null with the
  span where they said so.
- confidence is your own 0-1 estimate. Score inferred values below stated ones.
- Never diagnose, interpret symptoms, or give medical advice."""


def extract_model(transcript: str) -> ExtractionResult:
    """Ask a model, then put its answer through the validator.

    Raises LLMUnavailable if the call fails or the response survives nothing.
    """
    from app.validation import validate_extraction        # local: avoids a cycle

    response = complete(_prompt(), f'Transcript:\n"""{transcript}"""')
    accepted, rejected = validate_extraction(parse_json(response.text), transcript)

    by_name = {a.name: a for a in accepted}
    fields = [
        by_name.get(name, Extracted(name, None, None, None, "not returned by model"))
        for name in FIELDS
    ]
    return ExtractionResult(engine="model", transcript=transcript,
                            fields=fields, rejected=rejected)


def extract(transcript: str, *, prefer_model: bool = True) -> ExtractionResult:
    """The entry point. Model when configured and working, rules otherwise."""
    if prefer_model and settings.llm_available:
        try:
            return extract_model(transcript)
        except LLMUnavailable as exc:
            result = extract_rules(transcript)
            result.fallback_reason = str(exc)
            return result
    return extract_rules(transcript)
