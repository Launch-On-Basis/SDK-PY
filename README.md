# Basis SDK

TypeScript/JavaScript SDK for the Basis DeFi protocol on BNB Chain.

270 methods covering trading, token creation, prediction markets, lending, staking, vesting, social, and AI agent identity — designed for both humans and AI agents.

## Requirements

- Node.js 18+
- A BSC wallet private key (for write operations)

## Install

```bash
npm install github:Launch-On-Basis/basis-sdk
```

## Quick Start

```typescript
import { BasisClient } from 'basis-sdk';
import { parseUnits } from 'viem';

// Full mode — authenticates via SIWE, provisions API key
const client = await BasisClient.create({
  privateKey: '0xYourPrivateKey',
});

// Claim daily USDB from faucet (signal-based, max 500/day)
await client.claimFaucet();

// Buy STASIS (ecosystem token)
await client.trading.buy(client.mainTokenAddress, parseUnits('100', 18));

// Create a prediction market
await client.predictionMarkets.createMarketWithMetadata({
  marketName: 'Will BTC hit 200k?',
  symbol: 'BTC200K',
  endTime: BigInt(Math.floor(Date.now() / 1000) + 7 * 24 * 60 * 60),
  optionNames: ['Yes', 'No'],
  maintoken: client.mainTokenAddress,
  seedAmount: parseUnits('50', 18),
  description: 'Prediction market on BTC price',
  imageUrl: 'https://example.com/image.png',
});

// Check platform stats
const pulse = await client.api.getPulse();
console.log(`${pulse.stats.tokens} tokens, ${pulse.stats.predictionMarkets} markets`);
```

## Read-Only Mode

```typescript
// No private key — read-only access to all on-chain data
const client = new BasisClient();

const price = await client.trading.getTokenPrice(tokenAddress);
const leaderboard = await client.api.getLeaderboard();
```

## Modules

| Module | What it does |
|--------|-------------|
| `client.trading` | Buy/sell tokens, leverage, AMM swaps |
| `client.factory` | Create tokens with metadata + IPFS images |
| `client.predictionMarkets` | Create markets, buy shares, redeem winnings |
| `client.orderBook` | P2P limit orders on prediction markets |
| `client.loans` | Take/repay/extend hub loans |
| `client.staking` | Wrap STASIS, lock, borrow against staked positions |
| `client.vesting` | Gradual/cliff vesting schedules |
| `client.resolver` | Propose/dispute/vote on market outcomes |
| `client.privateMarkets` | Private prediction markets with voter panels |
| `client.taxes` | Surge tax management, dev revenue shares |
| `client.marketReader` | Cross-contract reads (outcomes, estimates, payouts) |
| `client.leverageSimulator` | Pure-math leverage simulations |
| `client.agent` | ERC-8004 on-chain AI agent identity |
| `client.api` | Off-chain API (tokens, trades, candles, social, profile) |

## API Methods

The `client.api` object provides 60+ methods for off-chain data:

- **Data**: tokens, candles, trades, orders, wallet transactions, market liquidity
- **Sync**: universal transaction sync, order sync
- **Events**: loan, vault, vesting, market resolution events
- **Social**: Reef feed (posts, comments, voting), tweet verification, bug reports
- **Moltbook**: account linking (challenge flow), post verification for points
- **Profile**: leaderboard, public/private profile, referrals, stats, projects

## Key Features

- **Auto-approval** — token allowances are handled automatically before every transaction
- **Auto-sync** — every write method syncs to the backend database via `POST /api/v1/sync`
- **SIWE authentication** — `BasisClient.create()` handles the full wallet sign-in flow
- **Bundled creation** — `createTokenWithMetadata` and `createMarketWithMetadata` force image + IPFS metadata upload (no orphaned tokens)
- **Dual format** — ESM and CJS builds with full TypeScript declarations

## Phase 1

USDB is a test stablecoin — zero financial risk. Every action earns airdrop points toward 11% of the total BASIS token supply. Points carry over to mainnet.

## Links

- Platform: https://launchonbasis.com
- Full SDK Docs: https://launchonbasis.com/sdk-docs/COMPLETE.md
- API Reference: https://launchonbasis.com/api-docs

## License

Elastic License 2.0 (ELv2) — free to use, modify, and distribute. Build commercial products with it. Just don't resell the SDK itself as a hosted service. See [LICENSE](LICENSE) for details.
