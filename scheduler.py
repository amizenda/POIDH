"""
Scheduler — polling loop, runs every N seconds.
Handles phase transitions and orchestrates one poll cycle.
"""
from __future__ import annotations

import time
import signal
import sys
from datetime import datetime, timedelta

from state import BotState, Phase, Evaluation
from poidh_client import get_claims, get_current_block_time, resolve_nft_contract
import config
import evaluator as evaler


# ─── Polling ───────────────────────────────────────────────────────────────

def poll_and_evaluate(state: BotState) -> BotState:
    """
    One polling cycle:
    1. Fetch all claims for the bounty
    2. Find new (unseen) claims
    3. Evaluate each new claim
    4. Return updated state
    """
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Polling bounty #{state.bounty_id}...")

    # Check deadline
    now = get_current_block_time()
    if state.deadline is not None and now >= state.deadline:
        print(f"  Deadline passed (block time {now} >= {state.deadline}). Moving to EVALUATE phase.")
        state.set_phase(Phase.DEADLINE_PASSED)
        return state

    # Fetch all claims
    try:
        claims = get_claims(state.bounty_id)
        print(f"  Total claims on-chain: {len(claims)}")
    except Exception as e:
        print(f"  [ERROR] Failed to fetch claims: {e}")
        state.set_error(f"poll: {e}")
        return state

    new_claims = [c for c in claims if c.id not in state.claims_seen]
    if not new_claims:
        print("  No new claims.")
        return state

    print(f"  New claims: {[c.id for c in new_claims]}")

    for claim in new_claims:
        state.record_claim(claim.id)

        evaluation = evaler.evaluate_claim(
            claim_id=claim.id,
            claim_name=claim.name,
            claim_description=claim.description,
            created_at=claim.created_at,
        )
        state.record_evaluation(claim.id, evaluation)

    return state


# ─── Evaluation phase ──────────────────────────────────────────────────────

def evaluate_all(state: BotState) -> BotState:
    """
    Re-evaluate any claims not yet scored (handles restart during EVALUATING).
    Then transition to DECIDED.
    """
    from decision import select_winner, generate_explanation

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Evaluating all claims...")

    # Re-evaluate any un-scored claims
    try:
        claims = get_claims(state.bounty_id)
    except Exception as e:
        print(f"  [ERROR] Failed to fetch claims: {e}")
        state.set_error(f"evaluate: {e}")
        return state

    for claim in claims:
        if claim.id not in state.claims_seen:
            state.record_claim(claim.id)
        if claim.id not in state.evaluations:
            evaluation = evaler.evaluate_claim(
                claim_id=claim.id,
                claim_name=claim.name,
                claim_description=claim.description,
                created_at=claim.created_at,
            )
            state.record_evaluation(claim.id, evaluation)

    # Select winner
    winner_id = select_winner(state)

    if winner_id is not None:
        state.winner_claim_id = winner_id
        state.set_phase(Phase.DECIDED)
        # Generate explanation
        generate_explanation(state, winner_id, config.LOG_DIR)
    else:
        # No valid winner — log and stay
        generate_explanation(state, None, config.LOG_DIR)
        print("  No valid winner found. Bot will continue monitoring.")
        state.set_phase(Phase.POLLING)  # go back to polling in case late submissions

    return state


# ─── Main run loop ─────────────────────────────────────────────────────────

def run(state: BotState) -> None:
    """
    Main run loop. Handles SIGINT / SIGTERM gracefully.
    State machine:
      IDLE → BOUNTY_CREATED → POLLING ↔ DEADLINE_PASSED → EVALUATING → DECIDED → ACCEPTED
    """
    print(f"\n{'='*60}")
    print(f"POIDH BOT — Starting (phase: {state.phase})")
    print(f"{'='*60}")

    running = True

    def signal_handler(sig, frame):
        nonlocal running
        print("\n[SIGNAL] Shutdown requested. Saving state and exiting...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while running:
        phase = state.phase

        # ── POLLING ────────────────────────────────────────────────────────
        if phase == Phase.POLLING:
            state = poll_and_evaluate(state)
            if state.phase == Phase.DEADLINE_PASSED:
                continue  # moved to deadline phase, don't sleep
            if state.error:
                state.clear_error()  # clear stale errors
            time.sleep(config.POLL_INTERVAL)

        # ── DEADLINE_PASSED — move to evaluate ────────────────────────────
        elif phase == Phase.DEADLINE_PASSED:
            state.set_phase(Phase.EVALUATING)

        # ── EVALUATING ─────────────────────────────────────────────────────
        elif phase == Phase.EVALUATING:
            state = evaluate_all(state)
            if state.phase == Phase.POLLING:
                continue  # no winner, back to polling
            time.sleep(config.POLL_INTERVAL)  # wait before next phase

        # ── DECIDED — wait for manual trigger or auto-accept ──────────────
        elif phase == Phase.DECIDED:
            print(f"\n  Winner decided: claim #{state.winner_claim_id}")
            print("  Call accept_claim() to finalize.")
            print("  Run: python main.py --accept")
            break  # loop exits, bot is done (accept handled externally or next run)

        # ── ACCEPTED ──────────────────────────────────────────────────────
        elif phase == Phase.ACCEPTED:
            print("\nBounty fully resolved. Bot shutdown.")
            break

        # ── IDLE ──────────────────────────────────────────────────────────
        elif phase == Phase.IDLE:
            print("State is IDLE. Nothing to do.")
            break

        # Unknown phase — reset to IDLE
        else:
            print(f"Unknown phase: {phase}. Resetting to IDLE.")
            state.set_phase(Phase.IDLE)
            break
