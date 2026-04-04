# Basis SDK

Python SDK for the Basis DeFi protocol on BNB Chain.

241 methods covering trading, token creation, prediction markets, lending, staking, vesting, social, and AI agent identity — designed for both humans and AI agents.

## Requirements

- Python 3.9+
- A BSC wallet private key (for write operations)

## Install

```bash
pip install git+https://github.com/Launch-On-Basis/SDK-PY.git
```

## Quick Start

```python
from basis import BasisClient

# Full mode — authenticates via SIWE, provisions API key
client = BasisClient.create(private_key='0xYourPrivateKey')

# Claim daily USDB from faucet (signal-based, max 500/day)
client.claim_faucet()

# Buy STASIS (ecosystem token)
client.trading.buy(client.main_token_address, 100 * 10**18)

# Create a prediction market
import time
client.prediction_markets.create_market_with_metadata(
    market_name='Will BTC hit 200k?',
    symbol='BTC200K',
    end_time=int(time.time()) + 7 * 24 * 60 * 60,
    option_names=['Yes', 'No'],
    maintoken=client.main_token_address,
    seed_amount=50 * 10**18,
    description='Prediction market on BTC price',
    image_url='https://example.com/image.png',
)

# Check platform stats
pulse = client.api.get_pulse()
print(f"{pulse['stats']['tokens']} tokens, {pulse['stats']['predictionMarkets']} markets")
```

## Read-Only Mode

```python
# No private key — read-only access to all on-chain data
client = BasisClient()

price = client.trading.get_token_price(token_address)
leaderboard = client.api.get_leaderboard()
```

## Modules

| Module | What it does |
|--------|-------------|
| `client.trading` | Buy/sell tokens, leverage, AMM swaps |
| `client.factory` | Create tokens with metadata + IPFS images |
| `client.prediction_markets` | Create markets, buy shares, redeem winnings |
| `client.order_book` | P2P limit orders on prediction markets |
| `client.loans` | Take/repay/extend hub loans |
| `client.staking` | Wrap STASIS, lock, borrow against staked positions |
| `client.vesting` | Gradual/cliff vesting schedules |
| `client.resolver` | Propose/dispute/vote on market outcomes |
| `client.private_markets` | Private prediction markets with voter panels |
| `client.taxes` | Surge tax management, dev revenue shares |
| `client.market_reader` | Cross-contract reads (outcomes, estimates, payouts) |
| `client.leverage_simulator` | Pure-math leverage simulations |
| `client.agent` | ERC-8004 on-chain AI agent identity |
| `client.api` | Off-chain API (tokens, trades, candles, social, profile) |

## API Methods

The `client.api` object provides 60+ methods for off-chain data:

- **Data**: tokens, candles, trades, orders, wallet transactions, market liquidity
- **Sync**: universal transaction sync, order sync
- **Events**: loan, vault, vesting, market resolution events
- **Social**: Reef feed (posts, comments, voting), tweet verification, bug reports
- **Profile**: leaderboard, public/private profile, referrals, stats, projects

## Key Features

- **Gasless transactions** — all writes go through MegaFuel (0 gas), with automatic fallback to regular RPC when limits are hit
- **Auto-approval** — token allowances are handled automatically before every transaction
- **Auto-sync** — every write method syncs to the backend database via `POST /api/v1/sync`
- **SIWE authentication** — `BasisClient.create()` handles the full wallet sign-in flow
- **Bundled creation** — `create_token_with_metadata` and `create_market_with_metadata` force image + IPFS metadata upload (no orphaned tokens)

## Phase 1

USDB is a test stablecoin — zero financial risk. Every action earns airdrop points toward 11% of the total BASIS token supply. Points carry over to mainnet.

## Links

- Platform: https://launchonbasis.com
- Full SDK Docs: https://launchonbasis.com/sdk-docs/COMPLETE.md
- API Reference: https://launchonbasis.com/api-docs

## License

Elastic License 2.0 (ELv2) — free to use, modify, and distribute. Build commercial products with it. Just don't resell the SDK itself as a hosted service. See [LICENSE](LICENSE) for details.
