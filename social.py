"""
Social — public explanation / social adapter.
MVP: writes explanation to a JSON file.
Extend with Twitter/X, Farcaster, or Discord adapters.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from state import BotState
import config


# ─── Mock output ───────────────────────────────────────────────────────────

def post_winner(
    state: BotState,
    winner_id: int,
    explanation_path: Path,
    tx_hash: str | None = None,
) -> bool:
    """
    Post winner announcement. MVP = save to file + optionally log to console.
    Extend with real adapters (Twitter API, Neynar, Discord webhook, etc.)

    Returns True if posted successfully.
    """
    winner_ev = state.evaluations[winner_id]
    bounty_url = f"{config.POIDH_BASE_URL}/bounty/{_frontend_id(state.bounty_id)}" if state.bounty_id else ""

    lines = [
        f"🏆 POIDH BOT — WINNER ANNOUNCED",
        f"",
        f"Bounty: {config.BountyConfig().name}",
        f"Winner: Claim #{winner_id}",
        f"Score: {winner_ev.score}/10",
        f"  Text Match   : {winner_ev.breakdown['text_match']}/5",
        f"  Physical Scene: {winner_ev.breakdown['physical_scene']}/3",
        f"  Image Quality : {winner_ev.breakdown['image_quality']}/1",
        f"  Anti-Screen   : {winner_ev.breakdown['anti_screen']}/1",
        f"",
        f"Full evaluation: {explanation_path}",
    ]
    if tx_hash:
        explorer_url = f"{config.EXPLORER}/tx/{tx_hash}"
        lines.append(f"On-chain: {explorer_url}")

    body = "\n".join(lines)

    # Write to social output file
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = config.LOG_DIR / f"social_post_{ts}.txt"
    out_path.write_text(body)
    print(f"\n[SOCIAL] Announcement saved: {out_path}")
    print(body)

    return True


def post_no_winner(state: BotState, explanation_path: Path) -> bool:
    """Post announcement that no valid winner was found."""
    lines = [
        f"😔 POIDH BOT — NO WINNER",
        f"",
        f"Bounty: {config.BountyConfig().name}",
        f"No submissions reached the minimum score threshold ({config.ScoringConfig().min_score}/10).",
        f"",
        f"Full evaluation: {explanation_path}",
    ]
    body = "\n".join(lines)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = config.LOG_DIR / f"social_post_{ts}.txt"
    out_path.write_text(body)
    print(f"\n[SOCIAL] Announcement saved: {out_path}")
    print(body)

    return True


# ─── Adapters (extend here) ───────────────────────────────────────────────

def post_to_twitter(text: str, credentials: dict | None = None) -> bool:
    """
    Post to Twitter/X via API.
    Requires: TWITTER_BEARER_TOKEN, TWITTER_CLIENT_ID, TWITTER_CLIENT_SECRET
    or use a library like tweepy.
    """
    raise NotImplementedError("Twitter adapter not yet configured.")


def post_to_farcaster(text: str, signer_uuid: str, neynar_api_key: str) -> bool:
    """
    Post to Farcaster via Neynar API.
    Requires: NEYNAR_API_KEY, FARCASTER_SIGNER_UUID
    (Same mechanism as livinalt/poidh-bot)
    """
    import requests

    url = "https://api.neynar.com/v2/farcaster/cast"
    headers = {
        "Content-Type": "application/json",
        "api_key": neynar_api_key,
    }
    payload = {
        "signer_uuid": signer_uuid,
        "text": text,
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[SOCIAL] Failed to post to Farcaster: {e}")
        return False


# ─── Helpers ────────────────────────────────────────────────────────────────

def _frontend_id(bounty_id: int | None) -> str:
    if bounty_id is None:
        return "?"
    return str(bounty_id + config.V2_OFFSET)
