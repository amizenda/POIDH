"""
Scheduler — autonomous polling loop.
Orchestrates the full bounty lifecycle:
  IDLE → (auto-create) → POLLING → DEADLINE_PASSED → EVALUATING → DECIDED → ACCEPTING → ACCEPTED

No manual steps required in the happy path.
"""
from __future__ import annotations

import time
import signal
import sys
from datetime import datetime

from config import BountyConfig, ScoringConfig, LOG_DIR, CONTRACT, EXPLORER, POIDH_BASE_URL, V2_OFFSET, POLL_INTERVAL
from poidh_client import (
    create_solo_bounty, accept_claim, get_claims, get_current_block_time,
    resolve_nft_contract,
)
from state import BotState, Phase
import evaluator as evaler
import social


# ─── Phase handlers ─────────────────────────────────────────────────────────

def _auto_create_bounty(state: BotState) -> BotState:
    """
    Called when phase is IDLE and no bounty exists yet.
    Automatically creates a SOLO bounty on-chain, then moves to POLLING.
    """
    bounty_cfg = BountyConfig()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] No active bounty — auto-creating SOLO bounty...")

    try:
        tx_hash, bounty_id = create_solo_bounty(
            name=bounty_cfg.name,
            description=bounty_cfg.description,
            amount_eth=bounty_cfg.amount_wei_str(),
        )
        deadline = get_current_block_time() + (bounty_cfg.deadline_hours * 3600)

        state.bounty_id = bounty_id
        state.bounty_tx_hash = tx_hash
        state.deadline = deadline
        state.set_phase(Phase.POLLING)
        print(f"  ✅ Bounty #{bounty_id} created | {tx_hash}")
        print(f"  Deadline: block {deadline} ({bounty_cfg.deadline_hours}h)")
        print(f"  View: {POIDH_BASE_URL}/bounty/{bounty_id + V2_OFFSET}")
        return state

    except Exception as e:
        print(f"  ❌ Failed to create bounty: {e}")
        state.set_error(f"auto_create: {e}")
        return state


def _poll(state: BotState) -> BotState:
    """
    One polling cycle:
    1. Check deadline
    2. Fetch all claims, skip already-seen
    3. Evaluate new claims
    4. Return updated state
    """
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Polling bounty #{state.bounty_id}...")

    now = get_current_block_time()
    if state.deadline is not None and now >= state.deadline:
        print(f"  Deadline reached (block {now} >= {state.deadline}). Moving to EVALUATE.")
        state.set_phase(Phase.DEADLINE_PASSED)
        return state

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
        state.clear_error()
        return state

    print(f"  New claims: {[c.id for c in new_claims]}")
    state.clear_error()

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


def _evaluate(state: BotState) -> BotState:
    """
    Re-evaluate any un-scored claims (handles restarts).
    Then select winner and move to DECIDED.
    """
    from decision import select_winner, generate_explanation

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Evaluating all claims...")

    try:
        claims = get_claims(state.bounty_id)
    except Exception as e:
        print(f"  [ERROR] Failed to fetch claims: {e}")
        state.set_error(f"evaluate: {e}")
        return state

    # Score any claims not yet evaluated
    for claim in claims:
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
        generate_explanation(state, winner_id, LOG_DIR)
        print(f"  Winner selected: claim #{winner_id}. Moving to ACCEPTING.")
    else:
        # No valid winner
        generate_explanation(state, None, LOG_DIR)
        print("  No valid winner. Will continue monitoring in case of late submissions.")
        state.set_phase(Phase.POLLING)

    return state


