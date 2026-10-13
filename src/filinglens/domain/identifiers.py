"""Canonical identifier helpers (CIK)."""

from __future__ import annotations

import re

_CIK_RE = re.compile(r"^(?:CIK)?\s*0*(\d{1,10})$", re.IGNORECASE)


def parse_cik(value: str | int) -> int:
    """Parse ``320193``, ``"0000320193"`` or ``"CIK0000320193"`` into the numeric CIK."""
    if isinstance(value, int):
        cik = value
    else:
        m = _CIK_RE.match(value.strip())
        if not m:
            raise ValueError(f"not a CIK: {value!r}")
        cik = int(m.group(1))
    if not 0 < cik < 10**10:
        raise ValueError(f"CIK out of range: {value!r}")
    return cik


def looks_like_cik(value: str) -> bool:
    return bool(_CIK_RE.match(value.strip()))


def cik10(value: str | int) -> str:
    """Zero-padded 10-digit CIK used in SEC API paths (``CIK##########.json``)."""
    return f"{parse_cik(value):010d}"
