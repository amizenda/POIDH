"""Configuration — loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ─── Chain config ────────────────────────────────────────────────────────────

CHAINS: dict[str, dict] = {
    "base": {
        "contract": "0x5555Fa783936C260f77385b4E153B9725feF1719",
        "explorer": "https://basescan.org",
        "url": "https://poidh.xyz/base",
        "offset": 986,
    },
    "arbitrum": {
        "contract": "0x5555Fa783936C260f77385b4E153B9725feF1719",
        "explorer": "https://arbiscan.io",
        "url": "https://poidh.xyz/arbitrum",
        "offset": 180,
    },
    "degen": {
        "contract": "0x18E5585ca7cE31b90Bc8BB7aAf84152857cE243f",
        "explorer": "https://explorer.degen.tips",
        "url": "https://poidh.xyz/degen",
        "offset": 1197,
    },
}

CHAIN = os.getenv("POIDH_CHAIN", "base")
CHAIN_CFG = CHAINS.get(CHAIN, CHAINS["base"])

# ─── Contract helpers ────────────────────────────────────────────────────────

CONTRACT = CHAIN_CFG["contract"]
EXPLORER = CHAIN_CFG["explorer"]
POIDH_BASE_URL = CHAIN_CFG["url"]
V2_OFFSET = CHAIN_CFG["offset"]


# ─── Wallet ─────────────────────────────────────────────────────────────────

PRIVATE_KEY: str | None = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://mainnet.base.org")

if PRIVATE_KEY is None:
    raise RuntimeError("PRIVATE_KEY not set in environment or .env file.")


# ─── Bounty ──────────────────────────────────────────────────────────────────

@dataclass
class BountyConfig:
    name: str = os.getenv("BOUNTY_NAME", "Show this code in public")
    description: str = os.getenv(
        "BOUNTY_DESCRIPTION",
        (
            'Take a real-world photo of a handwritten note that says "POIDH BOT 31" '
            "in a public place. The photo must clearly show the handwritten text and "
            "a real physical environment. Screens, edited overlays, AI-generated "
            "images, or fully digital images are invalid."
        ),
    )
    amount_eth: float = float(os.getenv("BOUNTY_AMOUNT_ETH", "0.001"))
    deadline_hours: int = int(os.getenv("BOUNTY_DEADLINE_HOURS", "168"))

    def amount_wei_str(self) -> str:
        return f"{self.amount_eth}ether"


# ─── Scoring ─────────────────────────────────────────────────────────────────

@dataclass
class ScoringConfig:
    min_score: float = float(os.getenv("MIN_SCORE", "7.5"))
    target_phrase: str = os.getenv("TARGET_PHRASE", "POIDH BOT 31")
    # Text match (0–5), Physical scene (0–3), Quality (0–1), Anti-screen (0–1)
    # weights are implicit in max scores above
    # Weights for tie-breaking: text > scene > time
    ipfs_gateways: list[str] = field(
        default_factory=lambda: [
            "https://ipfs.io/ipfs/",
            "https://cloudflare-ipfs.com/ipfs/",
            "https://w3s.link/ipfs/",
        ]
    )


# ─── Polling ─────────────────────────────────────────────────────────────────

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))


# ─── Social / Public Posting ───────────────────────────────────────────────

# When True: real platform is mandatory, missing credentials → bot stops (production)
# When False: mock fallback allowed if credentials missing (dev mode)
REQUIRE_PUBLIC_POST: bool = os.getenv("REQUIRE_PUBLIC_POST", "false").lower().strip() == "true"

# Platform: "mock" (local file), "farcaster" (Neynar), "x" (Twitter v2)
SOCIAL_PLATFORM: str = os.getenv("SOCIAL_PLATFORM", "mock").lower().strip()

# Required when SOCIAL_PLATFORM=farcaster:
NEYNAR_API_KEY: str | None = os.getenv("NEYNAR_API_KEY")
FARCASTER_SIGNER_UUID: str | None = os.getenv("FARCASTER_SIGNER_UUID")

# Required when SOCIAL_PLATFORM=x:
X_BEARER_TOKEN: str | None = os.getenv("X_BEARER_TOKEN")
X_API_KEY: str | None = os.getenv("X_API_KEY")
X_API_SECRET: str | None = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN: str | None = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET: str | None = os.getenv("X_ACCESS_TOKEN_SECRET")


# ─── Paths ──────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "state.json"
CLAIMS_DIR = DATA_DIR / "claims"
CLAIMS_DIR.mkdir(exist_ok=True)
