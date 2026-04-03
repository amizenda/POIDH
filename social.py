"""
Social — public explanation / social adapter layer.

Architecture:
  post_decision(message: str, platform: str | None) -> PostResult

Adapters:
  - "mock"   (default fallback) — writes to local file
  - "farcaster" — posts via Neynar API (requires NEYNAR_API_KEY + FARCASTER_SIGNER_UUID)

Wired into the scheduler's ACCEPTED phase. If real credentials are missing, falls back
to mock and logs a warning — the bot continues autonomously regardless.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config


# ─── Result type ───────────────────────────────────────────────────────────

@dataclass
class PostResult:
    success: bool
    adapter: str            # which adapter was used
    public_url: str | None  # URL/identifier if posted publicly, else None
    error: str | None       # error message if failed


# ─── Adapter registry ─────────────────────────────────────────────────────

def _load_adapters() -> dict[str, callable]:
    """Lazy-load adapters. Add new adapters here."""
    return {
        "mock":      _post_mock,
        "farcaster": _post_farcaster,
    }


# ─── Mock / file adapter (always works) ────────────────────────────────────

def _post_mock(message: str) -> PostResult:
    """Fallback: write to a timestamped local file. Always succeeds."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = config.LOG_DIR / f"social_post_{ts}.txt"
    out_path.write_text(message)
    return PostResult(
        success=True,
        adapter="mock",
        public_url=None,
        error=None,
    )


# ─── Real FARCASTER adapter ────────────────────────────────────────────────

def _post_farcaster(message: str) -> PostResult:
    """
    Post to Farcaster via Neynar v2 API.
    Requires env vars: NEYNAR_API_KEY, FARCASTER_SIGNER_UUID
    Returns the cast URL on success.
    """
    import requests

    api_key    = os.getenv("NEYNAR_API_KEY", "").strip()
    signer_uuid = os.getenv("FARCASTER_SIGNER_UUID", "").strip()

    if not api_key or not signer_uuid:
        return PostResult(
            success=False,
            adapter="farcaster",
            public_url=None,
            error="NEYNAR_API_KEY or FARCASTER_SIGNER_UUID not set — falling back to mock",
        )

    # Truncate to 320 chars (Farcaster limit), preserving meaning
    if len(message) > 320:
        message = message[:317] + "..."

    url = "https://api.neynar.com/v2/farcaster/cast"
    headers = {
        "Content-Type": "application/json",
        "api_key": api_key,
    }
    payload = {
        "signer_uuid": signer_uuid,
        "text": message,
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()

        # Neynar returns { "cast": { "hash": "...", "url": "..." } }
        cast_data = data.get("cast", {})
        cast_url  = cast_data.get("url") or f"https://warpcast.com/a/{cast_data.get('hash', 'unknown')}"

        return PostResult(
            success=True,
            adapter="farcaster",
            public_url=cast_url,
            error=None,
        )
    except requests.exceptions.HTTPError as e:
        body = e.response.text[:200]
        return PostResult(
            success=False,
            adapter="farcaster",
            public_url=None,
            error=f"HTTP {e.response.status_code}: {body}",
        )
    except Exception as e:
        return PostResult(
            success=False,
            adapter="farcaster",
            public_url=None,
            error=str(e),
        )


# ─── Public interface ──────────────────────────────────────────────────────

SOCIAL_PLATFORM = os.getenv("SOCIAL_PLATFORM", "mock").lower().strip()


def post_decision(message: str) -> PostResult:
    """
    Post the decision announcement to the configured platform.

    Platform selection (via SOCIAL_PLATFORM env var):
      "mock"      → local file only (default)
      "farcaster" → Neynar API + local file

    On failure: logs error and continues — never blocks the bot.
    Returns PostResult with success flag, adapter name, and public URL if available.
    """
    adapters = _load_adapters()
    adapter_fn = adapters.get(SOCIAL_PLATFORM, _post_mock)

    print(f"\n[SOCIAL] Attempting to post via '{SOCIAL_PLATFORM}'...")
    result = adapter_fn(message)

    if result.success:
        print(f"  [SOCIAL] ✅ Posted via {result.adapter}" +
              (f" — {result.public_url}" if result.public_url else ""))
    else:
        print(f"  [SOCIAL] ⚠️  {result.adapter} failed: {result.error}")
        print(f"  [SOCIAL] Falling back to mock (local file only)...")
        result = _post_mock(message)
        print(f"  [SOCIAL] ✅ Mock saved to logs/")

    return result


def build_winner_message(state, winner_id: int, tx_hash: str | None) -> str:
    """Build the public winner announcement message."""
    ev = state.evaluations[winner_id]
    bounty_name   = config.BountyConfig().name
    explorer_url  = f"{config.EXPLORER}/tx/{tx_hash}" if tx_hash else "https://poidh.xyz"
    bounty_url    = f"{config.POIDH_BASE_URL}/bounty/{state.bounty_id + config.V2_OFFSET}" if state.bounty_id else "https://poidh.xyz"

    lines = [
        f"🏆 POIDH BOT — WINNER SELECTED",
        f"",
        f"Bounty: {bounty_name}",
        f"Claim: #{winner_id}",
        f"Score: {ev.score}/10",
        f"  Text match     : {ev.breakdown['text_match']}/5",
        f"  Physical scene : {ev.breakdown['physical_scene']}/3",
        f"  Image quality  : {ev.breakdown['image_quality']}/1",
        f"  Anti-screen    : {ev.breakdown['anti_screen']}/1",
        f"",
        f"Bounty: {bounty_url}",
        f"Tx:    {explorer_url}",
        f"",
        f"Bot: {config.EXPLORER.replace('https://','')}/{state.bounty_id}",
    ]

    body = "\n".join(lines)

    # If too long for a platform, truncate
    if len(body) > 320:
        body = body[:317] + "..."

    return body


def post_winner(state, winner_id: int, tx_hash: str | None) -> PostResult:
    """Convenience wrapper: build winner message and post it."""
    message = build_winner_message(state, winner_id, tx_hash)
    return post_decision(message)


def post_no_winner(state) -> PostResult:
    """Post announcement that no valid winner was found."""
    message = (
        f"😔 POIDH BOT — NO WINNER\n\n"
        f"Bounty: {config.BountyConfig().name}\n"
        f"No submissions reached the minimum score threshold "
        f"({config.ScoringConfig().min_score}/10).\n"
        f"Bounty: {config.POIDH_BASE_URL}/bounty/{state.bounty_id + config.V2_OFFSET}"
    )
    return post_decision(message)