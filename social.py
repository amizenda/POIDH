"""
Social — public explanation / social adapter layer.

Architecture:
  post_decision(message: str) -> PostResult

Adapters:
  - "mock"      → local file only (dev mode only)
  - "farcaster" → posts via Neynar API
  - "x"         → posts via X/Twitter API

Mode enforcement:
  - REQUIRE_PUBLIC_POST=false (default): mock fallback allowed (dev mode)
  - REQUIRE_PUBLIC_POST=true:  real posting REQUIRED, missing credentials → bot STOPS
    This is the production mode required for the POIDH bounty submission.

Wired into the scheduler's ACCEPTED phase.
"""
from __future__ import annotations

import os
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
        "x":         _post_x,
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


# ─── Real X / TWITTER adapter ──────────────────────────────────────────────

def _post_x(message: str) -> PostResult:
    """
    Post to X/Twitter via v2 API.
    Requires env vars: X_BEARER_TOKEN, X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET

    Note: Requires a Twitter Developer account with Elevated or above access.
    Free sandbox accounts have read-only access — posting requires paid tiers.
    """
    import requests

    bearer    = os.getenv("X_BEARER_TOKEN", "").strip()
    api_key   = os.getenv("X_API_KEY", "").strip()
    api_secret = os.getenv("X_API_SECRET", "").strip()
    access_token  = os.getenv("X_ACCESS_TOKEN", "").strip()
    access_secret = os.getenv("X_ACCESS_TOKEN_SECRET", "").strip()

    if not all([bearer, api_key, api_secret, access_token, access_secret]):
        return PostResult(
            success=False,
            adapter="x",
            public_url=None,
            error="One or more X credentials missing (X_BEARER_TOKEN, X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET)",
        )

    # Truncate to 280 chars
    if len(message) > 280:
        message = message[:277] + "..."

    # OAuth 1.0a signing via requests-oauthlib
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        return PostResult(
            success=False,
            adapter="x",
            public_url=None,
            error="requests-oauthlib not installed — pip install requests-oauthlib",
        )

    try:
        url = "https://api.twitter.com/2/tweets"
        oauth = OAuth1Session(
            client_key=api_key,
            client_secret=api_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_secret,
        )
        payload = {"text": message}
        r = oauth.post(url, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()

        tweet_id  = data.get("data", {}).get("id", "")
        tweet_url = f"https://x.com/i/status/{tweet_id}" if tweet_id else ""

        return PostResult(
            success=True,
            adapter="x",
            public_url=tweet_url or None,
            error=None,
        )
    except requests.exceptions.HTTPError as e:
        body = e.response.text[:200]
        return PostResult(
            success=False,
            adapter="x",
            public_url=None,
            error=f"HTTP {e.response.status_code}: {body}",
        )
    except Exception as e:
        return PostResult(
            success=False,
            adapter="x",
            public_url=None,
            error=str(e),
        )


# ─── Public interface ──────────────────────────────────────────────────────

SOCIAL_PLATFORM = os.getenv("SOCIAL_PLATFORM", "mock").lower().strip()
REQUIRE_PUBLIC_POST = os.getenv("REQUIRE_PUBLIC_POST", "false").lower().strip() == "true"


def post_decision(message: str) -> PostResult:
    """
    Post the decision announcement to the configured platform.

    Platform selection (via SOCIAL_PLATFORM env var):
      "mock"      → local file only (dev mode)
      "farcaster" → Neynar API
      "x"         → Twitter/X v2 API

    When REQUIRE_PUBLIC_POST=true (production mode):
      - SOCIAL_PLATFORM must be "farcaster" or "x"
      - Missing credentials → raises RuntimeError (bot STOPS)
      - Posting failure → raises RuntimeError (bot STOPS)
      - No mock fallback allowed

    When REQUIRE_PUBLIC_POST=false (dev mode):
      - Falls back to mock on any failure
      - Bot continues regardless

    Returns PostResult with success flag, adapter name, and public URL if available.
    Raises RuntimeError in production mode on any social failure.
    """
    # ── Production enforcement ──────────────────────────────────────────────
    if REQUIRE_PUBLIC_POST and SOCIAL_PLATFORM == "mock":
        raise RuntimeError(
            "REQUIRE_PUBLIC_POST=true but SOCIAL_PLATFORM=mock — "
            "must set SOCIAL_PLATFORM=farcaster or SOCIAL_PLATFORM=x in production"
        )

    adapters = _load_adapters()
    adapter_fn = adapters.get(SOCIAL_PLATFORM, _post_mock)

    print(f"\n[SOCIAL] Attempting to post via '{SOCIAL_PLATFORM}'...")
    result = adapter_fn(message)

    if result.success:
        print(f"  [SOCIAL] ✅ Posted via {result.adapter}" +
              (f" — {result.public_url}" if result.public_url else ""))
    else:
        if REQUIRE_PUBLIC_POST:
            # Real posting is mandatory — do not continue
            raise RuntimeError(
                f"SOCIAL_PLATFORM={SOCIAL_PLATFORM} failed in production mode "
                f"(REQUIRE_PUBLIC_POST=true): {result.error}"
            )
        # Dev mode: graceful fallback
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