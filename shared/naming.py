"""
Team-name normalisation shared by the scraper and the match registry.

LotusBook shows the same side as "Hyderabad Kingsmen Srl" in one place and "... SRL" in
another. Only the trailing "SRL" token is normalised; every other word is kept as shown.
"""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def canonical_team_name(name: object) -> str:
    """Trim, collapse runs of whitespace, and upper-case a trailing ``srl`` token.

    >>> canonical_team_name("  Rawalpindiz  Srl ")
    'Rawalpindiz SRL'
    >>> canonical_team_name("St Lucia Kings SRL")
    'St Lucia Kings SRL'
    >>> canonical_team_name("Hobart Hurricanes W")
    'Hobart Hurricanes W'
    """
    if name is None:
        return ""
    text = _WHITESPACE.sub(" ", str(name)).strip()
    if not text:
        return ""
    words = text.split(" ")
    if words[-1].lower() == "srl":
        words[-1] = "SRL"
    return " ".join(words)
