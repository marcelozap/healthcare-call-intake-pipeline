"""
Settings for the care-outreach platform.

Everything configurable lives here rather than being scattered through the code,
so there is exactly one place to look when something needs changing and exactly
one place a reviewer has to read to know how the system is wired.

Nothing here is secret. The API key is read from the environment and is never
written to disk, never logged, and never returned by the API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"


@dataclass(frozen=True)
class Settings:
    # ---- data ----------------------------------------------------------
    db_path: Path = DATA_DIR / "outreach.db"
    raw_dir: Path = DATA_DIR / "raw"

    # Fixed seed. The whole point is reproducibility: the same seed produces
    # the same 420 members every time, in Python and in the browser, so any
    # number on screen can be re-derived rather than taken on trust.
    seed: int = 880231
    cohort_size: int = 420

    # ---- extraction ----------------------------------------------------
    # Below this confidence a field is held for human confirmation instead of
    # being written through to the record. 0.80 is a judgement call, which is
    # exactly why it is written down here and not buried in a function.
    confidence_threshold: float = 0.80

    # ---- query layer ---------------------------------------------------
    # Groups smaller than this are suppressed, so a query cannot be narrowed
    # until it identifies one person.
    min_group_size: int = 5
    max_row_limit: int = 50

    # ---- model ---------------------------------------------------------
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")  # anthropic | openai
    llm_model_anthropic: str = "claude-sonnet-5"
    llm_model_openai: str = "gpt-4o-mini"
    llm_timeout_s: float = 12.0

    # Whisper is optional and heavy. "base" is enough for clear speech and
    # loads in a few seconds; "small" is noticeably better on accented audio.
    whisper_model: str = os.getenv("WHISPER_MODEL", "base")

    @property
    def api_key(self) -> str | None:
        """Read at call time, not import time, so a key set after start works."""
        if self.llm_provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY") or None
        return os.getenv("OPENAI_API_KEY") or None

    @property
    def llm_available(self) -> bool:
        return bool(self.api_key)

    def ensure_dirs(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        self.raw_dir.mkdir(exist_ok=True)


settings = Settings()
