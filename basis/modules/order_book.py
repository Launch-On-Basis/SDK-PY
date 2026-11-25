"""The P2P order book of both prediction-market hosts (TypeScript SDK: src/modules/OrderBook.ts).

Sellers list shares at a price in the market's pair asset (locked, not escrowed); buyers fill at or below the live share
price (``get_share_price``) and pay in that pair asset. Every write syncs to ``/api/v1/sync``, which decodes the order
events.

A host is given as ``host="public"`` (``addresses.tranche``), ``"private"`` (``addresses.tranchePrivate``) or a host
address; omit it to detect it (cached per market).

Amounts: shares and pair-asset budgets take an int (raw) or a human str / float / Decimal (shares 18 decimals, budgets
the pair asset's decimals), like the rest of the SDK. A fill (``OrderFilled``) is a dict: order_id, seller, outcome_id,
amount (shares, raw 18 decimals), cost (base price), buyer_paid, seller_net (``Amount``s in the market's pair asset).
Write results: shares stay raw ints (as every read reports them); pair-asset and market-token figures are ``Amount``s.
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from web3 import Web3

from ..config.addresses import ZERO_ADDRESS, is_zero_address
from ..core.base import BaseModule
from ..core.errors import ValidationError
from ..core.amounts import AmountOf, amount_of
from ..core.events import add_missing_event_warning
from ..core.loans import is_asset_paused, pair_asset_paused
from ..core.ranking import whole_number
from ..core.revert import BasisContractError, rpc_error_kind
from ..core.units import (Amount, format_units, max_with_slippage, opt_uint, parse_amount, to_address, to_amount, to_bool,
                          to_uint)

HumanAmount = Union[int, str, float, Decimal]


PRICE_ONE = 10 ** 18
PRICE_MIN = PRICE_ONE // 1000
PRICE_MAX = PRICE_ONE * 1_000_000
READ_BATCH = 50
AMM_DUST = 255

_DECIMAL = re.compile(r"\d*\.?\d+", re.ASCII)


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _is_int(v: Any) -> bool:
    """The SDK's strict whole-number rule (``core.ranking.whole_number``): an int (not bool) or a whole float."""
    return whole_number(v) is not None


def _make_host(address: str, kind: str) -> Dict[str, str]:
    return {
        "address": Web3.to_checksum_address(address),
        "kind": kind,
        "contract": "TrancheMarketPrivate" if kind == "private" else "TrancheMarket",
    }


def _seller_blocked(orders: Sequence[Dict[str, Any]], symbol: str, more: Optional[str] = None) -> ValidationError:
    """The 'sellerBlocked' refusal for ``orders`` (their sellers can't receive ``symbol``), plus ``more`` when given."""
    ids = ", ".join(str(o["order_id"]) for o in orders)
    many = len(orders) > 1
    extra = f" Without {'them' if many else 'it'}: {more}" if more else ""
    return ValidationError(
        "sellerBlocked",
        f"Order{'s' if many else ''} {ids} can't fill: the seller can't receive {symbol} right now (the token refuses "
        f"transfers to that wallet). Pick other orders.{extra}")


def parse_book_price(v: Union[int, str, float, Decimal]) -> int:
    """Book price (pair asset per share) -> 1e18-scaled int, validated: 0 (market price) or 0.001 to 1,000,000.
    int = already scaled by 1e18; str / float / Decimal = a decimal like 1.25."""
    if isinstance(v, bool):
        raise ValidationError("pricePerShare", f"pricePerShare must be a decimal like 0.42 (or a 1e18-scaled int), got {v}.")
    if isinstance(v, int):
        raw = v
    else:
        s = str(v).strip(" \t\n\r\f\v")
        if not _DECIMAL.fullmatch(s):
            raise ValidationError("pricePerShare", f"pricePerShare must be a decimal like 0.42 (or a 1e18-scaled int), got {v}.")
        try:
            raw = int((Decimal(s) * PRICE_ONE).to_integral_value(rounding=ROUND_HALF_UP))
        except InvalidOperation:
            raise ValidationError("pricePerShare", f"pricePerShare must be a decimal like 0.42, got {v}.") from None
    if raw != 0 and (raw < PRICE_MIN or raw > PRICE_MAX):
        raise ValidationError(
            "pricePerShare",
            "Limit price must be 0 (market price) or between 0.001 and 1,000,000 of the pair asset per share, "
            f"got {format_units(raw, 18)}.",
        )
    return raw


