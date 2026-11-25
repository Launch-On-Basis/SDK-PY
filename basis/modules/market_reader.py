"""Read-only views over prediction markets (TypeScript SDK: src/modules/MarketReader.ts): outcomes, share quotes, sizes,
payouts, sliced redemption quotes and the bet quote chain (any accepted input token -> shares). Most calls take the
host first.

A host is ``'public'`` (``addresses["tranche"]``), ``'private'`` (``addresses["tranchePrivate"]``) or a host address.
Amounts are raw ints (market tokens and shares 18 dec, pair asset its own decimals). Struct results are dicts with
snake_case keys:

- market context: market, host, kind, main, pair_asset, data (``get_market_data`` dict)
- market data: market_token, creator, ecosystem (= main), usdc (= pair asset), market_name, creation_time, end_time,
  final_outcome, resolved, general_pot, total_odds_weight, is_private
- outcome: outcome_id, name, odds_weight, total_cost, circulating_shares, price_per_share (the odds, 1e18 = certain; not
  the order book's price, which is in the pair asset: ``order_book.get_share_price``), probability (bps), has_won
  (tranche-aware: every outcome in the final ranking)
- sizes: unit, seed_step, min_seed (raw pair asset); min_bet, per_outcome, total_pool (market tokens); kind;
  private host also min_seed_private_event
- redemption quote: bonded, token_amount, value_at_spot, single_out, sliced_out, impact_single_bps,
  impact_sliced_bps, recommended_slices, fits_in_one_tx, suggested_fraction_bps
- sliced sell: total_out, per_slice; payout: token_payout, pair_payout (Amount)
- bet input: token, amount, route ('direct' | 'cross' | 'hop'), path
- bet quote: shares, min_shares, net_token, tokens, min_tokens, route, converted_token, converted_amount, fee_bps, host
- min bet input: min_input, min_tokens, min_bet, precision_floor, fee_bps
- redeem plan: fraction_bps, slices, entitlement, pay_now, expected_out, min_out, recommended_slices,
  suggested_fraction, host
- market live (``get_market_live``): market, host, kind, main, pair_asset, market_name, creator, creation_time,
  end_time, resolved, status, final_outcome, is_private, paused, frozen, num_outcomes, sizes, outcomes (outcome dicts
  plus odds_e18), general_pot, total_odds_weight, pot, pot_value (Amount), token_sell_value (Amount),
  final_ranking, final_winners_mask (public, else None), final_key (private, else None)
- redeemable (``get_redeemable``): market, host, kind, user, resolved, status, token_payout, pair_payout (Amount),
  quote (redemption quote or None), skipped (``get_redeemables`` only: 'unknown host' / 'unknown market', else None)
"""

from typing import Any, Dict, List, Optional, Sequence, Union

from web3 import Web3

from ..config.addresses import ZERO_ADDRESS, is_zero_address
from ..core.base import BaseModule
from ..core.errors import NotDeployedError, ValidationError
from ..core.cross_hop import check_cross_hop, route_token_of
from ..core.loans import quote_hop_within_caps
from ..core.mains import pair_amount
from ..core.ranking import OUTCOME_INVALID, OUTCOME_UNRESOLVED, whole_number
from ..core.revert import classify_error, is_revert_named, zero_unless_rpc
from ..core.units import BPS, Amount, min_with_slippage, to_address, to_uint

HOST_MAX_SLICES = 25

_MARKET_DATA_FIELDS = (
    "market_token", "creator", "ecosystem", "usdc", "market_name", "creation_time", "end_time", "final_outcome",
    "resolved", "general_pot", "total_odds_weight", "is_private",
)


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _main_arg(m: Any) -> Any:
    """A caller's ``main``: an address (strict: :func:`to_address`) or an address-book key, passed on as given."""
    return to_address(m, "main") if isinstance(m, str) and m[:2].lower() == "0x" else m


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _status_of(resolved: bool, final_outcome: int) -> str:
    if not resolved or final_outcome == OUTCOME_UNRESOLVED:
        return "unresolved"
    return "invalid" if final_outcome == OUTCOME_INVALID else "resolved"


def _bytes32_hex(k: Any) -> str:
    """bytes32 as a lowercase 0x-prefixed hex string."""
    if isinstance(k, (bytes, bytearray)):
        return "0x" + bytes(k).hex()
    s = str(k).lower()
    return s if s.startswith("0x") else "0x" + s


def _is_int(v: Any) -> bool:
    """The SDK's strict whole-number rule (``core.ranking.whole_number``): an int (not bool) or a whole float."""
    return whole_number(v) is not None