def _accept(state: BotState) -> BotState:
    """
    Automatically call acceptClaim on the decided winner.
    Re-verifies score before sending. On success, transitions to ACCEPTED and posts publicly.
    On failure, records error — bot can be re-run to retry (restart-safe).
    """
    from decision import generate_explanation

    winner_id = state.winner_claim_id
    evaluation = state.evaluations.get(winner_id)

    # Safety re-check
    if evaluation is None:
        print(f"  ERROR: Claim #{winner_id} has no evaluation record.")
        state.set_error("accept: no evaluation for winner")
        return state

    if evaluation.score < ScoringConfig().min_score:
        print(f"  ERROR: Claim #{winner_id} score {evaluation.score} < min {ScoringConfig().min_score}. Will not accept.")
        state.set_error(f"accept: score {evaluation.score} below minimum")
        return state

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Auto-accepting claim #{winner_id}...")
    print(f"  Bounty #{state.bounty_id} | Claim #{winner_id} | Score {evaluation.score}/10")

    try:
        tx_hash = accept_claim(state.bounty_id, winner_id)
        state.accept_tx_hash = tx_hash
        state.set_phase(Phase.ACCEPTED)
        print(f"  ✅ Claim accepted! Tx: {tx_hash}")
        print(f"  Explorer: {EXPLORER}/tx/{tx_hash}")

        # Generate explanation and post publicly
        exp_path = generate_explanation(state, winner_id, LOG_DIR)
        try:
            post_result = social.post_winner(state, winner_id, tx_hash)
            state.set_post_url(post_result.public_url)
            if post_result.public_url:
                print(f"  🌐 Public post: {post_result.public_url}")
        except RuntimeError as e:
            # Production mode: social posting failure is fatal
            err_msg = str(e)
            print(f"  ❌ Social posting failed (production mode): {err_msg}")
            state.set_error(f"social: {err_msg}")
            # Stay in ACCEPTED but log — the on-chain tx succeeded, social is a best-effort public record
            # Don't revert phase — the bounty is paid. Log and continue.
            state.phase = Phase.ACCEPTED
            state.save()
            print("\n  🎉 Bounty paid on-chain. Bot shutdown.")
            return state

        print("\n  🎉 Bounty fully resolved. Bot shutdown.")
        return state

    except Exception as e:
        err_msg = str(e)
        print(f"  ❌ acceptClaim failed: {err_msg}")
        state.set_error(f"accept: {err_msg}")
        # Stay in DECIDED — re-running will retry
        return state


# ─── Main run loop ─────────────────────────────────────────────────────────

def run(state: BotState | None = None) -> None:
    """
    Main run loop. Handles SIGINT / SIGTERM gracefully.
    Starts a new bounty automatically if none exists.
    """
    if state is None:
        state = BotState.load()

    print(f"\n{'='*60}")
    print(f"POIDH BOT — Starting (phase: {state.phase})")
    print(f"{'='*60}")

    running = True

    def signal_handler(sig, frame):
        nonlocal running
        print("\n[SIGNAL] Shutdown requested. State saved, bot will resume on next run.")
        running = False

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # ── Bootstrap: auto-create bounty if starting fresh ─────────────────
    if state.phase == Phase.IDLE and state.bounty_id is None:
        state = _auto_create_bounty(state)
        if state.phase != Phase.POLLING:
            print("  Bot paused: bounty creation failed. Fix error and re-run.")
            return

    while running:
        phase = state.phase

        if phase == Phase.POLLING:
            state = _poll(state)
            if state.error:
                print(f"  [ERROR] {state.error}")
            time.sleep(POLL_INTERVAL)

        elif phase == Phase.DEADLINE_PASSED:
            state.set_phase(Phase.EVALUATING)

        elif phase == Phase.EVALUATING:
            state = _evaluate(state)

        elif phase == Phase.DECIDED:
            state = _accept(state)
            # _accept either moves to ACCEPTED or stays in DECIDED with an error
            if state.phase == Phase.DECIDED and state.error:
                print(f"  [ERROR] accept failed: {state.error}")
                print("  Re-run the bot to retry.")
                return

        elif phase == Phase.ACCEPTED:
            print("\nBounty fully resolved. Bot shutdown.")
            return

        else:
            print(f"Unknown phase: {phase}. Resetting.")
            state.set_phase(Phase.IDLE)
            return
