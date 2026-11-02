"""Typed problems: a `str` (so text checks keep working) that also carries a machine-readable `code`."""

from __future__ import annotations

CODES = (
    "NOT_JSON",
    "UNKNOWN_KEY",
    "WRONG_TYPE",
    "MISSING_KEY",
    "BAD_VALUE",
    "OVERSIZE",
    "CONFUSABLE_ID",
    "BAD_CHARACTER",
    "UNSUPPORTED_MINOR",
    "MINOR_TOO_LOW",
    "CHAIN",
    "RULE",
    "UNEXPECTED_FILE",
    "MISSING_FILE",
    "PAGE_MISMATCH",
    "REPORT_MISMATCH",
    "KIT_LOCK",
    "DENYLIST_HIT",
)


class Typed(str):
    code: str

    def __new__(cls, code: str, message: str) -> Typed:  # noqa: PYI034
        if code not in CODES:
            raise ValueError(f"unknown problem code {code!r}")
        obj = super().__new__(cls, message)
        obj.code = code
        return obj

    def __reduce__(self) -> tuple[type, tuple[str, str]]:  # copy, deepcopy and pickle keep the code
        return (Typed, (self.code, str(self)))


def code_of(problem: str) -> str:
    return getattr(problem, "code", "RULE")