class MarketReaderModule(BaseModule):
    """Read-only prediction-market views and the bet quote chain."""

    def __init__(self, client):
        super().__init__(client)
        self._host_cache: Dict[str, Dict[str, str]] = {}
        self._max_slices: Optional[int] = None

    @property
    def deployed(self) -> bool:
        """True when the market readers are deployed on this chain."""
        return not is_zero_address(self.client.addresses["trancheReader"]) and not is_zero_address(
            self.client.addresses["redemptionReader"])

    @property
    def _reader(self):
        return self._contract(self._require(self.client.addresses["trancheReader"], "TrancheReader"), "TrancheReader")

    @property
    def _redemption(self):
        return self._contract(self._require(self.client.addresses["redemptionReader"], "RedemptionReader"), "RedemptionReader")

    @property
    def _quoter(self) -> str:
        """The signer, else 0x0 (quotes then price an unreferred wallet)."""
        return self.client.account.address if self.client.account else ZERO_ADDRESS


    def resolve_host(self, host: str) -> str:
        """Host address of a host ref (no RPC)."""
        if host == "public":
            return self._require(self.client.addresses["tranche"], "TrancheMarket")
        if host == "private":
            return self._require(self.client.addresses["tranchePrivate"], "TrancheMarketPrivate")
        h = to_address(host, "host")
        if is_zero_address(h):
            raise ValidationError("host", "Host address is zero.")
        return h

    def host_kind(self, host: str) -> str:
        """'public' or 'private' for a host. Unknown addresses are probed."""
        if host in ("public", "private"):
            return host
        h = to_address(host, "host")
        tranche, tranche_private = self.client.addresses["tranche"], self.client.addresses["tranchePrivate"]
        if not is_zero_address(tranche) and _eq(h, tranche):
            return "public"
        if not is_zero_address(tranche_private) and _eq(h, tranche_private):
            return "private"
        try:
            self._contract(h, "TrancheMarket").functions.minSeed().call()
            return "public"
        except Exception:  # noqa: BLE001
            return "private"

    def host_of(self, market: str) -> Dict[str, str]:
        """The host a market lives on, public tried first. Cached. Returns ``{'host', 'kind'}``."""
        market = to_address(market, "market")
        key = market.lower()
        hit = self._host_cache.get(key)
        if hit is None:
            hit = self._find_host(market)
            self._host_cache[key] = hit
        return dict(hit)

    def _find_host(self, market: str) -> Dict[str, str]:
        tranche, tranche_private = self.client.addresses["tranche"], self.client.addresses["tranchePrivate"]
        candidates = []
        if not is_zero_address(tranche):
            candidates.append((_cs(tranche), "public"))
        if not is_zero_address(tranche_private):
            candidates.append((_cs(tranche_private), "private"))
        if not candidates:
            raise NotDeployedError("TrancheMarket", self.client.chain_id)
        for host, kind in candidates:
            md = self.get_market_data(host, market)
            if not is_zero_address(md["market_token"]) and _eq(md["market_token"], market):
                return {"host": host, "kind": kind}
        raise ValidationError("unknown market", f"{market} is not a market on the public or private host.")

    def get_market_data(self, host: str, market: str) -> Dict[str, Any]:
        """The host's market data as a snake_case dict (zero struct for an unknown market)."""
        market = to_address(market, "market")
        raw = self._contract(self.resolve_host(host), "TrancheMarket").functions.getMarketData(market).call()
        return dict(zip(_MARKET_DATA_FIELDS, raw))

    def market_context(self, market: str, host: Optional[str] = None) -> Dict[str, Any]:
        """Host, main, pair asset and market data of a market (host detected when omitted). Raises for an unknown market."""
        market = to_address(market, "market")
        if host is None:
            found = self.host_of(market)
            h, kind = found["host"], found["kind"]
        else:
            h = self.resolve_host(host)
            kind = self.host_kind(host)
        data = self.get_market_data(h, market)
        if is_zero_address(data["market_token"]) or not _eq(data["market_token"], market):
            raise ValidationError("unknown market", f"{market} is not a market on host {h}.")
        return {"market": market, "host": h, "kind": kind, "main": data["ecosystem"], "pair_asset": data["usdc"], "data": data}


    def get_all_outcomes(self, host: str, market: str) -> List[Dict[str, Any]]:
        """Every outcome with odds (``probability`` bps, ``price_per_share`` the odds x 1e18) and the tranche-aware
        ``has_won``."""
        market = to_address(market, "market")
        rows = self._reader.functions.getAllOutcomes(self.resolve_host(host), market).call()
        return [{
            "outcome_id": int(o[0]),
            "name": o[1],
            "odds_weight": o[2],
            "total_cost": o[3],
            "circulating_shares": o[4],
            "price_per_share": o[5],
            "probability": int(o[6]),
            "has_won": o[7],
        } for o in rows]

    def quote_shares(self, host: str, market: str, outcome: int, input_token: str, amount: int,
                     buyer: Optional[str] = None) -> Dict[str, int]:
        """Shares for a bet against the pool, conversion and the buyer's fee included.
        ``input_token`` must be the market token, its pair asset or its main (anything else quotes (0, 0); use
        ``quote_bet``). ``amount`` raw input units; ``net_token`` = market tokens reaching the pot. ``buyer`` defaults
        to the signer, else 0x0. Returns ``{'shares', 'net_token'}``."""
        market = to_address(market, "market")
        input_token = to_address(input_token, "inputToken")
        who = self._quoter if buyer is None else to_address(buyer, "buyer")
        shares, net_token = self._reader.functions.quoteShares(
            self.resolve_host(host), market, self._outcome_arg(outcome), input_token, to_uint(amount, "amount"), who,
        ).call()
        return {"shares": shares, "net_token": net_token}

    def estimate_shares_out(self, host: str, market: str, outcome: int, amount: int,
                            order_ids: Optional[Sequence[Union[int, float]]] = (), user: Optional[str] = None) -> int:
        """Shares for an order-book buy with a budget of ``amount`` raw pair asset, sweeping ``order_ids`` first, the
        rest against the pool (none = the pool only; None: none, as TS null). ``user`` defaults to the signer, else 0x0.
        The inputs are checked before any read (``order_ids`` a list/tuple, else 'orderIds'; each id a whole number,
        'orderId'); then the market: an unknown market is ``ValidationError('unknown market')``."""
        market = to_address(market, "market")
        who = self._quoter if user is None else to_address(user, "user")
        ids_in = [] if order_ids is None else order_ids
        if not isinstance(ids_in, (list, tuple)):
            raise ValidationError("orderIds", "orderIds must be an array of order ids.")
        ids = [to_uint(i, "orderId") for i in ids_in]
        pick = self._outcome_arg(outcome)
        raw = to_uint(amount, "amount")
        self.market_context(market, host)
        return self._reader.functions.estimateSharesOut(self.resolve_host(host), market, pick, raw, ids, who).call()

    def get_all_user_shares(self, host: str, market: str, user: Optional[str] = None) -> List[int]:
        """``user``'s shares on every outcome, indexed by outcome id (18 dec). ``user`` defaults to the signer."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        return list(self._reader.functions.getAllUserShares(self.resolve_host(host), market, who).call())

    def get_user_shares_batch(self, host: str, market: str, users: Sequence[str], outcome_ids: Sequence[int]) -> List[int]:
        """Shares for parallel ``(users[i], outcome_ids[i])`` pairs (18 dec). Both must be lists/tuples ('users' /
        'outcomeIds'), checked before any read."""
        market = to_address(market, "market")
        if not isinstance(users, (list, tuple)):
            raise ValidationError("users", "users must be an array of addresses.")
        if not isinstance(outcome_ids, (list, tuple)):
            raise ValidationError("outcomeIds", "outcomeIds must be an array of outcome ids.")
        if len(users) != len(outcome_ids):
            raise ValidationError(
                "length mismatch", f"users ({len(users)}) and outcomeIds ({len(outcome_ids)}) must be the same length.")
        wallets = [to_address(u, "users") for u in users]
        return list(self._reader.functions.getUserSharesBatch(
            self.resolve_host(host), market, wallets, [self._outcome_arg(o) for o in outcome_ids],
        ).call())

    def value_in_pair_asset(self, host: str, market: str, amount: int):
        """What ``amount`` market tokens sell for now, as an ``Amount`` in the market's pair asset."""
        market = to_address(market, "market")
        h = self.resolve_host(host)
        raw = self._reader.functions.valueInUsdb(h, market, to_uint(amount, "amount")).call()
        md = self.get_market_data(h, market)
        return pair_amount(raw, self.client.mains.get(md["ecosystem"]))

    def quote_scenario(self, host: str, market: str, ranking: Sequence[int], tie_mask: Union[int, float] = 0,
                       user: Optional[str] = None) -> Dict[str, Any]:
        """``user``'s payout if the market resolved with (``ranking``, ``tie_mask``) now.
        An invalid ranking quotes 0, and so does ``[]`` (INVALID): the hosts quote only rankings here. For INVALID use
        ``quote_invalid_refund``. ``user`` defaults to the signer. Checked before any read: ``ranking`` a list/tuple
        ('ranking') of outcome ids 0-255 ('outcome'), ``tie_mask`` a whole number ('tieMask': None is refused; left out
        = 0). Returns ``{'token_payout', 'pair_payout'}``."""
        market = to_address(market, "market")
        if not isinstance(ranking, (list, tuple)):
            raise ValidationError("ranking", "ranking must be a list of outcome ids.")
        picks = [self._outcome_arg(r) for r in ranking]
        mask = to_uint(tie_mask, "tieMask")
        who = self._account if user is None else to_address(user, "user")
        h = self.resolve_host(host)
        token_payout, pair = self._reader.functions.quoteScenarioUSDB(h, market, picks, mask, who).call()
        md = self.get_market_data(h, market)
        return {"token_payout": token_payout, "pair_payout": pair_amount(pair, self.client.mains.get(md["ecosystem"]))}

    def quote_redeem_value(self, host: str, market: str, user: Optional[str] = None) -> Dict[str, Any]:
        """``user``'s claim on a resolved market; 0 before resolution. ``user`` defaults to the signer.
        Returns ``{'token_payout', 'pair_payout'}``."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        h = self.resolve_host(host)
        token_payout, pair = self._reader.functions.quoteRedeemUSDB(h, market, who).call()
        md = self.get_market_data(h, market)
        return {"token_payout": token_payout, "pair_payout": pair_amount(pair, self.client.mains.get(md["ecosystem"]))}

    def quote_invalid_refund(self, host: str, market: str, user: Optional[str] = None) -> Dict[str, Any]:
        """What ``user`` (default the signer) would get back if the market resolved INVALID now (``quote_scenario``
        quotes ``[]`` as 0). It is based on an average cost, so it can differ from what the user paid. ``pair_payout`` =
        what the refund sells for now. Rules 'unknown market', 'market resolved' (then ``quote_redeem_value`` gives what
        is owed).
        Returns ``{'token_payout', 'pair_payout'}``."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        ctx = self.market_context(market, host)
        if ctx["data"]["resolved"]:
            raise ValidationError("market resolved", f"Market {market} is resolved: the redeem quote gives what is owed.")
        outcomes = self.get_all_outcomes(ctx["host"], market)
        shares = self.get_all_user_shares(ctx["host"], market, who)
        info = self.client.mains.get(ctx["main"])
        total_cost = 0
        user_cost = 0
        for o in outcomes:
            total_cost += o["total_cost"]
            i = o["outcome_id"]
            s = shares[i] if i < len(shares) else 0
            if s > 0 and o["circulating_shares"] > 0:
                user_cost += s * o["total_cost"] // o["circulating_shares"]
        pot = ctx["data"]["general_pot"]
        token_payout = user_cost + user_cost * pot // total_cost if total_cost > 0 and pot > 0 else user_cost
        pair = 0 if token_payout == 0 else self._reader.functions.valueInUsdb(ctx["host"], market, token_payout).call()
        return {"token_payout": token_payout, "pair_payout": pair_amount(pair, info)}

    def market_sizes(self, host: str, main: str, num_outcomes: Union[int, float]) -> Dict[str, Any]:
        """Sizes of a market of ``num_outcomes`` on ``main`` (address or key), with the host's seed floor. Raw units:
        unit / seed_step / min_seed in pair asset, min_bet / per_outcome / total_pool in market tokens."""
        return self._sizes_of(host, main, num_outcomes, True)

    def _sizes_of(self, host: str, main: str, num_outcomes: Union[int, float], creation: bool) -> Dict[str, Any]:
        """``market_sizes``; ``creation`` applies the 2 to outcome-limit rule (a creation rule: a live market keeps its
        outcome count)."""
        h = self.resolve_host(host)
        main_addr = self.client.mains.address_of(_main_arg(main))
        n = to_uint(num_outcomes, "numOutcomes")
        kind = self.host_kind(host)
        if creation:
            max_outcomes = self._contract(h, "TrancheMarket").functions.MAX_OUTCOMES().call()
            cap = max_outcomes if max_outcomes < 255 else 255
            if n < 2 or n > cap:
                raise ValidationError("outcomes", f"A market needs 2 to {cap} outcomes, got {n}.")

        unit, seed_step, min_bet, per_outcome, total_pool = self._reader.functions.sizesFor(h, main_addr, n).call()
        out = {"unit": unit, "seed_step": seed_step, "min_bet": min_bet, "per_outcome": per_outcome, "total_pool": total_pool}
        if kind == "public":
            floor = self._contract(h, "TrancheMarket").functions.minSeed().call()
            return {**out, "min_seed": floor * unit, "kind": kind}
        priv_fns = self._contract(h, "TrancheMarketPrivate").functions
        pub = priv_fns.minSeedPublic().call()
        priv = priv_fns.minSeedPrivate().call()
        return {**out, "min_seed": pub * unit, "min_seed_private_event": priv * unit, "kind": kind}


    def get_market_live(self, market: str, host: Optional[str] = None) -> Dict[str, Any]:
        """Live state of a market in one call: host ``paused``, the token's ``frozen``, ``sizes``, the outcomes with
        live odds (``odds_e18``, 1e18 scale), the pot and its sell value, one token's sell value, the final ranking and
        ``final_winners_mask`` (public) or ``final_key`` (private). Host detected when omitted. ``sizes`` uses the
        market's real outcome count (no outcome limit: that is a creation rule). Keys in the module docstring."""
        market = to_address(market, "market")
        ctx = self.market_context(market, host)
        h, d = ctx["host"], ctx["data"]
        host_c = self._contract(h, "TrancheMarket")
        paused = host_c.functions.paused().call()
        frozen = self._contract(market, "FactoryToken").functions.frozen().call()
        outcomes = self.get_all_outcomes(h, market)
        final_ranking = [int(r) for r in host_c.functions.getFinalRanking(market).call()]
        final_winners_mask = host_c.functions.finalWinnersMask(market).call() if ctx["kind"] == "public" else None
        final_key = (_bytes32_hex(self._contract(h, "TrancheMarketPrivate").functions.finalKey(market).call())
                     if ctx["kind"] == "private" else None)
        info = self.client.mains.get(ctx["main"])
        pot = d["general_pot"] + sum(o["total_cost"] for o in outcomes)
        total = sum(o["odds_weight"] for o in outcomes)
        sizes = self._sizes_of(h, ctx["main"], len(outcomes), False)
        pot_raw = self._reader.functions.valueInUsdb(h, market, pot).call()
        token_raw = self._reader.functions.valueInUsdb(h, market, 10 ** 18).call()
        fo = int(d["final_outcome"])
        return {
            "market": market, "host": h, "kind": ctx["kind"], "main": ctx["main"], "pair_asset": ctx["pair_asset"],
            "market_name": d["market_name"], "creator": d["creator"], "creation_time": d["creation_time"],
            "end_time": d["end_time"], "resolved": d["resolved"], "status": _status_of(d["resolved"], fo),
            "final_outcome": fo, "is_private": d["is_private"], "paused": paused, "frozen": frozen,
            "num_outcomes": len(outcomes), "sizes": sizes,
            "outcomes": [{**o, "odds_e18": 0 if total == 0 else o["odds_weight"] * 10 ** 18 // total} for o in outcomes],
            "general_pot": d["general_pot"], "total_odds_weight": d["total_odds_weight"],
            "pot": pot, "pot_value": pair_amount(pot_raw, info), "token_sell_value": pair_amount(token_raw, info),
            "final_ranking": final_ranking, "final_winners_mask": final_winners_mask, "final_key": final_key,
        }

    def get_redeemable(self, market: str, user: Optional[str] = None, host: Optional[str] = None) -> Dict[str, Any]:
        """What ``user`` (default the signer) can redeem on ``market`` now: the tokens owed, their one-shot value and,
        when something is owed, ``quote_redemption`` (single vs sliced). Host detected when omitted. Zero payout and
        ``quote`` None when unresolved or nothing is owed."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        ctx = self.market_context(market, host)
        token_payout, pair = self._reader.functions.quoteRedeemUSDB(ctx["host"], market, who).call()
        info = self.client.mains.get(ctx["main"])
        quote = self.quote_redemption(market, token_payout) if token_payout > 0 else None
        fo = int(ctx["data"]["final_outcome"])
        return {
            "market": market, "host": ctx["host"], "kind": ctx["kind"], "user": who,
            "resolved": ctx["data"]["resolved"], "status": _status_of(ctx["data"]["resolved"], fo),
            "token_payout": token_payout, "pair_payout": pair_amount(pair, info), "quote": quote, "skipped": None,
        }

    def get_redeemables(self, markets: Sequence[Union[str, Dict[str, Any]]], user: Optional[str] = None) -> List[Dict[str, Any]]:
        """``get_redeemable`` for each market, in order: an address (host detected) or ``{'market', 'host'}`` (host
        taken when it is an address-book host; 'public' / 'private' / an address). A market that cannot be placed does
        not fail the batch: its entry has ``skipped`` set ('unknown host': the host given is not in the address book, no
        RPC; 'unknown market': no host lists it) and a zero payout. A malformed market or host address raises
        ``ValidationError`` before any read, and so does a ``markets`` that is not a list/tuple ('markets') or an entry
        that is neither a string nor a dict with 'market' (+ optional 'host') ('market': None, a number, a list, a set);
        other errors (RPC) raise. ``user`` defaults to the signer."""
        if not isinstance(markets, (list, tuple)):
            raise ValidationError("markets", "markets must be an array of markets.")
        who = self._account if user is None else to_address(user, "user")
        refs = []
        for i, m in enumerate(markets):
            if not isinstance(m, (str, dict)):
                shown = repr(m) if m is None or isinstance(m, (bool, int, float)) else f"a {type(m).__name__}"
                raise ValidationError("market", f"markets[{i}] must be a market address or a {{'market', 'host'}} dict, got {shown}.")
            ref = {"market": m} if isinstance(m, str) else m
            h = ref.get("host")
            refs.append((to_address(ref.get("market"), "market"), h if h is None or h in ("public", "private") else to_address(h, "host")))
        out = []
        for market, host in refs:
            if host is not None and host not in ("public", "private"):
                tranche, tranche_private = self.client.addresses["tranche"], self.client.addresses["tranchePrivate"]
                known = (not is_zero_address(tranche) and _eq(host, tranche)) or (
                    not is_zero_address(tranche_private) and _eq(host, tranche_private))
                if not known:
                    out.append(self._skipped_redeemable(market, ZERO_ADDRESS if is_zero_address(host) else _cs(host), who, "unknown host"))
                    continue
            try:
                out.append(self.get_redeemable(market, who, host))
            except ValidationError as e:
                if e.rule != "unknown market":
                    raise
                h = ZERO_ADDRESS if host is None else self.resolve_host(host)
                out.append(self._skipped_redeemable(market, h, who, "unknown market"))
        return out

    @staticmethod
    def _skipped_redeemable(market: str, host: str, user: str, why: str) -> Dict[str, Any]:
        return {
            "market": _cs(market), "host": host, "kind": None, "user": user, "resolved": False, "status": "unresolved",
            "token_payout": 0, "pair_payout": Amount(raw=0, formatted="0", decimals=0, unit="pairAsset", asset=ZERO_ADDRESS, symbol=""),
            "quote": None, "skipped": why,
        }


    def quote_bet_input(self, input: str, amount: int, pair_asset: str, main: str, market_token: Optional[str] = None,
                        bettor: Optional[str] = None) -> Dict[str, Any]:
        """Step 1 of the bet quote chain: convert ``amount`` of ``input`` toward the token step 2 quotes with, the way
        the bet itself will.
        - ``market_token``, ``pair_asset``, ``main`` -> ``direct`` (no call);
        - a token on ``main`` -> ``cross``, step-2 token = main;
        - another main, or a token on another main -> ``cross``, step-2 token = pair_asset;
        - any other token -> ``hop``, step-2 token = pair_asset.
        ``bettor`` is the trader of a ``cross`` quote (referral discount included; default the signer, else 0x0).
        Raw units throughout. An outside hop (the whole of a ``hop``, and the last step of a ``cross`` from a token whose
        pair asset is not the market's) is checked as a swap sent now, as ``trading.swap_cross`` checks one: refused with
        'no route' when it has no route, and 'above cap' when the amount that reaches it is more than it takes right now
        (the message says whether waiting about 12 s clears it, or to send less). A failure of the outside-hop quote is
        classified: an RPC failure is ``BasisRpcError``, a revert ``BasisContractError``. Other read failures on the way
        come as the RPC client raised them (the cross quote's as ``trading.quote_cross`` decodes it).
        Returns ``{'token', 'amount', 'route', 'path'}``."""
        input = to_address(input, "input")
        pair_asset = to_address(pair_asset, "pairAsset")
        main = to_address(main, "main")
        market_token = None if market_token is None else to_address(market_token, "marketToken")
        bettor = self._quoter if bettor is None else to_address(bettor, "bettor")
        amount = to_uint(amount, "amount")
        if amount <= 0:
            raise ValidationError("amount", "The bet amount must be above 0.")
        if _eq(input, pair_asset) or _eq(input, main) or (market_token and _eq(input, market_token)):
            return {"token": input, "amount": amount, "route": "direct", "path": []}

        path: Optional[List[str]] = None
        token = pair_asset
        sold: Optional[List[Dict[str, Any]]] = None
        mains = self.client.mains
        if mains.is_main(input):
            its_pair = _cs(mains.pair_asset_of(input))
            if _eq(its_pair, pair_asset):
                path = [input, its_pair]
            else:
                path = [input, its_pair, pair_asset]
                sold = [{"address": input, "kind": "main", "main": input, "pair": its_pair}]
        elif self._is_ecosystem_token(input):
            its_main = _cs(self._contract(input, "EcosystemToken").functions.quoteToken().call())
            if not mains.is_main(its_main):
                raise ValidationError("unknown pair", f"{input} trades against {its_main}, which is not a main on SWAP.")
            if _eq(its_main, main):
                path = [input, main]
                token = main
            else:
                its_pair = _cs(mains.pair_asset_of(its_main))
                if _eq(its_pair, pair_asset):
                    path = [input, its_main, pair_asset]
                else:
                    path = [input, its_main, its_pair, pair_asset]
                    sold = [{"address": input, "kind": "token", "main": its_main, "pair": its_pair},
                            {"address": its_main, "kind": "main", "main": its_main, "pair": its_pair}]

        if path:
            hop = f" ({path[len(sold)]} to {path[len(sold) + 1]})" if sold else ""
            try:
                out = self.client.trading.quote_cross(path, amount, bettor)
            except Exception as e:  # noqa: BLE001
                if is_revert_named(e, "above cap"):
                    raise ValidationError(
                        "above cap",
                        f"{amount} of {input} is above the outside route's caps on its way{hop}: send less.",
                    ) from e
                raise
            if out <= 0:
                raise ValidationError("Zero sold", f"{amount} of {input} converts to nothing. Try a larger amount.")
            if sold:
                after = [route_token_of(self.client, t) for t in path[len(sold):]]
                check_cross_hop(self.client, path, sold + after, amount, bettor, self._hop_quote)
            return {"token": token, "amount": out, "route": "cross", "path": path}
        exec_ = self.client.trading.get_hop_executor()
        if is_zero_address(exec_):
            raise ValidationError(
                "Buy: need asset/MAIN",
                f"{input} is not a BASIS token and SWAP has no hop executor on chain {self.client.chain_id}.",
            )
        exec_ = _cs(exec_)
        if len(self._contract(exec_, "HopExecutor").functions.getRoute(input, pair_asset).call()) == 0:
            raise ValidationError("no route", f"No outside route from {input} to {pair_asset}.")
        out = quote_hop_within_caps(self.client, exec_, input, pair_asset, amount,
                                    lambda x: self._hop_quote(exec_, input, pair_asset, x))
        if out <= 0:
            raise ValidationError("zero hop", f"{amount} of {input} converts to nothing. Try a larger amount.")
        return {"token": pair_asset, "amount": out, "route": "hop", "path": [input, pair_asset]}

    def quote_bet(self, market: str, outcome: int, input: str, amount: int, host: Optional[str] = None,
                  bettor: Optional[str] = None, slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """The full bet quote for any accepted input token (pair asset, the market's main, the market token, any BASIS
        token, an outside token with a hop route): step 1 ``quote_bet_input``, step 2 ``quote_shares``, then the
        minimums for ``buy`` with ``slippage_bps`` (default ``client.slippage_bps["trade"]``). ``amount`` raw input
        units. Raises ``ValidationError`` when the bet is below ``min_bet`` or below its precision floor (see
        ``min_bet_input``); a bet that does not quote on a main whose pair is paused raises 'pair not active' instead."""
        market = to_address(market, "market")
        input = to_address(input, "input")
        who = self._quoter if bettor is None else to_address(bettor, "bettor")
        ctx = self.market_context(market, host)
        step1 = self.quote_bet_input(input, amount, ctx["pair_asset"], ctx["main"], market_token=market, bettor=who)
        q = self.quote_shares(ctx["host"], market, outcome, step1["token"], step1["amount"], who)
        shares, net_token = q["shares"], q["net_token"]
        fee_bps = self.client.taxes.get_tax_rate(market, who)
        min_bet = self._min_bet_of(ctx)
        if shares == 0:
            self._refuse_paused_pair(ctx, "bet")
            raise ValidationError(
                "quote",
                f"No shares for this bet (market resolved, outcome {outcome} out of range, or the input does not convert).",
            )

        tokens = step1["amount"] if _eq(step1["token"], market) else self._gross_of(net_token, fee_bps)
        if tokens < min_bet:
            raise ValidationError("minBet", f"The bet converts to {tokens} market tokens, below the minimum bet of {min_bet}.")
        self._check_fee_sells(ctx, tokens, fee_bps)

        bps = slippage_bps if slippage_bps is not None else self.client.slippage_bps["trade"]
        min_shares = self._resolve_min(None, bps, lambda: shares, "trade", "minShares")
        min_tokens = min_with_slippage(tokens, bps)
        if min_tokens < min_bet:
            min_tokens = min_bet
        return {
            "shares": shares, "min_shares": min_shares, "net_token": net_token, "tokens": tokens, "min_tokens": min_tokens,
            "route": step1["route"], "converted_token": step1["token"], "converted_amount": step1["amount"],
            "fee_bps": fee_bps, "host": ctx["host"],
        }

    def min_bet_input(self, host: Optional[str], market: str, input_token: str, bettor: Optional[str] = None) -> Dict[str, Any]:
        """The smallest bet ``bettor`` can place in ``input_token`` (raw input units, 1% margin): the input must
        convert to at least ``min_bet`` and clear the precision floor (a smaller bet reverts without a reason).
        ``host`` may be None (detected). ``bettor`` defaults to the signer, else 0x0. Raises
        'quote' when the market or the input cannot be priced ('pair not active' when that is because the market's main
        is on a paused pair), and 'above cap' when an outside hop on the input's route cannot take a probe of it right
        now (its message says when waiting about 12 seconds clears it, or to send less); an RPC failure of a probe is
        ``BasisRpcError``, never 'quote'."""
        market = to_address(market, "market")
        input_token = to_address(input_token, "inputToken")
        who = self._quoter if bettor is None else to_address(bettor, "bettor")
        ctx = self.market_context(market, host)
        min_bet = self._min_bet_of(ctx)
        fee_bps = self.client.taxes.get_tax_rate(market, who)

        precision_floor = 0
        min_tokens = min_bet
        if fee_bps > 0:
            precision_floor = _ceil_div(_ceil_div(BPS, fee_bps) * 12, 10)
            probe = min_bet * 100 if min_bet * 100 > 0 else 10 ** 18
            value = self._reader.functions.valueInUsdb(ctx["host"], market, probe).call()
            if value == 0:
                self._refuse_paused_pair(ctx, "bet")
                raise ValidationError("quote", f"Market {market} cannot be priced in its pair asset.")
            precision_tokens = _ceil_div(precision_floor * probe, value)
            if precision_tokens > min_tokens:
                min_tokens = precision_tokens

        if _eq(input_token, market):
            return {"min_input": min_tokens, "min_tokens": min_tokens, "min_bet": min_bet,
                    "precision_floor": precision_floor, "fee_bps": fee_bps}

        def tokens_for(x: int) -> int:
            s1 = self.quote_bet_input(input_token, x, ctx["pair_asset"], ctx["main"], market_token=market, bettor=who)
            net = self.quote_shares(ctx["host"], market, 0, s1["token"], s1["amount"], who)["net_token"]
            return self._gross_of(net, fee_bps)

        decimals = int(self.client.mains.token_meta(input_token)["decimals"])
        x = 10 ** decimals
        got = 0
        for _ in range(4):
            if got != 0:
                break
            try:
                got = tokens_for(x)
            except Exception as e:  # noqa: BLE001
                if isinstance(e, ValidationError) and e.rule == "above cap":
                    raise
                got = zero_unless_rpc(e, None, "quoteBetInput")
            if got == 0:
                x *= 1000
        if got == 0:
            self._refuse_paused_pair(ctx, "bet")
            raise ValidationError("quote", f"{input_token} does not convert into market {market}.")
        for _ in range(3):
            x = (_ceil_div(x * min_tokens, got) * 101) // 100 + 1
            got = tokens_for(x)
            if got >= min_tokens:
                break
        return {"min_input": x, "min_tokens": min_tokens, "min_bet": min_bet, "precision_floor": precision_floor, "fee_bps": fee_bps}


    def quote_redemption(self, market: str, amount: int) -> Dict[str, Any]:
        """Redemption quote: single vs sliced proceeds, recommended slices, partial plan. ``amount`` = market tokens;
        values raw pair asset."""
        q = self._redemption.functions.quoteRedemption(to_address(market, "market"), to_uint(amount, "amount")).call()
        return {
            "bonded": q[0],
            "token_amount": q[1],
            "value_at_spot": q[2],
            "single_out": q[3],
            "sliced_out": q[4],
            "impact_single_bps": int(q[5]),
            "impact_sliced_bps": int(q[6]),
            "recommended_slices": int(q[7]),
            "fits_in_one_tx": q[8],
            "suggested_fraction_bps": int(q[9]),
        }

    def simulate_sliced_sell(self, market: str, amount: int, slices: int) -> Dict[str, Any]:
        """Proceeds (raw pair asset) of selling ``amount`` market tokens in ``slices`` equal chunks (1 up to the
        reader's slice limit).
        Returns ``{'total_out', 'per_slice'}``."""
        market = to_address(market, "market")
        raw = to_uint(amount, "amount")
        s = self._slices_arg(slices)
        total_out, per_slice = self._redemption.functions.simulateSlicedSell(market, raw, s).call()
        return {"total_out": total_out, "per_slice": list(per_slice)}

    def quote_slices(self, market: str, amount: int, slices: int) -> Dict[str, Any]:
        """Same as ``simulate_sliced_sell``."""
        market = to_address(market, "market")
        raw = to_uint(amount, "amount")
        s = self._slices_arg(slices)
        total_out, per_slice = self._redemption.functions.quoteSlices(market, raw, s).call()
        return {"total_out": total_out, "per_slice": list(per_slice)}

    def spot_value(self, market: str, amount: int) -> int:
        """Face value of ``amount`` market tokens at the spot price (no price impact), raw pair asset."""
        return self._redemption.functions.spotValue(to_address(market, "market"), to_uint(amount, "amount")).call()

    def redeem_plan(self, market: str, user: Optional[str] = None, fraction_bps: Optional[int] = None,
                    slices: Optional[int] = None, slippage_bps: Optional[int] = None, min_out: Optional[int] = None,
                    host: Optional[str] = None) -> Dict[str, Any]:
        """Arguments for ``redeem`` (fraction_bps, slices, min_out): pay_now = the user's remaining entitlement x
        fraction_bps / 10000, proceeds from ``simulate_sliced_sell(pay_now, slices)``, min_out = proceeds minus
        ``slippage_bps`` (default ``client.slippage_bps["trade"]``). Defaults: ``fraction_bps`` 10000, or the reader's
        suggested fraction when the position cannot exit in one transaction; ``slices`` = the reader's recommendation
        for pay_now (1-25, the host's bound). Every slice must sell for at least 1 raw unit of the pair asset (the host
        reverts with no reason otherwise): defaulted slices are lowered until each does; caller-chosen ones that do not
        are refused with 'dust slice', and so is a single slice that sells for 0. An explicit ``min_out`` is returned as
        given. A market whose main is on a paused pair is refused ('pair not active').
        ``user`` defaults to the signer."""
        market = to_address(market, "market")
        who = self._account if user is None else to_address(user, "user")
        ctx = self.market_context(market, host)
        if not ctx["data"]["resolved"]:
            raise ValidationError("market unresolved", f"Market {market} is not resolved yet.")
        if fraction_bps is not None and (not _is_int(fraction_bps) or fraction_bps < 1 or fraction_bps > 10000):
            raise ValidationError("fraction 1-10000", f"fractionBps must be 1-10000, got {fraction_bps}.")
        if slices is not None and (not _is_int(slices) or slices < 1 or slices > HOST_MAX_SLICES):
            raise ValidationError("slices 1-25", f"slices must be 1-{HOST_MAX_SLICES}, got {slices}.")

        entitlement = self._contract(ctx["host"], "TrancheMarket").functions.quoteRedeem(market, who).call()
        if entitlement == 0:
            raise ValidationError("Nothing to redeem", f"{who} has nothing to redeem on {market}.")
        self._refuse_paused_pair(ctx, "redeem")

        whole = self.quote_redemption(market, entitlement)
        suggested_fraction = 0 if whole["fits_in_one_tx"] else whole["suggested_fraction_bps"]
        fbps = int(fraction_bps) if fraction_bps is not None else (suggested_fraction if suggested_fraction > 0 else 10000)
        pay_now = entitlement if fbps == 10000 else entitlement * fbps // BPS
        if pay_now == 0:
            raise ValidationError("Nothing to redeem", f"fractionBps {fbps} of {entitlement} rounds to 0.")

        now = whole if pay_now == entitlement else self.quote_redemption(market, pay_now)
        n_slices = int(slices) if slices is not None else min(max(int(now["recommended_slices"]), 1), HOST_MAX_SLICES)
        sim = self.simulate_sliced_sell(market, pay_now, n_slices)

        def dust(n: int, per_slice: List[int]) -> bool:
            base = pay_now // n
            rem = pay_now - base * n
            return any((base + rem if i == 0 else base) > 0 and out == 0 for i, out in enumerate(per_slice))

        while dust(n_slices, sim["per_slice"]):
            if n_slices == 1:
                raise ValidationError(
                    "dust slice",
                    f"{pay_now} market tokens sell for 0 of the pair asset (the redeem would revert)"
                    f"{': redeem a larger fraction' if fbps < 10000 else ''}.",
                )
            if slices is not None:
                raise ValidationError(
                    "dust slice",
                    f"With {n_slices} slices of {pay_now // n_slices} market tokens a slice sells for 0 of the pair asset "
                    "(the redeem would revert): use fewer slices.",
                )
            n_slices = max(1, n_slices // 2)
            sim = self.simulate_sliced_sell(market, pay_now, n_slices)
        expected_out = sim["total_out"]
        mo = self._resolve_min(min_out, slippage_bps, lambda: expected_out, "trade", "minOut")
        return {
            "fraction_bps": fbps, "slices": int(n_slices), "entitlement": entitlement, "pay_now": pay_now,
            "expected_out": expected_out, "min_out": mo, "recommended_slices": now["recommended_slices"],
            "suggested_fraction": suggested_fraction, "host": ctx["host"],
        }


    @staticmethod
    def _outcome_arg(o: Any) -> int:
        if not _is_int(o) or o < 0 or o > 255:
            raise ValidationError("outcome", f"Outcome ids are 0-255, got {o}.")
        return int(o)

    def _slices_arg(self, slices: Any) -> int:
        if self._max_slices is None:
            self._max_slices = self._redemption.functions.MAX_SLICES().call()
        mx = self._max_slices
        if not _is_int(slices) or slices < 1 or slices > mx:
            raise ValidationError(f"slices 1-{mx}", f"slices must be 1-{mx}, got {slices}.")
        return int(slices)

    def _min_bet_of(self, ctx: Dict[str, Any]) -> int:
        """The minimum bet of a market, market tokens."""
        return self._reader.functions.sizesFor(ctx["host"], _cs(ctx["main"]), 2).call()[2]

    @staticmethod
    def _gross_of(net_token: int, fee_bps: int) -> int:
        """Market tokens before the bet fee, from the net amount. When two amounts net the same, the smaller one is
        used: it keeps the minimum-bet check and ``min_tokens`` on the safe side."""
        if fee_bps <= 0 or fee_bps >= BPS:
            return net_token
        g = net_token * BPS // (BPS - fee_bps)
        while g > 0 and (g - 1) - (g - 1) * fee_bps // BPS == net_token:
            g -= 1
        return g

    def _check_fee_sells(self, ctx: Dict[str, Any], tokens: int, fee_bps: int) -> None:
        """Refuses a bet under the precision floor (the host would revert it with no reason)."""
        fee = tokens * fee_bps // BPS
        if fee == 0:
            return
        v = self._reader.functions.valueInUsdb(ctx["host"], ctx["market"], fee).call()
        if v == 0:
            self._refuse_paused_pair(ctx, "bet")
            raise ValidationError(
                "precision",
                f"The bet fee ({fee} market tokens) sells for less than one raw unit of the pair asset; "
                "bet more (see min_bet_input).",
            )

    def _refuse_paused_pair(self, ctx: Dict[str, Any], action: str) -> None:
        """'pair not active' when the market's main is on a paused pair, read now: it refuses bets and redeems
        ("unknown pair" on chain)."""
        swap = self._contract(ctx["host"], "TrancheMarket").functions.ecosystems(ctx["main"]).call()[1]
        if not is_zero_address(swap) and self._contract(swap, "Swap").functions.isMain(ctx["main"]).call():
            return
        what = ("no bets that convert through it until the pair is active again" if action == "bet"
                else "the payout sells through it, so no redeem until the pair is active again")
        raise ValidationError(
            "pair not active", f"Market {ctx['market']} settles through main {ctx['main']}, which is on a paused pair: {what}.")

    def _hop_quote(self, exec_: str, token_in: str, token_out: str, amount_in: int) -> int:
        """Quote of an outside hop, net of its fee. A failure is classified (``classify_error``): an RPC failure is
        ``BasisRpcError``, a revert ``BasisContractError``."""
        hop = self._contract(_cs(exec_), "HopExecutor")
        try:
            return hop.functions.quoteHop(_cs(token_in), _cs(token_out), int(amount_in)).call(
                {"from": self.client.account.address} if self.client.account else {})
        except Exception as e:  # noqa: BLE001
            err = classify_error(e, "HopExecutor", "quoteHop", hop.abi)
            if err is e:
                raise
            raise err from e

    def _is_ecosystem_token(self, token: str) -> bool:
        """Is ``token`` a BASIS factory token? False when the factory is not deployed."""
        factory = self.client.addresses["factory"]
        if is_zero_address(factory):
            return False
        return self._contract(_cs(factory), "Factory").functions.isEcosystemToken(_cs(token)).call()
