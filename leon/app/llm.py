"""
The only place this project talks to a language model.

Two things are deliberate.

**One provider interface.** Anthropic and OpenAI differ in headers, body shape
and where the text ends up in the response. Those differences are absorbed here
so no other module has to know or care which one is configured.

**Failure is a normal outcome, not an exception to handle later.** Every call
has a timeout and every caller has a deterministic fallback. A demo that dies
because a network was slow is worse than one that never used a model.

The trust boundary, which matters more than the code
----------------------------------------------------
There are two very different uses of a model in this system:

  * **Extraction** must see the transcript. Content crosses the boundary. For
    real patient audio that requires a BAA-covered endpoint (Azure OpenAI,
    Bedrock) — never a public API. This project uses synthetic transcripts,
    which is precisely why it is safe to send them.

  * **Question answering** never sees a row. The model receives metric and
    dimension *names* and returns a query plan, which is validated and executed
    locally. There is no path by which asking a question can leak a member.

Collapsing those two into "we use AI" is how organisations end up sending
records somewhere they did not intend to.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.config import settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised for every failure mode: no key, timeout, bad status, bad JSON.

    Callers are expected to catch this and fall back, so there is no value in
    distinguishing the causes at the call site.
    """


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str


def complete(system: str, user: str, *, max_tokens: int = 900) -> LLMResult:
    """One completion. Raises LLMUnavailable rather than returning a sentinel.

    Uses urllib rather than the vendor SDKs on purpose: it keeps the dependency
    list to FastAPI alone, and it makes the request shape visible to anyone
    reading the file instead of hidden inside a client library.
    """
    key = settings.api_key
    if not key:
        raise LLMUnavailable("no API key configured")

    provider = settings.llm_provider
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"content-type": "application/json", "x-api-key": key,
                   "anthropic-version": "2023-06-01"}
        model = settings.llm_model_anthropic
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
    else:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"content-type": "application/json", "authorization": f"Bearer {key}"}
        model = settings.llm_model_openai
        body = {"model": model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}

    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=settings.llm_timeout_s) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise LLMUnavailable(f"HTTP {exc.code}") from exc
    except Exception as exc:                       # timeout, DNS, TLS, malformed JSON
        raise LLMUnavailable(str(exc)) from exc

    try:
        text = (payload["content"][0]["text"] if provider == "anthropic"
                else payload["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable("unexpected response shape") from exc

    return LLMResult(text=text, provider=provider, model=model)


def parse_json(text: str) -> dict:
    """Pull JSON out of a model response.

    Models wrap JSON in code fences even when told not to, so strip those before
    parsing rather than failing on a formatting habit.

    >>> parse_json('```json\\n{"a": 1}\\n```')
    {'a': 1}
    >>> parse_json('{"b": 2}')
    {'b': 2}
    """
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"response was not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMUnavailable("response JSON was not an object")
    return parsed
