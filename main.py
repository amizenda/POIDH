"""
main.py — POIDH Bot entry point.
Handles: bootstrap (create bounty), resume, accept, and status commands.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from config import (
    BountyConfig,
    ScoringConfig,
    CONTRACT,
    EXPLORER,
    POIDH_BASE_URL,
    V2_OFFSET,
    PRIVATE_KEY,
    RPC_URL,
)
from state import BotState, Phase
from poidh_client import create_solo_bounty, accept_claim, get_current_block_time
import scheduler
import social


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_create(state: BotState) -> BotState:
    """Bootstrap: create the SOLO bounty on-chain."""
    if state.phase not in (Phase.IDLE,):
        print(f"State is {state.phase}. Use --reset to start fresh.")
        return state

    bounty_cfg = BountyConfig()
    print(f"\n{'='*60}")
    print("CREATING SOLO BOUNTY")
    print(f"{'='*60}")
    print(f"  Name       : {bounty_cfg.name}")
    print(f"  Description : {bounty_cfg.description[:80]}...")
    print(f"  Amount     : {bounty_cfg.amount_eth} ETH")
    print(f"  Deadline   : {bounty_cfg.deadline_hours}h from creation")
    print(f"  Contract   : {CONTRACT}")
    print(f"  RPC        : {RPC_URL}")
    print()

    # Confirm
    confirm = input("  Send transaction? [y/N]: ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return state

    try:
        tx_hash, bounty_id = create_solo_bounty(
            name=bounty_cfg.name,
            description=bounty_cfg.description,
            amount_eth=bounty_cfg.amount_wei_str(),
        )
        print(f"\n  ✅ Bounty created!")
        print(f"  Tx hash    : {tx_hash}")
        print(f"  Bounty ID  : {bounty_id}")
        print(f"  View at    : {POIDH_BASE_URL}/bounty/{bounty_id + V2_OFFSET}")
        print(f"  Explorer   : {EXPLORER}/tx/{tx_hash}")

        # Set deadline
        deadline = get_current_block_time() + (bounty_cfg.deadline_hours * 3600)

        state.bounty_id = bounty_id
        state.bounty_tx_hash = tx_hash
        state.deadline = deadline
        state.set_phase(Phase.POLLING)
        print(f"  Deadline   : block {deadline}")
        print(f"\n  Bot now in POLLING phase. Run `python main.py` to start.")
        return state

    except Exception as e:
        print(f"\n  ❌ Failed to create bounty: {e}")
        state.set_error(f"create: {e}")
        return state


def cmd_accept(state: BotState, auto_confirm: bool = False) -> BotState:
    """Execute acceptClaim for the decided winner. Re-validates score at accept time."""
    if state.phase != Phase.DECIDED:
        print(f"Can only accept in DECIDED phase. Current: {state.phase}")
        return state
    if state.winner_claim_id is None:
        print("No winner recorded.")
        return state

    # ── Re-verify score at accept time ──────────────────────────────────
    winner_claim_id = state.winner_claim_id
    evaluation = state.evaluations.get(winner_claim_id)
    if evaluation is None:
        print(f"ERROR: Claim #{winner_claim_id} has no evaluation record. Re-run evaluation first.")
        return state
    if evaluation.score < ScoringConfig().min_score:
        print(
            f"ERROR: Claim #{winner_claim_id} score {evaluation.score} is below "
            f"minimum {ScoringConfig().min_score}. Will not accept."
        )
        return state

    print(f"\n{'='*60}")
    print("ACCEPTING WINNING CLAIM")
    print(f"{'='*60}")
    print(f"  Bounty ID   : {state.bounty_id}")
    print(f"  Winner claim: {winner_claim_id}")
    print(f"  Score       : {evaluation.score}/10")
    print()

    if not auto_confirm:
        confirm = input("  Send transaction? [y/N]: ").strip().lower()
        if confirm != "y":
            print("  Cancelled.")
            return state

    try:
        tx_hash = accept_claim(state.bounty_id, winner_claim_id)
        print(f"\n  ✅ Claim accepted!")
        print(f"  Tx hash    : {tx_hash}")
        print(f"  Explorer   : {EXPLORER}/tx/{tx_hash}")

        state.accept_tx_hash = tx_hash
        state.set_phase(Phase.ACCEPTED)

        # Post social
        from decision import generate_explanation
        exp_path = generate_explanation(state, state.winner_claim_id, config.LOG_DIR)
        social.post_winner(state, state.winner_claim_id, exp_path, tx_hash)

        return state

    except Exception as e:
        print(f"\n  ❌ Failed to accept claim: {e}")
        state.set_error(f"accept: {e}")
        return state


def cmd_status(state: BotState) -> None:
    """Print current bot status."""
    print(f"\n{'='*60}")
    print("POIDH BOT — STATUS")
    print(f"{'='*60}")
    print(f"  Phase          : {state.phase}")
    print(f"  Bounty ID      : {state.bounty_id}")
    print(f"  Deadline       : {state.deadline}")
    print(f"  Claims seen    : {len(state.claims_seen)}")
    print(f"  Evaluations    : {len(state.evaluations)}")
    print(f"  Winner         : {state.winner_claim_id}")
    print(f"  Accept tx      : {state.accept_tx_hash}")
    print(f"  Error          : {state.error or 'none'}")

    if state.bounty_id:
        print(f"  View bounty    : {POIDH_BASE_URL}/bounty/{state.bounty_id + V2_OFFSET}")
    if state.deadline:
        now = get_current_block_time()
        remaining = max(0, state.deadline - now)
        print(f"  Deadline in    : {remaining // 3600}h {(remaining % 3600) // 60}m")

    if state.evaluations:
        print()
        print("  EVALUATIONS:")
        print(f"  {'Claim ID':>10} | {'Score':>6} | {'Text':>5} | {'Scene':>5} | {'Quality':>7} | {'Screen':>6} | Valid")
        print("  " + "-" * 65)
        for cid, ev in sorted(state.evaluations.items()):
            valid = "✅" if ev.score >= ScoringConfig().min_score else "❌"
            bd = ev.breakdown
            print(f"  {cid:>10} | {ev.score:>6.2f} | {bd['text_match']:>5.2f} | {bd['physical_scene']:>5.2f} | {bd['image_quality']:>7.2f} | {bd['anti_screen']:>6.2f} | {valid}")


# ─── Entrypoint ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="POIDH Autonomous Bounty Bot")
    parser.add_argument("--create", action="store_true", help="Create bounty and bootstrap")
    parser.add_argument("--accept", action="store_true", help="Accept the decided winner")
    parser.add_argument("--status", action="store_true", help="Show bot status and exit")
    parser.add_argument("--reset", action="store_true", help="Reset state to IDLE (DANGER: clears bounty state)")
    parser.add_argument("--run", action="store_true", help="Run the polling loop")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation prompts (for unattended runs)")
    args = parser.parse_args()

    state = BotState.load()

    # --reset
    if args.reset:
        if args.yes:
            state = BotState()
            state.save()
            print("State reset to IDLE.")
        else:
            confirm = input("Reset bot state to IDLE? This clears bounty/claim state. [y/N]: ").strip().lower()
            if confirm == "y":
                from config import STATE_FILE
                state = BotState()
                state.save()
                print("State reset to IDLE.")
            else:
                print("Aborted.")
        return

    # --status
    if args.status:
        cmd_status(state)
        return

    # --create
    if args.create:
        state = cmd_create(state)
        return

    # --accept
    if args.accept:
        state = cmd_accept(state, auto_confirm=args.yes)
        return

    # --run (default: run the scheduler)
    if args.run or (not any([args.create, args.accept, args.status])):
        # Check prerequisites
        if state.phase == Phase.IDLE and state.bounty_id is None:
            print("No active bounty. Run: python main.py --create")
            return
        scheduler.run(state)


if __name__ == "__main__":
    main()
