"""
Evaluator — orchestrates the full scoring pipeline for a claim.
Downloads image, runs all scorers, returns Evaluation.
"""
from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

import config
import poidh_client
from state import Evaluation
from poidh_client import resolve_nft_contract, fetch_token_uri, resolve_uri, fetch_metadata, fetch_image
import scorer


# ─── IPFS gateways ──────────────────────────────────────────────────────────

def resolve_content_url(uri: str) -> tuple[str, bool]:
    """
    Resolve a token URI to a fetchable HTTP URL.
    Returns (url, is_json_metadata).
    """
    if uri.startswith("ipfs://"):
        ipfs_hash = uri.replace("ipfs://", "")
        for gateway in config.ScoringConfig().ipfs_gateways:
            url = f"{gateway}{ipfs_hash}"
            return url, True  # may be JSON metadata
    elif uri.startswith("ar://"):
        ar_hash = uri.replace("ar://", "")
        return f"https://arweave.net/{ar_hash}", False

    # Direct HTTP
    return uri, False


def evaluate_claim(
    claim_id: int,
    claim_name: str,
    claim_description: str,
    created_at: int,
) -> Evaluation:
    """
    Full evaluation pipeline for a single claim:
    1. Get NFT contract + tokenURI
    2. Resolve content URL (IPFS/arweave/HTTP)
    3. Fetch metadata → get image URL
    4. Download image
    5. Score across all dimensions
    6. Return Evaluation
    """
    print(f"  Evaluating claim #{claim_id}: {claim_name}")

    # ── Step 1: Get tokenURI ───────────────────────────────────────────────
    nft_contract = resolve_nft_contract()
    raw_uri = fetch_token_uri(nft_contract, claim_id)
    print(f"    TokenURI: {raw_uri}")

    # ── Step 2: Resolve URL ────────────────────────────────────────────────
    content_url, _ = resolve_content_url(raw_uri)
    metadata = fetch_metadata(raw_uri)

    # Try to get image URL from metadata
    image_url = metadata.get("image") or metadata.get("animation_url") or content_url
    if image_url.startswith("ipfs://"):
        ipfs_hash = image_url.replace("ipfs://", "")
        for gateway in config.ScoringConfig().ipfs_gateways:
            image_url = f"{gateway}{ipfs_hash}"
            break

    print(f"    Image URL: {image_url}")

    # ── Step 3: Download image ────────────────────────────────────────────
    claim_dir = config.CLAIMS_DIR / str(claim_id)
    claim_dir.mkdir(exist_ok=True)
    image_path = claim_dir / "proof.jpg"

    if not fetch_image(image_url, image_path):
        print(f"    [WARN] Failed to download image for claim #{claim_id}")
        # Fall back to direct URL if metadata resolution failed
        direct_url = raw_uri if not raw_uri.startswith("ipfs://") else content_url
        if not fetch_image(direct_url, image_path):
            # Score as 0 if image can't be fetched
            return Evaluation(
                claim_id=claim_id,
                score=0.0,
                breakdown={
                    "text_match": 0.0,
                    "physical_scene": 0.0,
                    "image_quality": 0.0,
                    "anti_screen": 0.0,
                },
                ocr_text="[IMAGE_FETCH_FAILED]",
                image_path=None,
                created_at=created_at,
                timestamp=time.time(),
            )

    print(f"    Image saved: {image_path}")

    # ── Step 4: Verify it's actually an image + check dimensions ────────
    try:
        with Image.open(image_path) as im:
            im.verify()
        dims = poidh_client.validate_image_size(image_path)
        if dims is None:
            raise RuntimeError(
                f"Image dimensions exceed limit "
                f"(>{poidh_client.MAX_IMAGE_PIXELS // 1_000_000} MP)"
            )
        with Image.open(image_path) as im:
            w, h = im.size
            print(f"    Image: {w}x{h} {im.format}")
    except Exception as e:
        print(f"    [WARN] Not a valid image: {e}")
        # Clean up bad file
        image_path.unlink(missing_ok=True)
        return Evaluation(
            claim_id=claim_id,
            score=0.0,
            breakdown={"text_match": 0.0, "physical_scene": 0.0,
                       "image_quality": 0.0, "anti_screen": 0.0},
            ocr_text=f"[INVALID_IMAGE] {e}",
            image_path=None,
            created_at=created_at,
            timestamp=time.time(),
        )

    # ── Step 5: Run all scorers ───────────────────────────────────────────
    target_phrase = config.ScoringConfig().target_phrase

    text_score, ocr_text = scorer.score_text_match(image_path, target_phrase)
    print(f"    Text match: {text_score}/5 | OCR: {ocr_text[:80]}")

    scene_score = scorer.score_physical_scene(image_path)
    print(f"    Physical scene: {scene_score}/3")

    quality_score = scorer.score_image_quality(image_path)
    print(f"    Image quality: {quality_score}/1")

    screen_score = scorer.score_anti_screen(image_path)
    print(f"    Anti-screen: {screen_score}/1")

    total = text_score + scene_score + quality_score + screen_score

    breakdown = {
        "text_match": text_score,
        "physical_scene": scene_score,
        "image_quality": quality_score,
        "anti_screen": screen_score,
    }

    print(f"    TOTAL: {total}/10 {'✅ VALID' if total >= config.ScoringConfig().min_score else '❌ INVALID'}")

    return Evaluation(
        claim_id=claim_id,
        score=round(total, 2),
        breakdown=breakdown,
        ocr_text=ocr_text[:500],  # truncate long OCR output
        image_path=str(image_path),
        created_at=created_at,
        timestamp=time.time(),
    )