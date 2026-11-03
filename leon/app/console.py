"""
Make console output safe on Windows.

The Windows console still defaults to a legacy code page (cp1252 on most US
machines), and `print()` raises `UnicodeEncodeError` the moment it meets a
character outside it. An en dash or a `±` in a status line is enough to kill the
process — which is a genuinely bad way for a demo to fail, because the code that
crashes is the code that was only trying to tell you it worked.

Importing this module reconfigures stdout and stderr to UTF-8 and falls back to
replacing unencodable characters rather than raising. It is a no-op on macOS and
Linux, and on any Windows terminal that already speaks UTF-8.
"""

from __future__ import annotations

import sys


def make_output_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:          # not a text stream (redirected, captured)
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Locked to a code page we cannot change. `errors="replace"` on its
            # own still stops the crash; characters degrade to '?'.
            try:
                reconfigure(errors="replace")
            except Exception:            # noqa: BLE001 — never fail on setup
                pass


make_output_safe()
