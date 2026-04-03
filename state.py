"""Crash-safe persistence via state.json."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from config import STATE_FILE


# ─── Phases ──────────────────────────────────────────────────────────────────

class Phase:
    IDLE             = "IDLE"
    BOUNTY_CREATED   = "BOUNTY_CREATED"
    POLLING          = "POLLING"
    DEADLINE_PASSED  = "DEADLINE_PASSED"
    EVALUATING       = "EVALUATING"
    DECIDED          = "DECIDED"
    ACCEPTED         = "ACCEPTED"


# ─── State ───────────────────────────────────────────────────────────────────

@dataclass
class Evaluation:
    claim_id: int
    score: float
    breakdown: dict[str, float]
    ocr_text: str
    image_path: str | None
    created_at: int = 0        # on-chain claim submission timestamp (for tie-break)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BotState:
    phase: str = Phase.IDLE
    bounty_id: int | None = None
    bounty_tx_hash: str | None = None
    deadline: int | None = None          # Unix timestamp
    claims_seen: list[int] = field(default_factory=list)
    evaluations: dict[str, Evaluation] = field(default_factory=dict)
    # claim_id (str) -> Evaluation dict
    winner_claim_id: int | None = None
    accept_tx_hash: str | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)

    # ── Serialisation helpers ────────────────────────────────────────────────

    def _eval_to_dict(self, ev: Evaluation) -> dict:
        d = ev.to_dict()
        d["created_at"] = ev.created_at
        return d

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "bounty_id": self.bounty_id,
            "bounty_tx_hash": self.bounty_tx_hash,
            "deadline": self.deadline,
            "claims_seen": self.claims_seen,
            "evaluations": {
                str(k): self._eval_to_dict(v) for k, v in self.evaluations.items()
            },
            "winner_claim_id": self.winner_claim_id,
            "accept_tx_hash": self.accept_tx_hash,
            "error": self.error,
            "updated_at": time.time(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BotState":
        evals = {}
        for k, v in d.get("evaluations", {}).items():
            evals[int(k)] = Evaluation(
                claim_id=v["claim_id"],
                score=v["score"],
                breakdown=v["breakdown"],
                ocr_text=v["ocr_text"],
                image_path=v.get("image_path"),
                created_at=v.get("created_at", 0),
                timestamp=v.get("timestamp", time.time()),
            )
        return cls(
            phase=d.get("phase", Phase.IDLE),
            bounty_id=d.get("bounty_id"),
            bounty_tx_hash=d.get("bounty_tx_hash"),
            deadline=d.get("deadline"),
            claims_seen=d.get("claims_seen", []),
            evaluations=evals,
            winner_claim_id=d.get("winner_claim_id"),
            accept_tx_hash=d.get("accept_tx_hash"),
            error=d.get("error"),
            updated_at=d.get("updated_at", time.time()),
        )

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        STATE_FILE.write_text(json.dumps(self.to_dict(), indent=2))
        self.updated_at = time.time()

    @classmethod
    def load(cls) -> "BotState":
        if not STATE_FILE.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(STATE_FILE.read_text()))
        except Exception:
            return cls()

    # ── Convenience mutations ─────────────────────────────────────────────────

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.save()

    def record_claim(self, claim_id: int) -> None:
        if claim_id not in self.claims_seen:
            self.claims_seen.append(claim_id)
            self.save()

    def record_evaluation(self, claim_id: int, evaluation: Evaluation) -> None:
        self.evaluations[claim_id] = evaluation
        self.save()

    def set_winner(self, claim_id: int, tx_hash: str | None = None) -> None:
        self.winner_claim_id = claim_id
        self.accept_tx_hash = tx_hash
        self.set_phase(Phase.DECIDED)

    def set_error(self, msg: str) -> None:
        self.error = msg
        self.save()

    def clear_error(self) -> None:
        self.error = None
        self.save()

    def is_terminal(self) -> bool:
        return self.phase in (Phase.ACCEPTED, Phase.IDLE)
