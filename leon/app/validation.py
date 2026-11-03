"""
The gate. Nothing a model returns is trusted until it has been through here.

Four checks, in order of how much they matter
--------------------------------------------

1. **Allowlist.** A field not in the contract is refused before its value is
   even read. This is what stops a model helpfully inventing a `diagnosis`
   field — which this system must never infer, no matter how confident the
   model sounds.

2. **Type.** `"probably"` is not a boolean. A permissive parser coerces it
   silently, to True or False depending on how it was written, and either way
   produces a fabricated answer to a question the model declined to answer.

3. **Confidence in range.** A score of 7 on a 0–1 scale means the model is not
   honouring the contract. Clamping it to 1 is worse than refusing it: it
   normalises nonsense into looking certain.

4. **The span exists in the transcript.** This is the anti-fabrication control
   and the reason any of this is safe near a patient record. A model can
   produce a quote that is fluent, plausible, and entirely invented. The only
   defence is to check it character by character against the source.

Every rejection carries a reason, because a validator that silently drops things
is just a different kind of unreliable.
"""

from __future__ import annotations

from app.extraction import FIELDS, Extracted, fold_accents

_TRUE = {"true", "yes", "si", "sí", "1", "t", "y"}
_FALSE = {"false", "no", "0", "f", "n"}


def validate_extraction(payload: dict, transcript: str) -> tuple[list[Extracted], list[dict]]:
    """Check a model's extraction response. Returns (accepted, rejected).

    Raises ValueError if the response is so malformed that nothing survives —
    the caller treats that as "the model failed" and falls back.
    """
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list):
        raise ValueError("response had no fields array")

    haystack = fold_accents(transcript).lower()
    accepted: list[Extracted] = []
    rejected: list[dict] = []
    seen: set[str] = set()

    def refuse(name, reason, span=None):
        entry = {"field": str(name), "reason": reason}
        if span:
            entry["claimed_span"] = span
        rejected.append(entry)

    for item in raw_fields:
        if not isinstance(item, dict):
            refuse("?", "entry was not an object")
            continue

        name = item.get("field")
        spec = FIELDS.get(str(name))
        if spec is None:
            refuse(name, "not in the allowed field list")
            continue
        if name in seen:
            refuse(name, "duplicate field in response")
            continue

        value, error = _coerce(item.get("value"), spec)
        if error:
            refuse(name, error)
            continue

        span = item.get("source_span")
        if not isinstance(span, str) or not span.strip():
            refuse(name, "no source_span supplied — the value is unverifiable")
            continue
        index = haystack.find(fold_accents(span.strip()).lower())
        if index == -1:
            refuse(name, "source_span does not appear in the transcript — fabricated quote",
                   span=span.strip())
            continue

        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            refuse(name, "confidence missing or not a number")
            continue
        if not 0.0 <= float(confidence) <= 1.0:
            refuse(name, f"confidence {confidence} is outside 0-1 — the model is not "
                         f"honouring the contract, so the value is not trusted")
            continue

        seen.add(name)
        accepted.append(Extracted(
            name=str(name),
            value=value,
            confidence=float(confidence),
            source_text=transcript[index:index + len(span.strip())],
            basis="model-extracted, span verified",
        ))

    if not accepted:
        raise ValueError("every field was rejected by the validator")
    return accepted, rejected


def _coerce(value, spec) -> tuple[object, str | None]:
    """Return (value, error). None is always allowed — it means 'stated as unknown'."""
    if value is None:
        return None, None

    if spec.kind == "boolean":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            text = value.strip().lower()
            if text in _TRUE:
                return True, None
            if text in _FALSE:
                return False, None
            return None, f'"{value}" is not a boolean — refusing to guess which way it leans'
        return None, f"expected boolean, got {type(value).__name__}"

    if spec.kind == "enum":
        match = next((v for v in spec.values if str(value).lower() == v.lower()), None)
        if match is None:
            return None, f'"{value}" is not one of {", ".join(spec.values)}'
        return match, None

    if not isinstance(value, str):
        return None, f"expected string, got {type(value).__name__}"
    return value, None
