"""Text folding and name tokens, in one place (kit-local until the core guard/textnorm module is adopted).

`fold` is NFKC plus casefold, with every run of whitespace collapsed to one space and every control and format
character (zero-width space, joiner, soft hyphen, BOM, bidi marks) removed: the comparison used for deny-lists (so
"Ac<ZWSP>me" and "Acme  Corp" on two lines are found) and for "the same key once case is ignored". It does not fold
look-alike letters from other scripts or digits (the core guard's fold does; adopt it when it merges).
`tokens` splits an identifier on separators and camelCase boundaries (`amountUsd` -> amount, usd) so a name can be
matched word by word; callers refuse non-ASCII identifiers instead of folding look-alikes.
"""

from __future__ import annotations

import re
import unicodedata

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_WS = re.compile(r"\s+")


def fold(text: str) -> str:
    spaced = _WS.sub(" ", unicodedata.normalize("NFKC", text).casefold())
    return "".join(c for c in spaced if unicodedata.category(c) not in ("Cc", "Cf"))


def alnum(name: str) -> str:
    """Lower-case letters and digits only: how tool and action names are compared."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def tokens(name: str) -> list[str]:
    out: list[str] = []
    for part in _SPLIT.split(name):
        out += [t.lower() for t in _CAMEL.split(part) if t]
    return out
