"""ScriptedLLM: a model stand-in that replays `recorded/<case_id>/turn-N.json`, keyed by (case_id, turn).

Never calls a network. A missing script raises (no silent fallback), every reply says `model: scripted`.
A turn file may carry `prompt_sha256`; when it does, the prompt the agent actually built must hash to it
(drift check), so a changed prompt cannot hide behind an old recording.
Turns are numbered from 1. Turn file: {"text": str, "tool_calls": [..], "confidence": {..}, "input_tokens": int, "output_tokens": int,
            "prompt_sha256": hex64 (optional)}
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hadavin_kit import strict_json

MODEL = "scripted"
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")  # used with fullmatch: no trailing newline


class MissingScript(RuntimeError):
    """No recorded reply for this (case_id, turn). Not a KeyError, so a caller cannot swallow it as a missing dict key."""


class PromptDrift(AssertionError):
    """The prompt built now does not hash to the one the recording was made for."""


@dataclass(frozen=True)
class ScriptedReply:
    case_id: str
    turn: int
    text: str
    tool_calls: list[dict[str, Any]]
    confidence: dict[str, Any]
    input_tokens: int
    output_tokens: int
    prompt_sha256: str
    model: str = MODEL
    cost_usd: str = "0"
    raw: dict[str, Any] = field(default_factory=dict)


def prompt_sha256(system: str, messages: list[dict[str, Any]]) -> str:
    """Canonical hash of what the model would be sent. Use it to fill `prompt_sha256` in a turn file."""
    body = json.dumps(
        {"system": system, "messages": messages},
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def recorded_turns_sha256(recorded_dir: Path | str) -> str:
    """Hash of the whole recorded-turns set (sorted relative paths and file bytes): the export's `run.recorded_turns_sha256`."""
    root = Path(recorded_dir)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.json")):
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


class ScriptedLLM:
    def __init__(self, recorded_dir: Path | str, *, allow_unhashed: bool = False) -> None:
        self.recorded_dir = Path(recorded_dir)
        self.allow_unhashed = allow_unhashed
        self.calls: list[tuple[str, int]] = []

    def path_for(self, case_id: str, turn: int) -> Path:
        if not isinstance(case_id, str) or not _SLUG.fullmatch(case_id):
            raise ValueError(f"bad case_id: {case_id!r}")
        if type(turn) is not int or turn < 1:
            raise ValueError(f"turn must be an integer >= 1, got {turn!r}")
        return self.recorded_dir / case_id / f"turn-{turn}.json"

    def reply(
        self, case_id: str, turn: int, *, system: str, messages: list[dict[str, Any]]
    ) -> ScriptedReply:
        path = self.path_for(case_id, turn)
        if not path.is_file():
            raise MissingScript(f"{case_id}/turn-{turn}.json")
        data = strict_json.load_object(path)  # rejects NaN, duplicate keys and non-object files
        actual = prompt_sha256(system, messages)
        want = data.get("prompt_sha256")
        if want is None and not self.allow_unhashed:
            raise PromptDrift(
                f"{case_id}/turn-{turn}: no prompt_sha256 recorded (pass allow_unhashed=True to accept)"
            )
        if want is not None and want != actual:
            raise PromptDrift(f"{case_id}/turn-{turn}: recorded {want[:12]}, built {actual[:12]}")
        self.calls.append((case_id, turn))
        return ScriptedReply(
            case_id=case_id,
            turn=turn,
            text=data.get("text", ""),
            tool_calls=list(data.get("tool_calls", [])),
            confidence=dict(data.get("confidence", {})),
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            prompt_sha256=actual,
            raw=data,
        )
