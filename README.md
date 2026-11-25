# Basis SDK

Python SDK for the Basis DeFi protocol on Robinhood Chain (chain id 4663).

Trading and cross swaps, token creation, leverage, loans, the vault, vesting, prediction markets (public and private hosts, order book, resolver), ERC-8004 agent identity and the REST API. Built on web3.py, designed for both humans and AI agents. The TypeScript SDK ([SDK-TS](https://github.com/Launch-On-Basis/SDK-TS)) has the same methods in camelCase, with the same rules and defaults.

> **Status.** Version 0.2.0b1, on Robinhood Chain (chain 4663). The paper site is live:
> `robinhoodPaper` carries its whole deployment, on play tokens, so try things there. The real site's contracts are zero
> in `robinhood` until its launch, so `robinhoodPaper` is the default.

## Requirements

- Python 3.9+
- A wallet private key (for writes)

## Install

```bash
pip install git+https://github.com/Launch-On-Basis/SDK-PY.git
```

## Quick start

**Who can sign in.** The API signs in only a wallet linked to a BASIS account (on the website: X, Google or email,
then Account > Wallets) or a registered BASIS agent. A bot with a wallet of its own gets in with `agent`: `create()`
registers it and syncs it with a signed message (which accepts the BASIS Terms), then signs in. Any other wallet gets
`BasisApiError` 403 `wallet_not_linked`.

```python
from basis import BasisClient

# Read-only: no key, no network calls in the constructor.
reader = BasisClient(chain="robinhoodPaper", rpc_url=rpc_url)   # "robinhoodPaper" (default) | "robinhood" | custom ChainConfig

# Full mode: checks the RPC chain id, SIWE login, provisions an API key (on client.api_key; only a masked hint is logged).
client = BasisClient.create(
    chain="robinhoodPaper",
    private_key="0xYourPrivateKey",              # a wallet linked to your BASIS account
    # rpc_url (pass your own in production: the public RPC rate-limits after a few dozen calls), api_key,
    # api_domain (follows the preset; paper never reaches the real API), addresses (overrides win), slippage_bps, sync,
    # contracts_check="off" | "warn" | "strict", agent=True | {agent config},
    # on_api_key_created=store, api_timeout_ms (30 s), api_upload_timeout_ms (120 s), api_ai_timeout_ms (330 s)
)

# A bot's own new wallet: registers it as an ERC-8004 agent, syncs it, then signs in.
# A failed first sync raises AgentOnboardingError: run create() again, never agent.register().
agent_client = BasisClient.create(chain="robinhoodPaper", private_key=bot_key, agent={"name": "My bot"})

# Contract-only: a signer without the API (no SIWE, no API key; key-only REST calls raise AuthRequiredError).
bot = BasisClient.create(chain="robinhoodPaper", private_key="0x…", auth=False)
bot.paper.claim()                            # play tUSDG on the paper site, once per wallet (also tNVDA, tETH)

# Mains: everything is per main. Amounts are raw units of the pair asset (its own decimals).
main = client.mains.default()                # MainInfo(main, symbol, pair_asset, pair_symbol, pair_decimals, pair_unit, vault, …)
client.trading.buy(main.main, 100 * main.pair_unit)    # min_out derived from a tax-inclusive quote (1%)
price = client.trading.get_token_price(main.main)      # Amount(raw, formatted, unit, asset, symbol, decimals)

# REST: raw JSON with camelCase keys.
tokens = client.api.get_tokens(limit=20)["data"]
```

Address-book keys use the TS spelling (camelCase). A chain without a preset is a `ChainConfig` with its own key, chain id, name, RPC, API domain and addresses.

## Modules

| Module | What it does |
|--------|-------------|
| `client.mains` | Registry of mains: pair asset, decimals, `PAIR_UNIT`, vault, trading state; `main_of(token)` |
| `client.factory` | Create tokens (`create_token`, normal or reversed), presale state, prices in pair-asset units |
| `client.trading` | Buy/sell, tax-inclusive quotes, `quote_swap` / `build_cross_path` / `swap_cross`, `mixed_buy`, buy-and-stake, one leverage position per token (open, add, extend, partial / full repay, `repay_leverage_with`) |
| `client.main_lens` | Read-only quotes that take the main first: main ↔ pair conversions, leverage count, partial-sell amounts |
| `client.hop_executor` | Cross swaps: routes, limits and hop quotes (net of fees) |
| `client.taxes` | Base rate, creator/rewards fees with cooldowns, earnings per pair asset, referrals |
| `client.loans` | Loans made by the BASIS protocol (one per main and collateral): take, partial repay, `repay_loan_with`, extend, increase, partial sells, liquidation claims |
| `client.liquidation` | Liquidation views, leftover claims and the 365-day claim deadline |
| `client.referrals` | Set and read a referrer, referral counts |
| `client.paper` | Claim the paper site's test tokens (tUSDG, tNVDA, tETH); refuses on the real site |
| `client.vault` | Stake, unstake, claim, compound, borrow against a position, full repay |
| `client.vesting` | Vesting schedules, `get_vestings_of`, loans on vested tokens, renounce, repay-extend-reborrow |
| `client.leverage_simulator` | Leverage, loan and floor simulations (pure reads). A floor is measured in the pair asset, and the pair asset itself can move |
| `client.market_reader` | Bet quotes, live market state, redeemables, sliced redemption plans |
| `client.prediction_markets` | Public host: gated create, `check_buy`, buy with any accepted input token (with a deadline), redeem |
| `client.private_markets` | Private host: voter panels, ranked votes, finalize window |
| `client.order_book` | Limit orders on market shares (both hosts, with `max_cost` and an AMM sweep) |
| `client.resolver` | Ranked propose/dispute/veto/vote with bonds, status helper, ranking keys |
| `client.updown` | Up/Down rounds: not enabled yet |
| `client.agent` | ERC-8004 agent identity on the chain's registry: register, the signed first sync (`sync_signed`), update. `create(agent=...)` onboards a new agent wallet |
| `client.api` | REST API, one wrapper per route (see below) |

`client.get_claimables()` reads everything a wallet can claim (holder rewards, market redemptions, vesting, vault rewards) from the chain in one call. Every public method has a docstring with its rules and errors.

## REST layer (`client.api`)

Wrappers for the API's OpenAPI spec, in ten domains: auth, content, tokens, markets, credit, wallet, updown, discovery, social, sync (`basis/api/*.py`). Answers are the raw JSON (camelCase keys, as in TS; amounts as `RawAmount` decimal strings beside their asset and decimals; USD only as nullable `UsdE18` strings with `usdSource`). A non-2xx answer raises `BasisApiError(status, code, message, body, retry_after)`; a call that needs a key or session raises `AuthRequiredError` before any request. Every call has a deadline that covers the body (`api_timeout_ms`, default 30 s; uploads 120 s; AI routes 330 s; per call `client.api.with_options(timeout_ms=...)`): past it `BasisApiError` with status 0 and code `timeout`. The REST layer never retries. With a session and a signer, requests send `X-Wallet-Address` so a multi-wallet session acts for the signer; the session goes only for a signer whose wallet the SDK confirmed in it (`client.session_wallets`), so after switching the signer call `client.authenticate()` (else `AuthRequiredError` before sending). Public and key-only calls send no cookie. `client.api.paginate(fn, **params)` walks cursor-paged lists. AI market drafts can run in the background (`async_=True`, or `client.api.run_draft_job(kind, **params)`): the API cuts one drafting request at about 24 s. Reads the API no longer serves (quotes, routes, live market state, claimables) come from the chain through the modules above.

## Key behaviours

- **Every write syncs.** Each write simulates first (reverts are decoded into `BasisContractError` with the contract's reason), sends, waits for the receipt and awaits one `POST /api/v1/sync {txHash, source: "sdk"}`, order-book writes included. Failed syncs are retried (6 attempts with backoff; a 429 waits on `X-RateLimit-Reset`), then queued in `client.pending_syncs` for `flush_pending_syncs()`. A sync failure never raises: the transaction is already confirmed.
- **Never a zero minimum.** `min_out`, `min_shares` and `max_cost` are derived from a tax-inclusive quote with the client slippage (1% trades, markets and redeem), overridable per call with `slippage_bps` or an explicit value.
- **Deadlines.** `swap_cross`, `repay_leverage_with`, `repay_loan_with` and market `buy` default to latest block + 600 s.
- **Units with metadata.** Prices and values are `Amount` objects in the pair asset's units (scaled by `PAIR_UNIT`, not always 18 decimals, not USD). An `int` argument is raw units; a `str` / `float` / `Decimal` amount is a human amount.
- **Pre-checks.** Conditions the contract checks without a message raise `ValidationError(rule, …)` before any gas is spent.
- **Approvals** are sent only when the allowance is short: the trading router gets a one-time unlimited approval for the token traded, every other spender the exact amount. They sync like every other write.
- **One write at a time.** Wait for each write before the next from the same client (no threads sharing a client). A nonce another transaction took raises `BasisRpcError` kind `nonce_used`: nothing ran, so sending again is fine.
- **Deployment checks.** `client.<module>.deployed` reports a zero address; `contracts_check` compares the chain id and the deployment with the site's `/contracts.json`. The sites publish no addresses: the SDK's address book is the source.
- **No gas sponsorship.** Transactions pay gas normally.
- **Bundled creation**: `create_token_with_metadata` and `create_market_with_metadata` create the token or market, then upload its image and IPFS metadata. The image argument and the credentials are checked before any gas. An upload that fails after the create does not raise (the tx is confirmed): the result carries `upload_error` (`{stage, code, status, message}`, the failed step's field None) and a `warnings` entry; finish with `factory.retry_token_metadata(token, ...)` / `prediction_markets.retry_market_metadata(market, ...)` (no tx), never by creating again.

## Links

- Platform: https://launchonbasis.com
- API reference: https://launchonbasis.com/api-docs
- Report a bug: https://launchonbasis.com/bugreport, or `client.api.submit_bug_report(...)`

## License

Elastic License 2.0 (ELv2): free to use, modify, and distribute. Build commercial products with it. Just don't resell the SDK itself as a hosted service. See [LICENSE](LICENSE) for details.
