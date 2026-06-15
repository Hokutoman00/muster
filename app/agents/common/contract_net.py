"""Contract-Net leaf functions (FIPA terms, Band-native execution).

These are the small, testable units the Commander and responders share:
  - score_fit: Jaccard between an incident's symptom tags and a peer's capability tags
  - Bid: what a responder returns when it can/can't handle an incident
  - select_award: pick the winning bid (confidence, then lowest estimated blast)

Kept dependency-free so both the orchestrator and every responder import it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


def score_fit(incident_tags: Iterable[str], capability_tags: Iterable[str]) -> float:
    """Jaccard similarity len(a & b) / len(a | b). 0.0 when either side empty."""
    a = {t.lower() for t in incident_tags}
    b = {t.lower() for t in capability_tags}
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union)


@dataclass
class Incident:
    incident_id: str
    symptom: list[str]        # e.g. ["CrashLoopBackOff", "rollout"]
    scope: list[str]          # e.g. ["workload"]
    severity: str = "high"
    capability_required: list[str] = field(default_factory=list)

    @property
    def tags(self) -> list[str]:
        return [*self.symptom, *self.scope, *self.capability_required]

    def to_metadata(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "symptom": self.symptom,
            "scope": self.scope,
            "severity": self.severity,
            "tags": self.tags,
            "capability_required": self.capability_required,
        }


@dataclass
class Bid:
    responder_handle: str
    can_handle: bool
    fit: float
    planned_actions: list[str]
    confidence: float
    estimated_blast: int = 0
    note: str = ""

    def to_line(self, commander_handle: str) -> str:
        """Human/agent-readable bid posted as a chat message.

        Carries `from=@<responder>` so the Commander can reconstruct the bid from
        the Band message stream without depending on Band's author-field schema.
        """
        if not self.can_handle:
            return (f"@{commander_handle} NO-BID {self.note or 'out of scope'} "
                    f"(fit={self.fit:.2f}) from=@{self.responder_handle}")
        actions = "; ".join(self.planned_actions)
        return (f"@{commander_handle} BID fit={self.fit:.2f} conf={self.confidence:.2f} "
                f"blast~{self.estimated_blast} actions=[{actions}] "
                f"from=@{self.responder_handle}")

    _BID_RE = re.compile(
        r"BID fit=(?P<fit>[\d.]+) conf=(?P<conf>[\d.]+) blast~(?P<blast>\d+) "
        r"actions=\[(?P<actions>.*?)\] from=@(?P<who>\S+)")
    _NOBID_RE = re.compile(r"NO-BID (?P<note>.*) \(fit=(?P<fit>[\d.]+)\) from=@(?P<who>\S+)")

    @classmethod
    def parse(cls, content: str) -> "Bid | None":
        """Reconstruct a Bid from a posted bid line (BID or NO-BID), or None."""
        if not content:
            return None
        m = cls._BID_RE.search(content)
        if m:
            actions = [a.strip() for a in m["actions"].split(";") if a.strip()]
            return cls(responder_handle=m["who"], can_handle=True,
                       fit=float(m["fit"]), planned_actions=actions,
                       confidence=float(m["conf"]), estimated_blast=int(m["blast"]))
        m = cls._NOBID_RE.search(content)
        if m:
            return cls(responder_handle=m["who"], can_handle=False,
                       fit=float(m["fit"]), planned_actions=[], confidence=0.0,
                       note=m["note"].strip())
        return None


def select_award(bids: list[Bid]) -> Bid | None:
    """Winner = highest confidence among handleable bids, tie-broken by lowest blast."""
    handleable = [b for b in bids if b.can_handle]
    if not handleable:
        return None
    return sorted(handleable, key=lambda b: (-b.confidence, b.estimated_blast, -b.fit))[0]
