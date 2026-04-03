# POIDH Autonomous Bounty Bot — MVP

Autonomous Python bot that creates a SOLO bounty on Base, polls for photo claims every 5 minutes, scores them using OCR + image analysis, and accepts the winning claim on-chain — zero manual intervention after launch.

## How It Works

```
[1] Create SOLO bounty on Base
[2] Poll getClaimsByBountyId() every 5 minutes
[3] Fetch tokenURI for each new claim
[4] Download + preprocess image
[5] Score via OCR + image analysis
[6] Wait until deadline, then select winner
[7] acceptClaim() on-chain
[8] Save evaluation logs + generate public explanation
```

## Prerequisites

- [Foundry](https://github.com/foundry-rs/foundry) — for `cast` CLI (on-chain reads/writes)
  ```bash
  curl -L https://foundry.paradigm.xyz | bash
  foundryup
  cast --version   # verify
  ```

- Python 3.10+
- Tesseract OCR (OS-level):
  ```bash
  # macOS
  brew install tesseract
  # Ubuntu/Debian
  sudo apt install tesseract-ocr
  # Windows: https://github.com/UB-Mannheim/tesseract/wiki
  ```

## Setup

```bash
# 1. Clone
git clone https://github.com/amizenda/poidh-bot
cd poidh-bot

# 2. Install Python deps
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env — see Configuration below

# 4. Bootstrap: create the bounty
python main.py --create

# 5. Run the bot (polling loop)
python main.py --run
```

## Configuration (.env)

```env
PRIVATE_KEY=0xYourEOAPrivateKeyWithout0xPrefix
RPC_URL=https://mainnet.base.org
BOUNTY_NAME=Show this code in public
BOUNTY_DESCRIPTION=Take a real-world photo of a handwritten note that says "POIDH BOT 31" in a public place. The photo must clearly show the handwritten text and a real physical environment. Screens, edited overlays, AI-generated images, or fully digital images are invalid.
BOUNTY_AMOUNT_ETH=0.001
BOUNTY_DEADLINE_HOURS=168
POLL_INTERVAL_SECONDS=300
MIN_SCORE=7.5
TARGET_PHRASE=POIDH BOT 31
LOG_DIR=./logs
DATA_DIR=./data
```

## Scoring Rubric

| Dimension | Max | Method |
|---|---|---|
| Text match | 5 | pytesseract OCR → fuzzy match target phrase |
| Physical scene | 3 | Sobel edge density + depth heuristics |
| Image quality | 1 | Laplacian variance (blur detection) |
| Anti-screen | 1 | EXIF absence + moiré / subpixel grid detection |
| **Total** | **10** | Valid if **≥ 7.5** |

**Tie-break:** Higher text match → higher physical scene → earlier submission time

## File Structure

```
poidh-bot/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── config.py          # Env vars, constants, bounty params
├── state.py           # state.json read/write (restart-safe)
├── poidh_client.py    # ALL on-chain interaction (isolated)
├── evaluator.py       # Orchestrates scoring pipeline
├── scorer.py          # Individual scoring functions
├── decision.py        # Winner selection + tie-break
├── scheduler.py       # Polling loop
├── social.py           # Public explanation (mock / adapter)
└── main.py             # State machine entry point
```

## State Machine

```
IDLE → BOUNTY_CREATED → POLLING → DEADLINE_PASSED → EVALUATING → DECIDED → ACCEPTED
```

Persisted in `data/state.json`. Restart-safe — resumes from last known phase.

## Persistence

- `data/state.json` — bot state (phase, bounty_id, claims_seen, evaluations, winner)
- `data/claims/` — downloaded claim images
- `logs/` — timestamped evaluation logs + winner explanations

## Chain Support

| Chain | Contract | Min Bounty |
|---|---|---|
| Base | `0x5555Fa783936C260f77385b4E153B9725feF1719` | 0.001 ETH |
| Arbitrum | `0x5555Fa783936C260f77385b4E153B9725feF1719` | 0.001 ETH |
| Degen | `0x18E5585ca7cE31b90Bc8BB7aAf84152857cE243f` | 1000 DEGEN |

## Security Notes

- `PRIVATE_KEY` is read from env only — never hardcoded
- Bot wallet is a hot wallet — only fund with what you can afford to lose
- State machine prevents double-accepting
- `cast` CLI handles all signing — no external signer needed

MIT