class OrderBookModule(BaseModule):
    """The P2P order book of both prediction-market hosts."""

    def __init__(self, client):
        super().__init__(client)
        self._host_cache: Dict[str, Dict[str, str]] = {}


    def list_order(self, market: str, outcome: int, amount: HumanAmount, price_per_share: Union[int, str, float, Decimal],
                   host: Optional[str] = None) -> Dict[str, Any]:
        """List ``amount`` shares of ``outcome`` for sale: int = raw (18 decimals), str/float/Decimal = human shares.
        ``price_per_share``: the pair asset per share, a str/float/Decimal like 1.25, or an int scaled by 1e18. 0 = the
        live share price at fill time; else 0.001 to 1,000,000. A price above the live share price lists, but fills only
        once the live price reaches it. Returns {'hash','receipt','result','warnings','order_id' (from ``OrderCreated``;
        None when the event is missing),'amount','price_per_share'}."""
        market = to_address(market, "market")
        h = self._resolve_host(market, host)
        price = parse_book_price(price_per_share)
        shares = parse_amount(amount, 18, "amount")
        if shares == 0:
            raise ValidationError("empty order", "List at least some shares (amount must be above 0).")
        md = self._market_data(h, market)
        if md["resolved"]:
            raise ValidationError("market resolved", "This market is resolved. Listings are closed.")
        self._check_outcome(h, market, outcome)
        outcome = int(outcome)
        me = self._account
        c = self._host_contract(h)
        held = c.functions.getUserShares(market, me, outcome).call()
        locked = c.functions.sharesLockedInOrders(market, me, outcome).call()
        if held < locked + shares:
            raise ValidationError(
                "unlockedShares", f"Not enough free shares: you hold {held}, {locked} are already listed, {shares} requested."
            )

        tx = self._write(h, c.functions.listOrder(market, outcome, shares, price))
        created = next((e for e in self._events(tx, c, "OrderCreated", h["address"])
                        if _eq(e["marketToken"], market) and _eq(e["seller"], me)), None)
        if created is None:
            add_missing_event_warning(tx, h["contract"], "OrderCreated", ("order_id",))
        return {**tx, "order_id": created["orderId"] if created else None, "amount": shares, "price_per_share": price}

    def cancel_order(self, market: str, order_id: int, host: Optional[str] = None) -> Dict[str, Any]:
        """Cancel one of the signer's active orders (also allowed while paused or after resolution).
        Returns the tx plus {'order_id', 'cancelled' (True once ``OrderCancelled`` is in the logs, else None)}."""
        market = to_address(market, "market")
        h = self._resolve_host(market, host)
        oid = to_uint(order_id, "orderId")
        order = self._read_order(h, market, oid)
        if not _eq(order["seller"], self._account):
            raise ValidationError("orderSeller", f"Order {oid} is not yours.")
        if not order["active"]:
            raise ValidationError("orderActive", f"Order {oid} is already filled or cancelled.")
        c = self._host_contract(h)
        tx = self._write(h, c.functions.cancelOrder(market, oid))
        ev = next((e for e in self._events(tx, c, "OrderCancelled", h["address"])
                   if _eq(e["marketToken"], market) and e["orderId"] == oid), None)
        if ev is None:
            add_missing_event_warning(tx, h["contract"], "OrderCancelled", ("cancelled",))
        return {**tx, "order_id": oid, "cancelled": True if ev else None}

    def buy_order(self, market: str, order_id: int, fill: HumanAmount, max_cost: Optional[int] = None,
                  slippage_bps: Optional[int] = None, host: Optional[str] = None) -> Dict[str, Any]:
        """Fill ``fill`` shares of one order: int = raw (18 decimals), str/float/Decimal = human. Pays in the market's
        pair asset (approved to the host). ``max_cost`` (raw pair asset, buyer tax included: a whole number, never 0,
        ``ValidationError('maxCost')`` before any read) defaults to the quoted ``total_cost_to_buyer`` plus slippage
        (``slippage_bps``, default the client's trade slippage).
        On the public host no order fills while INVALID is proposed or backed by a vote (rule ``'Invalid proposed'``).
        A fill that carries a fee (buyer or seller tax) is refused before the approval while the host cannot take fees
        (``'convert closed'``) or the market's main is on a paused pair (``'pair not active'``). Every fill is refused
        before the approval while the issuer of the pair asset has paused
        it (``'pair asset paused'``: the fill is paid in it). When the fill's simulation fails because the seller can't
        receive the pair asset right now (the token refuses transfers to that wallet), the rule is ``'sellerBlocked'``.
        Returns the tx plus {'fill' (the ``OrderFilled`` fill dict, None when the event is missing), 'max_cost' (an
        ``Amount`` in the market's pair asset)}."""
        market = to_address(market, "market")
        max_cost_in = opt_uint(max_cost, "maxCost")
        if max_cost_in == 0:
            raise ValidationError("maxCost", "A zero maxCost is never sent; omit it to derive it from a quote.")
        h = self._resolve_host(market, host)
        oid = to_uint(order_id, "orderId")
        amount = parse_amount(fill, 18, "fill")
        me = self._account
        md = self._market_data(h, market)
        paused = self._host_contract(h).functions.paused().call()
        order = self._read_order(h, market, oid)
        if paused:
            raise ValidationError("host paused", "Trading is paused on this market host right now.")
        if md["resolved"]:
            raise ValidationError("market resolved", "This market is resolved. Trading is closed.")
        self._check_invalid_live(h, market)
        block = self._fill_block(order, me, self._current_price(h, market, order["outcome_id"], md))
        if block:
            raise block
        if amount == 0 or amount > order["amount"]:
            raise ValidationError(
                "fillAmount", f"Fill must be above 0 and at most the {order['amount']} shares left on order {oid}."
            )
        self._check_pair_unit(md)

        quote = self._quote_fill(h, market, oid, amount, me)
        if quote["base_usdc"] == 0:
            raise ValidationError("dust fill", "That fill is worth less than 1 raw unit of the pair asset. Buy more.")
        if max_cost_in is not None:
            cap = max_cost_in
        else:
            bps = slippage_bps if slippage_bps is not None else self.client.slippage_bps["trade"]
            cap = max_with_slippage(quote["total_cost_to_buyer"], bps)
        if quote["total_cost_to_buyer"] > cap:
            raise ValidationError(
                "max cost", f"The fill costs {quote['total_cost_to_buyer']} (raw pair asset), above maxCost {cap}."
            )
        balance = self._balance_of(md["usdc"], me)
        if balance < quote["total_cost_to_buyer"]:
            raise ValidationError(
                "insufficient balance",
                f"Not enough pair asset: the fill costs {quote['total_cost_to_buyer']}, you hold {balance} (raw).",
            )
        if quote["total_cost_to_buyer"] > quote["net_to_seller"]:
            self._check_fee_path(h, market, md)
        self._refuse_paused_pair(md)

        self._ensure_allowance(md["usdc"], h["address"], cap)
        pair_of = self._pair_of(md)
        try:
            tx = self._write(h, self._host_contract(h).functions.buyOrder(market, oid, amount, cap))
        except BasisContractError as e:
            if self._unpayable(md["usdc"], me, h["address"], [order]):
                raise _seller_blocked([order], pair_of(0).symbol) from e
            raise
        fills = self._fills_of(tx, h, market, me, pair_of)
        if not fills:
            add_missing_event_warning(tx, h["contract"], "OrderFilled", ("fill",))
        return {**tx, "fill": fills[0] if fills else None, "max_cost": pair_of(cap)}

    def buy_orders(self, market: str, outcome: int, order_ids: Sequence[int], budget: HumanAmount,
                   min_shares: Optional[int] = None, slippage_bps: Optional[int] = None,
                   host: Optional[str] = None, amm: bool = False) -> Dict[str, Any]:
        """Sweep several orders on ``outcome`` with a pair-asset ``budget``: int = raw, str/float/Decimal = human in the
        pair asset's decimals. Orders only by default: the unspent budget stays with the buyer. With ``amm=True`` the
        rest buys the AMM (``inputToken`` = the pair asset; the AMM bet rules of ``check_buy`` apply when the rest is
        above fill dust, 255 raw: below it no AMM leg runs) and the whole budget is approved. ``min_shares`` (raw, 18
        decimals, never 0) defaults to the quoted shares (order quotes, + ``estimate_shares_out`` with ``amm``) minus
        slippage. As on chain, an order the host cannot fill is skipped: another outcome, your own, priced above the live
        share price, already filled or cancelled. Without ``amm``, when that leaves nothing to fill, the first skipped
        order's rule is raised (``orderOutcome``, ``ownOrder``, ``orderPrice``). On the public host a sweep that would
        fill an order is refused while INVALID is proposed or backed by a vote (``'Invalid proposed'``). A sweep whose
        fills carry a fee (buyer or seller tax) is refused before the approval while the host cannot take fees
        (``'convert closed'``) or the market's main is on a paused pair (``'pair not active'``), with or without ``amm``;
        every sweep is refused before the approval while the issuer of the pair asset has paused it (``'pair asset
        paused'``: the budget is paid in it). When the sweep's simulation fails, the sellers of the orders it would fill are checked: orders whose seller can't
        receive the pair asset right now (the token refuses transfers to that wallet) are left out, the sweep is quoted
        and sent once more without them, and they come back in ``skipped`` with a warning (rule ``'sellerBlocked'`` when
        that leaves nothing to fill).
        Returns {'hash','receipt','result','warnings','skipped','min_shares','quoted_shares','quoted_cost','amm_amount',
        'amm_shares','fills','amm','shares_bought','spent'}: ``skipped`` = the orders left out, [{'order_id', 'seller',
        'rule': 'sellerBlocked'}] (empty otherwise); ``quoted_shares`` / ``quoted_cost`` include the AMM leg; ``amm_amount`` =
        pair asset the orders leave for the AMM (0 orders-only or when only fill dust <= 255 is left); ``amm_shares`` =
        its quoted shares (0 when it converts below the minimum bet: the market tokens then go back to the buyer);
        ``fills`` = the ``OrderFilled`` fills made; ``amm`` = the AMM leg run ({'shares','tokens','fee','paid',
        'returned_tokens'} from ``BetFunded`` + ``SharesTraded``; a rest below the minimum bet has no ``SharesTraded``:
        shares and fee 0, ``returned_tokens`` = the market tokens sent back) or None; ``shares_bought`` / ``spent`` = shares
        gained on ``outcome`` and pair asset paid, fills plus AMM leg (None when the logs show neither). Shares (``min_shares``,
        ``quoted_shares``, ``amm_shares``, ``shares_bought``, the leg's ``shares``) are raw ints; ``quoted_cost``,
        ``amm_amount``, ``spent`` and the leg's ``paid`` are ``Amount``s in the market's pair asset; the leg's ``tokens``,
        ``fee`` and ``returned_tokens`` are ``Amount``s in the market token (unit ``token``). ``amm`` must be a real
        bool (``ValidationError('amm')`` otherwise); ``order_ids`` a list/tuple of whole numbers, at least one, none twice
        (``ValidationError('orderIds')`` otherwise, before any read)."""
        amm = to_bool(amm, "amm", False)
        market = to_address(market, "market")
        if order_ids is None or (isinstance(order_ids, (list, tuple)) and len(order_ids) == 0):
            raise ValidationError("orderIds", "Pass at least one order id.")
        if not isinstance(order_ids, (list, tuple)):
            raise ValidationError("orderIds", "orderIds must be an array of order ids.")
        ids = [to_uint(o, "orderIds") for o in order_ids]
        if len(set(ids)) != len(ids):
            raise ValidationError("orderIds", "Order ids must not repeat.")
        h = self._resolve_host(market, host)
        me = self._account
        c = self._host_contract(h)
        md = self._market_data(h, market)
        paused = c.functions.paused().call()
        total = parse_amount(budget, int(self.client.mains.token_meta(md["usdc"])["decimals"]), "budget")
        if total == 0:
            raise ValidationError("budget", "The budget must be above 0.")
        if paused:
            raise ValidationError("host paused", "Trading is paused on this market host right now.")
        if md["resolved"]:
            raise ValidationError("market resolved", "This market is resolved. Trading is closed.")
        self._check_outcome(h, market, outcome)
        outcome = int(outcome)
        self._check_pair_unit(md)

        orders = [self._read_order(h, market, i) for i in ids]
        if not amm and not any(o["active"] for o in orders):
            raise ValidationError("orderActive", "None of these orders is still available (all filled or cancelled).")

        plan = self._plan_sweep(h, market, outcome, md, orders, total, amm, me, min_shares, slippage_bps)
        self._refuse_paused_pair(md)

        self._ensure_allowance(md["usdc"], h["address"], total)
        pair_of = self._pair_of(md)
        tokens_of = amount_of(self.client, market)
        token_in = md["usdc"] if amm else ZERO_ADDRESS

        def sweep(p: Dict[str, Any]) -> Dict[str, Any]:
            return self._write(h, c.functions.buyOrdersAndContract(market, outcome, p["ids"], token_in, total,
                                                                   p["min_shares"]))

        skipped_orders: List[Dict[str, Any]] = []
        try:
            tx = sweep(plan)
        except BasisContractError as e:
            blocked = self._unpayable(md["usdc"], me, h["address"], plan["fillable"])
            if not blocked:
                raise
            symbol = pair_of(0).symbol
            gone = {o["order_id"] for o in blocked}
            rest = [o for o in orders if o["order_id"] not in gone]
            if not amm and not any(o["active"] for o in rest):
                raise _seller_blocked(blocked, symbol) from e
            try:
                plan = self._plan_sweep(h, market, outcome, md, rest, total, amm, me, min_shares, slippage_bps)
            except ValidationError as e2:
                raise _seller_blocked(blocked, symbol, str(e2).replace("[basis-sdk] ", "", 1)) from e2
            skipped_orders = [{"order_id": o["order_id"], "seller": o["seller"], "rule": "sellerBlocked"} for o in blocked]
            tx = sweep(plan)
            tx["warnings"].append(
                f"{h['contract']}.buyOrdersAndContract left out order{'s' if len(blocked) > 1 else ''} "
                f"{', '.join(str(o['order_id']) for o in blocked)}: the seller can't receive {symbol} right now (the "
                "token refuses transfers to that wallet). See `skipped`.")
        mins, quoted_shares, quoted_cost = plan["min_shares"], plan["quoted_shares"], plan["quoted_cost"]
        amm_amount, amm_shares = plan["amm_amount"], plan["amm_shares"]
        fills = self._fills_of(tx, h, market, me, pair_of)
        traded = next((e for e in self._events(tx, c, "SharesTraded", h["address"])
                       if _eq(e["marketToken"], market) and _eq(e["buyer"], me) and e["outcomeId"] == outcome), None)
        funded = next((e for e in self._events(tx, c, "BetFunded", h["address"])
                       if _eq(e["marketToken"], market) and _eq(e["buyer"], me)), None)
        if traded:
            amm_leg = {"shares": traded["shares"], "tokens": tokens_of(funded["tokens"] if funded else 0),
                       "fee": tokens_of(traded["fee"]), "paid": pair_of(funded["paid"] if funded else 0),
                       "returned_tokens": tokens_of(0)}
        elif funded:
            amm_leg = {"shares": 0, "tokens": tokens_of(funded["tokens"]), "fee": tokens_of(0), "paid": pair_of(funded["paid"]),
                       "returned_tokens": tokens_of(funded["tokens"])}
        else:
            amm_leg = None
        seen = bool(fills) or amm_leg is not None
        if not seen:
            add_missing_event_warning(tx, h["contract"], "OrderFilled/SharesTraded", ("shares_bought", "spent"))
        shares_bought = (sum(f["amount"] for f in fills if f["outcome_id"] == outcome) + (amm_leg["shares"] if amm_leg else 0)
                         if seen else None)
        spent = (pair_of(sum(f["buyer_paid"].raw for f in fills) + (amm_leg["paid"].raw if amm_leg else 0))
                 if seen else None)
        return {**tx, "skipped": skipped_orders, "min_shares": mins, "quoted_shares": quoted_shares,
                "quoted_cost": pair_of(quoted_cost),
                "amm_amount": pair_of(amm_amount), "amm_shares": amm_shares, "fills": fills, "amm": amm_leg,
                "shares_bought": shares_bought, "spent": spent}

    def _plan_sweep(self, h: Dict[str, str], market: str, outcome: int, md: Dict[str, Any],
                    orders: Sequence[Dict[str, Any]], total: int, amm: bool, me: str, min_shares: Optional[int],
                    slippage_bps: Optional[int]) -> Dict[str, Any]:
        """The quotes of a sweep of ``orders`` (in the order sent) with ``total``: {ids, fillable (the orders the host
        would fill), quoted_shares, quoted_cost, amm_amount, amm_shares, min_shares}, after the balance check. Raises the
        refusals ``buy_orders`` documents."""
        ids = [o["order_id"] for o in orders]
        price = self._current_price(h, market, outcome, md)
        remaining = total
        quoted_shares = 0
        skipped: Optional[ValidationError] = None
        fillable: List[Dict[str, Any]] = []
        fills: List[Dict[str, int]] = []
        for o in orders:
            if not o["active"]:
                continue
            block = (ValidationError("orderOutcome", f"Order {o['order_id']} is on outcome {o['outcome_id']}, not {outcome}.")
                     if o["outcome_id"] != outcome else self._fill_block(o, me, price))
            if block:
                skipped = skipped or block
                continue
            q = self._quote_budget(h, market, o["order_id"], remaining, me)
            if q["fill"] > 0:
                quoted_shares += q["fill"]
                remaining -= q["total_cost_to_buyer"]
                fillable.append(o)
                fills.append({"order_id": o["order_id"], "fill": q["fill"], "buyer_tax": q["buyer_tax"]})
            if remaining == 0:
                break
        if quoted_shares > 0:
            self._check_invalid_live(h, market)
        if self._fills_carry_fee(h, market, fills, me):
            self._check_fee_path(h, market, md)
        if not amm and quoted_shares == 0 and skipped:
            raise skipped
        amm_amount = 0
        amm_shares = 0
        if amm and remaining > AMM_DUST:
            markets = self.client.private_markets if h["kind"] == "private" else self.client.prediction_markets
            if not _eq(markets.address, h["address"]):
                raise ValidationError(
                    "host", f"An AMM sweep needs the address-book {h['kind']} host ({markets.address}), not {h['address']}."
                )
            chk = markets.check_buy(market, outcome, buyer=me, input=md["usdc"])
            if not chk["can_bet"]:
                raise ValidationError(chk["reason"], chk["message"])
            amm_amount = remaining
            reader = self.client.market_reader
            try:
                reader.quote_bet(market=market, outcome=outcome, input=md["usdc"], amount=remaining, host=h["address"], bettor=me)
                est = reader.estimate_shares_out(h["address"], market, outcome, total, ids, me)
                amm_shares = est - quoted_shares
                quoted_shares = est
            except ValidationError as e:
                if e.rule != "minBet":
                    raise
        quoted_cost = total - remaining + amm_amount
        mins = self._resolve_min(min_shares, slippage_bps, lambda: quoted_shares, "trade", "minShares")
        balance = self._balance_of(md["usdc"], me)
        if balance < quoted_cost:
            raise ValidationError(
                "insufficient balance", f"Not enough pair asset: the sweep costs {quoted_cost}, you hold {balance} (raw).")
        return {"ids": ids, "fillable": fillable, "quoted_shares": quoted_shares, "quoted_cost": quoted_cost,
                "amm_amount": amm_amount, "amm_shares": amm_shares, "min_shares": mins}

    def _unpayable(self, asset: str, buyer: str, control: str, orders: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The orders of ``orders`` whose seller can't receive ``asset`` right now: a 1-unit ``transfer`` of it from
        ``buyer`` to the seller is refused (simulated), while one to ``control`` (the host) goes through. Only run after
        a fill failed. A transfer the RPC could not answer counts as payable."""
        if not orders:
            return []
        erc20 = self._contract(asset, "ERC20")

        def can_receive(to: str) -> Optional[bool]:
            try:
                return erc20.functions.transfer(Web3.to_checksum_address(to), 1).call({"from": buyer}) is not False
            except Exception as e:  # noqa: BLE001
                return None if rpc_error_kind(e) else False

        if can_receive(control) is not True:
            return []
        answers: Dict[str, Optional[bool]] = {}
        for o in orders:
            k = o["seller"].lower()
            if k not in answers:
                answers[k] = can_receive(o["seller"])
        return [o for o in orders if answers[o["seller"].lower()] is False]


    def get_order(self, market: str, order_id: int, host: Optional[str] = None) -> Dict[str, Any]:
        """One order. An id that was never listed reads as an empty, inactive order.

        Keys: order_id, market, host, host_kind, seller, outcome_id, amount (shares still for sale, raw 18 dec),
        total_amount (listed originally), filled, price_per_share (whole pair-asset units per share x 1e18; 0 = the live
        share price at fill time), price ("2.5" or "market"), is_market_price, active (not cancelled, not fully
        filled)."""
        market = to_address(market, "market")
        h = self._resolve_host(market, host)
        return self._read_order(h, market, to_uint(order_id, "orderId"))

    def get_market_orders(self, market: str, active_only: bool = False, outcome: Optional[int] = None,
                          host: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every order listed on a market (ids 0 .. nextOrderId-1), optionally only active ones / one outcome."""
        market = to_address(market, "market")
        h = self._resolve_host(market, host)
        nxt = self._host_contract(h).functions.nextOrderId(market).call()
        out = [self._read_order(h, market, i) for i in range(nxt)]
        return [o for o in out if (not active_only or o["active"]) and (outcome is None or o["outcome_id"] == outcome)]

    def get_locked_shares(self, market: str, outcome: int, user: Optional[str] = None, host: Optional[str] = None) -> int:
        """Shares (raw, 18 decimals) of ``outcome`` that ``user`` (default: the signer) has locked in active listings. A
        listing needs held - locked >= amount."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        h = self._resolve_host(market, host)
        o = self._outcome_id(outcome)
        return self._host_contract(h).functions.sharesLockedInOrders(market, who, o).call()

    def get_free_shares(self, market: str, outcome: int, user: Optional[str] = None, host: Optional[str] = None) -> int:
        """Shares (raw, 18 decimals) of ``outcome`` that ``user`` (default: the signer) can still list: held minus
        locked."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        h = self._resolve_host(market, host)
        o = self._outcome_id(outcome)
        c = self._host_contract(h)
        held = c.functions.getUserShares(market, who, o).call()
        locked = c.functions.sharesLockedInOrders(market, who, o).call()
        return held - locked if held > locked else 0

    def get_next_order_id(self, market: str, host: Optional[str] = None) -> int:
        """The id the next listing on ``market`` gets (= the number of orders ever listed)."""
        market = to_address(market, "market")
        h = self._resolve_host(market, host)
        return self._host_contract(h).functions.nextOrderId(market).call()

    def get_buy_order_cost(self, market: str, order_id: int, fill: HumanAmount, buyer: Optional[str] = None,
                           host: Optional[str] = None) -> Dict[str, Amount]:
        """Cost of filling ``fill`` shares (int raw, 18 decimals, or a human str/float/Decimal) of an order, in the
        market's pair asset, at ``buyer``'s tax rate (default: the signer). {'base_cost','buyer_tax',
        'total_cost_to_buyer','net_to_seller'} as Amounts."""
        market = to_address(market, "market")
        who = self._signer if buyer is None else to_address(buyer, "buyer")
        h = self._resolve_host(market, host)
        q = self._quote_fill(h, market, to_uint(order_id, "orderId"), parse_amount(fill, 18, "fill"), who)
        amt = self._pair_amounter(h, market)
        return {"base_cost": amt(q["base_usdc"]), "buyer_tax": amt(q["buyer_tax"]),
                "total_cost_to_buyer": amt(q["total_cost_to_buyer"]), "net_to_seller": amt(q["net_to_seller"])}

    def get_buy_order_amounts_out(self, market: str, order_id: int, budget: HumanAmount, buyer: Optional[str] = None,
                                  host: Optional[str] = None) -> Dict[str, Any]:
        """Shares a pair-asset ``budget`` (int raw, or a human str/float/Decimal in the pair asset's decimals) fills on
        one order, at ``buyer``'s tax rate (default: the signer). {'fill' (raw shares; 0 for a filled or cancelled order,
        for the buyer's own order, for an order priced above the live share price, and when the fill would be worth less
        than 1 raw unit), 'base_cost', 'buyer_tax', 'total_cost_to_buyer'}."""
        market = to_address(market, "market")
        who = self._signer if buyer is None else to_address(buyer, "buyer")
        h = self._resolve_host(market, host)
        amt = self._pair_amounter(h, market)
        raw = parse_amount(budget, amt(0).decimals, "budget")
        q = self._quote_budget(h, market, to_uint(order_id, "orderId"), raw, who)
        return {"fill": q["fill"], "base_cost": amt(q["base_usdc"]), "buyer_tax": amt(q["buyer_tax"]),
                "total_cost_to_buyer": amt(q["total_cost_to_buyer"])}

    def get_share_price(self, market: str, outcome: int, host: Optional[str] = None) -> int:
        """The live share price of ``outcome``, in the book's unit: whole pair-asset units per share, scaled by 1e18. An
        order fills only at or below it, and a market-priced order fills at it. It moves with bets and with the market
        token's price."""
        market = to_address(market, "market")
        h = self._resolve_host(market, host)
        md = self._market_data(h, market)
        self._check_outcome(h, market, outcome)
        return self._current_price(h, market, int(outcome), md)


    def _resolve_host(self, market: str, ref: Optional[str] = None) -> Dict[str, str]:
        """The host of ``market``: explicit, or detected (public first, then private; cached per market)."""
        a = self.client.addresses
        if ref == "public":
            return _make_host(self._require(a.get("tranche"), "TrancheMarket"), "public")
        if ref == "private":
            return _make_host(self._require(a.get("tranchePrivate"), "TrancheMarketPrivate"), "private")
        if ref is not None:
            h = to_address(ref, "host")
            priv = a.get("tranchePrivate")
            is_private = not is_zero_address(priv) and _eq(h, priv)
            return _make_host(self._require(h, "TrancheMarket"), "private" if is_private else "public")
        key = market.lower()
        h = self._host_cache.get(key)
        if h is None:
            h = self._detect_host(market)
            self._host_cache[key] = h
        return h

    def _detect_host(self, market: str) -> Dict[str, str]:
        a = self.client.addresses
        candidates = []
        if not is_zero_address(a.get("tranche")):
            candidates.append(_make_host(a["tranche"], "public"))
        if not is_zero_address(a.get("tranchePrivate")):
            candidates.append(_make_host(a["tranchePrivate"], "private"))
        if not candidates:
            self._require(a.get("tranche"), "TrancheMarket")
        for h in candidates:
            md = self._host_contract(h).functions.getMarketData(market).call()
            if not is_zero_address(md[0]):
                return h
        raise ValidationError("unknown market", f"{market} is not a market on either host.")


    @property
    def _signer(self) -> Optional[str]:
        return self.client.account.address if self.client.account else None

    def _host_contract(self, host: Dict[str, str]):
        """The host with its own ABI (both hosts expose the same order-book functions)."""
        if host["kind"] == "private":
            return self._contract(host["address"], "TrancheMarketPrivate")
        return self._contract(host["address"], "TrancheMarket")

    def _write(self, host: Dict[str, str], function_call) -> Dict[str, Any]:
        """``_send`` on the host (its ``/api/v1/sync`` is awaited there)."""
        return self._send(function_call, host["contract"])

    def _market_data(self, host: Dict[str, str], market: str) -> Dict[str, Any]:
        d = self._host_contract(host).functions.getMarketData(market).call()
        if is_zero_address(d[0]):
            raise ValidationError("unknown market", f"{market} is not a market on host {host['address']}.")
        return {"market_token": d[0], "creator": d[1], "ecosystem": d[2], "usdc": d[3], "resolved": d[8],
                "total_odds_weight": d[10]}

    def _read_order(self, host: Dict[str, str], market: str, order_id: int) -> Dict[str, Any]:
        seller, outcome_id, amount, total_amount, price_per_share, active = \
            self._host_contract(host).functions.marketOrders(market, order_id).call()
        is_market_price = price_per_share == 0
        return {
            "order_id": order_id, "market": market, "host": host["address"], "host_kind": host["kind"],
            "seller": seller, "outcome_id": outcome_id, "amount": amount, "total_amount": total_amount,
            "filled": total_amount - amount, "price_per_share": price_per_share,
            "price": "market" if is_market_price else format_units(price_per_share, 18),
            "is_market_price": is_market_price, "active": active,
        }

    @staticmethod
    def _outcome_id(outcome: Any) -> int:
        o = whole_number(outcome)
        if o is None or o < 0 or o > 255:
            raise ValidationError("outcome", f"Outcome must be 0-255, got {outcome}.")
        return o

    def _check_outcome(self, host: Dict[str, str], market: str, outcome: int) -> None:
        outcome = self._outcome_id(outcome)
        n = self._host_contract(host).functions.getNumOutcomes(market).call()
        if outcome >= n:
            raise ValidationError("outcome", f"Outcome {outcome} is out of range (the market has {n}).")

    def _current_price(self, host: Dict[str, str], market: str, outcome: int, md: Dict[str, Any]) -> int:
        """The live share price of an outcome, as a fill uses it (same rounding): pair asset per share, 1e18 scale."""
        vr = self._host_contract(host).functions.getOutcome(market, outcome).call()[0]
        pair_price = self._contract(market, "FactoryToken").functions.getPairPrice().call()
        pair_unit = self.client.mains.get(md["ecosystem"]).pair_unit
        if md["total_odds_weight"] == 0 or pair_unit == 0:
            return 0
        return vr * PRICE_ONE // md["total_odds_weight"] * pair_price // pair_unit

    def _check_invalid_live(self, host: Dict[str, str], market: str) -> None:
        """"Invalid proposed": the public host fills no order while INVALID is the live proposal or has a panel vote in
        the current round. The private host has no such stop."""
        if host["kind"] == "private":
            return
        resolver = self._contract(host["address"], "TrancheMarket").functions.marketResolver(market).call()
        if is_zero_address(resolver):
            return
        if self._contract(resolver, "MarketResolver").functions.invalidLive(market).call():
            raise ValidationError(
                "Invalid proposed",
                "Listed shares can't be bought while the market might be voided (INVALID is proposed or backed by a vote).",
            )

    def _fills_carry_fee(self, host: Dict[str, str], market: str, fills: Sequence[Dict[str, int]], buyer: str) -> bool:
        """Does any of a sweep's quoted fills carry a fee (a buyer or seller tax above 0)? The buyer tax comes with the
        budget quote; the seller's is read (``get_buy_order_cost`` of that fill) only when no fill has a buyer tax."""
        if any(f["buyer_tax"] > 0 for f in fills):
            return True
        for f in fills:
            q = self._quote_fill(host, market, f["order_id"], f["fill"], buyer)
            if q["total_cost_to_buyer"] > q["net_to_seller"]:
                return True
        return False

    def _check_fee_path(self, host: Dict[str, str], market: str, md: Dict[str, Any]) -> None:
        """The checks of a fill that carries a fee: 'convert closed' while the host cannot take fees, 'pair not active'
        while the market's main is on a paused pair. Either would revert the fill after its approval."""
        taxes = self.client.addresses.get("taxes")
        if not is_zero_address(taxes):
            t = self._contract(taxes, "Taxes")
            registered = (t.functions.isPrediction(host["address"]).call()
                          or t.functions.isFeeSource(host["address"]).call())
            if not registered:
                raise ValidationError(
                    "convert closed",
                    f"The {host['kind']} host {host['address']} cannot pass on an order fill's fee right now: no fills that "
                    "carry a fee.",
                )
        swap = self._host_contract(host).functions.ecosystems(md["ecosystem"]).call()[1]
        active = not is_zero_address(swap) and bool(self._contract(swap, "Swap").functions.isMain(md["ecosystem"]).call())
        if not active:
            raise ValidationError(
                "pair not active",
                f"Market {market} settles through main {md['ecosystem']}, which is on a paused pair: no order fills that "
                "carry a fee until the pair is active again.",
            )

    @staticmethod
    def _fill_block(order: Dict[str, Any], buyer: str, current_price: int) -> Optional[ValidationError]:
        """The first reasonless ``fill`` rule an order breaks (active, not the buyer's own, priced at or below the live
        share price), or None."""
        if not order["active"]:
            return ValidationError("orderActive", f"Order {order['order_id']} is no longer available.")
        if _eq(order["seller"], buyer):
            return ValidationError("ownOrder", f"Order {order['order_id']} is your own; you can't buy it.")
        if not order["is_market_price"] and order["price_per_share"] > current_price:
            return ValidationError(
                "orderPrice",
                f"Order {order['order_id']} asks {order['price']} per share, above the live share price of "
                f"{format_units(current_price, 18)} (pair asset per share): it can't fill now.",
            )
        return None

    def _check_pair_unit(self, md: Dict[str, Any]) -> None:
        """"pair unit": the book settles only when the pair asset's decimals match the market's main."""
        info = self.client.mains.get(md["ecosystem"])
        meta = self.client.mains.token_meta(md["usdc"])
        if 10 ** int(meta["decimals"]) != info.pair_unit:
            raise ValidationError(
                "pair unit",
                "The order book can't settle in this market's currency (pair asset decimals do not match the main's PAIR_UNIT).",
            )

    def _fills_of(self, tx: Dict[str, Any], host: Dict[str, str], market: str, buyer: str,
                  pair_of: AmountOf) -> List[Dict[str, Any]]:
        """The ``OrderFilled`` logs of ``buyer`` on ``market`` from the host, in order, as fill dicts."""
        return [{
            "order_id": e["orderId"], "seller": e["seller"], "outcome_id": int(e["outcomeId"]), "amount": e["amount"],
            "cost": pair_of(e["usdcSpent"]), "buyer_paid": pair_of(e["buyerPaid"]), "seller_net": pair_of(e["sellerNet"]),
        } for e in self._events(tx, self._host_contract(host), "OrderFilled", host["address"])
            if _eq(e["marketToken"], market) and _eq(e["buyer"], buyer)]

    def _refuse_paused_pair(self, md: Dict[str, Any]) -> None:
        """'pair asset paused' when the issuer of the market's pair asset has paused it: a fill pays in it. The main is
        looked up for the symbol only after a paused answer. TS: ``refusePausedPair``."""
        if is_asset_paused(self.client, md["usdc"]):
            raise pair_asset_paused(md["usdc"], self.client.mains.get(md["ecosystem"]).pair_symbol,
                                    "no order can be paid for in it")

    def _pair_of(self, md: Dict[str, Any]) -> AmountOf:
        """Formatter for a write result's amounts in the market's pair asset (its main's), resolved before sending."""
        return amount_of(self.client, md["usdc"], self.client.mains.get(md["ecosystem"]))

    def _call_as(self, function_call, who: Optional[str]):
        return function_call.call({"from": Web3.to_checksum_address(who)}) if who else function_call.call()

    def _quote_fill(self, host: Dict[str, str], market: str, order_id: int, fill: int, buyer: Optional[str]) -> Dict[str, int]:
        fn = self._host_contract(host).functions.getBuyOrderCost(market, order_id, fill)
        base, tax, total, net = self._call_as(fn, buyer)
        return {"base_usdc": base, "buyer_tax": tax, "total_cost_to_buyer": total, "net_to_seller": net}

    def _quote_budget(self, host: Dict[str, str], market: str, order_id: int, budget: int, buyer: Optional[str]) -> Dict[str, int]:
        fn = self._host_contract(host).functions.getBuyOrderAmountsOut(market, order_id, budget)
        fill, base, tax, total = self._call_as(fn, buyer)
        return {"fill": fill, "base_usdc": base, "buyer_tax": tax, "total_cost_to_buyer": total}

    def _pair_amounter(self, host: Dict[str, str], market: str) -> Callable[[int], Amount]:
        """Formatter for raw amounts in the market's pair asset."""
        md = self._market_data(host, market)
        meta = self.client.mains.token_meta(md["usdc"])
        return lambda raw: to_amount(raw, int(meta["decimals"]), "pairAsset", md["usdc"], str(meta["symbol"]))
