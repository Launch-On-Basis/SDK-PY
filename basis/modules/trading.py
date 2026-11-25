"""``client.trading``: plain buys/sells per main, cross-pair swaps, tax-inclusive quotes, and leverage (open, add, sell
part, extend, repay, claim). Every trade sends a real minimum.
TypeScript SDK: src/modules/Trading.ts.

Amount inputs, quotes and previews are raw ints in the unit of the token they belong to (pair assets have their own
decimals); write results carry ``Amount`` objects (below).
Leverage positions are dicts (see ``core/loans.py``); a leverage simulation is the leverage result dict of
``leverage_simulator``.

One leverage position per token: with one open, ``leverage_buy`` / ``mixed_buy`` ADD to it (same end date, priced on
the days left). Result dicts:
  leverage quote (``quote_leverage``): path, leverage_pct (100 = leverage_buy, 1-99 = mixed_buy: lower keeps more of
    the position debt-free), days (asked days for a new position, days left for an add), open_loan_id (0 = opens a new
    one), trader (the wallet the quote is priced for: ``user``, else the signer; the zero address means neither was
    given and the quote is at the list rate, no wallet discounts, the most any wallet pays, so a real open gets at least
    this collateral), tax_bps (trader's buy fee rate for the bought token, read live), sim (the exact figures the open
    gets for trader; sim["total_collateral"] = tokens the position gains), spot (what the amount buys at spot, before
    fees; a position under 2x leverage is refused "Under 2x"), blocked (why ``leverage_buy`` / ``mixed_buy`` would
    refuse this buy for trader now, in order, under the rule names those writes refuse it with; empty:
    nothing in the way: 'In presale' (an auto-vest token whose presale is not complete), 'No WL for freeze' (a frozen
    token, and trader is not on its whitelist; the zero address never is) or 'WL cap' (the position's collateral over
    what is left of trader's presale cap), 'Under 2x', 'Trading is not enabled' (the main is not live yet), 'pair asset
    paused' (its issuer paused the main's pair asset: the buy cannot be paid in); the wallet's balance is not checked,
    the days range and "Extend first" still raise). Only a quote for the opening wallet matches
    its open to the wei.
  leverage partial-sell quote (``quote_partial_sell_leverage``): gross, taxable (the profit in it), tax_bps (the
    wallet's own sell fee rate), fee (tax_bps on taxable, 0 at a loss),
    net (what the wallet gets, what ``min_out`` binds), collateral_sold, debt_repaid.
  open leverage (``get_open_leverage``): loan_id, position, days_left.
  repay preview (``get_leverage_repay_preview``): loan_id, debt, minimum_loan, max_partial, amount, full, active,
    allowed (False for a position that is not active), collateral_released, collateral_left, debt_left.
  repay-with quote (``quote_repay_leverage_with``): loan_id, token_in, pair_asset, debt, amount_in, pair_out, covers,
    refund, collateral_released.

Swap routing (``build_cross_path`` / ``quote_swap``):
  cross route: path (sell steps, at most one outside hop, buy steps), uses_outside, outside_hop ([from, to] or None).
  swap quote: token_in, token_out, amount_in, trader, path ([] without a route), kind ('buy' | 'sell' | 'cross' | 'hop',
    None without a route), engine ('buyTokens' | 'sellTokens' | 'swapCross' | None), amount_out (``quote``, raw units
    of token_out; 0 when blocked before the quote), min_out (amount_out minus the slippage, never 0 unless blocked),
    taxes ([{side: 'sell' | 'buy', token, tax_bps, amount, asset}]: amount in raw units of asset, what the side's tax is
    taken in), outside_hop ({token_in, token_out, caps: {max_in, max_out}, caps_left: {in_left, out_left}} or None;
    caps = the most one window allows, caps_left = what other swaps left of them now. On Robinhood Chain usage counts
    per window of about 12 s, so caps_left is what a swap sent now meets and what ``blocked`` checks; elsewhere a swap
    sent now meets the full caps and those are checked (``hop_executor.check_caps``); never cache), whitelist
    (``factory.get_whitelist_status`` of a frozen presale token_out, else None), blocked (None, or 'no route', 'pair
    paused' (a main on the route has its pair paused), 'trading not enabled', 'pair asset paused' (its issuer paused the
    pair asset of a main the route trades on, and the swap moves it: on the path, as that side's fee, or as the trader's
    pending holder rewards, which a sale of a factory token pays out in it whatever the fee), 'no executor', 'autovest
    presale', 'not whitelisted', 'Overcapped on WL buying', 'above cap' (the outside leg over what its caps allow now: caps_left on Robinhood Chain,
    where waiting about 12 seconds can clear it; caps elsewhere), 'too small', or a decoded revert reason).

Write results: the tx dict ({hash, receipt, result, warnings}) plus the figures decoded from the tx's own events (only
logs of the expected contract count), every money figure an ``Amount`` in its token (decimals, unit and symbol resolved
before sending; ids, timestamps and flags stay plain). A figure is None when its event is missing from a confirmed tx;
``warnings`` then says so. Never resend a confirmed tx:
  trade (``buy_tokens`` / ``sell_tokens`` / ``buy`` / ``sell`` / ``sell_percentage``): amount_in,
    amount_out (a buy's tokens, also when they went to the vault or a vesting; a sell's proceeds after the tax), tax (a
    buy's in path[0], a sell's in the last token), auto_vested ({vesting_id, amount, gradual, ends_at}: a presale buy
    of an auto-vest token lands in a vesting for the signer, not the wallet; ends_at None if its block could not be
    read; None otherwise and on sells), rewards_claimed (a sell of a factory token first pays the signer's pending
    holder rewards, in its main's pair asset, in the same tx: on top of amount_out, and in another token when the sell
    stops at the main; 0 when none were owed or rewards are off; None on buys and when a main is sold).
  ``buy_and_stake``: the trade keys plus staked (the amount staked in the vault).
  ``swap_cross``: amount_in (path[0]), amount_out (delivered, last token; ``result`` is the
    pre-send simulation), recipient, sell_fee / sell_out (in the sell side's last token: its main or pair asset),
    buy_fee / buy_in (in the buy side's first token); a side the route lacks is 0 (in path[0] / the last token);
    rewards_claimed (a route selling a factory token, path[0], first pays the signer's pending holder rewards in that
    token's pair asset, to the signer, not ``recipient``; 0 when none were owed; None when path[0] is not a factory
    token).
  ``leverage_buy`` / ``mixed_buy`` (a new position, or an add to the open one): loan_id, added, collateral_added
    (the path's last token), borrowed_added, debt_added, initial_buy (pair asset), liquidation_time (None on an add:
    the end date does not change).
  ``partial_sell_leverage``: gross, fee (the exit fee, at the signer's own sell fee rate on the profit only), payout
    (gross - fee, what ``min_out`` applies to), debt_left (pair asset), collateral_left (collateral token), closed,
    rewards_claimed (the sale of a factory-token position first pays the signer's pending holder rewards, in the pair
    asset, on top of payout; 0 when none were owed; None for a position on the main itself).
  ``extend_leverage``: new_liquidation_time, new_full_amount, new_borrowed_amount, extra_out
    (a refinance's payout, else 0).
  ``repay_leverage`` (partial or full): repaid (pair asset taken), closed, debt_left,
    collateral_released.
  ``repay_leverage_with``: amount_in (token_in), pair_out, refund, closed,
    debt_left, collateral_released. All of pair_out goes to the debt: a hop paying more than quoted repays more on a
    partial (lower debt_left, more collateral back, refund 0); only what is above the whole debt is refunded.
  ``claim_leverage_liquidation``: claimed (collateral token).
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from web3 import Web3

from ..config.addresses import ZERO_ADDRESS, is_zero_address
from ..core.amounts import amount_of, opt_amount, pair_amount_of
from ..core.base import BaseModule
from ..core.cross_hop import check_cross_hop, check_cross_plan, cross_sides
from ..core.errors import ValidationError
from ..core.events import add_missing_event_warning
from ..core.loans import (check_deadline, check_explicit_min, core_extend_refusal, gross_of_hop, is_asset_paused,
                          pair_asset_paused, quote_hop_within_caps, quote_partial_sell, read_core_loan, read_repay_sizing,
                          refuse_paused_asset, size_repay_with)
from ..core.mains import MainInfo, main_amount
from ..core.revert import decode_revert
from ..core.redact import redact_urls
from ..core.units import (BPS, Amount, max_with_slippage, min_with_slippage, opt_uint, to_address, to_amount, to_bool,
                          to_uint, validate_slippage_bps)

Number = Union[int, float]
logger = logging.getLogger("basis")

MAX_UINT256 = 2 ** 256 - 1
REPAY_LEVERAGE_WITH_BUFFER_BPS = 50
_TRADE_PAUSED = "no trade that moves it (paid in, paid out or as the fee) can go through"


def _rewards_paused(token: str) -> str:
    """What a sale of the factory token ``token`` cannot do while its main's pair asset is paused and the signer has
    holder rewards on it to claim (``pair_asset_paused``): every sale pays them out in that asset, whatever the fee. TS:
    ``rewardsPaused``."""
    return f"no sale of {token} can go through: every sale pays out your pending holder rewards on it in this asset"


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _is_int(v: Any) -> bool:
    """JS ``Number.isInteger``: an int (not bool) or a whole float."""
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and v.is_integer())


class TradingModule(BaseModule):
    """``client.trading``: plain trades per main, cross swaps, tax-inclusive quotes and leverage. Every write returns
    the figures decoded from its own events (module docstring)."""

    @property
    def deployed(self) -> bool:
        """True when trading is deployed on this chain."""
        return not is_zero_address(self.client.addresses["swap"])

    @property
    def _swap_address(self) -> str:
        return self._require(self.client.addresses["swap"], "Swap")

    @property
    def _swap(self):
        return self._contract(self._swap_address, "Swap")

    @property
    def _signer(self) -> Optional[str]:
        return self.client.account.address if self.client.account else None


    def quote_cross(self, path: Sequence[str], amount_in: int, trader: Optional[str] = None) -> int:
        """The on-chain quote, taxes included as ``trader`` (default: the signer, else 0x0). Sent as an eth_call. An
        outside hop is quoted net of the hop fee (``hop_executor.fee_ppm``). Prices plain paths too. Raw units of the
        last token."""
        amt = to_uint(amount_in, "amountIn")
        path = _to_path(path)
        who = to_address(trader, "trader") if trader is not None else self._signer
        swap = self._swap
        tx = {"from": who} if who else {}
        try:
            return swap.functions.quoteCross(path, amt, who or ZERO_ADDRESS).call(tx)
        except Exception as e:  # noqa: BLE001
            raise decode_revert(e, contract="Swap", function_name="quoteCross", abi=swap.abi) from e

    def quote(self, path: Sequence[str], amount_in: int, trader: Optional[str] = None) -> int:
        """Tax-inclusive output of a trade (raw units of the path's last token), as ``trader`` (default the signer).
        Uses ``quote_cross``; a plain buy of a token in an auto-vest presale (``[main, token]`` or ``[pairAsset, main,
        token]``, which ``quote_cross`` refuses with "autovest presale" but ``buy_tokens`` runs) is priced from
        ``get_amounts_out`` and the live fee rate instead. Any other route into such a token (from another token)
        re-raises the revert: it cannot be traded."""
        amt = to_uint(amount_in, "amountIn")
        who = to_address(trader, "trader") if trader is not None else None
        return self._quote_trade(_to_path(path), amt, who)[0]

    def _quote_trade(self, path: Sequence[str], amt: int, trader: Optional[str] = None) -> Tuple[int, bool]:
        """``quote`` on a checked path: (output, priced by hand: a plain buy of an auto-vest token in presale)."""
        try:
            return self.quote_cross(path, amt, trader), False
        except Exception as e:  # noqa: BLE001
            if decode_revert(e).reason != "autovest presale" or not self._is_plain_buy(path):
                raise
            return self._quote_buy_by_hand(path, amt, trader or self._signer or ZERO_ADDRESS), True

    def _quote_buy(self, path: Sequence[str], amount: int) -> int:
        """A plain buy's quote for the signer. A quote priced by hand (an auto-vest token in presale) checks the presale
        whitelist itself for a frozen token, as ``quote_swap`` does: "No WL for freeze", then "Overcapped on WL buying"
        (bought so far plus this buy over the wallet's cap)."""
        me = self._account
        out, by_hand = self._quote_trade(path, amount, me)
        if by_hand:
            self._check_presale_whitelist(path[-1], me, out)
        return out

    def _check_presale_whitelist(self, token: str, who: str, tokens: int) -> None:
        """The presale whitelist of a frozen factory token, for a presale buy of ``tokens`` by ``who`` (the caller knows
        the presale is on): "No WL for freeze" for a wallet not on it, "Overcapped on WL buying" when the buy takes it
        over its cap. Nothing for a token that is not frozen."""
        if not self._contract(token, "FactoryToken").functions.frozen().call():
            return
        wl = self.client.factory.get_whitelist_status(token, who)
        if not wl["whitelisted"]:
            raise ValidationError("No WL for freeze", "This token is frozen and the wallet is not whitelisted.")
        if wl["bought"] + tokens > wl["max_buy"]:
            raise ValidationError(
                "Overcapped on WL buying",
                f"This buy gets {tokens} tokens, over what is left of the wallet's presale cap ({wl['remaining']} of "
                f"{wl['max_buy']}): use a smaller amount.")

    def _is_plain_buy(self, path: Sequence[str]) -> bool:
        """A path ``buyTokens`` runs as a buy: 2 or 3 tokens, the first not a factory token selling into the second."""
        if len(path) not in (2, 3):
            return False
        try:
            q = self._contract(_cs(path[0]), "EcosystemToken").functions.quoteToken().call()
        except Exception:  # noqa: BLE001
            return True
        return not _eq(q, path[1])

    def _quote_buy_by_hand(self, path: Sequence[str], amount_in: int, trader: str) -> int:
        """Plain buy quote by hand, at the live fee rate of the bought token."""
        taxes = self._require(self.client.addresses["taxes"], "Taxes")
        rate = self._contract(taxes, "Taxes").functions.getTaxRate(_cs(path[-1]), _cs(trader)).call()
        return self.get_amounts_out(amount_in - (amount_in * rate) // BPS, path)

    def get_amounts_out(self, amount: int, path: Sequence[str]) -> int:
        """The output of a trade **excluding tax** (use ``quote`` for what a trade pays).
        Raw units of the last token."""
        amt = to_uint(amount, "amount")
        return self._swap.functions.getAmountsOut(amt, _to_path(path)).call()

    def get_token_price(self, token: str) -> Amount:
        """A factory token's price in its main (unit ``main``, 18 decimals); for a main, its price in the pair asset
        (unit ``pairAsset``, scaled by PAIR_UNIT)."""
        token = to_address(token, "token")
        info, is_main = self._token_main(token)
        if is_main:
            return self.get_pair_price(token)
        raw = self._contract(token, "FactoryToken").functions.getTokenPrice().call()
        return to_amount(raw, 18, "main", info.main, info.symbol)

    def get_pair_price(self, token: str) -> Amount:
        """The token's price in its main's pair asset (scaled by PAIR_UNIT)."""
        token = to_address(token, "token")
        info, is_main = self._token_main(token)
        if is_main:
            raw = self._contract(token, "MainToken").functions.getPairPrice().call()
        else:
            raw = self._contract(token, "FactoryToken").functions.getPairPrice().call()
        return to_amount(raw, info.pair_decimals, "pairAsset", info.pair_asset, info.pair_symbol)

    def get_hop_executor(self) -> str:
        """The hop executor trading uses for outside hops (zero when there is none; outside hops then revert "no
        executor")."""
        return self._swap.functions.hopExecutor().call()


    def build_buy_path(self, token: str, input: str = "pairAsset") -> List[str]:
        """Buy path for ``token``: ``[pairAsset, main]`` for a main; ``[pairAsset, main, token]`` or ``[main, token]``
        (``input="main"``) for a factory token."""
        if not isinstance(input, str) or input not in ("pairAsset", "main"):
            raise ValidationError("input", f"input must be 'pairAsset' or 'main', got {input!r}.")
        token = to_address(token, "token")
        info, is_main = self._token_main(token)
        if is_main:
            if input == "main":
                raise ValidationError("Bad path", "A main is bought with its pair asset, not with itself.")
            return [info.pair_asset, info.main]
        return [info.main, token] if input == "main" else [info.pair_asset, info.main, token]

    def build_sell_path(self, token: str, to_pair_asset: bool = False) -> List[str]:
        """Sell path for ``token``: ``[main, pairAsset]`` for a main; ``[token, main]`` or ``[token, main, pairAsset]``
        for a factory token."""
        to_pair = to_bool(to_pair_asset, "toPairAsset", False)
        token = to_address(token, "token")
        info, is_main = self._token_main(token)
        if is_main:
            return [info.main, info.pair_asset]
        return [token, info.main, info.pair_asset] if to_pair else [token, info.main]

    def build_cross_path(self, token_in: str, token_out: str) -> Optional[Dict[str, Any]]:
        """The ``swapCross`` route from ``token_in`` to ``token_out``: {path, uses_outside, outside_hop}, or None when none
        exists (the same token, two non-BASIS assets, a round trip, a factory token on an unknown main). Each token is a
        main (``mains.is_main``), a listed factory token or an asset (a pair asset or an outside token). Route = the sell
        steps (token -> main -> pair asset), at most one outside hop
        between two different assets, then the buy steps; no token twice. Plain buys and sells come out as their 2-3
        token paths."""
        token_in, token_out = to_address(token_in, "tokenIn"), to_address(token_out, "tokenOut")
        r = _plan_route(self._route_token(token_in), self._route_token(token_out))
        return None if r is None else {"path": r["path"], "uses_outside": r["uses_outside"], "outside_hop": r["outside_hop"]}

    def quote_swap(self, token_in: str, token_out: str, amount_in: int, trader: Optional[str] = None,
                   slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """One-call swap quote (what the API's ``/swap/quote`` returned), read from the chain as ``trader`` (default the
        signer, else 0x0): the route (``build_cross_path``), its kind and engine (``buyTokens`` / ``sellTokens`` /
        ``swapCross``), the tax-inclusive ``amount_out`` (``quote``) and ``min_out`` (the client trade slippage, or
        ``slippage_bps``), each side's tax (read live), the outside leg's caps and caps left, the whitelist entry of a
        frozen presale output, and ``blocked`` (paused pairs and trading not yet enabled, read live, the issuer pause of a
        traded main's pair asset the swap moves, executor and route, auto-vest presale, whitelist, the outside leg
        against what its caps allow a swap sent now: ``hop_executor.check_caps``, which counts the current window's usage
        on Robinhood Chain). A blocked quote carries ``min_out`` 0 and is not sendable. Send with ``buy_tokens`` / ``sell_tokens`` /
        ``swap_cross`` on ``path``. Keys: see the module docstring."""
        amount_in = _check_amount(amount_in, "amountIn")
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        token_in, token_out = to_address(token_in, "tokenIn"), to_address(token_out, "tokenOut")
        who = to_address(trader, "trader") if trader is not None else _cs(self._signer or ZERO_ADDRESS)
        a, b = self._route_token(token_in), self._route_token(token_out)
        route = _plan_route(a, b)
        q: Dict[str, Any] = {
            "token_in": token_in, "token_out": token_out, "amount_in": amount_in, "trader": who,
            "path": route["path"] if route else [], "kind": None, "engine": None, "amount_out": 0, "min_out": 0,
            "taxes": [], "outside_hop": None, "whitelist": None, "blocked": None,
        }
        if route is None:
            q["blocked"] = "no route"
            return q
        sell_side, buy_side = len(route["sell"]) > 1, len(route["buy"]) > 1
        if route["uses_outside"]:
            q["kind"] = "hop"
        elif sell_side and buy_side:
            q["kind"] = "cross"
        else:
            q["kind"] = "sell" if sell_side else "buy"
        q["engine"] = {"buy": "buyTokens", "sell": "sellTokens"}.get(q["kind"], "swapCross")
        blocked: Optional[str] = None

        if self._paused_mains(route["path"]):
            blocked = "pair paused"

        taxes = self.client.taxes
        base = taxes.get_base_rate()
        sell_base = buy_base = 0
        if sell_side and buy_side:
            if not _eq(a["main"], b["main"]):
                sell_base = base // 2
                buy_base = base - sell_base
            else:
                sell_base = base
        elif sell_side:
            sell_base = base
        else:
            buy_base = base

        def rate_of(token: str, bps: int) -> int:
            return taxes.get_tax_rate(token, who) if bps == base else taxes.get_tax_rate_base(token, who, bps)

        sell_bps = rate_of(token_in, sell_base) if sell_side else 0
        buy_bps = rate_of(token_out, buy_base) if buy_side else 0
        sell_tax = {"side": "sell", "token": token_in, "tax_bps": sell_bps, "amount": 0, "asset": route["sell"][-1]} if sell_side else None
        buy_tax = {"side": "buy", "token": token_out, "tax_bps": buy_bps, "amount": 0, "asset": route["buy"][0]} if buy_side else None
        q["taxes"] = [t for t in (sell_tax, buy_tax) if t is not None]

        gated: Dict[str, str] = {}
        for side, steps, bps in ((a, route["sell"], sell_bps), (b, route["buy"], buy_bps)):
            if len(steps) < 2 or not side["main"]:
                continue
            if side["kind"] == "main" or len(steps) == 3 or bps > 0:
                gated[side["main"].lower()] = side["main"]
        if any(not self._contract(m, "MainToken").functions.tradingEnabled().call() for m in gated.values()):
            blocked = blocked or "trading not enabled"

        if not blocked:
            moved: Dict[str, str] = {}
            for side, steps, bps in ((a, route["sell"], sell_bps), (b, route["buy"], buy_bps)):
                if len(steps) < 2 or not side["main"] or not side["pair"]:
                    continue
                if bps > 0 or any(_eq(x, side["pair"]) for x in route["path"]):
                    moved[side["pair"].lower()] = side["pair"]
            rewards_in = a["pair"] if (sell_side and a["kind"] == "token" and a["pair"]
                                       and a["pair"].lower() not in moved) else None
            if any(is_asset_paused(self.client, x) for x in moved.values()):
                blocked = "pair asset paused"
            elif rewards_in and is_asset_paused(self.client, rewards_in) and self._claimable_rewards(token_in, who) > 0:
                blocked = "pair asset paused"

        ex: Optional[str] = None
        if route["outside_hop"]:
            h_in, h_out = route["outside_hop"]
            ex = self.get_hop_executor()
            if is_zero_address(ex):
                blocked = blocked or "no executor"
            else:
                hops = self._contract(ex, "HopExecutor").functions.getRoute(h_in, h_out).call()
                idle = self.client.hop_executor.check_caps(h_in, h_out, 0, executor=ex)
                q["outside_hop"] = {"token_in": h_in, "token_out": h_out, "caps": idle["caps"], "caps_left": idle["caps_left"]}
                if len(hops) == 0:
                    blocked = blocked or "no route"

        if b["kind"] == "token":
            f = self._contract(token_out, "FactoryToken").functions
            presale_complete, auto_vest, frozen = f.presaleComplete().call(), f.autoVest().call(), f.frozen().call()
            if not presale_complete and auto_vest and q["engine"] != "buyTokens":
                blocked = blocked or "autovest presale"
            if not presale_complete and frozen:
                q["whitelist"] = self.client.factory.get_whitelist_status(token_out, who)
                if not q["whitelist"]["whitelisted"]:
                    blocked = blocked or "not whitelisted"

        if not blocked:
            try:
                x = amount_in
                if sell_tax:
                    gross = self.get_amounts_out(amount_in, route["sell"])
                    sell_tax["amount"] = gross * sell_bps // BPS
                    x = gross - sell_tax["amount"]
                if route["outside_hop"] and q["outside_hop"] and ex:
                    h_in, h_out = route["outside_hop"]
                    hop = self.client.hop_executor
                    pre = hop.check_caps(h_in, h_out, x, executor=ex)
                    if pre["blocked"]:
                        blocked = pre["blocked"]
                    else:
                        out = self._simulate_hop(ex, h_in, h_out, x)
                        fee_ppm = self._contract(ex, "HopExecutor").functions.feePpm().call()
                        post = hop.check_caps(h_in, h_out, x, gross_of_hop(out, fee_ppm), executor=ex)
                        if post["blocked"]:
                            blocked = post["blocked"]
                        x = out
                if not blocked:
                    if buy_tax:
                        buy_tax["amount"] = x * buy_bps // BPS
                    q["amount_out"] = self.quote(route["path"], amount_in, who)
            except Exception as e:  # noqa: BLE001
                blocked = _swap_block_reason(e)
        wl = q["whitelist"]
        if not blocked and wl and wl["bought"] + q["amount_out"] > wl["max_buy"]:
            blocked = "Overcapped on WL buying"
        if not blocked:
            bps = slippage_bps if slippage_bps is not None else self.client.slippage_bps["trade"]
            q["min_out"] = min_with_slippage(q["amount_out"], bps)
            if q["min_out"] == 0:
                blocked = "too small"
        q["blocked"] = blocked
        return q


    def buy_tokens(self, amount: int, min_out: Optional[int], path: Sequence[str], wrap_tokens: bool = False,
                   slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """Buy along ``path`` (``amount`` raw units of path[0]). Approves path[0] to the swap. The trade is quoted first
        (``quote``, one eth_call), also with an explicit ``min_out``, so its run-time refusals ("Trading is not
        enabled", the frozen presale whitelist: "No WL for freeze", "Overcapped on WL buying", ...) come before the
        approval; ``min_out`` None -> the quote minus the client slippage, an explicit one above the quote -> "Min out".
        ``wrap_tokens`` stakes a bought **main** in its vault (ignored for factory tokens), with ``buy_and_stake``'s
        rule: a quote under the vault's ``minStake`` is refused ("Below min stake") and the minimum sent is raised to it.
        A presale buy of an auto-vest token goes into a vesting for the signer, not the wallet (``auto_vested``); a frozen
        one checks the wallet's presale whitelist first. Refuses a paused pair ("unknown pair"), then a path in the wrong
        direction, before any approval: a buy starts at a main's pair asset (or at the main, for a factory token on it)
        and ends in a main or a factory token ("Alien output", "Bad pair", "Not pair asset", "Not ecosystem"). After the
        quote, "pair asset paused" (its issuer paused the main's pair asset and the buy moves it: a buy of a factory
        token with the main is refused only when the signer pays a fee on it). Returns the trade dict (module
        docstring)."""
        self._check_plain_path(path)
        amount = _check_amount(amount, "amount")
        explicit = opt_uint(min_out, "minOut")
        wrap = to_bool(wrap_tokens, "wrapTokens", False)
        path = _to_path(path)
        pairs = self._check_plain_route("buyTokens", path)
        out_asset, staking, _ = pairs[-1]
        if wrap and not is_zero_address(out_asset):
            m = self._stake_min(self.client.mains.get(path[-1]), staking, path, amount, explicit, slippage_bps)
        else:
            m = self._trade_min(explicit, slippage_bps, lambda: self._quote_buy(path, amount), "what this buy gets")
        self._refuse_paused_plain(path, pairs)
        return self._send_trade("buyTokens", amount, m, path, wrap)

    def sell_tokens(self, amount: int, min_out: Optional[int], path: Sequence[str],
                    slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """Sell along ``path`` (``amount`` raw units of path[0]). Approves path[0] to the swap. For native ETH out use
        ``swap_cross`` with ``unwrap_native``. Quoted first (``quote``, one eth_call), also with an explicit ``min_out``
        (its run-time refusals come before the approval; an explicit minimum above the quote:
        "Min out"). Refuses a paused pair ("unknown pair"), then a path in the wrong direction, before any approval: a
        sell starts at a main or a factory token and ends at the main's pair asset (or at the main, for a factory token)
        ("Wrong token", "Bad pair", "Not ecosystem", "Not MAIN", "Not pair asset"). After the quote, "pair asset paused"
        (its issuer paused the main's pair asset and the sale moves it, also as the signer's pending holder rewards,
        which a sale of a factory token pays out in it whatever the fee: a sale of a factory token for the main is
        refused only when the signer pays a fee on it or has holder rewards on it to claim,
        ``factory.get_claimable_rewards`` above 0). Returns the trade dict (module docstring)."""
        self._check_plain_path(path)
        amount = _check_amount(amount, "amount")
        explicit = opt_uint(min_out, "minOut")
        path = _to_path(path)
        pairs = self._check_plain_route("sellTokens", path)
        m = self._trade_min(explicit, slippage_bps, lambda: self.quote(path, amount), "what this sale pays")
        self._refuse_paused_plain(path, pairs)
        return self._send_trade("sellTokens", amount, m, path, False)

    def buy(self, token: str, amount_in: int, min_out: Optional[int] = None, slippage_bps: Optional[int] = None,
            input: str = "pairAsset") -> Dict[str, Any]:
        """Buy ``token`` (a main or a factory token) with ``amount_in`` raw units of its main's pair asset
        (or of the main: ``input="main"``, factory tokens only). A presale buy of an auto-vest token lands in a vesting
        (``auto_vested``), not the wallet."""
        path = self.build_buy_path(token, input)
        return self.buy_tokens(amount_in, min_out, path, False, slippage_bps)

    def sell(self, token: str, amount: int, min_out: Optional[int] = None, slippage_bps: Optional[int] = None,
             to_pair_asset: bool = False) -> Dict[str, Any]:
        """Sell ``amount`` raw units of ``token``; a factory token stops at its main unless ``to_pair_asset``."""
        path = self.build_sell_path(token, to_pair_asset)
        return self.sell_tokens(amount, min_out, path, slippage_bps)

    def sell_percentage(self, token: str, percentage: Number, min_out: Optional[int] = None,
                        slippage_bps: Optional[int] = None, to_pair_asset: bool = False) -> Dict[str, Any]:
        """Sell ``percentage`` (1-100) of the signer's balance of ``token``."""
        if not _is_int(percentage) or percentage < 1 or percentage > 100:
            raise ValidationError("percentage", f"percentage must be an integer 1-100, got {percentage!r}.")
        to_bool(to_pair_asset, "toPairAsset", False)
        token = to_address(token, "token")
        balance = self._balance_of(token)
        amount = balance * int(percentage) // 100
        if amount == 0:
            raise ValidationError("balance", f"Nothing to sell: balance of {token} is {balance}.")
        return self.sell(token, amount, min_out=min_out, slippage_bps=slippage_bps, to_pair_asset=to_pair_asset)

    def buy_and_stake(self, main: str, amount: int, min_out: Optional[int] = None,
                      slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """Buy a main with ``amount`` raw pair asset and stake it in that main's vault in one tx
        (``buy_tokens(..., wrap_tokens=True)``). ``main``: address or address-book key. The vault stakes at least its
        ``minStake``: a quote under it is refused ("Below min stake") and the minimum sent is raised to ``minStake``, so
        the slippage guard covers the stake too. Then "pair
        asset paused" (its issuer paused the pair asset the buy is paid in), before any approval. Returns the trade dict
        plus ``staked``."""
        info = self.client.mains.get(main)
        amount = _check_amount(amount, "amount")
        explicit = opt_uint(min_out, "minOut")
        _, staking, active = self._swap.functions.pairs(_cs(info.main)).call()
        if not active:
            raise _paused_error(info.main)
        path = [_cs(info.pair_asset), _cs(info.main)]
        m = self._stake_min(info, staking, path, amount, explicit, slippage_bps)
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, _TRADE_PAUSED)
        vault = self._contract(staking, "Vault")
        tx = self._send_trade("buyTokens", amount, m, path, True)
        me = self._account
        st = self._first_event(tx, vault, "Staked", "Vault", ("staked",),
                               lambda e: _eq(e["user"], me) and _eq(e["main"], info.main))
        return {**tx, "staked": main_amount(st["amount"], info) if st else None}


    def swap_cross(self, path: Sequence[str], amount_in: int, min_out: Optional[int] = None,
                   slippage_bps: Optional[int] = None, recipient: Optional[str] = None, deadline: Optional[int] = None,
                   unwrap_native: bool = False, native: bool = False) -> Dict[str, Any]:
        """Any route in one tx (sell-token, sell-main, outside, buy-main, buy-token, each at most once). Approves path[0]
        to the swap, or with ``native`` sends ``amount_in`` as msg.value (path[0] = WETH). ``unwrap_native`` pays out
        native ETH (the path must end in WETH). ``min_out`` None -> derived from ``quote_cross`` as the signer.
        ``recipient`` defaults to the signer, ``deadline`` (unix seconds) to the latest block time + 600 s (a deadline
        equal to the latest block time can still be met, but only just: give some room). Refuses, before any approval: a
        zero ``recipient`` ("no recipient"), a ``deadline`` before the latest block time ("Deadline"), a paused main on
        the path ("unknown pair"), a path refused by its steps ("Bad route order": steps out of order or repeated; "no
        BASIS leg": nothing sold or bought on BASIS; "round trip": a main sold into its pair asset and bought back),
        "pair asset paused" (its issuer paused the pair asset of a main the route trades on and the route moves it: on
        the path, as a side's fee, or as the signer's pending holder rewards, which a sale of a factory token pays out in
        it whatever the fee: refused only when the signer pays a fee there or has rewards to claim on the token sold,
        ``factory.get_claimable_rewards`` above 0), an outside hop over what its caps allow a swap sent now ("above cap":
        what other swaps left of them in the current window on Robinhood Chain, where waiting about 12 seconds can clear
        it; ``hop_executor.check_caps``), all as ``ValidationError`` with or without ``min_out``, then what
        ``quote_cross`` (read either way, one eth_call) refuses, as the contract's revert ("autovest presale", "Trading
        is not enabled", the frozen presale whitelist, ...), and an explicit ``min_out`` above that quote ("Min out").
        Returns the swap figures (module docstring; amount_out = what was delivered)."""
        if len(path) < 2:
            raise ValidationError("Bad path", "A cross path needs at least 2 tokens.")
        path = _to_path(path)
        if _eq(path[0], path[-1]):
            raise ValidationError("same token", "Input and output token are the same.")
        amount_in = _check_amount(amount_in, "amountIn")
        explicit = opt_uint(min_out, "minOut")
        native = to_bool(native, "native", False)
        unwrap_native = to_bool(unwrap_native, "unwrapNative", False)
        opt_deadline = opt_uint(deadline, "deadline")
        me = self._account
        to = me if recipient is None else to_address(recipient, "recipient")
        if is_zero_address(to):
            raise ValidationError("no recipient",
                                  "The output cannot go to the zero address: omit recipient to receive it yourself.")
        dl = self._send_deadline(opt_deadline)
        self._check_pairs_active(path)

        weth: Optional[str] = None
        if native or unwrap_native:
            ex = self.get_hop_executor()
            if is_zero_address(ex):
                raise ValidationError("no executor", "Native in/out needs the hop executor, which SWAP does not have yet.")
            weth = self._contract(ex, "HopExecutor").functions.weth().call()
        if native and not _eq(path[0], weth):
            raise ValidationError("native in", f"Native input needs path[0] = WETH ({weth}).")
        if unwrap_native and not _eq(path[-1], weth):
            raise ValidationError("not native", f"unwrapNative needs the path to end in WETH ({weth}).")

        rt = [self._route_token(t) for t in path]
        check_cross_plan(path, rt)
        self._refuse_paused_cross(path, rt)
        check_cross_hop(self.client, path, rt, amount_in, me, self._simulate_hop)
        m = self._trade_min(explicit, slippage_bps, lambda: self.quote_cross(path, amount_in, me), "what this swap pays")
        fmt = self._cross_formats(path, rt[0], rt[-1])

        if native:
            bal = self.web3.eth.get_balance(me)
            if bal < amount_in:
                raise ValidationError("balance", f"Native balance {bal} is below {amount_in}.")
        else:
            self._check_balance(path[0], amount_in)
            self._ensure_allowance(path[0], self._swap_address, amount_in)
        tx = self._send(
            self._swap.functions.swapCross(path, amount_in, m, to, dl, unwrap_native), "Swap",
            value=amount_in if native else 0,
        )
        fields = ("amount_in", "amount_out", "recipient", "sell_fee", "buy_fee", "sell_out", "buy_in")
        ev = self._first_event(tx, self._swap, "CrossSwap", "Swap", fields, lambda e: _eq(e["trader"], me))
        return {**tx, "amount_in": opt_amount(fmt["in"], ev["amountIn"] if ev else None),
                "amount_out": opt_amount(fmt["out"], ev["amountOut"] if ev else None),
                "recipient": ev["recipient"] if ev else None,
                "sell_fee": opt_amount(fmt["sell"], ev["sellFee"] if ev else None),
                "buy_fee": opt_amount(fmt["buy"], ev["buyFee"] if ev else None),
                "sell_out": opt_amount(fmt["sell"], ev["sellOut"] if ev else None),
                "buy_in": opt_amount(fmt["buy"], ev["buyIn"] if ev else None),
                "rewards_claimed": self._rewards_paid(tx, path[0], fmt["rewards"]) if fmt["rewards"] else None}


    def quote_leverage(self, target: Union[str, Sequence[str]], amount: int, number_of_days: Number,
                       leverage_pct: Number = 100, user: Optional[str] = None) -> Dict[str, Any]:
        """What a leverage buy (``leverage_pct`` 100) or mixed buy (1-99) of ``amount`` raw pair asset on ``target`` (a
        token, or an explicit ``[pairAsset, main(, token)]`` path) does now, priced at the rate of ``user`` (default the
        signer; the open pays that wallet's rate). A client with neither gets the list rate (``trader`` is then the zero
        address): no wallet discounts, so a real open gets at least that collateral. One position per token: with one
        open the buy adds to it, priced on its days left (``number_of_days`` is then ignored), and "Extend first" is
        raised below the main's minimum loan days left. A new position checks ``number_of_days`` against the main's
        range. A bad path and a paused pair are refused first, in ``leverage_buy``'s order
        ("wrong path", "unknown pair"). Everything else the open would be refused with now is listed in ``blocked`` ("In
        presale", "No WL for freeze", "WL cap", "Under 2x", "Trading is not enabled", "pair asset paused"), not raised:
        the quote is sendable only while ``blocked`` is empty. Returns the leverage-quote dict (module docstring)."""
        pct = _check_lev_pct(leverage_pct, 100)
        who = to_address(user, "user") if user is not None else self._signer
        path = self._leverage_path(target)
        amount = _check_amount(amount, "amount")
        days = to_uint(number_of_days, "numberOfDays")
        q, days_error = self._quote_on(path, amount, days, pct, who)
        if days_error is not None:
            raise days_error
        blocks = self._leverage_blocks(path, q, q["trader"])
        return {**q, "blocked": [b.rule for b in blocks]}

    def get_open_leverage(self, main: Optional[str], token: str, user: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The user's (default the signer's) one open leverage position on ``token``, or None: {loan_id, position,
        days_left}. ``main`` None: the token's own main. ``days_left``: whole days left until ``liquidation_time``,
        counted from the next block (latest block time + 1 s): what an add sent now is priced on ("Extend first" below
        the main's minimum loan days); a later block only means fewer days, more collateral."""
        token = to_address(token, "token")
        given = to_address(user, "user") if user is not None else None
        main_addr = self.client.mains.main_of(token) if main is None else self.client.mains.address_of(main)
        who = given or self._account
        loan_id = self._contract(main_addr, "MainToken").functions.openLeverageOf(who, token).call()
        if loan_id == 0:
            return None
        position = read_core_loan(self.client, main_addr, who, loan_id, True)
        now = self.web3.eth.get_block("latest")["timestamp"] + 1
        end = position["liquidation_time"]
        return {"loan_id": loan_id, "position": position, "days_left": (end - now) // 86_400 if end > now else 0}

    def get_leverage_ids_for_token(self, main: Optional[str], token: str, user: Optional[str] = None) -> List[int]:
        """Every leverage position id ``user`` (default the signer) ever opened on ``token`` (open and closed, oldest
        first). ``main`` None: the token's own main. Positions: ``get_leverage_position(main, user, id)``."""
        token = to_address(token, "token")
        given = to_address(user, "user") if user is not None else None
        main_addr = self.client.mains.main_of(token) if main is None else self.client.mains.address_of(main)
        who = given or self._account
        n = self.client.main_lens.get_leverage_count_for_user_and_token(main_addr, who, token)
        fns = self._contract(main_addr, "MainToken").functions
        return [fns.userTokenLeverageIds(who, token, i).call() for i in range(n)]

    def leverage_buy(self, target: Union[str, Sequence[str]], amount: int, number_of_days: Number,
                     min_out: Optional[int] = None, slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """Open a leverage position on ``target`` (a token, path built as ``[pairAsset, main(, token)]``, or an
        explicit path starting at the main's pair asset), or ADD to the open one on that token (same end date, priced on
        the days left). ``amount`` raw pair asset, approved to the swap. ``min_out`` None -> ``quote_leverage(...)``
        collateral minus the slippage. Pre-checks, in order: "wrong path" (path[1] not a main, path[0] not its pair
        asset), "unknown pair" (paused), "wrong path" (not 2 or 3 tokens, or a factory token that is not listed: a
        delisted token cannot be leveraged), "Extend
        first" (add), "In presale", "No WL for freeze", "WL cap" (frozen factory token), "Under 2x", "Min out" (an
        explicit ``min_out`` above the quote's collateral), days range (new position), then "Trading is not enabled" (a
        main not live yet) and "pair asset paused" (its issuer paused the pair asset the buy is paid in), all before the
        approval (``quote_leverage`` lists the same refusals in ``blocked``). Returns the leverage-buy dict (module
        docstring)."""
        return self._lev_buy(target, amount, number_of_days, 100, min_out, slippage_bps)

    def mixed_buy(self, target: Union[str, Sequence[str]], amount: int, number_of_days: Number,
                  percentage_leverage: Number, min_out: Optional[int] = None,
                  slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """ONE leverage position at a chosen leverage, ``percentage_leverage`` (1-99): the whole amount goes into the
        position, and a lower value keeps more of it as debt-free collateral (nothing goes to the wallet). Adds to an
        open position like ``leverage_buy``, same pre-checks. The position must still reach 2x ("Under 2x").
        ``min_out`` = minimum tokens into the position."""
        pct = _check_lev_pct(percentage_leverage, 99)
        return self._lev_buy(target, amount, number_of_days, pct, min_out, slippage_bps)

    def _lev_buy(self, target: Union[str, Sequence[str]], amount: int, number_of_days: Number, pct: int,
                 min_out: Optional[int], slippage_bps: Optional[int]) -> Dict[str, Any]:
        path = self._leverage_path(target)
        amount = _check_amount(amount, "amount")
        days = to_uint(number_of_days, "numberOfDays")
        explicit = opt_uint(min_out, "minOut")
        me = self._account
        q, days_error = self._quote_on(path, amount, days, pct, me)
        blocks = self._leverage_blocks(path, q, me)
        gate = next((b for b in blocks if b.rule == "Trading is not enabled"), None)
        paused = next((b for b in blocks if b.rule == "pair asset paused"), None)
        first = next((b for b in blocks if b is not gate and b is not paused), None)
        if first is not None:
            raise first
        check_explicit_min(explicit, q["sim"]["total_collateral"], "minOut", "what this buy puts in the position")
        if days_error is not None:
            raise days_error
        m = self._resolve_min(explicit, slippage_bps, lambda: q["sim"]["total_collateral"], field="minOut")
        if gate is not None:
            raise gate
        if paused is not None:
            raise paused
        self._check_balance(path[0], amount)
        info = self.client.mains.get(path[1])
        pair = pair_amount_of(info)
        col = amount_of(self.client, path[-1], info)
        self._ensure_allowance(path[0], self._swap_address, amount)
        if pct == 100:
            tx = self._send(self._swap.functions.leverageBuy(amount, m, path, q["days"]), "Swap")
        else:
            tx = self._send(self._swap.functions.mixedBuy(amount, m, path, q["days"], pct), "Swap")
        main_c = self._contract(path[1], "MainToken")
        created = next((e for e in self._events(tx, main_c, "LeverageCreated") if _eq(e["user"], me)), None)
        if created:
            return {**tx, "loan_id": created["loanId"], "added": False, "collateral_added": col(created["collateralAmount"]),
                    "borrowed_added": pair(created["borrowedAmount"]), "debt_added": pair(created["fullAmount"]),
                    "initial_buy": pair(created["initialBuy"]), "liquidation_time": created["liquidationTime"]}
        inc = self._first_event(tx, main_c, "LeverageIncreased", "MainToken",
                                ("loan_id", "added", "collateral_added", "borrowed_added", "debt_added", "initial_buy"),
                                lambda e: _eq(e["user"], me))
        return {**tx, "loan_id": inc["loanId"] if inc else None, "added": True if inc else None,
                "collateral_added": col(inc["collateralAdded"]) if inc else None,
                "borrowed_added": pair(inc["borrowedAdded"]) if inc else None,
                "debt_added": pair(inc["fullAmountAdded"]) if inc else None,
                "initial_buy": pair(inc["initialBuy"]) if inc else None, "liquidation_time": None}

    def partial_sell_leverage(self, main: str, loan_id: int, percentage: Number, min_out: Optional[int] = None,
                              slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """Sell ``percentage`` (10-100, step 10) of the signer's leverage position on ``main``; 100 closes it (hub loans
        are sold through ``loans.partial_sell_loan``). The exit fee is the signer's own sell fee rate on the profit only:
        a sale at a loss pays none, and neither does a fee-whitelisted wallet. ``min_out`` binds what the signer
        receives, after the fee; None -> ``quote_partial_sell_leverage(...)["net"]`` minus the slippage (never 0: a quote
        that rounds to 0 is refused). An explicit ``min_out`` of 0 is sent as given: a main-collateral position, or a
        factory-token one on the standard curve (hybrid multiplier 100, not reversed), whose sale pays nothing
        (underwater: the debt takes all it brings in) can still be closed with it, and its slot freed; the sale then pays
        whatever it pays, from 0 up, without a guard. On any other factory token an underwater sale is refused ("cannot
        sell off with no profit"), with ``min_out`` 0 too: nothing is sent. Rules, in order: "unknown pair" (paused),
        "% by 10", "% range", "Not user", "Loan not active", then "pair asset paused" (its issuer paused the pair asset
        the sale pays out in). Returns the sale dict (module docstring)."""
        lid = to_uint(loan_id, "loanId")
        explicit = opt_uint(min_out, "minOut")
        main_addr = _cs(self.client.mains.address_of(main))
        self._check_pairs_active([main_addr])
        pct = _check_sell_pct(percentage)
        me = self._account
        pos = self._sellable_position(main_addr, me, lid)
        if explicit == 0:
            if slippage_bps is not None:
                validate_slippage_bps(slippage_bps)
            m = 0
        else:
            m = self._resolve_min(explicit, slippage_bps,
                                  lambda: quote_partial_sell(self.client, main_addr, pos, me, lid, pct, True, me)["net"],
                                  field="minOut")
        info = self.client.mains.get(main_addr)
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "no sale can pay it out")
        pair = pair_amount_of(info)
        col = amount_of(self.client, pos["token"], info)
        tx = self._send(self._swap.functions.partialLoanSellFor(main_addr, lid, pct, True, m), "Swap")
        ev = self._first_event(tx, self._swap, "LoanSold", "Swap",
                               ("gross", "fee", "payout", "debt_left", "collateral_left", "closed"),
                               lambda e: _eq(e["owner"], me) and e["loanId"] == lid)
        rewards = None if _eq(pos["token"], main_addr) else self._rewards_paid(tx, pos["token"], pair)
        if ev is None:
            return {**tx, "gross": None, "fee": None, "payout": None, "debt_left": None, "collateral_left": None,
                    "closed": None, "rewards_claimed": rewards}
        return {**tx, "gross": pair(ev["gross"]), "fee": pair(ev["fee"]), "payout": pair(ev["gross"] - ev["fee"]),
                "debt_left": pair(ev["debtLeft"]), "collateral_left": col(ev["collateralLeft"]),
                "closed": ev["debtLeft"] == 0 and ev["collateralLeft"] == 0, "rewards_claimed": rewards}

    def quote_partial_sell_leverage(self, main: str, loan_id: int, percentage: Number,
                                    user: Optional[str] = None) -> Dict[str, int]:
        """Preview ``partial_sell_leverage(main, loan_id, percentage)`` for ``user`` (default the signer), in the same
        state: the leverage partial-sell quote dict (module docstring). Exact for a position in a main; for a factory
        token the payout may be a little higher than quoted (dust), never lower. Rules, in order: "unknown pair"
        (paused), "% by 10", "% range", "Not user", "Loan not active"."""
        lid = to_uint(loan_id, "loanId")
        given = to_address(user, "user") if user is not None else None
        main_addr = _cs(self.client.mains.address_of(main))
        self._check_pairs_active([main_addr])
        pct = _check_sell_pct(percentage)
        who = given or self._account
        pos = self._sellable_position(main_addr, who, lid)
        exit_ = quote_partial_sell(self.client, main_addr, pos, who, lid, pct, True, who)
        return {**exit_, "collateral_sold": pos["collateral_amount"] * pct // 100,
                "debt_repaid": pos["full_amount"] * pct // 100}

    def extend_leverage(self, main: str, loan_id: int, number_of_days: Number, pay_in_pair_asset: bool,
                        refinance: bool) -> Dict[str, Any]:
        """Extend the signer's leverage position on its main by ``number_of_days`` (the main's ``min_days..max_days``,
        ``loans.get_loan_terms``, or 0 with ``refinance``). The days count from the due date, or from the time of the
        transaction when the position is past due (not yet liquidated): the new due date is the later of the two plus
        ``number_of_days``. ``pay_in_pair_asset`` pays the fee in the pair asset (approved to the main); otherwise it is
        added to the debt, or a refinance pays the surplus out. Both together are refused ("Pay in stable or refinance,
        not both"). A paused pair is refused first ("unknown pair"), then a pair asset its issuer paused ("pair asset
        paused"), before any approval. Other refusals carry the contract's revert reason, in order (the same check as
        ``loans.extend_loan`` / ``vault.extend_loan``): "Must be refinance if not extending time", "incorrect loan
        duration", "Loan not active", then "Not enough collateral to cover fees" (add to debt) or "No equity", "Minimum
        surplus not met", "Time remaining too short", "Fees exceed surplus value" (refinance). Returns the extension dict
        (module docstring)."""
        lid = to_uint(loan_id, "loanId")
        pay_in_pair_asset = to_bool(pay_in_pair_asset, "payInPairAsset")
        refinance = to_bool(refinance, "refinance")
        if pay_in_pair_asset and refinance:
            raise ValidationError(
                "Pay in stable or refinance, not both",
                "Choose either payInPairAsset or refinance, not both (the main silently ignores refinance when paying).",
            )
        info = self.client.mains.get(main)
        days = to_uint(number_of_days, "numberOfDays")
        me = self._account
        self._check_pairs_active([info.main])
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "no extension can take its fee")
        pos = read_core_loan(self.client, info.main, me, lid, True)
        q = self._leverage_extension_quote(info.main, (me, lid, days, True, pay_in_pair_asset, refinance))
        refusal = core_extend_refusal(self.client, info.main, pos, days, pay_in_pair_asset, refinance, q)
        if refusal is not None:
            raise refusal
        if pay_in_pair_asset:
            self._check_balance(info.pair_asset, q["fee"])
            self._ensure_allowance(info.pair_asset, info.main, q["fee"])
        c = self._contract(info.main, "MainToken")
        tx = self._send(c.functions.extendLoan(lid, days, True, pay_in_pair_asset, refinance), "MainToken")
        ev = self._first_event(tx, c, "LoanExtended", "MainToken",
                               ("new_liquidation_time", "new_full_amount", "new_borrowed_amount", "extra_out"),
                               lambda e: _eq(e["user"], me) and e["loanId"] == lid and e["isLeverage"])
        if ev is None:
            return {**tx, "new_liquidation_time": None, "new_full_amount": None, "new_borrowed_amount": None,
                    "extra_out": None}
        borrowed = ev["newBorrowedAmount"]
        extra = borrowed - pos["borrowed_amount"] if refinance and borrowed > pos["borrowed_amount"] else 0
        pair = pair_amount_of(info)
        return {**tx, "new_liquidation_time": ev["newLiquidationTime"], "new_full_amount": pair(ev["newFullAmount"]),
                "new_borrowed_amount": pair(borrowed), "extra_out": pair(extra)}

    def repay_leverage(self, main: str, loan_id: int, amount: Optional[int] = None) -> Dict[str, Any]:
        """Repay the signer's leverage position with the pair asset (nothing is sold). ``amount`` (raw) None = in full
        (exactly the debt is taken and all collateral comes back). Under the debt: the debt drops by it, the repaid share
        of the collateral comes back (and the fee-free part of a later sale shrinks by the same share) and the
        position stays open; what is left must stay at least the main's
        ``minimum_loan`` ("Repay in full"; ``get_leverage_repay_preview`` gives the largest partial). Rules, in order:
        "Loan not active", "Zero amount", "Repay in full", then "pair asset paused" (its issuer paused
        the pair asset: the debt cannot be paid in) and the balance. Approves the pair asset taken to the main. Returns
        the repay dict (module docstring)."""
        lid = to_uint(loan_id, "loanId")
        amt = MAX_UINT256 if amount is None else to_uint(amount, "amount")
        info = self.client.mains.get(main)
        pos = read_core_loan(self.client, info.main, self._account, lid, True)
        if not pos["active"]:
            raise ValidationError("Loan not active", f"Leverage {lid} is not active.")
        if amt == 0:
            raise ValidationError("Zero amount", "amount must be greater than 0 (omit it to repay in full).")
        p = self._repay_preview(info.main, lid, pos, amt)
        if not p["allowed"]:
            raise ValidationError(
                "Repay in full",
                f"Repaying {amt} of {p['debt']} would leave {p['debt_left']}, under the minimum loan "
                f"{p['minimum_loan']}: repay at most {p['max_partial']}, or in full.",
            )
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "the position cannot be repaid in it now")
        self._check_balance(info.pair_asset, p["amount"])
        col = amount_of(self.client, pos["token"], info)
        self._ensure_allowance(info.pair_asset, info.main, p["amount"])
        c = self._contract(info.main, "MainToken")
        tx = self._send(c.functions.repayLoan(lid, amt, True), "MainToken")
        outcome, taken = self._repay_outcome(tx, info, lid, pos, col,
                                             ("repaid", "closed", "debt_left", "collateral_released"))
        return {**tx, **outcome, "repaid": taken}

    def get_leverage_repay_preview(self, main: str, loan_id: int, amount: Optional[int] = None,
                                   user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``repay_leverage(main, loan_id, amount)`` for ``user`` (default the signer): whether the position is
        active, the debt, the minimum loan, the largest partial repay, the pair asset taken and the collateral released.
        ``amount`` None = in full. ``allowed`` is False for a position that is not active. Returns the repay-preview
        dict (module docstring)."""
        lid = to_uint(loan_id, "loanId")
        amt = MAX_UINT256 if amount is None else to_uint(amount, "amount")
        given = to_address(user, "user") if user is not None else None
        main_addr = self.client.mains.address_of(main)
        pos = read_core_loan(self.client, main_addr, given or self._account, lid, True)
        return self._repay_preview(main_addr, lid, pos, amt)

    def repay_leverage_with(self, main: str, loan_id: int, token_in: str, amount_in: Optional[int] = None,
                            min_pair_out: Optional[int] = None, close: Optional[bool] = None,
                            slippage_bps: Optional[int] = None, buffer_bps: Optional[int] = None,
                            deadline: Optional[int] = None) -> Dict[str, Any]:
        """Repay the signer's leverage position with another token: ``token_in`` is swapped to the main's pair asset
        through an outside exchange (net of the hop fee, within what the route's caps allow now; no swap when
        ``token_in`` is the pair asset), all of it goes to the debt (a partial repay releases its share of the
        collateral) and the pair asset left over is refunded.
        Without ``amount_in`` it is sized to repay everything: the amount quoting the debt, plus ``buffer_bps`` (0.5%),
        with ``close`` = True (else False). ``min_pair_out`` None: the quote minus the slippage, raised to the debt when
        closing or when the quote covers it (a payout just under the debt would revert "Repay in full"); an explicit one
        above the quote is refused ("Min out"). Checks the deadline first (default latest block + 600 s; an explicit one
        before the latest block time: "Deadline"), then the route ("no executor", "no route"), "pair asset paused" (its
        issuer paused the pair asset the debt is paid in), an explicit ``amount_in`` against what the route's caps allow
        a swap sent now ("above cap": what other swaps left of them in the current window on Robinhood Chain, where
        waiting about 12 seconds can clear it), the minimum-loan rule on a partial (the quote plus the slippage must leave at least
        ``minimum_loan``), the explicit minimum and the balance; approves ``token_in`` to the swap. Returns the tx dict
        plus ``amount_in`` / ``pair_out`` / ``refund`` and the repay outcome."""
        lid = to_uint(loan_id, "loanId")
        explicit = opt_uint(min_pair_out, "minPairOut")
        opt_deadline = opt_uint(deadline, "deadline")
        close_opt = None if close is None else to_bool(close, "close")
        if buffer_bps is not None:
            validate_slippage_bps(buffer_bps, "bufferBps")
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        token_in = to_address(token_in, "tokenIn")
        info = self.client.mains.get(main)
        me = self._account
        dl = self._send_deadline(opt_deadline)
        pos = read_core_loan(self.client, info.main, me, lid, True)
        if not pos["active"]:
            raise ValidationError("Loan not active", f"Leverage {lid} is not active.")
        sizing = amount_in is None
        route = self._repay_with_route(info, token_in)
        refuse_paused_asset(self.client, info.pair_asset, info.pair_symbol, "the position cannot be repaid in it now")
        q = self._repay_with_quote(info, lid, pos, token_in, amount_in, buffer_bps, route)
        close = sizing if close_opt is None else close_opt
        if close and not q["covers"]:
            raise ValidationError("Not enough to close",
                                  f"{q['amount_in']} of {token_in} converts to {q['pair_out']}, under the {q['debt']} debt.")
        if not q["covers"]:
            p = self._repay_preview(info.main, lid, pos, q["pair_out"])
            bps = slippage_bps if slippage_bps is not None else self.client.slippage_bps["trade"]
            high = q["pair_out"] if _eq(token_in, info.pair_asset) else max_with_slippage(q["pair_out"], bps)
            if not p["allowed"] or high > p["max_partial"]:
                raise ValidationError(
                    "Repay in full",
                    f"{q['amount_in']} of {token_in} converts to {q['pair_out']} (up to {high} after slippage), which "
                    f"could leave under the minimum loan {p['minimum_loan']} of the {p['debt']} debt: repay at most "
                    f"{p['max_partial']} worth (less the slippage), or everything.",
                )
        m = self._resolve_min(explicit, slippage_bps, lambda: q["pair_out"], field="minPairOut")
        check_explicit_min(explicit, q["pair_out"], "minPairOut", f"what {q['amount_in']} of {token_in} converts to")
        if explicit is None and (close or q["covers"]) and m < q["debt"]:
            m = q["debt"]
        self._check_balance(token_in, q["amount_in"])
        f_in = amount_of(self.client, token_in, info)
        col = amount_of(self.client, pos["token"], info)
        pair = pair_amount_of(info)
        self._ensure_allowance(token_in, self._swap_address, q["amount_in"])
        tx = self._send(self._swap.functions.repayWith(
            _cs(info.main), lid, token_in, q["amount_in"], m, close, dl), "Swap")
        ev = self._first_event(tx, self._swap, "RepaidWith", "Swap", ("amount_in", "pair_out", "refund"),
                               lambda e: _eq(e["owner"], me) and e["loanId"] == lid)
        outcome, _ = self._repay_outcome(tx, info, lid, pos, col, ("closed", "debt_left", "collateral_released"))
        return {**tx, **outcome, "amount_in": f_in(ev["amountIn"]) if ev else None,
                "pair_out": pair(ev["pairOut"]) if ev else None, "refund": pair(ev["refund"]) if ev else None}

    def quote_repay_leverage_with(self, main: str, loan_id: int, token_in: str, amount_in: Optional[int] = None,
                                  buffer_bps: Optional[int] = None, user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``repay_leverage_with(main, loan_id, token_in, amount_in)`` for ``user`` (default the signer).
        ``amount_in`` None: the ``token_in`` that repays the whole debt (the least amount quoting it, plus ``buffer_bps``,
        default 0.5%, trimmed to the route's ``max_in`` / ``max_out`` caps, or to what other swaps left of them in the
        current window on Robinhood Chain). Quoted on the hop executor trading uses. Checks the route ("no executor" / "no
        route"; a debt, or an explicit ``amount_in``, over the caps or over what is left of them now: "above cap").
        Returns the repay-with-quote dict (module docstring)."""
        lid = to_uint(loan_id, "loanId")
        if buffer_bps is not None:
            validate_slippage_bps(buffer_bps, "bufferBps")
        token_in = to_address(token_in, "tokenIn")
        given = to_address(user, "user") if user is not None else None
        info = self.client.mains.get(main)
        pos = read_core_loan(self.client, info.main, given or self._account, lid, True)
        if not pos["active"]:
            raise ValidationError("Loan not active", f"Leverage {lid} is not active.")
        return self._repay_with_quote(info, lid, pos, token_in, amount_in, buffer_bps)

    def get_leverage_extension_quote(self, main: str, loan_id: int, number_of_days: Number, pay_in_pair_asset: bool,
                                     refinance: bool, user: Optional[str] = None) -> Dict[str, Any]:
        """Preview ``extend_leverage(main, loan_id, number_of_days, pay_in_pair_asset, refinance)`` for ``user``
        (default the signer). Returns {possible, fee, extra_out}; fee / extra_out raw pair asset (extra_out = paid out by
        a refinance). Both flags are never possible: extend_leverage refuses them. On a paused pair
        (extend_leverage refuses "unknown pair"), or while the issuer of the pair asset has paused it ("pair asset
        paused"), ``possible`` is False; the figures stay."""
        who = to_address(user, "user") if user is not None else self._account
        lid, days = to_uint(loan_id, "loanId"), to_uint(number_of_days, "numberOfDays")
        pay, refi = to_bool(pay_in_pair_asset, "payInPairAsset"), to_bool(refinance, "refinance")
        if pay and refi:
            return {"possible": False, "fee": 0, "extra_out": 0}
        main_addr = self.client.mains.address_of(main)
        q = self._leverage_extension_quote(main_addr, (who, lid, days, True, pay, refi))
        possible = q["possible"] and not is_asset_paused(self.client, self.client.mains.get(main_addr).pair_asset)
        return {**q, "possible": possible}

    def _leverage_extension_quote(self, main_addr: str, args: Tuple[str, int, int, bool, bool, bool]) -> Dict[str, Any]:
        """The extension preview on ``main_addr`` (checked arguments), ``possible`` False on a paused pair. The issuer
        pause is not read: ``get_leverage_extension_quote`` adds it, ``extend_leverage`` refuses it first. TS:
        ``leverageExtensionQuote``."""
        user, lid, days, is_leverage, pay, refi = args
        c = self._contract(main_addr, "MainToken")
        possible, fee, extra_out = c.functions.extensionEligibility(user, lid, days, is_leverage, pay, refi).call()
        paused = self._paused_mains([main_addr])
        return {"possible": bool(possible) and not paused, "fee": fee, "extra_out": extra_out}

    def claim_leverage_liquidation(self, main: str, loan_id: int) -> Dict[str, Any]:
        """Claim the tokens left over after the signer's leverage position was liquidated. A leftover still unclaimed
        365 days after the liquidation can be taken. Returns the tx dict plus ``claimed`` (an ``Amount`` in the
        collateral token)."""
        lid = to_uint(loan_id, "loanId")
        main_addr = self.client.mains.address_of(main)
        me = self._account
        pos = read_core_loan(self.client, main_addr, me, lid, True)
        if pos["active"]:
            raise ValidationError("Loan is still active", f"Leverage {lid} is still active.")
        if pos["liquidation_claim"] == 0:
            raise ValidationError("Nothing to claim", f"Nothing to claim on leverage {lid}.")
        col = amount_of(self.client, pos["token"])
        c = self._contract(main_addr, "MainToken")
        tx = self._send(c.functions.claimLiquidation(lid, True), "MainToken")
        ev = self._first_event(tx, c, "LiquidationClaimed", "MainToken", ("claimed",),
                               lambda e: _eq(e["user"], me) and e["loanId"] == lid and e["isLeverage"])
        return {**tx, "claimed": col(ev["amount"]) if ev else None}

    def get_leverage_count(self, main: Optional[str] = None, user: Optional[str] = None) -> int:
        """Number of leverage positions ``user`` (default the signer) opened on ``main`` (default the default main).
        Ids run 1..count."""
        given = to_address(user, "user") if user is not None else None
        addr = self.client.mains.default_address() if main is None else self.client.mains.address_of(main)
        return self._contract(addr, "MainToken").functions.leverageCount(given or self._account).call()

    def get_leverage_position(self, main: str, user: str, loan_id: int) -> Dict[str, Any]:
        """Leverage position ``loan_id`` of ``user`` on ``main`` (see ``core/loans.py`` for the keys)."""
        who = to_address(user, "user")
        return read_core_loan(self.client, self.client.mains.address_of(main), who, to_uint(loan_id, "loanId"), True)


    def _route_token(self, token: str) -> Dict[str, Any]:
        """How the router sees a token: {address, kind: 'main' | 'token' | 'pair', main, pair} ('pair': a pair asset or
        an outside token; main / pair None for it, and for a factory token on an unknown main)."""
        token = _cs(token)
        mains = self.client.mains
        if mains.is_main(token):
            info = mains.get(token)
            return {"address": token, "kind": "main", "main": _cs(info.main), "pair": _cs(info.pair_asset)}
        if self.client.factory.is_ecosystem_token(token):
            main = _cs(self._contract(token, "EcosystemToken").functions.quoteToken().call())
            if not mains.is_main(main):
                return {"address": token, "kind": "token", "main": None, "pair": None}
            return {"address": token, "kind": "token", "main": main, "pair": _cs(mains.get(main).pair_asset)}
        return {"address": token, "kind": "pair", "main": None, "pair": None}

    def _cross_formats(self, path: Sequence[str], a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        """Formatters for a ``swap_cross`` result, resolved before sending: {in: path[0], out: the last token, sell: the
        sell side's unit (the token after its last sell step: the main or its pair asset), buy: the buy side's (the token
        before its first buy step)}. A side the route does not have is formatted in path[0] (sell) / the last token
        (buy); its figures are 0. ``rewards``: the pair asset of path[0]'s main when the route sells that factory token
        (its holder rewards are paid out by the sale), else None. ``a`` / ``b``: how the router sees path[0] / the last
        token (``_route_token``)."""
        n = len(path) - 1
        sell_unit = path[0]
        sells_token = a["kind"] == "token" and bool(a["main"]) and _eq(path[1], a["main"])
        if sells_token:
            sell_unit = path[2] if n >= 2 and a["pair"] and _eq(path[2], a["pair"]) else path[1]
        elif a["kind"] == "main" and a["pair"] and _eq(path[1], a["pair"]):
            sell_unit = path[1]
        buy_unit = path[n]
        if b["kind"] == "token" and b["main"] and _eq(path[n - 1], b["main"]):
            buy_unit = path[n - 2] if n >= 2 and b["pair"] and _eq(path[n - 2], b["pair"]) else path[n - 1]
        elif b["kind"] == "main" and b["pair"] and _eq(path[n - 1], b["pair"]):
            buy_unit = path[n - 1]
        return {"in": amount_of(self.client, path[0]), "out": amount_of(self.client, path[n]),
                "sell": amount_of(self.client, sell_unit), "buy": amount_of(self.client, buy_unit),
                "rewards": pair_amount_of(self.client.mains.get(a["main"])) if sells_token else None}

    def _send_deadline(self, explicit: Optional[int]) -> int:
        """The deadline to send (unix seconds, chain time): ``explicit``, refused ("Deadline") when it is before the latest
        block time (``check_deadline``), else the latest block time + 600 s."""
        now = self._pre_send_read(lambda: self.web3.eth.get_block("latest"))["timestamp"]
        return now + 600 if explicit is None else check_deadline(explicit, now)

    def _simulate_hop(self, ex: str, token_in: str, token_out: str, amount_in: int) -> int:
        """``quoteHop`` on the hop executor trading uses (an eth_call), net of the hop fee."""
        c = self._contract(ex, "HopExecutor")
        tx = {"from": self._signer} if self._signer else {}
        try:
            return c.functions.quoteHop(_cs(token_in), _cs(token_out), int(amount_in)).call(tx)
        except Exception as e:  # noqa: BLE001
            raise decode_revert(e, contract="HopExecutor", function_name="quoteHop", abi=c.abi) from e

    def _token_main(self, token: str) -> Tuple[MainInfo, bool]:
        main = self.client.mains.main_of(token)
        info = self.client.mains.get(main)
        return info, _eq(main, token)

    def _paused_mains(self, tokens: Sequence[str]) -> List[str]:
        """The tokens of ``tokens`` that are mains with their pair paused, read live. Trading then treats such a main as
        an outside token ("unknown pair", "Not main", "no BASIS leg")."""
        fns = self._swap.functions
        out = []
        for t in tokens:
            pair_asset, _, active = fns.pairs(_cs(t)).call()
            if not is_zero_address(pair_asset) and not active:
                out.append(_cs(t))
        return out

    def _check_pairs_active(self, tokens: Sequence[str]) -> None:
        """Refuses a route through a paused main ("unknown pair")."""
        paused = self._paused_mains(tokens)
        if paused:
            raise _paused_error(paused[0])

    def _check_plain_route(self, fn: str, path: Sequence[str]) -> List[Any]:
        """A plain buy / sell path, from the pause check's reads (no other read): a paused main first ("unknown pair"),
        then the path's direction (``_plain_path_error``). Returns each token's pair row (pair asset, staking, active; a
        zero pair asset for a token that is not a main)."""
        fns = self._swap.functions
        pairs = [fns.pairs(_cs(t)).call() for t in path]
        for t, (pair_asset, _, active) in zip(path, pairs):
            if not is_zero_address(pair_asset) and not active:
                raise _paused_error(_cs(t))
        err = _plain_path_error(fn, path, [_cs(p[0]) if p[2] else None for p in pairs])
        if err is not None:
            raise err
        return pairs

    def _refuse_paused_trade(self, main: str, asset: str, moves: Optional[Dict[str, Any]]) -> None:
        """"pair asset paused" for a trade on the main ``main`` while the issuer of its pair asset ``asset`` has paused it,
        before any approval. ``moves`` None: the trade moves the asset itself (it is on the path: paid in, paid out, or
        swapped on an outside hop; ``_TRADE_PAUSED``). Otherwise ({fees, sold}) the trade stays between the main and
        factory tokens on it, and the asset moves only as a fee (``fees``, each side's rate for the signer as a
        callable: refused, ``_TRADE_PAUSED``, when one is above 0) or as the signer's pending holder rewards on the
        factory token it sells (``sold``: a sale pays them out in the pair asset, fee or not; refused,
        ``_rewards_paused``, when the signer has rewards to claim). All read only while the asset is paused, in that
        order, and only until one refuses. TS: ``refusePausedTrade``."""
        if not is_asset_paused(self.client, asset):
            return
        if moves is None or any(rate() > 0 for rate in moves["fees"]):
            raise pair_asset_paused(asset, self.client.mains.get(main).pair_symbol, _TRADE_PAUSED)
        sold = moves["sold"]
        if sold is not None and self._claimable_rewards(sold, self._account) > 0:
            raise pair_asset_paused(asset, self.client.mains.get(main).pair_symbol, _rewards_paused(sold))

    def _refuse_paused_plain(self, path: Sequence[str], pairs: Sequence[Any]) -> None:
        """``_refuse_paused_trade`` for a plain path, from ``_check_plain_route``'s rows (``pairs``): the path trades on
        one main. Off the path, the asset moves only on a trade between the main and a factory token on it: as its fee
        (the signer's fee rate on the token), or, on a sale of the token (``[token, main]``), as the signer's holder
        rewards. TS: ``refusePausedPlain``."""
        i = next((j for j, row in enumerate(pairs) if not is_zero_address(row[0])), None)
        if i is None:
            return
        asset = _cs(pairs[i][0])
        token = next((t for j, t in enumerate(path) if j != i), None)
        if any(_eq(t, asset) for t in path) or token is None:
            self._refuse_paused_trade(path[i], asset, None)
            return
        me = self._account
        self._refuse_paused_trade(path[i], asset, {"fees": [lambda: self.client.taxes.get_tax_rate(token, me)],
                                                   "sold": token if i > 0 else None})

    def _refuse_paused_cross(self, path: Sequence[str], rt: Sequence[Dict[str, Any]]) -> None:
        """``_refuse_paused_trade`` for each main a cross route trades on (``rt``: how the router sees each token of
        ``path``; the route's steps already checked, ``check_cross_plan``). Off the path, a main's pair asset moves only
        as the fee of a side on that main (``cross_sides``: each side's own fee rate for the signer) or as the signer's
        holder rewards on the factory token the route sells on it. A main traded only on the outside exchange
        moves nothing of its pair asset (the outside exchange's own route aside). TS: ``refusePausedCross``."""
        sides = cross_sides(path, rt)
        sell, buy, split = sides["sell"], sides["buy"], sides["split"]
        me = self._account
        taxes = self.client.taxes

        def rate_of(token: str, side: str) -> Callable[[], int]:
            def read() -> int:
                if not split:
                    if side == "buy" and sell is not None:
                        return taxes.get_tax_rate_base(token, me, 0)
                    return taxes.get_tax_rate(token, me)
                base = taxes.get_base_rate()
                half = base // 2
                return taxes.get_tax_rate_base(token, me, half if side == "sell" else base - half)
            return read

        done = set()
        for t in rt:
            if not t["main"] or not t["pair"] or t["main"].lower() in done:
                continue
            done.add(t["main"].lower())
            main, asset = t["main"], t["pair"]
            if any(_eq(x, asset) for x in path):
                self._refuse_paused_trade(main, asset, None)
                continue
            sell_side = sell if sell is not None and _eq(sell["main"], main) else None
            buy_side = buy if buy is not None and _eq(buy["main"], main) else None
            fees: List[Callable[[], int]] = []
            if sell_side:
                fees.append(rate_of(sell_side["token"] or main, "sell"))
            if buy_side:
                fees.append(rate_of(buy_side["token"] or main, "buy"))
            self._refuse_paused_trade(main, asset, {"fees": fees, "sold": sell_side["token"] if sell_side else None})

    def _claimable_rewards(self, token: str, user: str) -> int:
        """The holder rewards (raw pair asset) a sale of the factory token ``token`` pays ``user`` now. TS:
        ``claimableRewards``."""
        return self._contract(token, "FactoryToken").functions.getClaimableRewards(_cs(user)).call()

    def _stake_min(self, info: MainInfo, staking: str, path: Sequence[str], amount: int, explicit: Optional[int],
                   slippage_bps: Optional[int]) -> int:
        """The minimum of a buy of the main ``info`` staked in its vault ``staking`` (``buy_and_stake``, ``buy_tokens``
        with ``wrap_tokens``): the vault stakes at least its ``minStake``, so a quote under it is refused ("Below min
        stake"), an explicit minimum above the quote too ("Min out"), and the minimum sent (explicit or derived) is raised
        to ``minStake``, so the slippage guard covers the stake too."""
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        quoted = self.quote(path, amount)
        min_stake = self._contract(staking, "Vault").functions.minStake().call()
        if quoted < min_stake:
            raise ValidationError(
                "Below min stake",
                f"{amount} raw {info.pair_symbol} buys {quoted} raw {info.symbol}, under the vault's minimum stake "
                f"{min_stake}: buy more.")
        m = self._resolve_min(explicit, slippage_bps, lambda: quoted, field="minOut")
        check_explicit_min(explicit, quoted, "minOut", "what this buy stakes")
        return min_stake if m < min_stake else m

    def _trade_min(self, explicit: Optional[int], slippage_bps: Optional[int], quote, what: str) -> int:
        """The minimum of a plain trade or a cross swap, from the on-chain quote ``quote`` (exact in the current state),
        read with an explicit minimum too (one eth_call): the trade's run-time refusals ("autovest presale", "Trading is
        not enabled", "No WL for freeze", "Overcapped on WL buying", ...) then come from the quote, before any approval, and
        an explicit minimum above it is refused ("Min out", ``what`` names the output). Explicit: as given (never 0,
        refused before the read); None: the quote minus the slippage."""
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        if explicit is not None:
            self._resolve_min(explicit, slippage_bps, lambda: 0, field="minOut")
        quoted = self._pre_send_read(quote)
        check_explicit_min(explicit, quoted, "minOut", what)
        return self._resolve_min(explicit, slippage_bps, lambda: quoted, field="minOut")

    def _send_trade(self, fn: str, amount: int, m: int, path: List[str], wrap: bool) -> Dict[str, Any]:
        """Checks, approves, sends a plain trade and decodes its figures (trader = the signer). A buy of an auto-vest
        token in presale (read before sending) also expects the signer's new vesting."""
        me = self._account
        out = path[-1]
        vest: Optional[Tuple[bool, int, str]] = None
        if fn == "buyTokens" and not self.client.mains.is_main(out):
            f = self._contract(out, "FactoryToken").functions
            if f.autoVest().call() and not f.presaleComplete().call():
                vest = (f.gradualAutoVesting().call(), f.autoVestDuration().call(), self._swap.functions.vesting().call())
        rewards_of = None
        if fn == "sellTokens" and not self.client.mains.is_main(path[0]) and self.client.mains.is_main(path[1]):
            rewards_of = pair_amount_of(self.client.mains.get(path[1]))
        self._check_balance(path[0], amount)
        f_in, f_out = amount_of(self.client, path[0]), amount_of(self.client, out)
        self._ensure_allowance(path[0], self._swap_address, amount)
        if fn == "buyTokens":
            tx = self._send(self._swap.functions.buyTokens(amount, m, path, wrap), "Swap")
        else:
            tx = self._send(self._swap.functions.sellTokens(amount, m, path, False), "Swap")
        ev = self._first_event(tx, self._swap, "Traded", "Swap", ("amount_in", "amount_out", "tax"),
                               lambda e: _eq(e["trader"], me))
        auto_vested = None
        if vest is not None:
            vc = self._first_event(tx, self._contract(vest[2], "Vesting"), "VestingCreated", "Vesting", ("auto_vested",),
                                   lambda e: _eq(e["beneficiary"], me) and _eq(e["token"], out))
            if vc:
                ends_at = None
                block_number = tx["receipt"]["blockNumber"]
                try:
                    ends_at = self.web3.eth.get_block(block_number)["timestamp"] + vest[1] * 86_400
                except Exception as e:  # noqa: BLE001
                    msg = (f"Block {block_number} of {tx['hash']} could not be read ({redact_urls(e)}): auto_vested.ends_at is null. "
                           "The transaction is confirmed: do not resend it.")
                    tx["warnings"].append(msg)
                    logger.warning("[basis-sdk] %s", msg)
                auto_vested = {"vesting_id": vc["vestingId"], "amount": f_out(vc["totalAmount"]), "gradual": bool(vest[0]),
                               "ends_at": ends_at}
        f_tax = f_in if fn == "buyTokens" else f_out
        return {**tx, "amount_in": f_in(ev["amountIn"]) if ev else None, "amount_out": f_out(ev["amountOut"]) if ev else None,
                "tax": f_tax(ev["tax"]) if ev else None, "auto_vested": auto_vested,
                "rewards_claimed": self._rewards_paid(tx, path[0], rewards_of) if rewards_of else None}

    def _rewards_paid(self, tx: Dict[str, Any], token: str, fmt) -> Amount:
        """The holder rewards ``token`` paid the signer in ``tx`` (from its own logs: a sale of a factory token pays the
        seller's pending rewards first), formatted by ``fmt`` (its main's pair asset). 0 when none were
        owed: the token then emits nothing, so no missing-event warning. Never raises: the tx is confirmed."""
        me = self._account
        t = self._contract(token, "FactoryToken")
        return fmt(sum(e["claimableNow"] for e in self._events(tx, t, "RewardsClaimed") if _eq(e["investor"], me)))

    def _first_event(self, tx: Dict[str, Any], contract: Any, event_name: str, contract_name: str,
                     fields: Sequence[str], match=lambda e: True) -> Optional[Dict[str, Any]]:
        """The first ``event_name`` log of ``tx`` emitted by ``contract`` that ``match``es, decoded; or None with a
        missing-event warning naming ``fields`` (the result fields left None). Never raises: the tx is confirmed."""
        ev = next((e for e in self._events(tx, contract, event_name) if match(e)), None)
        if ev is None:
            add_missing_event_warning(tx, contract_name, event_name, fields)
        return ev

    def _repay_outcome(self, tx: Dict[str, Any], info: MainInfo, loan_id: int, pos: Dict[str, Any], col,
                       fields: Sequence[str]) -> Tuple[Dict[str, Any], Optional[Amount]]:
        """How a repay of leverage ``loan_id`` on ``info``'s main ended: ({closed, debt_left, collateral_released},
        amount taken), ``Amount``s (``col`` formats the collateral): a partial repay's figures, or a full repay (the debt
        and collateral ``pos`` held, all taken and sent back). Neither: Nones and a warning naming ``fields``."""
        me = self._account
        pair = pair_amount_of(info)
        c = self._contract(info.main, "MainToken")
        part = next((e for e in self._events(tx, c, "LoanPartlyRepaid")
                     if _eq(e["user"], me) and e["loanId"] == loan_id and e["isLeverage"]), None)
        if part:
            return {"closed": False, "debt_left": pair(part["fullAmountLeft"]),
                    "collateral_released": col(part["collateralReleased"])}, pair(part["amount"])
        full = next((e for e in self._events(tx, c, "LoanRepaid") if _eq(e["user"], me) and e["loanId"] == loan_id), None)
        if full:
            return ({"closed": True, "debt_left": pair(0), "collateral_released": col(pos["collateral_amount"])},
                    pair(pos["full_amount"]))
        add_missing_event_warning(tx, "MainToken", "LoanPartlyRepaid / LoanRepaid", fields)
        return {"closed": None, "debt_left": None, "collateral_released": None}, None

    @staticmethod
    def _check_plain_path(path: Sequence[str]) -> None:
        if len(path) not in (2, 3):
            raise ValidationError(
                "Bad path",
                f"buyTokens/sellTokens take a path of 2 or 3 tokens, got {len(path)} (use swapCross for longer routes).",
            )

    def _check_balance(self, token: str, amount: int) -> None:
        bal = self._balance_of(token)
        if bal < amount:
            raise ValidationError("balance", f"Balance of {token} is {bal}, below the {amount} needed.")

    def _days_error(self, main: str, days: int) -> Optional[ValidationError]:
        """The main's day range check for a new position ("incorrect loan duration"), or None when ``days`` is in
        range."""
        fns = self._contract(main, "MainToken").functions
        min_d = fns.minDaysLoan().call()
        max_d = fns.maxDaysLoan().call()
        if days < min_d or days > max_d:
            return ValidationError("incorrect loan duration", f"Days must be {min_d}-{max_d} on this main, got {days}.")
        return None

    def _leverage_path(self, target: Union[str, Sequence[str]]) -> List[str]:
        """Leverage path from a token or an explicit path, refused in order: at least 2 tokens with path[1] a main
        (paused or not) and path[0] its pair asset ("wrong path"), then a paused main ("unknown pair"), then 2 or 3
        tokens and a listed factory token at path[2] (a delisted token cannot be leveraged) trading against that main
        ("wrong path")."""
        explicit = not isinstance(target, str)
        if not explicit:
            path = self.build_buy_path(target, "pairAsset")
        else:
            if isinstance(target, (list, tuple)) and len(target) < 2:
                raise ValidationError("wrong path", f"A leverage path has 2 or 3 tokens, got {len(target)}.")
            path = _to_path(target)
            if not self.client.mains.is_main(path[1]):
                raise ValidationError("wrong path", f"path[1] ({path[1]}) is not a main.")
            info = self.client.mains.get(path[1])
            if not _eq(path[0], info.pair_asset):
                raise ValidationError("wrong path", f"path[0] must be the main's pair asset {info.pair_asset}.")
        self._check_pairs_active([path[1]])
        if len(path) not in (2, 3):
            raise ValidationError("wrong path", f"A leverage path has 2 or 3 tokens, got {len(path)}.")
        if len(path) == 3:
            if not self.client.factory.is_ecosystem_token(path[2]):
                raise ValidationError("wrong path", f"{path[2]} is not a listed factory token: leverage is open on listed "
                                                    "tokens only.")
            if explicit:
                q = self._contract(path[2], "EcosystemToken").functions.quoteToken().call()
                if not _eq(q, path[1]):
                    raise ValidationError("wrong path", f"{path[2]} does not trade against main {path[1]}.")
        return path

    def _leverage_blocks(self, path: Sequence[str], q: Dict[str, Any], who: str) -> List[ValidationError]:
        """Why ``leverage_buy`` / ``mixed_buy`` would refuse the buy quoted in ``q`` for ``who`` now (the quote's
        ``blocked``), in order, each as the refusal the write raises: for a factory token, auto-vest needs a finished
        presale ("In presale"), a frozen token needs ``who`` on the whitelist ("No WL for freeze") and room under its
        presale cap for the position's collateral ("WL cap"); then "Under 2x" (under 2x leverage), the main's own trading
        gate ("Trading is not enabled", read live) and the issuer pause of the pair asset the buy is paid in,
        ``path[0]`` ("pair asset paused"). The days range is ``_quote_on``'s."""
        collateral = q["sim"]["total_collateral"]
        out = self._factory_leverage_blocks(path[2], collateral, who) if len(path) == 3 else []
        enabled = self._contract(path[1], "MainToken").functions.tradingEnabled().call()
        if not q["spot"] * 2 < collateral:
            out.append(ValidationError(
                "Under 2x",
                f"This leverage would be under 2x (collateral {collateral} vs spot {q['spot']}); "
                "try more days, a higher leverage % or a different amount."))
        if not enabled:
            out.append(ValidationError("Trading is not enabled",
                                       f"Trading on main {path[1]} has not opened yet: no leverage until it does."))
        if is_asset_paused(self.client, path[0]):
            out.append(pair_asset_paused(path[0], self.client.mains.get(path[1]).pair_symbol,
                                         "no leverage buy can be paid in it"))
        return out

    def _factory_leverage_blocks(self, token: str, collateral: int, who: str) -> List[ValidationError]:
        """The factory token's part of ``_leverage_blocks``: "In presale", then "No WL for freeze" or "WL cap" (frozen
        token)."""
        fns = self._contract(token, "FactoryToken").functions
        auto_vest = fns.autoVest().call()
        presale_complete = fns.presaleComplete().call()
        frozen = fns.frozen().call()
        out: List[ValidationError] = []
        if auto_vest and not presale_complete:
            out.append(ValidationError("In presale", "Leverage on an auto-vest token needs its presale to be complete."))
        if not frozen:
            return out
        wl = self.client.factory.get_whitelist_status(token, who)
        if not wl["whitelisted"]:
            out.append(ValidationError("No WL for freeze", "This token is frozen and the wallet is not whitelisted."))
        elif wl["bought"] + collateral > wl["max_buy"]:
            out.append(ValidationError(
                "WL cap",
                f"This position takes {collateral} tokens, over what is left of the wallet's presale cap "
                f"({wl['remaining']} of {wl['max_buy']}): use a smaller amount."))
        return out

    def _sellable_position(self, main: str, who: str, loan_id: int) -> Dict[str, Any]:
        """The open leverage position ``loan_id`` of ``who`` on ``main``: "Not user" for a position that is not theirs,
        "Loan not active" for a closed one."""
        pos = read_core_loan(self.client, main, who, loan_id, True)
        if not _eq(pos["user"], who):
            raise ValidationError("Not user", f"No leverage position {loan_id} of {who} on main {main} (hub loans are "
                                              "sold through loans.partial_sell_loan).")
        if not pos["active"]:
            raise ValidationError("Loan not active", f"Position {loan_id} is not active.")
        return pos

    def _quote_on(self, path: List[str], amount: int, asked: int, pct: int,
                  user: Optional[str]) -> Tuple[Dict[str, Any], Optional[ValidationError]]:
        """Quote on a checked path. The term: with an open position on the token, its days left (at least the main's
        minimum, else "Extend first"); otherwise the asked days. A new position's day range is checked last, so its
        refusal comes back as the second item for the caller to raise in that order (and in place of a simulation that
        fails on out-of-range days). The simulation is priced for ``user``, as the open is: without one, the zero
        address (the list rate)."""
        main = path[1]
        open_ = self.get_open_leverage(main, path[-1], user) if user else None
        days = asked
        days_error: Optional[ValidationError] = None
        if open_:
            min_d = self._contract(main, "MainToken").functions.minDaysLoan().call()
            if open_["days_left"] < min_d:
                raise ValidationError(
                    "Extend first",
                    f"The open position {open_['loan_id']} on this token has {open_['days_left']} days left, under the "
                    f"minimum {min_d}: extend it before adding to it.",
                )
            days = open_["days_left"]
        else:
            days_error = self._days_error(main, asked)
        trader = user or ZERO_ADDRESS
        try:
            sim = self.client.leverage_simulator.simulate(amount, path, days, pct, trader)
            spot = self.get_amounts_out(amount, path)
            tax_bps = self.client.taxes.get_tax_rate(path[-1], trader)
        except Exception:
            if days_error is not None:
                raise days_error
            raise
        return {"path": list(path), "leverage_pct": pct, "days": days,
                "open_loan_id": open_["loan_id"] if open_ else 0, "trader": trader, "tax_bps": tax_bps, "sim": sim,
                "spot": spot}, days_error

    def _repay_preview(self, main: str, loan_id: int, pos: Dict[str, Any], amount: int) -> Dict[str, Any]:
        minimum_loan = self._contract(main, "MainToken").functions.minimumLoan().call()
        debt = pos["full_amount"]
        full = amount >= debt
        pay = debt if full else amount
        coll = pos["collateral_amount"]
        released = coll if full else (0 if debt == 0 else coll * pay // debt)
        active = bool(pos["active"])
        return {
            "loan_id": int(loan_id), "debt": debt, "minimum_loan": minimum_loan,
            "max_partial": debt - minimum_loan if debt > minimum_loan else 0,
            "amount": pay, "full": full, "active": active,
            "allowed": active and pay > 0 and (full or debt - pay >= minimum_loan),
            "collateral_released": released, "collateral_left": coll - released, "debt_left": debt - pay,
        }

    def _repay_with_route(self, info: MainInfo, token_in: str) -> Dict[str, Any]:
        """How ``token_in`` converts to the pair asset of ``info`` on the hop executor trading uses: {direct, ex,
        quote}: itself (``direct``), or ``quoteHop`` on the executor ``ex``, route
        checked ("no executor", "no route"). TS: ``repayWithRoute``."""
        asset = info.pair_asset
        if _eq(token_in, asset):
            return {"direct": True, "ex": None, "quote": lambda x: x}
        ex = self.get_hop_executor()
        if is_zero_address(ex):
            raise ValidationError("no executor", f"SWAP has no outside-route executor: repay in {asset}.")
        if len(self._contract(ex, "HopExecutor").functions.getRoute(_cs(token_in), _cs(asset)).call()) == 0:
            raise ValidationError("no route", f"No outside route from {token_in} to {asset}: repay in {asset} or "
                                              "another token.")
        quote: Callable[[int], int] = lambda x: self._simulate_hop(ex, token_in, asset, x)  # noqa: E731
        return {"direct": False, "ex": ex, "quote": quote}

    def _repay_with_quote(self, info: MainInfo, loan_id: int, pos: Dict[str, Any], token_in: str,
                          amount_in: Optional[int], buffer_bps: Optional[int],
                          route: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Repay-with quote on the hop executor trading uses; ``route`` from ``_repay_with_route`` (read when not
        given)."""
        asset = info.pair_asset
        debt = pos["full_amount"]
        r = route if route is not None else self._repay_with_route(info, token_in)
        direct, ex, quote = r["direct"], r["ex"], r["quote"]
        if amount_in is not None:
            x = to_uint(amount_in, "amountIn")
            if x == 0:
                raise ValidationError("Zero amount", "amountIn must be greater than 0.")
            pair_out = x if direct else quote_hop_within_caps(self.client, ex, token_in, asset, x, quote)
        else:
            if direct:
                x = debt
            else:
                x = self._size_for(ex, token_in, asset, debt, quote,
                                   REPAY_LEVERAGE_WITH_BUFFER_BPS if buffer_bps is None else buffer_bps)
            pair_out = quote(x)
        covers = pair_out >= debt
        coll = pos["collateral_amount"]
        released = coll if covers else (0 if debt == 0 else coll * pair_out // debt)
        return {
            "loan_id": int(loan_id), "token_in": _cs(token_in), "pair_asset": asset, "debt": debt, "amount_in": x,
            "pair_out": pair_out, "covers": covers, "refund": pair_out - debt if covers else 0,
            "collateral_released": released,
        }

    def _size_for(self, ex: str, token_in: str, asset: str, target: int, quote, buffer_bps: int) -> int:
        """``token_in`` to send for ``target`` of ``asset``: the least amount quoting it, plus ``buffer_bps``, within what
        ``ex``'s route takes now (on Robinhood Chain, what other swaps left of its caps in the current window)."""
        return size_repay_with(quote, target, {**read_repay_sizing(self.client, ex, token_in, asset),
                                               "buffer_bps": buffer_bps})


def _plan_route(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The route between two classified tokens, with its sell and buy steps (``[token_in]`` / ``[token_out]`` alone when
    a side has none); None when none exists. Hop order: sell token, sell main, outside, buy main, buy token."""
    if _eq(a["address"], b["address"]):
        return None
    if a["kind"] == "pair" and b["kind"] == "pair":
        return None

    def direct(path, sell, buy):
        return {"path": path, "uses_outside": False, "outside_hop": None, "sell": sell, "buy": buy}

    if a["kind"] == "token" and b["kind"] == "token" and a["main"] and b["main"] and _eq(a["main"], b["main"]):
        return direct([a["address"], a["main"], b["address"]], [a["address"], a["main"]], [a["main"], b["address"]])
    if a["kind"] == "token" and b["kind"] == "main" and a["main"] and _eq(a["main"], b["address"]):
        return direct([a["address"], b["address"]], [a["address"], b["address"]], [b["address"]])
    if a["kind"] == "main" and b["kind"] == "token" and b["main"] and _eq(b["main"], a["address"]):
        return direct([a["address"], b["address"]], [a["address"]], [a["address"], b["address"]])

    if a["kind"] == "token":
        if not a["main"] or not a["pair"]:
            return None
        left = [a["address"], a["main"], a["pair"]]
    elif a["kind"] == "main":
        left = [a["address"], a["pair"]]
    else:
        left = [a["address"]]
    if b["kind"] == "token":
        if not b["main"] or not b["pair"]:
            return None
        right = [b["pair"], b["main"], b["address"]]
    elif b["kind"] == "main":
        right = [b["pair"], b["address"]]
    else:
        right = [b["address"]]

    joint = left[-1]
    if _eq(joint, right[0]):
        path = left + right[1:]
        if any(_eq(path[i], path[i + 2]) for i in range(len(path) - 2)):
            return None
        return direct(path, left, right)
    path = left + right
    if len({x.lower() for x in path}) != len(path):
        return None
    return {"path": path, "uses_outside": True, "outside_hop": [joint, right[0]], "sell": left, "buy": right}


def _to_path(path: Any) -> List[str]:
    """A caller's path, every token through ``to_address`` (rule ``path``), checksummed."""
    if isinstance(path, str) or not isinstance(path, (list, tuple)):
        raise ValidationError("path", f"path must be a list of token addresses, got {path!r}.")
    return [to_address(t, "path") for t in path]


def _swap_block_reason(e: Exception) -> str:
    """A reverted quote read as a ``quote_swap`` block reason (the contract's reason, the whitelist / gate ones
    renamed); anything else is re-raised."""
    reason = decode_revert(e).reason
    if not reason:
        raise e
    if reason == "No WL for freeze":
        return "not whitelisted"
    if reason == "Trading is not enabled":
        return "trading not enabled"
    return reason


def _check_lev_pct(v: Any, max_pct: int) -> int:
    """A whole-number leverage % in 1..max_pct (rule "Range")."""
    if not _is_int(v) or v < 1 or v > max_pct:
        raise ValidationError("Range", f"The leverage % must be a whole number 1-{max_pct}, got {v}.")
    return int(v)


def _check_amount(amount: Any, field: str) -> int:
    """A raw amount argument as int (``to_uint`` under ``field``), refused when 0 (rule 'amount')."""
    v = to_uint(amount, field)
    if v == 0:
        raise ValidationError("amount", "Amount must be greater than 0.")
    return v


def _check_sell_pct(v: Any) -> int:
    """A partial-sell percentage, in order: a whole multiple of 10 ("% by 10"), then 10..100 ("% range")."""
    if not _is_int(v) or int(v) % 10 != 0:
        raise ValidationError("% by 10", f"percentage must be a whole multiple of 10, got {v}.")
    if v < 10 or v > 100:
        raise ValidationError("% range", f"percentage must be 10-100, got {int(v)}.")
    return int(v)


def _paused_error(main: str) -> ValidationError:
    """The refusal for a main whose pair is paused ("unknown pair")."""
    return ValidationError(
        "unknown pair",
        f"Main {main} is paused on SWAP (pairs(main).active is false): it cannot be traded or leveraged through SWAP "
        "until it is reactivated (repay_leverage on the main still works; extend_leverage does not).")


def _plain_path_error(fn: str, path: Sequence[str], asset_of: Sequence[Optional[str]]) -> Optional[ValidationError]:
    """The direction rules for a plain 2- or 3-token path, as far as ``asset_of`` tells them: the pair asset of each
    live main on the path, None for any other token. The first rule broken, named by the contract's own reason, or None.
    A factory token's own main is left to the contract (it needs a read)."""
    def is_pair_asset(i: int) -> bool:
        return asset_of[i] is None and any(a is not None and _eq(a, path[i]) for a in asset_of)

    last = len(path) - 1
    first, out = path[0], path[last]
    if fn == "buyTokens":
        if is_pair_asset(last):
            return ValidationError("Alien output", f"A buy ends in a main or a factory token, but the path ends at the pair "
                                                   f"asset {out}: to sell, use sell_tokens.")
        if len(path) == 2:
            if asset_of[1] is not None:
                return None if _eq(first, asset_of[1]) else ValidationError(
                    "Bad pair", f"A buy of the main {out} starts at its pair asset {asset_of[1]}, not at {first}.")
            return None if asset_of[0] is not None else ValidationError(
                "Bad pair", f"A buy of the factory token {out} starts at its main, and {first} is not a main.")
        if asset_of[1] is None:
            return ValidationError("unknown pair", f"A 3-token buy goes through a main, and {path[1]} is not a main on SWAP.")
        if not _eq(first, asset_of[1]):
            return ValidationError("Not pair asset", f"A 3-token buy starts at the pair asset {asset_of[1]} of {path[1]}, "
                                                     f"not at {first}.")
        return None if asset_of[2] is None else ValidationError(
            "Not ecosystem", f"A 3-token buy ends in a factory token, and {out} is a main.")
    if is_pair_asset(0):
        return ValidationError("Wrong token", f"A sell starts at a main or a factory token, but the path starts at the pair "
                                              f"asset {first}: to buy, use buy_tokens.")
    if len(path) == 2:
        if asset_of[0] is not None:
            return None if _eq(out, asset_of[0]) else ValidationError(
                "Bad pair", f"A sell of the main {first} ends at its pair asset {asset_of[0]}, not at {out}.")
        return None if asset_of[1] is not None else ValidationError(
            "Bad pair", f"A sell of the factory token {first} ends at its main, and {out} is not a main.")
    if asset_of[0] is not None:
        return ValidationError("Not ecosystem", f"A 3-token sell starts at a factory token, and {first} is a main.")
    if asset_of[1] is None:
        return ValidationError("Not MAIN", f"A 3-token sell goes through a main, and {path[1]} is not a main on SWAP.")
    return None if _eq(out, asset_of[1]) else ValidationError(
        "Not pair asset", f"A 3-token sell ends at the pair asset {asset_of[1]} of {path[1]}, not at {out}.")
