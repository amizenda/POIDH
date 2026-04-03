"""
Decision — winner selection with tie-breaking rules.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from datetime import datetime

from state import BotState, Evaluation
import config


def select_winner(state: BotState) -> int | None:
    """
    Select the winning claim based on scoring rubric.

    Rules:
    1. Only consider claims with score >= MIN_SCORE
    2. Tie-break: higher text_match → higher physical_scene → earlier created_at
    3. Returns claim_id of winner, or None if no valid claims.
    """
    min_score = config.ScoringConfig().min_score
    valid: list[tuple[int, Evaluation]] = [
        (cid, ev) for cid, ev in state.evaluations.items()
        if ev.score >= min_score
    ]

    if not valid:
        print("  No claims reached minimum score.")
        return None

    # Sort by: score desc, text_match desc, scene desc, created_at asc
    valid.sort(
        key=lambda x: (
            -x[1].score,
            -x[1].breakdown["text_match"],
            -x[1].breakdown["physical_scene"],
            x[1].timestamp,         # earlier = better (lower timestamp)
        )
    )

    winner_id, winner_ev = valid[0]
    print(f"  Winner: claim #{winner_id} | score={winner_ev.score} | text={winner_ev.breakdown['text_match']}/5 | scene={winner_ev.breakdown['physical_scene']}/3")
    return winner_id


def generate_explanation(
    state: BotState,
    winner_id: int | None,
    output_path: Path,
) -> Path:
    """
    Generate a human-readable public explanation of the decision.
    Saved as JSON + text summary to logs/.
    Returns the path to the explanation file.
    """
    if winner_id is None:
        explanation = {
            "result": "NO_WINNER",
            "reason": "No claims reached the minimum score threshold.",
            "min_score": config.ScoringConfig().min_score,
            "all_scores": {
                str(cid): {
                    "score": ev.score,
                    "breakdown": ev.breakdown,
                    "ocr_text": ev.ocr_text,
                    "timestamp": datetime.fromtimestamp(ev.timestamp).isoformat(),
                }
                for cid, ev in state.evaluations.items()
            },
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
    else:
        winner_ev = state.evaluations[winner_id]
        losers = {
            str(cid): {
                "score": ev.score,
                "breakdown": ev.breakdown,
            }
            for cid, ev in state.evaluations.items()
            if cid != winner_id and ev.score >= config.ScoringConfig().min_score
        }

        explanation = {
            "result": "WINNER_SELECTED",
            "winner_claim_id": winner_id,
            "winner_score": winner_ev.score,
            "winner_breakdown": winner_ev.breakdown,
            "winner_ocr_text": winner_ev.ocr_text,
            "runner_ups": losers,
            "all_evaluations": {
                str(cid): {
                    "score": ev.score,
                    "breakdown": ev.breakdown,
                    "ocr_text": ev.ocr_text,
                    "timestamp": datetime.fromtimestamp(ev.timestamp).isoformat(),
                }
                for cid, ev in state.evaluations.items()
            },
            "bounty_id": state.bounty_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

    # Write JSON log
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    json_path = output_path / f"explanation_{ts}.json"
    json_path.write_text(json.dumps(explanation, indent=2))

    # Write text summary
    summary_lines = [
        "=" * 60,
        "POIDH BOT — WINNER DECISION SUMMARY",
        "=" * 60,
        f"Bounty ID : {state.bounty_id}",
        f"Timestamp : {datetime.utcnow().isoformat()}Z",
        f"Min Score : {config.ScoringConfig().min_score}",
        "",
    ]

    if winner_id is None:
        summary_lines += [
            "RESULT: NO WINNER",
            "No submissions met the minimum score threshold.",
            "",
        ]
    else:
        winner_ev = state.evaluations[winner_id]
        summary_lines += [
            "RESULT: WINNER SELECTED",
            f"Claim ID  : {winner_id}",
            f"Total Score: {winner_ev.score}/10",
            f"  Text Match   : {winner_ev.breakdown['text_match']}/5",
            f"  Physical Scene: {winner_ev.breakdown['physical_scene']}/3",
            f"  Image Quality : {winner_ev.breakdown['image_quality']}/1",
            f"  Anti-Screen   : {winner_ev.breakdown['anti_screen']}/1",
            "",
            "OCR Output:",
            winner_ev.ocr_text[:300] if winner_ev.ocr_text else "(none)",
            "",
        ]

    summary_lines += [
        "Full JSON log: " + str(json_path),
        "=" * 60,
    ]

    txt_path = output_path / f"explanation_{ts}.txt"
    txt_path.write_text("\n".join(summary_lines))

    print(f"  Explanation saved: {json_path}")
    print(f"  Summary saved: {txt_path}")
    return json_path
