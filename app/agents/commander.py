"""Commander — the Contract-Net manager that musters specialists on demand.

For each incident the Commander runs the full FIPA Contract-Net protocol over
Band primitives:

  discover()      GET peers              -> who exists on the substrate
  shortlist()     score_fit by signature -> who to even invite (dynamic muster)
  create_chat + create_event(task)       -> open a room, ANNOUNCE the CFP
  add_participant + @mention CFP         -> RECRUIT the shortlist
  collect_bids()  read_messages          -> gather competing BIDs
  select_award()  + @mention award       -> AWARD the best bid
  remove_participant() for losers        -> DE-MUSTER (the muster is dynamic)

Decision policy is **deterministic and reproducible**: the shortlist is a
capability score_fit and the award is select_award (highest confidence, lowest
blast). LLM reasoning is isolated behind the `Reasoner` seam — it *narrates* the
triage/award onto the timeline (thought events) but never overrides a binding
decision, so a demo run is repeatable and free of token spend by default. An
LLM-backed Reasoner can be dropped in (same Protocol) without touching the
protocol mechanics.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from common.band_client import Agent, BandClient
from common.contract_net import Bid, Incident, score_fit, select_award
from common import tools as T

DOMAINS = ("workload", "network", "data")


def domain_of(handle: str) -> str | None:
    """Infer a responder's domain from its self-describing handle."""
    h = (handle or "").lower()
    return next((d for d in DOMAINS if d in h), None)


@dataclass
class Candidate:
    """A discovered peer the Commander may recruit. `agent.key` is empty: the
    Commander knows a peer's id+handle from discovery, not its secret."""
    agent: Agent
    domain: str
    capability_tags: list[str]
    fit: float = 0.0


# ---------------------------------------------------------------- reasoner seam
class Reasoner(Protocol):
    name: str

    def triage(self, incident: Incident, ranked: list[Candidate]) -> str: ...
    def rationale(self, incident: Incident, bids: list[Bid],
                  winner: Bid | None) -> str: ...


@dataclass
class NativeReasoner:
    """Deterministic narration of the Contract-Net decisions for the timeline.

    The binding decisions are made by score_fit (shortlist) and select_award
    (award); this only puts them into words so the observatory shows the
    Commander reasoning. Swap in an LLM Reasoner (same Protocol) for richer
    natural-language triage without changing a single decision.
    """
    name: str = "native"

    def triage(self, incident: Incident, ranked: list[Candidate]) -> str:
        match = ", ".join(f"{c.domain}={c.fit:.2f}" for c in ranked) or "none"
        return (f"triage {incident.incident_id} sev={incident.severity} "
                f"tags={incident.tags}; capability fit -> {match}")

    def rationale(self, incident: Incident, bids: list[Bid],
                  winner: Bid | None) -> str:
        if winner is None:
            return (f"{incident.incident_id}: no handleable bid "
                    f"({len(bids)} responses) -> escalate to human")
        losers = [b.responder_handle for b in bids
                  if b.can_handle and b.responder_handle != winner.responder_handle]
        nobids = [b.responder_handle for b in bids if not b.can_handle]
        return (f"award {winner.responder_handle} conf={winner.confidence:.2f} "
                f"blast~{winner.estimated_blast}; outbid={losers or 'none'} "
                f"nobid={nobids or 'none'} -> de-muster all but winner")


