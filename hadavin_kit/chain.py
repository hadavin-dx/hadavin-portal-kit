"""Hash-chain writer and verifier for the `audit` block of `hadavin.demo-export/1`.

Two formats, both frozen once published.
- `hash-chain/1` (schema_minor 0): SHA-256 over the UTF-8 bytes of the compact JSON array
  `[prev_sha256, seq, event_id, ts, type, actor, case_id, summary]` (separators `,` and `:`, ASCII-escaped, the same
  text `JSON.stringify` yields once every non-ASCII character is written as a lower-case 4-hex unicode escape).
- `hash-chain/2` (schema_minor 1): the same array with `tenant_id` appended as the ninth element, so the tenant of
  every audit row is covered by the hash (a row cannot be moved to another tenant unnoticed).
The first `prev_sha256` is 64 zeros. Tamper-evident, not tamper-proof: whoever rewrites the whole chain and the
head can forge it; the head hash is what a reader pins elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CHAIN = "hash-chain/1"
CHAIN_2 = "hash-chain/2"
CHAINS = (CHAIN, CHAIN_2)
GENESIS = "0" * 64
EVENT_TYPES_V0 = ("intent", "gate", "approval", "outcome", "adapter_call", "deny")
EVENT_TYPES_V1_ADDED = ("hold", "veto", "attest", "clear", "handback")
EVENT_TYPES = EVENT_TYPES_V0 + EVENT_TYPES_V1_ADDED


def event_hash(ev: dict[str, Any], chain: str = CHAIN) -> str:
    if chain not in CHAINS:
        raise ValueError(f"unknown chain format {chain!r}")
    if type(ev["seq"]) is not int:  # 1.0 would hash as "1.0" here and "1" in the browser
        raise TypeError("seq must be an integer")
    parts = [
        ev["prev_sha256"],
        ev["seq"],
        ev["event_id"],
        ev["ts"],
        ev["type"],
        ev["actor"],
        ev["case_id"],
        ev["summary"],
    ]
    if chain == CHAIN_2:
        if type(ev["tenant_id"]) is not str:
            raise TypeError("tenant_id must be a string")
        parts.append(ev["tenant_id"])
    material = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ChainWriter:
    """`tenant_id=None` writes hash-chain/1; a tenant id writes hash-chain/2 (every event carries it, hashed)."""

    def __init__(self, tenant_id: str | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.tenant_id = tenant_id
        self.chain = CHAIN_2 if tenant_id is not None else CHAIN

    @property
    def head(self) -> str:
        return self.events[-1]["sha256"] if self.events else GENESIS

    def append(
        self,
        *,
        ts: str,
        type: str,
        actor: str,
        case_id: str,
        summary: str,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}")
        if type in EVENT_TYPES_V1_ADDED and self.chain == CHAIN:
            raise ValueError(f"event type {type!r} needs hash-chain/2 (schema_minor 1)")
        seq = len(self.events) + 1
        ev = {
            "seq": seq,
            "event_id": event_id or f"evt-{seq:04d}",
            "ts": ts,
            "type": type,
            "actor": actor,
            "case_id": case_id,
            "summary": summary,
            "prev_sha256": self.head,
        }
        if self.tenant_id is not None:
            ev["tenant_id"] = self.tenant_id
        ev["sha256"] = event_hash(ev, self.chain)
        self.events.append(ev)
        return ev

    def audit_block(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "events": list(self.events),
            "verify": verify(self.events, self.chain),
        }


def verify(events: list[dict[str, Any]], chain: str = CHAIN) -> dict[str, Any]:
    """Recompute the whole chain. `matched` counts events whose link and hash are both right, in order."""
    matched, prev = 0, GENESIS
    for i, ev in enumerate(events, start=1):
        try:
            good = (
                ev["seq"] == i
                and ev["prev_sha256"] == prev
                and event_hash(ev, chain) == ev["sha256"]
            )
        except (TypeError, KeyError, ValueError):
            good = False
        if good:
            matched += 1
        prev = ev.get("sha256", prev) if isinstance(ev, dict) else prev
    n = len(events)
    return {
        "events": n,
        "matched": matched,
        "ok": matched == n,
        "text": f"local integrity check: {matched} of {n} events match",
    }


def reseal(doc: dict[str, Any]) -> None:
    """Recompute links, hashes and the verify block of `doc['audit']` in place (tests and exporters)."""
    audit = doc["audit"]
    prev = GENESIS
    for i, e in enumerate(audit["events"], start=1):
        e["seq"], e["prev_sha256"] = i, prev
        e["sha256"] = prev = event_hash(e, audit["chain"])
    audit["verify"] = verify(audit["events"], audit["chain"])
