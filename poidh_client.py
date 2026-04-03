"""
POIDH on-chain client — isolated module for ALL contract interaction.
Uses `cast` CLI (Foundry) for signing + RPC calls.
No external signer, no web3 — pure subprocess + cast.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

import config


# ─── Helpers ────────────────────────────────────────────────────────────────

def _run(*args: str, timeout: int = 60) -> str:
    """Run a cast command and return stdout."""
    result = subprocess.run(
        ["cast", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cast failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _cast_call(fn_sig: str, *args: str, contract: str = "") -> str:
    """Make a read-only call. Defaults to main POIDH contract."""
    target = contract or config.CONTRACT
    return _run(
        "call", target, fn_sig, *args,
        "--rpc-url", config.RPC_URL,
    )


def _cast_send(fn_sig: str, *args: str, value: str = "") -> str:
    """Send a signed transaction. Returns tx hash."""
    cmd = [
        "cast", "send", config.CONTRACT,
        fn_sig, *args,
        "--private-key", config.PRIVATE_KEY,
        "--rpc-url", config.RPC_URL,
    ]
    if value:
        cmd += ["--value", value]
    return _run(*cmd)


# ─── Types ──────────────────────────────────────────────────────────────────

@dataclass
class Bounty:
    id: int
    issuer: str
    name: str
    description: str
    amount: int       # wei
    claimer: str      # 0x0 = active, issuer = cancelled, other = won
    created_at: int   # unix timestamp
    claim_id: int     # accepted claim id (0 if none)


@dataclass
class Claim:
    id: int
    issuer: str
    bounty_id: int
    bounty_issuer: str
    name: str
    description: str
    created_at: int   # unix timestamp
    accepted: bool


# ─── Contract reads ─────────────────────────────────────────────────────────

def resolve_nft_contract() -> str:
    """Get the NFT contract address from the main contract."""
    out = _run(
        "call", config.CONTRACT, "poidhNft()(address)",
        "--rpc-url", config.RPC_URL,
    )
    return out  # already a clean address


def get_bounty(bounty_id: int) -> Bounty:
    """Fetch bounty details. claimer == 0x0 means active."""
    out = _cast_call(
        "bounties(uint256)(uint256,address,string,string,uint256,address,uint256,uint256)",
        str(bounty_id),
    )
    # cast returns space-separated hex values
    parts = out.split()
    amount_wei = int(parts[4], 16) if parts[4].startswith("0x") else int(parts[4])
    claim_id_int = int(parts[7], 16) if parts[7].startswith("0x") else int(parts[7])
    return Bounty(
        id=bounty_id,
        issuer=parts[1],
        name=parts[2],
        description=parts[3],
        amount=amount_wei,
        claimer=parts[5],
        created_at=int(parts[6]),
        claim_id=claim_id_int,
    )


def get_claims(bounty_id: int, offset: int = 0, limit: int = 10) -> list[Claim]:
    """
    Fetch claims for a bounty (paginated, 10 at a time).
    Returns all claims found (handles pagination automatically).
    """
    all_claims: list[Claim] = []
    current_offset = offset

    while True:
        out = _cast_call(
            "getClaimsByBountyId(uint256,uint256)"
            "(tuple(uint256,address,uint256,address,string,string,uint256,bool)[])",
            str(bounty_id), str(current_offset),
        )
        if not out or out == "()":
            break

        # Parse the tuple output from cast
        # Format: (id, issuer, bountyId, bountyIssuer, name, description, createdAt, accepted)
        claims = _parse_claims_tuple(out)
        if not claims:
            break
        all_claims.extend(claims)
        current_offset += limit

        # If we got fewer than limit, we're done
        if len(claims) < limit:
            break

    return all_claims


def _parse_claims_tuple(raw: str) -> list[Claim]:
    """Parse the raw cast output for claim tuples."""
    claims: list[Claim] = []
    # cast tuple output is space-separated; parse carefully
    # Each claim is 8 fields: (id, issuer, bounty_id, bounty_issuer, name, description, createdAt, accepted)
    # Names and descriptions may contain spaces and be double-quoted in cast output
    # Simple heuristic: look for patterns starting with (
    import re
    # Match each tuple: starts with (, ends with )
    tuples = re.findall(r'\(([^)]+)\)', raw)
    for t in tuples:
        parts = _split_tuple_fields(t)
        if len(parts) < 8:
            continue
        try:
            claim = Claim(
                id=int(parts[0]),
                issuer=parts[1],
                bounty_id=int(parts[2]),
                bounty_issuer=parts[3],
                name=parts[4],
                description=parts[5],
                created_at=int(parts[6]),
                accepted=parts[7].lower() in ("true", "1", "0x1"),
            )
            claims.append(claim)
        except (ValueError, IndexError):
            continue
    return claims


def _split_tuple_fields(t: str) -> list[str]:
    """Split a tuple's fields, respecting double-quoted strings."""
    fields: list[str] = []
    current = ""
    in_string = False
    for ch in t:
        if ch == '"':
            in_string = not in_string
            current += ch
        elif ch == "," and not in_string:
            fields.append(current.strip().strip('"'))
            current = ""
        else:
            current += ch
    if current.strip():
        fields.append(current.strip().strip('"'))
    return fields