# ---------------------------------------------------------------- commander
class Commander:
    def __init__(self, agent: Agent, client: BandClient | None = None,
                 reasoner: Reasoner | None = None, shortlist_k: int = 2):
        self.agent = agent
        self.client = client or BandClient(agent)
        # default reasoner: a real LLM narrator if MUSTER_LLM_* is configured,
        # else the deterministic native one (so the no-creds reproducer is unchanged).
        # The LLM only narrates; score_fit/select_award still make every binding call.
        if reasoner is None:
            from common.reasoner import make_reasoner
            reasoner = make_reasoner(fallback=NativeReasoner())
        self.reasoner = reasoner
        self.shortlist_k = shortlist_k

    # ---- room reuse (the platform caps owned rooms; reuse a stable channel) --
    def get_or_create_chat(self, title: str) -> tuple[str, bool]:
        """Reuse the muster channel with this exact title if it exists, else
        create it. Band's agent API has no delete/archive and caps owned rooms,
        so a stable per-incident channel (also a stable public URL for the
        observatory) is the correct design — not a fresh room per run."""
        for c in self.client.read_chats():
            if (c.get("title") or "") == title and c.get("id"):
                return c["id"], False
        return self.client.create_chat(title), True

    # ---- discovery -------------------------------------------------------
    def discover(self) -> list[Candidate]:
        out: list[Candidate] = []
        for p in self.client.peers():
            handle = (p.get("handle") or p.get("name") or "").lstrip("@")
            pid = p.get("id") or p.get("agent_id")
            if not handle or not pid or "responder" not in handle.lower():
                continue
            dom = domain_of(handle)
            if dom is None:
                continue
            out.append(Candidate(agent=Agent(key="", id=pid, handle=handle),
                                 domain=dom, capability_tags=T.capability_tags(dom)))
        return out

    # ---- signature-driven shortlist (who to invite at all) ---------------
    def shortlist(self, incident: Incident,
                  candidates: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
        for c in candidates:
            c.fit = score_fit(incident.tags, c.capability_tags)
        ranked = sorted(candidates, key=lambda c: -c.fit)
        # invite the top-k by capability so a real contest forms (the strong match
        # bids YES, the next-best likely NO-BID). Escalate if nobody matches.
        invited = ranked[: self.shortlist_k] if ranked and ranked[0].fit > 0 else []
        return ranked, invited

    # ---- bid collection from the Band message stream ---------------------
    def collect_bids(self, chat_id: str, expected_handles: list[str],
                     timeout: float = 30.0, interval: float = 2.0,
                     since_ids: set | None = None) -> list[Bid]:
        # since_ids = message ids that existed BEFORE this CFP. When a room is
        # reused, this scopes bid parsing to *this* contest so old bids from a
        # previous incident in the same channel are never miscounted.
        skip = since_ids or set()
        seen: dict[str, Bid] = {}
        deadline = time.time() + timeout
        want = {h.lstrip("@") for h in expected_handles}
        while True:
            for m in self.client.read_messages(chat_id, limit=80):
                if m.get("id") in skip:
                    continue
                bid = Bid.parse(m.get("content", ""))
                if bid and bid.responder_handle not in seen:
                    seen[bid.responder_handle] = bid
            if want <= set(seen) or time.time() >= deadline:
                break
            time.sleep(interval)
        return list(seen.values())

    # ---- full muster cycle ----------------------------------------------
    def run_incident(self, incident: Incident, *,
                     chat_title: str | None = None,
                     bid_driver: Callable[[str, list[Candidate]], None] | None = None,
                     collect_timeout: float = 30.0,
                     log: Callable[..., None] | None = None) -> dict:
        log = log or (lambda *a, **k: None)

        candidates = self.discover()
        log("discovered", n=len(candidates), handles=[c.agent.handle for c in candidates])

        ranked, invited = self.shortlist(incident, candidates)
        triage = self.reasoner.triage(incident, ranked)
        log("triage", note=triage)
        if not invited:
            return {"incident": incident.incident_id, "status": "no_match",
                    "winner": None}

        # ANNOUNCE: open (or reuse) the muster channel and post the CFP. Reuse
        # keeps a stable public URL and respects the platform's owned-room cap;
        # bids are scoped to messages posted after this announcement.
        title = chat_title or f"MUSTER {incident.incident_id}"
        chat_id, created = self.get_or_create_chat(title)
        since_ids = {m.get("id") for m in self.client.read_messages(chat_id, limit=80)}
        log("muster_room", chat_id=chat_id, title=title, created=created)
        self.client.create_event(chat_id, "task", f"CFP {incident.incident_id}",
                                 metadata=incident.to_metadata())
        self.client.create_event(chat_id, "thought", triage,
                                 metadata={"phase": "triage",
                                           "reasoner": self.reasoner.name})

        # RECRUIT: add only the shortlisted responders and @mention the CFP.
        # add_participant is idempotent here: on a reused room a responder may
        # already be a participant, which must not abort the muster.
        for c in invited:
            try:
                self.client.add_participant(chat_id, c.agent.id)
            except Exception as e:  # noqa: BLE001 — already a member is fine
                msg = str(e)
                # A reused room (Band has no delete/archive + a 10-room cap) means
                # a responder is often already a participant; Band returns 409
                # conflict. For add_participant that is the idempotent desired
                # end-state ("responder is in the room"), not a failure — record
                # it as such so the live-coordination evidence stays clean. Only
                # genuinely unexpected errors surface as a warning.
                if "409" in msg or "conflict" in msg.lower():
                    log("recruit.idempotent", handle=c.agent.handle,
                        note="already a participant of the reused muster room "
                             "(Band 409 conflict on add_participant is idempotent)")
                else:
                    log("recruit.warn", handle=c.agent.handle, error=msg)
            self.client.create_message(
                chat_id, f"@{c.agent.handle} bid please. MUSTER-CFP " +
                json.dumps(incident.to_metadata()), mentions=[c.agent])
        log("recruited", invited=[c.agent.handle for c in invited])

        # responders bid: in production they run independently; in a spike the
        # bid_driver pumps their poll loops so the bids land on real Band.
        if bid_driver is not None:
            bid_driver(chat_id, invited)

        bids = self.collect_bids(chat_id, [c.agent.handle for c in invited],
                                 timeout=collect_timeout, since_ids=since_ids)
        log("bids", lines=[b.to_line(self.agent.handle) for b in bids])

        # AWARD
        winner = select_award(bids)
        rationale = self.reasoner.rationale(incident, bids, winner)
        self.client.create_event(chat_id, "thought", rationale,
                                 metadata={"phase": "award",
                                           "reasoner": self.reasoner.name})
        if winner is None:
            log("escalated", reason=rationale)
            return {"incident": incident.incident_id, "status": "escalated",
                    "chat_id": chat_id, "winner": None,
                    "bids": {b.responder_handle: b.can_handle for b in bids}}

        winner_cand = next(c for c in invited
                           if c.agent.handle == winner.responder_handle)
        award = {"winner": winner.responder_handle,
                 "tool": winner.planned_actions[0], "args": {}}
        self.client.create_event(chat_id, "task", f"awarded {winner.responder_handle}",
                                 metadata=award)
        self.client.create_message(
            chat_id, f"@{winner_cand.agent.handle} you are awarded. MUSTER-AWARD " +
            json.dumps(award), mentions=[winner_cand.agent])
        log("awarded", award=award)

        # DE-MUSTER: remove everyone but the winner (the muster is dynamic)
        removed: list[str] = []
        for c in invited:
            if c.agent.handle == winner.responder_handle:
                continue
            try:
                self.client.remove_participant(chat_id, c.agent.id)
                removed.append(c.agent.handle)
            except Exception as e:  # noqa: BLE001 — keep the cycle going, note it
                log("demuster.warn", handle=c.agent.handle, error=str(e))
        log("demustered", removed=removed)

        return {"incident": incident.incident_id, "status": "awarded",
                "chat_id": chat_id, "winner": winner.responder_handle,
                "tool": award["tool"], "removed": removed,
                "bids": {b.responder_handle: b.can_handle for b in bids}}
