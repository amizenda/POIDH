# POIDH Autonomous Bounty Bot

Autonomous Python bot that creates a SOLO bounty on Base, polls for photo claims every 5 minutes, scores them via OCR + image analysis, selects a winner deterministically, calls `acceptClaim` on-chain, and posts the decision publicly — **zero manual intervention after launch**.

## How It Works (End-to-End)

```
[1] Start with config/.env
[2] Auto-create SOLO bounty on Base (if none exists)
[3] Poll getClaimsByBountyId() every 5 minutes
[4] Fetch + resolve tokenURI for each new claim
[5] Download + preprocess image (IPFS / arweave / HTTP)
[6] Score via OCR + image analysis
[7] Wait until deadline
[8] Fetch and evaluate all claims
[9] Select winner (deterministic tie-break)
[10] Auto-call acceptClaim on-chain
[11] Generate + publish public explanation
[12] Persist final state — restart-safe
```

## Prerequisites

- **Python 3.10+**
- **Foundry** (`cast` CLI) — [install](https://book.getfoundry.sh/getting-started/installation)
  ```bash
  curl -L https://foundry.paradigm.xyz | bash && foundryup
  cast --version
  ```
- **Tesseract OCR** (OS-level):
  ```bash
  # macOS
  brew install tesseract
  # Ubuntu/Debian
  sudo apt install tesseract-ocr
  # Windows: https://github.com/UB-Mannheim/tesseract/wiki
  ```

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/amizenda/POIDH
cd POIDH

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env (see Environment Variables below)

# 4. Run — fully autonomous from here
python main.py
```

That's it. The bot auto-creates the bounty on first run if none exists, then runs the full lifecycle autonomously.

## Environment Variables

```env
# REQUIRED
PRIVATE_KEY=0xYourEOAPrivateKeyHex   # EOA wallet (with or without 0x)

# OPTIONAL (with defaults)
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

# SOCIAL POSTING (optional — bot runs with mock fallback if not set)
SOCIAL_PLATFORM=mock          # "mock" (default) or "farcaster"
NEYNAR_API_KEY=              # required for real Faracster posting
FARCASTER_SIGNER_UUID=       # required for real Faracster posting
```

## Autonomy & Recovery

- **First run**: creates bounty automatically → no `--create` step needed
- **Restart**: reads `data/state.json`, resumes from last phase
- **Accept failure**: records error in state, re-running retries from DECIDED
- **Social failure**: falls back to local file, bot continues regardless

## Scoring Rubric

| Dimension | Max | Method |
|---|---|---|
| Text match | 5 | pytesseract OCR → fuzzy match target phrase |
| Physical scene | 3 | Sobel edge density + color entropy |
| Image quality | 1 | Laplacian variance (blur detection) |
| Anti-screen | 1 | EXIF check + FFT frequency analysis |
| **Total** | **10** | Valid if **≥ 7.5** |

**Tie-break order** (exact, audit-clean):
1. Higher total score
2. Higher text_match
3. Higher physical_scene
4. **Earlier on-chain submission time** (`created_at`)

## Social Posting

By default, announcements are saved to `logs/social_post_<timestamp>.txt`.

For **real public posting** to **Farcaster**:
```env
SOCIAL_PLATFORM=farcaster
NEYNAR_API_KEY=your_neynar_api_key
FARCASTER_SIGNER_UUID=your_signer_uuid
```

If credentials are missing or posting fails, the bot falls back to local file and continues — **never blocks the bounty lifecycle**.

## File Structure

```
POIDH/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── config.py          # env vars, chain config, contract addresses
├── state.py           # BotState, Evaluation, state.json persistence
├── poidh_client.py    # ALL on-chain interaction (cast CLI, isolated)
├── evaluator.py        # tokenURI → metadata → image → scoring pipeline
├── scorer.py           # text_match, scene, quality, anti-screen
├── decision.py         # winner selection + explanation generation
├── scheduler.py        # autonomous polling loop + phase transitions
├── social.py           # pluggable social adapter (mock / farcaster)
└── main.py             # entry point + CLI commands
```

## Persistence

| File | Purpose |
|---|---|
| `data/state.json` | Bot phase, bounty ID, seen claims, evaluations, winner |
| `data/claims/<id>/proof.jpg` | Downloaded claim images |
| `logs/explanation_*.json` | Full evaluation logs |
| `logs/social_post_*.txt` | Social announcements |

## Chain Support

| Chain | Contract | Min Bounty |
|---|---|---|
| Base | `0x5555Fa783936C260f77385b4E153B9725feF1719` | 0.001 ETH |
| Arbitrum | `0x5555Fa783936C260f77385b4E153B9725feF1719` | 0.001 ETH |
| Degen | `0x18E5585ca7cE31b90Bc8BB7aAf84152857cE243f` | 1000 DEGEN |

Set with: `POIDH_CHAIN=base` (default) or `arbitrum` or `degen`.

## Security

- File size limit: 50 MB max per download (chunked, aborts on exceed)
- Image dimension limit: 80 MP max (rejects pixel bombs)
- IPFS path traversal blocked (`../` rejected, CID format validated)
- Arweave path traversal blocked
- Content-Type validated before JSON parsing
- Pagination guard: max 10,000 claims per bounty
- Score re-verified at `acceptClaim` time
- PRIVATE_KEY never logged or echoed

## CLI Commands

```bash
python main.py           # Default: auto-run full lifecycle
python main.py --run    # Same as above (explicit)
python main.py --status # Show current state + evaluations
python main.py --reset  # Reset to IDLE (DANGER: clears all state)
python main.py --accept --yes  # Debug: force accept (emergency only)
```

## MVP Limitations

- **Image-only claims**: video, HTML, or non-image URIs are scored 0
- **OCR quality**: pytesseract accuracy varies with handwriting, lighting, angle
- **Anti-screen**: EXIF + FFT analysis — not foolproof against high-quality photos of screens
- **Single bounty**: one active bounty at a time (delete `data/state.json` to restart)

MIT