def fetch_token_uri(nft_contract: str, claim_id: int) -> str:
    """Get the tokenURI for a claim NFT."""
    return _cast_call("tokenURI(uint256)(string)", str(claim_id), contract=nft_contract)


def resolve_uri(uri: str) -> str:
    """
    Convert ipfs:// / ar:// URIs to fetchable HTTP URLs.
    Returns the resolved URL (or original if already HTTP).
    """
    if uri.startswith("ipfs://"):
        # Try each gateway
        ipfs_hash = uri.replace("ipfs://", "")
        for gateway in config.ScoringConfig().ipfs_gateways:
            url = f"{gateway}{ipfs_hash}"
            try:
                r = requests.head(url, timeout=5, allow_redirects=True)
                if r.status_code == 200:
                    return url
            except Exception:
                continue
        # Last resort: return first gateway
        return f"{config.ScoringConfig().ipfs_gateways[0]}{ipfs_hash}"
    elif uri.startswith("ar://"):
        ar_hash = uri.replace("ar://", "")
        return f"https://arweave.net/{ar_hash}"
    return uri  # already HTTP


def fetch_metadata(uri: str) -> dict:
    """
    Fetch JSON metadata from URI.
    Returns dict with 'image' / 'animation_url' / etc.
    Falls back to treating URI as direct content URL.
    """
    url = resolve_uri(uri)
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if "json" in content_type:
            return r.json()
        # Try parsing as JSON anyway
        return r.json()
    except Exception:
        return {"image": url, "direct": True}


def fetch_image(url: str, dest_path: Path) -> bool:
    """Download image to dest_path. Returns True on success."""
    try:
        r = requests.get(url, timeout=30, stream=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; poidh-bot/1.0)"
        })
        r.raise_for_status()
        dest_path.write_bytes(r.content)
        return True
    except Exception:
        return False


def get_current_block_time() -> int:
    """Get current block timestamp."""
    out = _run("latest-block-timestamp", "--rpc-url", config.RPC_URL)
    return int(out.strip())


# ─── Contract writes ────────────────────────────────────────────────────────

def create_solo_bounty(name: str, description: str, amount_eth: str) -> str:
    """
    Create a SOLO bounty. Returns tx hash.
    Parses the BountyCreated event from the receipt to extract bounty ID.
    """
    tx_hash = _cast_send(
        "createSoloBounty(string,string)",
        name, description,
        value=amount_eth,
    )
    # Parse bounty ID from receipt
    bounty_id = parse_bounty_created_event(tx_hash)
    return tx_hash, bounty_id


def accept_claim(bounty_id: int, claim_id: int) -> str:
    """Accept a winning claim. Returns tx hash."""
    return _cast_send(
        "acceptClaim(uint256,uint256)",
        str(bounty_id), str(claim_id),
    )


def parse_bounty_created_event(tx_hash: str) -> int:
    """
    Parse BountyCreated event from tx receipt to get bounty ID.
    event BountyCreated(uint256 indexed bountyId, address indexed issuer, uint256 amount);
    """
    receipt = _run(
        "receipt", tx_hash, "--rpc-url", config.RPC_URL, "--json",
    )
    data = json.loads(receipt)
    for log in data.get("logs", []):
        if log["address"].lower() != config.CONTRACT.lower():
            continue
        topics = log.get("topics", [])
        if len(topics) >= 2:
            # topics[1] = indexed bountyId
            bounty_id = int(topics[1], 16)
            return bounty_id
    raise RuntimeError(f"Could not parse bounty ID from tx {tx_hash}")


def parse_claim_created_event(tx_hash: str) -> int | None:
    """Parse ClaimCreated event to get claim ID."""
    receipt = _run(
        "receipt", tx_hash, "--rpc-url", config.RPC_URL, "--json",
    )
    data = json.loads(receipt)
    for log in data.get("logs", []):
        if log["address"].lower() != config.CONTRACT.lower():
            continue
        topics = log.get("topics", [])
        if len(topics) >= 2:
            claim_id = int(topics[1], 16)
            return claim_id
    return None
