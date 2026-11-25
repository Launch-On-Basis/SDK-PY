"""Up/Down price rounds.

Not enabled yet: while ``FEATURES["up_down"]`` (basis.config.features) is False, every call here raises
``NotDeployedError`` ("Up/Down is not enabled yet.") before any network call.

Every asset and timeframe, keyed ``(asset, tf)``. Assets are resolved by their on-chain label (case-insensitive) or
raw index. ``client.updown.asset("BTC")`` returns a view with the asset fixed. Anyone can call
``settle_current_round``, ``cancel_current_round_and_start_next`` and ``advance_round``. There may be no assets listed
yet (``assets()``).
"""

import time
from decimal import Decimal
from enum import IntEnum
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from web3 import Web3

from ..config.addresses import ZERO_ADDRESS, is_zero_address
from ..config.features import FEATURES
from ..core.amounts import AmountOf, amount_of, opt_amount
from ..core.base import BaseModule
from ..core.errors import BasisError, NotDeployedError, ValidationError
from ..core.events import add_missing_event_warning
from ..core.loans import check_deadline as _check_explicit_deadline
from ..core.loans import is_asset_paused, pair_asset_paused, refuse_paused_asset
from ..core.pinned import pinned_call
from ..core.revert import is_revert_named, zero_unless_rpc
from ..core.units import (BPS, Amount, opt_uint, parse_amount, to_address, to_amount, to_bool, to_uint,
                          validate_slippage_bps)



class Timeframe(IntEnum):
    """Timeframe ids: 0=5m, 1=15m, 2=1h, 3=4h, 4=24h (``tf_duration(tf)`` has the seconds)."""
    FIVE_MIN = 0
    FIFTEEN_MIN = 1
    ONE_HOUR = 2
    FOUR_HOUR = 3
    ONE_DAY = 4


class Side(IntEnum):
    """Side enum: 0=None, 1=Bull, 2=Bear. A bet side is 'bull' | 'bear' or 1 | 2."""
    NONE = 0
    BULL = 1
    BEAR = 2


class Outcome(IntEnum):
    """Outcome enum: 0=Pending, 1=BullWins, 2=BearWins, 3=Canceled."""
    PENDING = 0
    BULL_WINS = 1
    BEAR_WINS = 2
    CANCELED = 3


UpDownAssetRef = Union[str, int]
SideInput = Union[str, int]
AmountInput = Union[int, str, float, Decimal]

MAX_CLAIM_SLICES = 25

BET_DEADLINE_SECONDS = 600



class OracleNotReadyError(BasisError):
    """Settling failed because the price feed has no usable round past the round's ``end_time`` yet. Transient: wait
    for the feed to tick and retry, or use ``advance_round`` (it polls the feed).

    ``contract_error``:
      - ``NoUpdateAfterEndTime``: the feed has not updated since the round's ``end_time``.
      - ``NoValidPriceInWindow``: no recent feed price from ``end_time`` on differs from the start price (a flat price
        never settles; the round ends with ``cancel_current_round_and_start_next``).
      - ``WaitTimeout``: ``advance_round`` polled the feed for ``max_wait_ms`` without an update past ``endTime``.
    """

    def __init__(self, message: str, asset: int, tf: int, end_time: int, contract_error: str):
        super().__init__(message)
        self.asset = asset
        self.tf = tf
        self.end_time = end_time
        self.contract_error = contract_error


def _cs(a: str) -> str:
    """Checksums an internal address (address book, chain reads). Addresses a caller passes go through
    ``to_address``, which refuses a mixed-case address with a wrong checksum instead of re-checksumming it."""
    return Web3.to_checksum_address(a)


def _eq(a: str, b: str) -> bool:
    return str(a).lower() == str(b).lower()


def _input_sale_paused(inp: str, factory_token: bool) -> str:
    """The 'pair asset paused' message for a bet paid in ``inp``, a BASIS token of another pair, while that pair's
    asset is paused. Same text as markets ``buy`` and in TS."""
    return (f"no bet paid in {inp} can be placed: the bet sells it for this asset"
            + (", and every sale pays out your pending holder rewards on it in this asset" if factory_token else ""))


def _input_sale_asset(module: Any, inp: str, main: str, settle: str) -> Optional[Dict[str, Any]]:
    """The other pair asset a bet paid in ``inp`` needs unpaused, when it is not the settle asset ``settle`` (checked
    on its own): for a main of another pair, or a factory token on one. None for anything else (a token of the asset's
    own main ``main``, or an outside token). ``factory_token``: paying in it also pays out the bettor's pending holder
    rewards on it, in that asset. Same rule as markets ``buy``."""
    mains = module.client.mains
    root = inp
    factory_token = False
    if not mains.is_main(inp):
        factory = module.client.addresses.get("factory")
        if is_zero_address(factory):
            return None
        if not module._contract(_cs(factory), "Factory").functions.isEcosystemToken(_cs(inp)).call():
            return None
        root = _cs(module._contract(_cs(inp), "EcosystemToken").functions.quoteToken().call())
        factory_token = True
        if not mains.is_main(root):
            return None
    if _eq(root, main):
        return None
    info = mains.get(root)
    if _eq(info.pair_asset, settle):
        return None
    return {"asset": info.pair_asset, "symbol": info.pair_symbol, "factory_token": factory_token}


def _is_int(v: Any) -> bool:
    """JS ``Number.isInteger``: an int (not bool) or a whole float."""
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and v.is_integer())


def _ms_option(v: Any, field: str, default: int) -> int:
    """A time option in ms: a whole number (int, or a whole float like a TS number), ``default`` when None; else
    ``ValidationError(field)``."""
    if v is None:
        return default
    return to_uint(v, field)


def _to_round(r: Sequence[Any], price_decimals: int) -> Dict[str, Any]:
    """A raw round plus the feed's decimals."""
    return {
        "start_time": r[0],
        "end_time": r[1],
        "settled_at": r[2],
        "start_price": r[3],
        "end_price": r[4],
        "end_price_round_id": r[5],
        "virt_bull": r[6],
        "virt_bear": r[7],
        "bull_pool": r[8],
        "bear_pool": r[9],
        "shares_bull": r[10],
        "shares_bear": r[11],
        "seed_bonus": r[12],
        "outcome": Outcome(r[13]),
        "price_decimals": price_decimals,
    }


def _fee_of(net_amount: int, fee_bps: int) -> int:
    """The bet fee in pool tokens, worked out from the net stake and the fee rate (bps): the largest fee consistent
    with ``net``, never below the real fee and at most 1 wei above it (two gross amounts can net the same). 0 for a
    rate of 0 or >= 10000."""
    if fee_bps <= 0 or fee_bps >= BPS:
        return 0
    return net_amount * BPS // (BPS - fee_bps) - net_amount


def _fee_low_of(net_amount: int, fee_bps: int, fee: int) -> int:
    """The smallest fee consistent with the net stake (``_fee_of`` is the largest). Equal to ``fee`` except at a
    rounding boundary, where it is ``fee - 1``."""
    if fee_bps <= 0 or fee_bps >= BPS:
        return 0
    x = net_amount * fee_bps - BPS
    low = 0 if x < 0 else x // (BPS - fee_bps) + 1
    return min(low, fee)


def _entitlement_of(r: Dict[str, Any], b: Dict[str, Any]) -> int:
    """The pool tokens ``claim`` still owes on a settled round: ``pending`` after a first claim; else the stake back
    on a canceled round or one nobody won, the stake plus winnings for a winner, 0 for a loser (and while pending)."""
    if b["amount"] == 0:
        return 0
    if b["claimed"]:
        return b["pending"]
    if r["outcome"] == Outcome.PENDING:
        return 0
    if r["outcome"] == Outcome.CANCELED:
        return b["amount"]
    bull_wins = r["outcome"] == Outcome.BULL_WINS
    seed = r["seed_bonus"]
    winner_pool = r["bull_pool"] - seed // 2 if bull_wins else r["bear_pool"] - (seed - seed // 2)
    if winner_pool == 0:
        return b["amount"]
    if b["side"] != (Side.BULL if bull_wins else Side.BEAR):
        return 0
    winner_shares = r["shares_bull"] if bull_wins else r["shares_bear"]
    return b["amount"] + b["shares"] * (r["bull_pool"] + r["bear_pool"] - winner_pool) // winner_shares


_ADVANCE_RACES = ("RoundAlreadySettled", "TooEarlyToSettle", "NoActiveRound", "TooLateForValidPrice", "PriceDriftTooLarge")


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _payout_if_win(r: Sequence[Any], side: int, net: int, shares: int, carryover_bps: int) -> Optional[int]:
    """Pool tokens a new bet (``net`` stake, ``shares``) on ``side`` would be owed if that side wins, at the current
    pools of the raw round ``r`` with this bet added (as ``quote_current_payout`` estimates it). None when the winning
    side would hold nothing."""
    bull_pool, bear_pool, shares_bull, shares_bear, seed = r[8], r[9], r[10], r[11], r[12]
    bull = side == Side.BULL
    seed_half = seed // 2 if bull else seed - seed // 2
    winner_pool = (bull_pool if bull else bear_pool) + net - seed_half
    winner_shares = (shares_bull if bull else shares_bear) + shares
    if winner_pool <= 0 or winner_shares <= 0:
        return None
    matched = min(seed, winner_pool)
    loser_real = bull_pool + bear_pool + net - winner_pool - seed
    loser_seed_half = matched - matched // 2 if bull else matched // 2
    carry = (loser_real + loser_seed_half) * carryover_bps // BPS
    return net + shares * (loser_real + matched - carry) // winner_shares


def _is_advance_race(e: BaseException) -> bool:
    """Errors auto-advance may swallow: another caller advanced first, or the feed is not ready."""
    if isinstance(e, OracleNotReadyError):
        return True
    if isinstance(e, ValidationError):
        return e.rule in _ADVANCE_RACES
    return any(is_revert_named(e, n) for n in _ADVANCE_RACES)




class UpDownModule(BaseModule):
    """Up/Down price rounds (``client.addresses["upDown"]``).

    Result dicts (snake_case keys):
      - asset info: index, label, price_feed, feed_decimals, pool_token, paused, main, settle, settle_decimals,
        settle_symbol, virt_floor (the asset's odds-floor setting, pool tokens; it sets the minimum net bet, see
        ``min_net_bet``).
      - round: start_time, end_time, settled_at, start_price, end_price, end_price_round_id, virt_bull, virt_bear,
        bull_pool, bear_pool, shares_bull, shares_bear, seed_bonus, outcome, price_decimals. Pools / odds weights
        (``virt_*``) / shares / seed are pool-token raw (18 dec); prices are scaled by ``price_decimals``.
      - user bet: side, amount (net stake; 0 = no bet), shares, claimed, was_win, pending.
      - lobby (``get_lobby``): block_number, chain_time, paused, bet_lock, host_listed, assets[] with index, label,
        paused, price_feed, feed_decimals, price, price_updated_at, price_age, price_stale, pool_token, main, settle,
        settle_decimals, settle_symbol, pair_active, main_trading_enabled, timeframes[] (tf, round_id, round,
        bull_probability, slippage_threshold, closes_at, bettable, needs_advance).
      - claimables (``get_claimable``): user, block_number, items[] (asset, label, tf, round_id, payout, quotable),
        totals[] (settle, amount).
      - round end (``settle_current_round`` / ``cancel_current_round_and_start_next`` / ``advance_round``): the tx dict
        plus round_id (the round closed), outcome, end_price (0 on a cancel), no_winner (nobody on the winning side:
        every bet refunds), carryover (Amount, pool tokens rolled into the next seed), next_round {round_id,
        start_time, end_time, start_price, seed_bonus (Amount, pool tokens)} (the round opened); each None, with a
        ``warnings`` entry, when its event is missing.

    Inputs, checked before any read: an address a caller passes (``input``, ``bettor``, ``user``, ``input_token``) is
    0x plus 40 hex characters, a mixed-case one with a correct checksum (else ``ValidationError`` named after it, as
    in TS); an optional argument given as None takes its default (required ones refuse None).
    """

    def __init__(self, client):
        super().__init__(client)
        self._label_index: Optional[Dict[str, List[int]]] = None
        self._count_cache: Optional[int] = None
        self._constants_cache: Optional[Dict[str, int]] = None
        self._feed_decimals: Dict[str, int] = {}
        self._views: Dict[Tuple[str, Any], "UpDownAsset"] = {}

    _ALWAYS = frozenset({"client", "web3", "deployed"})

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("_") and name not in UpDownModule._ALWAYS and not FEATURES["up_down"]:
            raise NotDeployedError("MultiUpDown", object.__getattribute__(self, "client").chain_id,
                                   "Up/Down is not enabled yet.")
        return object.__getattribute__(self, name)

    @property
    def deployed(self) -> bool:
        """True when Up/Down is enabled (``FEATURES["up_down"]``) and has an address on this chain."""
        return FEATURES["up_down"] and not is_zero_address(self.client.addresses["upDown"])

    @property
    def address(self) -> str:
        """The Up/Down contract address (raises ``NotDeployedError`` when zero)."""
        return self._require(self.client.addresses["upDown"], "MultiUpDown")

    @property
    def _ud(self):
        return self._contract(self.address, "MultiUpDown")

    @property
    def _quoter(self) -> str:
        """The signer, else 0x0 (for quotes)."""
        return self.client.account.address if self.client.account else ZERO_ADDRESS


    def asset(self, ref: UpDownAssetRef) -> "UpDownAsset":
        """A view with ``asset`` fixed (no RPC until a method is called). Views are cached per ref: an index and a
        label never share one (``asset(1)`` and ``asset("#1")`` are two views)."""
        if isinstance(ref, str):
            key: Tuple[str, Any] = ("label", ref.strip().lower())
        elif isinstance(ref, (int, float)) and not isinstance(ref, bool):
            key = ("index", ref)
        else:
            return UpDownAsset(self, ref)
        v = self._views.get(key)
        if v is None:
            v = UpDownAsset(self, ref)
            self._views[key] = v
        return v

    def asset_count(self) -> int:
        """Number of listed assets."""
        return int(self._ud.functions.assetCount().call())

    def assets(self) -> List[Dict[str, Any]]:
        """Every listed asset, in index order (fresh reads; feed and settle decimals cached)."""
        return [self._read_asset(i) for i in range(self.asset_count())]

    def get_asset(self, ref: UpDownAssetRef) -> Dict[str, Any]:
        """One asset by label or index. Raises ``ValidationError('InvalidAsset')`` for an unknown one."""
        return self._read_asset(self.resolve_asset(ref))

    def resolve_asset(self, ref: UpDownAssetRef) -> int:
        """The index of an asset. Labels are matched case-insensitively against each asset's label (cached; re-read
        once on a miss, since assets can be added). A label listed twice is ambiguous: pass the index."""
        if not isinstance(ref, str):
            if isinstance(ref, bool) or not isinstance(ref, (int, float)):
                raise ValidationError("InvalidAsset", f"An asset is a label or an index 0-255, got {ref}.")
            if not _is_int(ref) or ref < 0 or ref > 255:
                raise ValidationError("InvalidAsset", f"Asset indexes are 0-255, got {ref}.")
            ref = int(ref)
            count = self._cached_count()
            if ref >= count:
                self._count_cache = None
                count = self._cached_count()
            if ref >= count:
                raise ValidationError("InvalidAsset", f"Asset {ref} does not exist (assetCount = {count}).")
            return ref
        key = ref.strip().lower()
        if not key:
            raise ValidationError("InvalidAsset", "Empty asset label.")
        hit = self._labels().get(key)
        if not hit:
            self._label_index = None
            self._count_cache = None
            hit = self._labels().get(key)
        if not hit:
            known = ", ".join(self._labels().keys()) or "none"
            raise ValidationError("InvalidAsset", f'No Up/Down asset labelled "{ref}" (known: {known}).')
        if len(hit) > 1:
            raise ValidationError(
                "InvalidAsset", f'The label "{ref}" is used by assets {", ".join(map(str, hit))}; pass the index instead.')
        return hit[0]

    def _cached_count(self) -> int:
        if self._count_cache is None:
            self._count_cache = self.asset_count()
        return self._count_cache

    def _labels(self) -> Dict[str, List[int]]:
        if self._label_index is None:
            n = self.asset_count()
            m: Dict[str, List[int]] = {}
            for i in range(n):
                k = self._ud.functions.assets(i).call()[3].strip().lower()
                m.setdefault(k, []).append(i)
            self._label_index = m
        return self._label_index

    def _read_asset(self, index: int) -> Dict[str, Any]:
        price_feed, pool_token, paused, label, main, settle, virt_floor = self._ud.functions.assets(index).call()
        meta = self.client.mains.token_meta(settle)
        return {
            "index": index, "label": label, "price_feed": price_feed, "feed_decimals": self._feed_decimals_of(price_feed),
            "pool_token": pool_token, "paused": paused, "main": main, "settle": settle,
            "settle_decimals": int(meta["decimals"]), "settle_symbol": meta["symbol"], "virt_floor": virt_floor,
        }

    def _feed_decimals_of(self, feed: str) -> int:
        """``decimals()`` of a price feed (cached per feed)."""
        key = feed.lower()
        d = self._feed_decimals.get(key)
        if d is None:
            d = int(self._contract(feed, "AggregatorV3").functions.decimals().call())
            self._feed_decimals[key] = d
        return d


    def BET_LOCK(self) -> int:  # noqa: N802
        """``BET_LOCK()``: seconds before ``end_time`` when betting closes."""
        return self._constants()["bet_lock"]

    def FINALIZE_WINDOW(self) -> int:  # noqa: N802
        """``FINALIZE_WINDOW()``: seconds after ``end_time`` during which a round can be settled; after it, only canceled."""
        return self._constants()["finalize_window"]

    def PRICE_STALENESS(self) -> int:  # noqa: N802
        """``PRICE_STALENESS()``: seconds; a round needing a fresh start price cannot open on an older feed update."""
        return self._constants()["price_staleness"]

    def swap(self) -> str:
        """``swap()``."""
        return self._ud.functions.swap().call()

    def taxes(self) -> str:
        """``taxes()``."""
        return self._ud.functions.taxes().call()

    def factory(self) -> str:
        """``factory()``."""
        return self._ud.functions.factory().call()

    def paused(self) -> bool:
        """Global pause flag (bets revert; settle and claim still work). Each asset also has its own
        (``get_asset()["paused"]``)."""
        return self._ud.functions.paused().call()

    def tf_duration(self, tf: int) -> int:
        """Round duration of a timeframe, seconds."""
        return self._ud.functions.tfDuration(self._tf_arg(tf)).call()

    def _constants(self) -> Dict[str, int]:
        if self._constants_cache is None:
            fns = self._ud.functions
            self._constants_cache = {
                "bet_lock": fns.BET_LOCK().call(),
                "finalize_window": fns.FINALIZE_WINDOW().call(),
                "price_staleness": fns.PRICE_STALENESS().call(),
                "tf_count": int(fns.TF_COUNT().call()),
                "carryover_bps": int(fns.CARRYOVER_BPS().call()),
            }
        return self._constants_cache

    def _tf_arg(self, tf: int) -> int:
        tf_count = self._constants()["tf_count"]
        if not _is_int(tf) or tf < 0 or tf >= tf_count:
            raise ValidationError("InvalidTimeframe", f"Timeframes are 0-{tf_count - 1} (5m, 15m, 1h, 4h, 24h), got {tf}.")
        return int(tf)

    def _args(self, asset: UpDownAssetRef, tf: int):
        """The asset, then the timeframe, one after the other (as TS)."""
        a = self.resolve_asset(asset)
        return a, self._tf_arg(tf)

    @staticmethod
    def _side_arg(side: SideInput) -> int:
        if not isinstance(side, bool):
            if side == "bull" or side == Side.BULL:
                return 1
            if side == "bear" or side == Side.BEAR:
                return 2
        raise ValidationError("side", f"Side must be 'bull' | 'bear' (1 | 2), got {side}.")

    def _chain_now(self) -> int:
        return self.web3.eth.get_block("latest")["timestamp"]

    def _settle_amount(self, asset: int, raw: int) -> Amount:
        settle = self._ud.functions.assets(asset).call()[5]
        meta = self.client.mains.token_meta(settle)
        return to_amount(raw, int(meta["decimals"]), "pairAsset", settle, meta["symbol"])

    def _parse(self, amount: AmountInput, token: str) -> int:
        """int = raw units; str / float / Decimal = human amount in ``token``'s decimals. Bools rejected."""
        if isinstance(amount, bool):
            raise ValidationError("amount", f"The bet amount must be a number, got {amount}.")
        if isinstance(amount, int):
            return amount
        if not isinstance(amount, (str, float, Decimal)):
            raise ValidationError("amount", f"The bet amount must be a number, got {amount!r}.")
        return parse_amount(amount, int(self.client.mains.token_meta(token)["decimals"]))


    def current_round_id(self, asset: UpDownAssetRef, tf: int) -> int:
        """Current round id of ``(asset, tf)``; 0 = never started."""
        a, t = self._args(asset, tf)
        return self._ud.functions.currentRoundId(a, t).call()

    def get_round(self, asset: UpDownAssetRef, tf: int, round_id: int) -> Dict[str, Any]:
        """A round plus the feed's ``price_decimals``. A round id past the current one reads back zeroed."""
        rid = to_uint(round_id, "roundId")
        a, t = self._args(asset, tf)
        r = self._ud.functions.getRound(a, t, rid).call()
        info = self._ud.functions.assets(a).call()
        return _to_round(r, self._feed_decimals_of(info[0]))

    def get_current_round(self, asset: UpDownAssetRef, tf: int) -> Optional[Dict[str, Any]]:
        """``{"round_id", "round"}`` for the current round, or None when ``(asset, tf)`` was never started."""
        round_id = self.current_round_id(asset, tf)
        if round_id == 0:
            return None
        return {"round_id": round_id, "round": self.get_round(asset, tf, round_id)}

    def get_user_bet(self, asset: UpDownAssetRef, tf: int, round_id: int, user: Optional[str] = None) -> Dict[str, Any]:
        """``user``'s bet on a round (``amount`` 0 = no bet). ``user`` defaults to the signer (None)."""
        rid = to_uint(round_id, "roundId")
        who = self._account if user is None else to_address(user, "user")
        a, t = self._args(asset, tf)
        b = self._ud.functions.getUserBet(a, t, rid, who).call()
        return {"side": Side(b[0]), "amount": b[1], "shares": b[2], "claimed": b[3], "was_win": b[4], "pending": b[5]}

    def current_bull_probability(self, asset: UpDownAssetRef, tf: int) -> int:
        """Implied bull probability of the current round, scaled by 1e18 (0 when not started)."""
        a, t = self._args(asset, tf)
        return self._ud.functions.currentBullProbability(a, t).call()

    def current_slippage_threshold(self, asset: UpDownAssetRef, tf: int) -> int:
        """Slippage threshold of the current round, bps (it tightens as the round runs)."""
        a, t = self._args(asset, tf)
        return self._ud.functions.currentSlippageThreshold(a, t).call()

    def pending_carryover(self, asset: UpDownAssetRef, tf: int) -> int:
        """Carry-over waiting to seed the next round, pool tokens (raw, 18 dec)."""
        a, t = self._args(asset, tf)
        return self._ud.functions.pendingCarryover(a, t).call()

    def protocol_virt_base(self, asset: UpDownAssetRef, tf: int) -> int:
        """Odds weight each new round opens with (per side), pool tokens (raw, 18 dec)."""
        a, t = self._args(asset, tf)
        return self._ud.functions.protocolVirtBase(a, t).call()

    def min_net_bet(self, asset: UpDownAssetRef) -> int:
        """Smallest net bet, pool tokens (raw, 18 dec), compared with the stake after conversion and the bet fee. A
        smaller bet reverts ``InvalidAmount``."""
        a = self.resolve_asset(asset)
        return self._ud.functions.assets(a).call()[6] // BPS

    def min_bet_input(self, asset: UpDownAssetRef, input: Optional[str] = None,
                      bettor: Optional[str] = None) -> Dict[str, Any]:
        """The smallest bet ``bettor`` can place on ``asset`` in ``input`` (raw input units). The bet must net at least
        ``min_net_bet`` pool tokens after the fee, and enough that its fee does not round to nothing (below that the bet
        reverts with no reason): ``min_net_amount`` is the larger of the two. For the pool token the answer is exact.
        For any other input the net stake is estimated from a probe of one whole input token, scaled to the target and
        confirmed, with a 1% margin. It does not need an open round. ``input`` defaults to the settle asset, ``bettor``
        to the signer, else 0x0.

        Returns {min_input (raw input units; estimated from the current conversion for any input but the pool token, so
        it moves with the price), input_token, min_net_amount (pool tokens), min_net_bet (pool tokens), precision_floor
        (raw settle asset the net stake must be worth; 0 when the bettor pays no fee), fee_bps}.
        Raises ``ValidationError('quote')`` when the pool token or the input cannot be priced right now, 'pair not
        active' while the main's pair is paused (for any input but the pool token, and for the pool token when the
        bettor pays a bet fee; priced or not, as ``quote_bet``), and 'above cap' when an outside hop on the input's route
        cannot take a probe of it right now (its message says when waiting clears it, or to send less); an RPC failure on
        the way is ``BasisRpcError``, never 'quote'."""
        inp_in = None if input is None else to_address(input, "input")
        who_in = None if bettor is None else to_address(bettor, "bettor")
        info = self._read_asset(self.resolve_asset(asset))
        inp = info["settle"] if inp_in is None else inp_in
        who = self._quoter if who_in is None else who_in
        fee_bps = int(self.client.taxes.get_tax_rate(info["pool_token"], who))
        if fee_bps >= BPS:
            raise ValidationError("quote", f"A bet fee of {fee_bps} bps leaves no stake: no bet on {info['label']} can pass.")
        min_net_bet = info["virt_floor"] // BPS
        precision_floor = 0
        min_net_amount = min_net_bet
        if fee_bps > 0:
            precision_floor = _ceil_div(_ceil_div(BPS, fee_bps) * 12, 10)
            probe = min_net_bet * 100 if min_net_bet * 100 > 0 else 10 ** 18
            try:
                value = self.client.trading.get_amounts_out(probe, [info["pool_token"], info["main"], info["settle"]])
            except Exception as e:  # noqa: BLE001
                value = zero_unless_rpc(e, "Swap", "getAmountsOut")
            if value == 0:
                self._refuse_paused_pair(info, "bet")
                raise ValidationError(
                    "quote", f"The {info['label']} pool token cannot be priced in {info['settle_symbol']} right now.")
            min_net_amount = max(min_net_amount, _ceil_div(precision_floor * probe, value))

        def result(min_input: int) -> Dict[str, Any]:
            return {"min_input": min_input, "input_token": inp, "min_net_amount": min_net_amount,
                    "min_net_bet": min_net_bet, "precision_floor": precision_floor, "fee_bps": fee_bps}

        if _eq(inp, info["pool_token"]):
            if fee_bps > 0:
                self._refuse_paused_pair(info, "bet")
            return result((min_net_amount - 1) * BPS // (BPS - fee_bps) + 1 if min_net_amount > 0 else 0)

        x = 10 ** int(self.client.mains.token_meta(inp)["decimals"])
        got = 0
        for _ in range(4):
            if got != 0:
                break
            try:
                got = self._estimate_net(info, inp, x, who, fee_bps)
            except Exception as e:  # noqa: BLE001
                if isinstance(e, ValidationError) and e.rule == "above cap":
                    raise
                got = zero_unless_rpc(e, None, "quoteBetInput")
            if got == 0:
                x *= 1000
        self._refuse_paused_pair(info, "bet")
        if got == 0:
            raise ValidationError("quote", f"{inp} does not convert into the {info['label']} pool token right now.")
        for _ in range(3):
            x = _ceil_div(x * min_net_amount, got) * 101 // 100 + 1
            got = self._estimate_net(info, inp, x, who, fee_bps)
            if got >= min_net_amount:
                break
        return result(x)

    def _estimate_net(self, info: Dict[str, Any], inp: str, raw: int, bettor: str,
                      fee_bps: Optional[int] = None) -> int:
        """The net pool tokens a bet of ``raw`` ``inp`` would credit: step 1 of ``quote_bet``, then the quote into the
        pool token, minus the bet fee. Needs no open round. 0 when the pool-token quote is refused right now; an RPC
        failure is raised (``BasisRpcError``), and so is a refusal of step 1 (``quote_bet_input``)."""
        step1 = self.client.market_reader.quote_bet_input(
            input=inp, amount=raw, pair_asset=info["settle"], main=info["main"], market_token=info["pool_token"],
            bettor=bettor)
        tok = step1["token"].lower()
        if tok == info["pool_token"].lower():
            gross = step1["amount"]
        else:
            path = ([info["main"], info["pool_token"]] if tok == info["main"].lower()
                    else [info["settle"], info["main"], info["pool_token"]] if tok == info["settle"].lower() else None)
            gross = 0
            if path is not None:
                try:
                    gross = self.client.trading.get_amounts_out(step1["amount"], path)
                except Exception as e:  # noqa: BLE001
                    gross = zero_unless_rpc(e, "Swap", "getAmountsOut")
        rate = int(self.client.taxes.get_tax_rate(info["pool_token"], bettor)) if fee_bps is None else fee_bps
        return gross - gross * rate // BPS


    def quote_shares_for_input(self, asset: UpDownAssetRef, tf: int, side: SideInput, input_token: str, amount: int,
                               user: Optional[str] = None) -> Dict[str, int]:
        """The ``{"shares", "net_amount"}`` a bet of ``amount`` (raw ``input_token`` units) mints and credits now. ``input_token`` must be the pool token, the settle asset or the main; anything else quotes (0, 0) (use
        ``quote_bet``). (0, 0) also when the round is not bettable. Exact only in this block: never use it raw as
        ``min_shares``. ``user`` defaults to the signer, else 0x0 (None)."""
        s = self._side_arg(side)
        token = to_address(input_token, "inputToken")
        raw = to_uint(amount, "amount")
        who = self._quoter if user is None else to_address(user, "user")
        a, t = self._args(asset, tf)
        shares, net_amount = self._ud.functions.quoteSharesForInput(a, t, s, token, raw, who).call()
        return {"shares": shares, "net_amount": net_amount}

    def quote_bet(self, asset: UpDownAssetRef, tf: int, side: SideInput, amount: AmountInput,
                  input: Optional[str] = None, bettor: Optional[str] = None,
                  slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """The full bet quote for any accepted input (pool token, settle asset, the asset's main, any BASIS token, an
        outside token with a hop route): step 1 ``market_reader.quote_bet_input`` (cross / hop conversion toward the
        settle asset or the main), step 2 ``quote_shares_for_input``, then ``min_shares`` = shares minus
        ``slippage_bps`` (default 3%). ``amount``: int = raw input units, str / float / Decimal = human amount.

        Returns {shares, min_shares, net_amount (pool tokens after the fee), route ('direct' | 'cross' | 'hop'),
        input_token, input_amount, converted_token, converted_amount (after the first conversion step), path
        (first-step path: [] direct, the ``trading.quote_cross`` path, [input, settle] hop), min_net_bet, fee_bps (read
        live for the bettor), fee (pool tokens, worked out from net_amount and fee_bps: never below the fee taken, at
        most 1 wei above it), round_id, end_time, closes_at (end_time - BET_LOCK), slippage_threshold
        (``current_slippage_threshold``, bps), other_side (the bettor already holds the other side of this round: the
        bet would revert; a flag, not an error), allowance (the bettor's allowance of ``input_token`` to the Up/Down
        contract, raw; 0 for a 0x0 bettor), needs_approval (``allowance < input_amount``: ``bet`` sends an approve
        first, on its own), potential_payout (Amount, settle asset, unit 'pairAsset': what this bet alone would pay if
        its side wins: an estimate at the current pools with this bet added, valued as a claim would pay it. Every later
        bet moves it. It leaves out a stake the bettor already holds in the round (``quote_current_payout`` covers that
        one). None when the payout cannot be priced right now, or when that read fails (an RPC failure too): it is for
        display only and never fails the quote)}.
        Raises ``ValidationError``, in the order checked, when the round is not bettable ('BettingClosed'), the main's
        pair is paused and the bet needs it ('pair not active', as markets ``quote_bet``: any input but the pool token,
        or any bet that pays a fee; quoted or not, such a bet reverts), the bet fee would be worth less than one raw
        settle unit ('precision': the bet would revert with no reason), the input converts to 0 pool tokens or the net
        stake is below ``min_net_bet`` ('InvalidAmount'), or the side is too skewed to mint a share ('ZeroShares'). Step
        1's own refusals ('no route', 'above cap', ...) come first, as they are. ``min_bet_input`` gives the smallest bet
        that passes.
        """
        inp_in = None if input is None else to_address(input, "input")
        who_in = None if bettor is None else to_address(bettor, "bettor")
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        s = self._side_arg(side)
        a, t = self._args(asset, tf)
        info = self._read_asset(a)
        inp = info["settle"] if inp_in is None else inp_in
        raw = self._parse(amount, inp)
        if raw <= 0:
            raise ValidationError("amount", "The bet amount must be above 0.")
        who = self._quoter if who_in is None else who_in

        step1 = self.client.market_reader.quote_bet_input(
            input=inp, amount=raw, pair_asset=info["settle"], main=info["main"], market_token=info["pool_token"],
            bettor=who)
        q = self.quote_shares_for_input(a, t, s, step1["token"], step1["amount"], who)
        shares, net_amount = q["shares"], q["net_amount"]
        fee_bps = int(self.client.taxes.get_tax_rate(info["pool_token"], who))
        round_id = self._ud.functions.currentRoundId(a, t).call()
        slippage_threshold = self._ud.functions.currentSlippageThreshold(a, t).call()
        min_net_bet = info["virt_floor"] // BPS
        if shares == 0:
            self._refuse_closed_round(info, t, round_id)
        if not _eq(inp, info["pool_token"]) or fee_bps > 0:
            self._refuse_paused_pair(info, "bet")
        fee = _fee_of(net_amount, fee_bps)
        if net_amount < info["virt_floor"]:
            self._check_fee_sells(info, net_amount, fee_bps, fee)
        if net_amount == 0:
            raise ValidationError(
                "InvalidAmount",
                f"The bet converts to 0 pool tokens on {info['label']} tf={t} (too small, or the input cannot be priced "
                "right now), so it would revert. Bet more (see min_bet_input).",
            )
        if net_amount < min_net_bet:
            raise ValidationError(
                "InvalidAmount",
                f"The bet nets {net_amount} pool tokens after the fee, below the minimum net bet of {min_net_bet}. "
                "Bet more (see min_bet_input).",
            )
        if shares == 0:
            raise ValidationError(
                "ZeroShares",
                f"The bet would mint 0 shares on {info['label']} tf={t} round {round_id}: that side is too skewed for a "
                "bet this small. Bet more.",
            )
        min_shares = self._resolve_min(None, slippage_bps, lambda: shares, "updown", "minShares")
        rnd = self._ud.functions.getRound(a, t, round_id).call()
        end_time = rnd[1]
        other_side = False
        allowance = 0
        if not is_zero_address(who):
            bet_side = self._ud.functions.getUserBet(a, t, round_id, who).call()[0]
            other_side = bet_side != Side.NONE and bet_side != s
            allowance = self._contract(inp, "ERC20").functions.allowance(who, self.address).call()
        win_tokens = _payout_if_win(rnd, s, net_amount, shares, self._constants()["carryover_bps"])
        win_raw = None
        if win_tokens is not None:
            try:
                win_raw = self.client.trading.get_amounts_out(
                    win_tokens, [info["pool_token"], info["main"], info["settle"]])
            except Exception:  # noqa: BLE001
                win_raw = None
        return {
            "shares": shares, "min_shares": min_shares, "net_amount": net_amount, "route": step1["route"],
            "input_token": inp, "input_amount": raw, "converted_token": step1["token"],
            "converted_amount": step1["amount"], "path": list(step1["path"]), "min_net_bet": min_net_bet,
            "fee_bps": fee_bps, "fee": fee, "round_id": round_id, "end_time": end_time,
            "closes_at": end_time - self._constants()["bet_lock"], "slippage_threshold": slippage_threshold,
            "other_side": other_side, "allowance": allowance, "needs_approval": allowance < raw,
            "potential_payout": None if win_raw is None else to_amount(
                win_raw, info["settle_decimals"], "pairAsset", info["settle"], info["settle_symbol"]),
        }

    def _refuse_closed_round(self, info: Dict[str, Any], tf: int, round_id: int) -> None:
        """When ``quote_shares_for_input`` gave 0 shares: (0, 0) comes back for a round that is not open
        ('BettingClosed'). On an open round it returns, and the quote's next checks name the cause (the fee, the
        minimum bet, the share check)."""
        rnd = self._ud.functions.getRound(info["index"], tf, round_id).call()
        now = self._chain_now()
        if round_id == 0 or rnd[13] != Outcome.PENDING or now + self._constants()["bet_lock"] + 1 >= rnd[1]:
            raise ValidationError(
                "BettingClosed",
                f"No shares for this bet on {info['label']} tf={tf}: the round is not open for bets (not started, "
                "settled, or within BET_LOCK of its end).",
            )

    def _check_fee_sells(self, info: Dict[str, Any], net_amount: int, fee_bps: int, fee: int) -> None:
        """'precision' when the bet fee is worth less than one raw settle unit: such a bet reverts with no reason
        (possible near the minimum net bet on a 6-decimal settle asset). ``fee`` (from the net stake) can be 1 wei above
        the real fee, so the smallest fee consistent with the net stake is valued: the check never passes a bet that
        reverts, and at worst asks for a slightly larger bet at the exact boundary. An RPC failure of the valuation is
        ``BasisRpcError``, never 'precision'. The caller (``quote_bet``) has already found the main's pair active."""
        if fee == 0:
            return
        low = _fee_low_of(net_amount, fee_bps, fee)
        valued = low if low > 0 else fee
        try:
            value = self.client.trading.get_amounts_out(valued, [info["pool_token"], info["main"], info["settle"]])
        except Exception as e:  # noqa: BLE001
            value = zero_unless_rpc(e, "Swap", "getAmountsOut")
        if value == 0:
            raise ValidationError(
                "precision",
                f"The bet fee ({fee} pool tokens) sells for less than one raw unit of {info['settle_symbol']}, "
                "so the bet would revert. Bet more (see min_bet_input).",
            )

    def quote_current_payout(self, asset: UpDownAssetRef, tf: int, user: Optional[str] = None) -> Amount:
        """``user``'s estimated full payout on the current round if their side wins (settle asset). ``user`` defaults
        to the signer (None)."""
        who = self._account if user is None else to_address(user, "user")
        a, t = self._args(asset, tf)
        raw = self._ud.functions.quoteCurrentPayout(a, t, who).call()
        return self._settle_amount(a, raw)

    def quote_claim_payout(self, asset: UpDownAssetRef, tf: int, round_id: int, user: Optional[str] = None) -> Amount:
        """What ``claim`` would pay ``user`` now for the whole remaining entitlement in one slice (settle asset): after
        a partial claim, the value of what is still owed. 0 when there is nothing to claim. ``user`` defaults to the
        signer (None)."""
        rid = to_uint(round_id, "roundId")
        who = self._account if user is None else to_address(user, "user")
        a, t = self._args(asset, tf)
        raw = self._ud.functions.quoteClaimPayout(a, t, rid, who).call()
        return self._settle_amount(a, raw)


    def get_lobby(self, assets: Optional[Sequence[UpDownAssetRef]] = None,
                  timeframes: Optional[Sequence[int]] = None) -> Dict[str, Any]:
        """The Up/Down lobby in one pass, every read pinned to the latest block: per asset its label, pause flag, feed
        price and age, pool token, main and settle asset, ``pair_active`` (the main's pair is not paused) and
        ``main_trading_enabled`` (trading on the main has opened); per timeframe the current round id and round, bull
        probability, slippage threshold, when betting closes (``closes_at`` = end_time - BET_LOCK), ``bettable`` now and
        ``needs_advance``. Global: pause, ``bet_lock``, ``host_listed`` (betting is switched on for Up/Down; while False
        every bet reverts "hosts only").

        ``assets`` (labels or indexes, default all) and ``timeframes`` (default 0-4) filter; duplicates are dropped.
        Each must be a list or tuple when given (``ValidationError('assets' / 'timeframes')``, before any read: a
        string is never read character by character).
        ``bettable``: nothing paused, host listed, pair active, trading enabled and the round pending with
        ``chain_time + BET_LOCK < end_time`` (the minimum bet, the one-side rule and the input route are per bet).
        Feed fields (price, price_updated_at, price_age, price_stale) are None when the feed cannot be read;
        ``price_stale`` = older than ``PRICE_STALENESS`` (a round needing a fresh start price cannot open on it).
        """
        if assets is not None and not isinstance(assets, (list, tuple)):
            raise ValidationError("assets", "assets must be an array of asset labels or indexes.")
        if timeframes is not None and not isinstance(timeframes, (list, tuple)):
            raise ValidationError("timeframes", "timeframes must be an array of timeframes.")
        address = self.address
        ud = self._ud
        block = self.web3.eth.get_block("latest")
        bn = block["number"]
        now = block["timestamp"]
        c = self._constants()
        bet_lock, staleness = c["bet_lock"], c["price_staleness"]
        if assets is not None:
            indexes = list(dict.fromkeys(self.resolve_asset(r) for r in assets))
        else:
            indexes = list(range(int(pinned_call(ud.functions.assetCount(), bn))))
        if timeframes is not None:
            tfs = list(dict.fromkeys(self._tf_arg(t) for t in timeframes))
        else:
            tfs = list(range(c["tf_count"]))

        paused = pinned_call(ud.functions.paused(), bn)
        taxes_addr = pinned_call(ud.functions.taxes(), bn)
        swap_addr = pinned_call(ud.functions.swap(), bn)
        host_listed = pinned_call(self._contract(taxes_addr, "Taxes").functions.isPrediction(_cs(address)), bn)
        swap = self._contract(swap_addr, "Swap")

        out_assets: List[Dict[str, Any]] = []
        for i in indexes:
            price_feed, pool_token, asset_paused, label, main, settle, _vf = pinned_call(ud.functions.assets(i), bn)
            feed_decimals = self._feed_decimals_of(price_feed)
            meta = self.client.mains.token_meta(settle)
            try:
                feed = pinned_call(self._contract(price_feed, "AggregatorV3").functions.latestRoundData(), bn)
            except Exception:  # noqa: BLE001
                feed = None
            pair_active = bool(pinned_call(swap.functions.pairs(main), bn)[2])
            trading = bool(pinned_call(self._contract(main, "MainToken").functions.tradingEnabled(), bn))
            open_ = (not paused) and (not asset_paused) and bool(host_listed) and pair_active and trading
            updated_at = feed[3] if feed is not None else None
            age = None if updated_at is None else (now - updated_at if now > updated_at else 0)

            out_tfs: List[Dict[str, Any]] = []
            for tf in tfs:
                rid = pinned_call(ud.functions.currentRoundId(i, tf), bn)
                rnd = _to_round(pinned_call(ud.functions.getRound(i, tf, rid), bn), feed_decimals) if rid else None
                prob = pinned_call(ud.functions.currentBullProbability(i, tf), bn)
                th = pinned_call(ud.functions.currentSlippageThreshold(i, tf), bn)
                pending_end = rnd["end_time"] if rnd is not None and rnd["outcome"] == Outcome.PENDING else None
                out_tfs.append({
                    "tf": tf, "round_id": rid, "round": rnd, "bull_probability": prob, "slippage_threshold": th,
                    "closes_at": rnd["end_time"] - bet_lock if rnd is not None else 0,
                    "bettable": open_ and pending_end is not None and now + bet_lock < pending_end,
                    "needs_advance": pending_end is not None and now > pending_end,
                })

            out_assets.append({
                "index": i, "label": label, "paused": asset_paused, "price_feed": price_feed,
                "feed_decimals": feed_decimals, "price": feed[1] if feed is not None else None,
                "price_updated_at": updated_at, "price_age": age, "price_stale": None if age is None else age > staleness,
                "pool_token": pool_token, "main": main, "settle": settle,
                "settle_decimals": int(meta["decimals"]), "settle_symbol": meta["symbol"],
                "pair_active": pair_active, "main_trading_enabled": trading, "timeframes": out_tfs,
            })

        return {"block_number": bn, "chain_time": now, "paused": paused, "bet_lock": bet_lock,
                "host_listed": bool(host_listed), "assets": out_assets}

    def get_claimable(self, user: Optional[str], rounds: Sequence[Any]) -> Dict[str, Any]:
        """Claim quotes for a list of rounds (e.g. the API's claimable list): ``quote_claim_payout`` for each, pinned to
        the latest block, with the totals per settle asset. ``user`` defaults to the signer (None). ``rounds``: a
        list or tuple of dicts ``{"asset", "tf", "round_id"}`` (or ``(asset, tf, round_id)`` tuples); anything else
        is ``ValidationError('rounds')``, before any read.

        Returns {user, block_number, items: [{asset (index), label, tf, round_id, payout (Amount, settle asset),
        quotable}], totals: [{settle, amount (Amount)}] (first-seen order)}. A 0 payout means "not quotable now", not
        certainly "nothing to claim": the bet lost or was claimed, the round is pending, or the payout cannot be priced.
        """
        who = self._account if user is None else to_address(user, "user")
        if not isinstance(rounds, (list, tuple)):
            raise ValidationError("rounds", "rounds must be an array of rounds.")
        given = []
        for r in rounds:
            if isinstance(r, dict):
                asset, tf, rid = r.get("asset"), r.get("tf"), r.get("round_id")
            elif isinstance(r, (list, tuple)) and len(r) == 3:
                asset, tf, rid = r
            else:
                raise ValidationError(
                    "rounds",
                    f'Each round is a dict {{"asset", "tf", "round_id"}} or an (asset, tf, round_id) tuple, got {r!r}.')
            given.append((asset, tf, to_uint(rid, "roundId")))
        refs = []
        for asset, tf, rid in given:
            a, t = self._args(asset, tf)
            refs.append((a, t, rid))
        ud = self._ud
        bn = self.web3.eth.get_block("latest")["number"]
        infos: Dict[int, Dict[str, Any]] = {}
        items: List[Dict[str, Any]] = []
        for a, t, rid in refs:
            info = infos.get(a)
            if info is None:
                row = pinned_call(ud.functions.assets(a), bn)
                meta = self.client.mains.token_meta(row[5])
                info = {"label": row[3], "settle": row[5], "decimals": int(meta["decimals"]), "symbol": meta["symbol"]}
                infos[a] = info
            raw = pinned_call(ud.functions.quoteClaimPayout(a, t, rid, who), bn)
            items.append({
                "asset": a, "label": info["label"], "tf": t, "round_id": rid,
                "payout": to_amount(raw, info["decimals"], "pairAsset", info["settle"], info["symbol"]),
                "quotable": raw > 0,
            })
        sums: Dict[str, Amount] = {}
        for it in items:
            p = it["payout"]
            k = p.asset.lower()
            prev = sums.get(k)
            sums[k] = to_amount((prev.raw if prev else 0) + p.raw, p.decimals, "pairAsset", p.asset, p.symbol)
        totals = [{"settle": amt.asset, "amount": amt} for amt in sums.values()]
        return {"user": who, "block_number": bn, "items": items, "totals": totals}


    def bet_bull(self, asset: UpDownAssetRef, tf: int, amount: AmountInput, **opts: Any) -> Dict[str, Any]:
        """Bet Up on the current round of ``(asset, tf)``. See ``bet`` for the flow and options."""
        return self.bet(asset, tf, "bull", amount, **opts)

    def bet_bear(self, asset: UpDownAssetRef, tf: int, amount: AmountInput, **opts: Any) -> Dict[str, Any]:
        """Bet Down on the current round of ``(asset, tf)``. See ``bet`` for the flow and options."""
        return self.bet(asset, tf, "bear", amount, **opts)

    def bet(self, asset: UpDownAssetRef, tf: int, side: SideInput, amount: AmountInput,
            input: Optional[str] = None, min_shares: Optional[int] = None, slippage_bps: Optional[int] = None,
            auto_advance: bool = True, auto_advance_delay_ms: int = 500,
            auto_advance_max_wait_ms: int = 30_000, deadline: Optional[int] = None) -> Dict[str, Any]:
        """Place a bet. ``amount``: int = raw input units, str / float / Decimal = human amount in the input's
        decimals; ``input`` defaults to the asset's settle asset (also accepted: the pool token, the asset's main, any
        BASIS token and an outside token with a hop route).

        Flow, in order: the option checks, before any read (the input address, ``deadline``, ``min_shares``,
        ``slippage_bps``, the auto-advance options, the side) -> the global pause -> the asset (``InvalidAsset``) -> its
        own pause -> the timeframe (``InvalidTimeframe``) -> the amount (above 0, read in the input's decimals) -> an
        explicit deadline -> balance -> whether the bet can go through now ('convert closed': Up/Down is not taking bets
        right now; 'pair not active': the main's pair is paused; 'Trading is not enabled': trading on the main has not
        opened, for a bet that buys or sells the main; 'pair asset paused': the settle asset, or the pair asset of
        another pair whose BASIS token you pay in, is paused by its issuer (paying in a factory token of another pair
        also pays out your pending holder rewards on it, in that asset); a bet paid in the pool token with no bet fee
        skips these) -> when the round has ended unadvanced and ``auto_advance`` is on: the round the advance would open
        (refused with ``ValidationError("BettingClosed")``, nothing sent, when it would already be inside ``BET_LOCK``)
        and the minimum net bet (``InvalidAmount``, estimated), then the advance -> betting window
        (``now + BET_LOCK < end_time``) -> one side per round -> quote (``quote_bet``, min net bet; an explicit
        ``min_shares`` above the quoted shares is ``ValidationError("MinSharesNotMet")`` on a direct route: the pool
        token, the settle asset or the main) -> approve the input to the contract -> re-check that the same round is
        still open (else ``ValidationError("BettingClosed")``, nothing sent but the approval) -> the bet.

        ``min_shares``: explicit minimum, raw (a whole number, never 0; checked before anything is sent), default the
        quote minus ``slippage_bps`` (default 3%). ``auto_advance`` (a real bool, default True): settle (or cancel) the
        current round first when it has ended but nobody advanced it, then bet on the new one. Expected races (someone
        else advanced, oracle not ready) are swallowed; the bet's own checks follow. The cheap checks (amount, options,
        pauses, deadline, balance, whether the bet can convert, the minimum net bet, and whether the round the advance
        opens would still take bets) run before the advance is sent.
        ``auto_advance_delay_ms`` (ms, default 500) pauses after a successful auto-advance; ``auto_advance_max_wait_ms``
        (ms, default 30000) bounds its wait for the feed. If the bet raises after an advance was sent, the exception
        carries it as ``e.advanced`` (the round-end dict of ``advance_round``).
        ``deadline``: unix seconds (chain time) after which the bet reverts ("Deadline"), checked on every bet whatever
        the input; default the latest block's time + 600. Either way it is capped at the observed round's betting
        cutoff (``end_time - BET_LOCK``, ``quote["closes_at"]``), so the bet can only land in the round it was checked
        against, never in the next one; the result's ``deadline`` is what was sent. One already past (before the latest
        block time; a deadline equal to the time of the block that runs the bet still counts) is rejected up front with
        ``ValidationError("Deadline")``, before any approval or auto-advance is sent, and again after the approval
        (nothing more is sent then).

        Returns the tx dict plus ``round_id`` (the round the bet landed in, from the bet's event; the round it was
        checked against when the event is missing), ``min_shares``, ``deadline`` (as sent), ``quote``, the figures of
        the bet's event (each None, with a ``warnings`` entry, if it is missing): ``shares`` (minted,
        raw 18 dec; can differ from ``quote["shares"]`` when the block changed), ``net_amount`` (Amount: net stake,
        pool tokens after the fee), ``fee`` (Amount, pool tokens), ``paid`` (Amount, input token), and ``advanced``
        (the auto-advance write sent first, already synced, or None).
        The options (input address, ``deadline``, ``min_shares``, ``slippage_bps``, the auto-advance ones) and the
        side are checked before any read; None takes the default.
        """
        me = self._account
        inp_in = None if input is None else to_address(input, "input")
        explicit_deadline = opt_uint(deadline, "deadline")
        explicit_min = opt_uint(min_shares, "minShares")
        if explicit_min == 0:
            raise ValidationError("minShares", "A zero minShares is never sent; omit it to derive it from a quote.")
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        do_advance = to_bool(auto_advance, "autoAdvance", True)
        delay_ms = _ms_option(auto_advance_delay_ms, "autoAdvanceDelayMs", 500)
        max_wait_ms = _ms_option(auto_advance_max_wait_ms, "autoAdvanceMaxWaitMs", 30_000)
        s = self._side_arg(side)
        if self.paused():
            raise ValidationError("Paused", "Up/Down is paused: no bets right now.")
        a = self.resolve_asset(asset)
        info = self._read_asset(a)
        if info["paused"]:
            raise ValidationError("Paused", f"Up/Down asset {info['label']} is paused: no bets on it right now.")
        t = self._tf_arg(tf)
        inp = info["settle"] if inp_in is None else inp_in
        raw = self._parse(amount, inp)
        if raw <= 0:
            raise ValidationError("amount", "The bet amount must be above 0.")

        def check_deadline(now: int) -> None:
            if explicit_deadline is not None:
                _check_explicit_deadline(explicit_deadline, now)

        if explicit_deadline is not None:
            check_deadline(self._chain_now())

        balance = self._balance_of(inp, me)
        if balance < raw:
            meta = self.client.mains.token_meta(inp)
            dec, sym = int(meta["decimals"]), meta["symbol"]
            raise ValidationError(
                "balance",
                f"Insufficient {sym}: have {to_amount(balance, dec, 'token', inp, sym).formatted}, "
                f"need {to_amount(raw, dec, 'token', inp, sym).formatted}.",
            )
        self._check_convert(info, inp, me)

        advanced = None
        try:
            if do_advance:
                cur = self.get_current_round(a, t)
                now_pre = self._chain_now()
                if cur and cur["round"]["outcome"] == Outcome.PENDING and now_pre > cur["round"]["end_time"]:
                    self._precheck_before_advance(info, t, cur["round"]["end_time"], now_pre, inp, raw, me)
                    try:
                        advanced = self.advance_round(a, t, max_wait_ms=max_wait_ms)
                        if delay_ms > 0:
                            time.sleep(delay_ms / 1000)
                    except Exception as e:  # noqa: BLE001
                        if not _is_advance_race(e):
                            raise
            return self._place_bet(me, a, t, s, info, inp, raw, explicit_deadline, explicit_min, slippage_bps,
                                   check_deadline, advanced)
        except Exception as e:
            if advanced is not None:
                e.advanced = advanced  # type: ignore[attr-defined]
            raise

    def _precheck_before_advance(self, info: Dict[str, Any], tf: int, prev_end: int, now: int, inp: str, raw: int,
                                 bettor: str) -> None:
        """Before an auto-advance, the checks that need no open round. First the round the advance would open, worked
        out from the latest block (the next block can carry the same timestamp): when even the next block would be
        inside its ``BET_LOCK``, the advance would open a round nobody can bet on now, so nothing is sent
        ('BettingClosed'). Then the minimum net bet, from the estimated net stake (``_estimate_net``): 'InvalidAmount'."""
        bet_lock = self.BET_LOCK()
        dur = self.tf_duration(tf)
        t_exec = now + 1
        chain_end = prev_end + dur
        next_end = chain_end if chain_end > now else (now // dur) * dur + dur
        if t_exec + bet_lock >= next_end:
            raise ValidationError(
                "BettingClosed",
                f"{info['label']} tf={tf}: the round an advance would open ends at {next_end}, already inside its "
                f"{bet_lock}s bet lock (chain time {now}). Nothing was sent; bet on the round after it.",
            )
        net = self._estimate_net(info, inp, raw, bettor)
        min_net_bet = info["virt_floor"] // BPS
        if net == 0:
            raise ValidationError(
                "InvalidAmount",
                f"The bet converts to 0 pool tokens on {info['label']} tf={tf} (too small, or the input cannot be priced "
                "right now), so it would revert. Bet more (see min_bet_input).",
            )
        if net < min_net_bet:
            raise ValidationError(
                "InvalidAmount",
                f"The bet nets about {net} pool tokens after the fee, below the minimum net bet of {min_net_bet}. "
                "Bet more (see min_bet_input).",
            )

    def _check_convert(self, info: Dict[str, Any], inp: str, bettor: str) -> None:
        """Whether a bet paid in ``inp`` can go through now, in order (as markets ``check_buy`` does): betting switched
        on for Up/Down ('convert closed'), the main's pair active ('pair not active'), then, for a bet that buys or sells
        the main (paid in anything but the pool token or the main, or with a bet fee), the main's trading open ('Trading
        is not enabled'), then no asset the bet moves paused by its issuer ('pair asset paused'): the pair asset of
        another pair whose BASIS token is paid in (paying in a factory token also pays out the bettor's pending holder
        rewards on it), then the settle asset. A bet paid in the pool token with no bet fee needs none of these. The
        same reads as ``get_lobby``'s ``host_listed``, ``pair_active`` and ``main_trading_enabled``, plus the issuer
        pause."""
        in_pool = _eq(inp, info["pool_token"])
        in_main = _eq(inp, info["main"])
        fee = (in_pool or in_main) and int(self.client.taxes.get_tax_rate(info["pool_token"], bettor)) > 0
        if in_pool and not fee:
            return
        address = self.address
        listed = self._contract(_cs(self.taxes()), "Taxes").functions.isPrediction(_cs(address)).call()
        if not listed:
            raise ValidationError("convert closed", f"Up/Down ({address}) cannot convert a bet right now: no bets.")
        if not self._pair_active(info["main"]):
            raise self._pair_not_active(info, "bet")
        if (not in_pool and not in_main) or fee:
            open_ = (self.client.mains.get(info["main"]).trading_enabled
                     or self._contract(_cs(info["main"]), "MainToken").functions.tradingEnabled().call())
            if not open_:
                raise ValidationError(
                    "Trading is not enabled",
                    f"Trading on main {info['main']} has not opened yet: no bets on {info['label']} that buy or sell "
                    "it until it does.",
                )
            if not in_pool and not in_main and not _eq(inp, info["settle"]):
                sale = _input_sale_asset(self, inp, info["main"], info["settle"])
                if sale is not None and is_asset_paused(self.client, sale["asset"]):
                    raise pair_asset_paused(sale["asset"], sale["symbol"], _input_sale_paused(inp, sale["factory_token"]))
            if is_asset_paused(self.client, info["settle"]):
                raise pair_asset_paused(info["settle"], info["settle_symbol"],
                                        "no bet that pays in or converts through it can be placed")

    def _pair_active(self, main: str) -> bool:
        """Whether the main's pair is active, read now."""
        return bool(self._contract(_cs(self.swap()), "Swap").functions.pairs(_cs(main)).call()[2])

    def _refuse_paused_pair(self, info: Dict[str, Any], action: str) -> None:
        """'pair not active' when ``info``'s main is on a paused pair, read now (``_pair_active``): bets and claims that
        need the pair revert, and a quote of 0 then means the pause, not the amount. Returns when the pair is active."""
        if not self._pair_active(info["main"]):
            raise self._pair_not_active(info, action)

    @staticmethod
    def _pair_not_active(info: Dict[str, Any], action: str) -> ValidationError:
        """The 'pair not active' refusal of a bet or a claim on ``info``."""
        what = ("no bets that convert through it until the pair is active again" if action == "bet"
                else "the payout sells through it, so no claim until the pair is active again")
        return ValidationError(
            "pair not active", f"{info['label']} settles through main {info['main']}, which is on a paused pair: {what}.")

    def _place_bet(self, me: str, a: int, t: int, s: int, info: Dict[str, Any], inp: str, raw: int,
                   explicit_deadline: Optional[int], explicit_min: Optional[int], slippage_bps: Optional[int],
                   check_deadline, advanced: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """``bet`` from the betting-window check on (after any auto-advance). The window
        (``now + BET_LOCK < end_time``) is checked for the next block (chain time + 1): a bet the latest block would
        still take can land a second later and revert on chain."""
        round_id = self.current_round_id(a, t)
        if round_id == 0:
            raise ValidationError("NoActiveRound", f"{info['label']} tf={t} has not been started.")
        rnd = self.get_round(a, t, round_id)
        bet_lock = self.BET_LOCK()
        now = self._chain_now()
        my_bet = self.get_user_bet(a, t, round_id, me)
        if rnd["outcome"] != Outcome.PENDING or now + 1 + bet_lock >= rnd["end_time"]:
            raise ValidationError(
                "BettingClosed",
                f"Betting is closed on {info['label']} tf={t} round {round_id} (it locks {bet_lock}s before the end at "
                f"{rnd['end_time']}; chain time {now}). Bet on the next round.",
            )
        check_deadline(now)
        if my_bet["side"] != Side.NONE and my_bet["side"] != s:
            other = "Up" if my_bet["side"] == Side.BULL else "Down"
            raise ValidationError("AlreadyBetOnOtherSide", f"You already bet {other} on round {round_id}. One side per round.")

        quote = self.quote_bet(a, t, s, raw, input=inp, bettor=me, slippage_bps=slippage_bps)
        if quote.get("route") == "direct" and explicit_min is not None and explicit_min > quote["shares"]:
            raise ValidationError(
                "MinSharesNotMet",
                f"minShares {explicit_min} is above the shares this bet mints now ({quote['shares']}), so it would be "
                "refused: lower it, or omit it to derive it from the quote.",
            )
        min_sh = self._resolve_min(explicit_min, slippage_bps, lambda: quote["shares"], "updown", "minShares")
        mi = self.client.mains.get(info["main"])
        pool_fmt = amount_of(self.client, info["pool_token"], mi)
        input_fmt = amount_of(self.client, inp, mi)

        self._ensure_allowance(inp, self.address, raw)
        closes_at = rnd["end_time"] - bet_lock
        rid_now = self.current_round_id(a, t)
        now_after = self._chain_now()
        if rid_now != round_id or now_after + 1 + bet_lock >= rnd["end_time"]:
            moved = f", current round {rid_now}" if rid_now != round_id else ""
            raise ValidationError(
                "BettingClosed",
                f"Betting on {info['label']} tf={t} round {round_id} closed while the bet was being prepared (it locked "
                f"at {closes_at}; chain time {now_after}{moved}). The bet was not sent (an approval already made "
                "stays). Bet on the next round.",
            )
        check_deadline(now_after)
        wanted = explicit_deadline if explicit_deadline is not None else self._block_deadline(BET_DEADLINE_SECONDS)
        dl = min(wanted, closes_at)
        fns = self._ud.functions
        call = fns.betBull(a, t, inp, raw, min_sh, dl) if s == 1 else fns.betBear(a, t, inp, raw, min_sh, dl)
        tx = self._send(call, "MultiUpDown")
        placed = next((e for e in self._events(tx, self._ud, "BetPlaced")
                       if e["asset"] == a and e["tf"] == t and _eq(e["user"], me)), None)
        if placed is None:
            add_missing_event_warning(tx, "MultiUpDown", "BetPlaced", ("shares", "net_amount", "fee", "paid"))
        elif placed["roundId"] != round_id:
            tx["warnings"].append(f"MultiUpDown.BetPlaced: the bet landed in round {placed['roundId']}, not in round "
                                  f"{round_id} it was checked against.")
        return {
            **tx, "round_id": placed["roundId"] if placed else round_id, "min_shares": min_sh, "deadline": dl,
            "quote": quote, "shares": placed["shares"] if placed else None,
            "net_amount": opt_amount(pool_fmt, placed["amount"] if placed else None),
            "fee": opt_amount(pool_fmt, placed["fee"] if placed else None),
            "paid": opt_amount(input_fmt, placed["paid"] if placed else None), "advanced": advanced,
        }


    def claim(self, asset: UpDownAssetRef, tf: int, round_id: int, fraction_bps: Optional[int] = None,
              slices: Optional[int] = None, min_out: Optional[int] = None,
              slippage_bps: Optional[int] = None) -> Dict[str, Any]:
        """Claim a settled round (win or refund), paid in the asset's settle asset. Defaults (also for None): all of it
        (``fraction_bps`` 10000) in one slice (``slices`` 1-25). A partial claim leaves the rest in
        ``get_user_bet()["pending"]`` for a later claim. ``min_out`` (raw settle asset, a whole number, never 0; checked
        before anything is sent) defaults to ``quote_claim_payout x fraction_bps / 10000`` minus ``slippage_bps`` (3%).
        Pre-checks run in this order: NoActiveRound, NothingToClaim (no bet), RoundNotSettled, 'fraction 1-10000',
        NothingToClaim (nothing owed, or the fraction rounds to 0 pool tokens), 'slices 1-25', then the payout: something
        owed that cannot be priced now is 'pair not active' while the main's pair is paused, as markets ``redeem``, else
        NothingToClaim; then 'precision' (a slice worth less than one raw settle unit reverts the claim with no reason:
        use fewer slices), then 'pair asset paused' (the issuer of the settle asset paused it: the payout is made in it).
        Returns the tx dict plus ``min_out`` (the floor sent, an Amount in the settle asset, unit 'pairAsset') and,
        from the claim's event (each None, with a ``warnings`` entry, if it is missing): ``payout`` (Amount, settle
        asset paid), ``tokens`` (Amount, pool tokens sold), ``remaining`` (Amount, pool tokens still owed; 0 = fully
        claimed) and ``won`` (True = a win, False = a refund: a canceled round, or a settled one nobody won).
        ``round_id``, ``min_out`` and ``slippage_bps`` are checked before any read."""
        me = self._account
        rid = to_uint(round_id, "roundId")
        fraction_bps = 10000 if fraction_bps is None else fraction_bps
        slices = 1 if slices is None else slices
        explicit_min = opt_uint(min_out, "minOut")
        if explicit_min == 0:
            raise ValidationError("minOut", "A zero minOut is never sent; omit it to derive it from a quote.")
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        a, t = self._args(asset, tf)

        current = self.current_round_id(a, t)
        if rid == 0 or rid > current:
            raise ValidationError("NoActiveRound", f"Round {rid} does not exist (current is {current}).")
        rnd = self.get_round(a, t, rid)
        bet = self.get_user_bet(a, t, rid, me)
        payout = self.quote_claim_payout(a, t, rid, me)
        if bet["amount"] == 0:
            raise ValidationError("NothingToClaim", f"{me} has no bet on round {rid}.")
        if rnd["outcome"] == Outcome.PENDING:
            raise ValidationError("RoundNotSettled", f"Round {rid} is not settled yet.")
        if not _is_int(fraction_bps) or fraction_bps < 1 or fraction_bps > 10000:
            raise ValidationError("fraction 1-10000", f"fractionBps must be 1-10000, got {fraction_bps}.")
        fraction_bps = int(fraction_bps)
        entitlement = _entitlement_of(rnd, bet)
        if entitlement == 0:
            raise ValidationError("NothingToClaim", f"Nothing to claim on round {rid} for {me} (lost, or already claimed).")
        pay_now = entitlement if fraction_bps == 10000 else entitlement * fraction_bps // BPS
        if pay_now == 0:
            raise ValidationError(
                "NothingToClaim",
                f"{fraction_bps} bps of the {entitlement} pool tokens still owed on round {rid} rounds to 0. "
                "Claim a larger fraction.")
        if not _is_int(slices) or slices < 1 or slices > MAX_CLAIM_SLICES:
            raise ValidationError(f"slices 1-{MAX_CLAIM_SLICES}", f"slices must be 1-{MAX_CLAIM_SLICES}, got {slices}.")
        slices = int(slices)
        if payout.raw == 0:
            self._refuse_paused_pair(self._read_asset(a), "claim")
            raise ValidationError(
                "NothingToClaim", f"Nothing to claim on round {rid} for {me} right now: the payout cannot be priced.")
        if slices > 1 or pay_now != entitlement:
            self._check_slice_sells(a, pay_now, slices)

        expected = payout.raw * fraction_bps // BPS
        mo = self._resolve_min(explicit_min, slippage_bps, lambda: expected, "updown", "minOut")
        info = self._read_asset(a)
        refuse_paused_asset(self.client, info["settle"], info["settle_symbol"], "no claim can pay it out")
        mi = self.client.mains.get(info["main"])
        pool_fmt = amount_of(self.client, info["pool_token"], mi)
        settle_fmt = amount_of(self.client, info["settle"], mi)
        tx = self._send(self._ud.functions.claim(a, t, rid, fraction_bps, slices, mo), "MultiUpDown")

        def mine(e: Dict[str, Any]) -> bool:
            return e["asset"] == a and e["tf"] == t and e["roundId"] == rid and _eq(e["user"], me)

        won = next((e for e in self._events(tx, self._ud, "BetWon") if mine(e)), None)
        refunded = None if won else next((e for e in self._events(tx, self._ud, "BetRefunded") if mine(e)), None)
        if won is None and refunded is None:
            add_missing_event_warning(tx, "MultiUpDown", "BetWon / BetRefunded", ("payout", "tokens", "remaining", "won"))
        ev = won or refunded
        return {
            **tx, "min_out": settle_fmt(mo),
            "payout": opt_amount(settle_fmt, won["payout"] if won else refunded["amount"] if refunded else None),
            "tokens": opt_amount(pool_fmt, ev["tokens"] if ev else None),
            "remaining": opt_amount(pool_fmt, ev["remaining"] if ev else None),
            "won": True if won else False if refunded else None,
        }

    def _check_slice_sells(self, asset: int, pay_now: int, slices: int) -> None:
        """'precision' when a claim of ``pay_now`` in ``slices`` chunks has a chunk worth less than one raw settle unit
        (the claim would revert with no reason). An RPC failure of the valuation is ``BasisRpcError``, never
        'precision'."""
        info = self._read_asset(asset)
        base = pay_now // slices
        chunk = base if base > 0 else pay_now
        try:
            value = self.client.trading.get_amounts_out(chunk, [info["pool_token"], info["main"], info["settle"]])
        except Exception as e:  # noqa: BLE001
            value = zero_unless_rpc(e, "Swap", "getAmountsOut")
        if value > 0:
            return
        if slices > 1 and base > 0:
            msg = (f"Each of the {slices} slices ({chunk} pool tokens) sells for less than one raw unit of "
                   f"{info['settle_symbol']}, so the claim would revert. Use fewer slices.")
        else:
            msg = (f"This claim ({chunk} pool tokens) sells for less than one raw unit of {info['settle_symbol']}, so it "
                   "would revert. Claim a larger fraction.")
        raise ValidationError("precision", msg)


    def settle_current_round(self, asset: UpDownAssetRef, tf: int) -> Dict[str, Any]:
        """Settle the current round of ``(asset, tf)`` and open the next one. Anyone may call, after ``end_time`` and
        within ``FINALIZE_WINDOW``. One attempt, no polling (see ``advance_round``). A round without bets settles as a
        cancel (no feed price needed). Returns the round-end dict (class docstring): outcome, end price, ``no_winner``,
        carry-over and the round it opened, from the tx's events.
        Raises ``OracleNotReadyError`` when the feed has no usable price past ``end_time`` yet (retry later)."""
        self._account  # noqa: B018
        a, t = self._args(asset, tf)
        round_id, rnd = self._pre_check_timing(a, t, "settle")
        pool_fmt = self._pool_amount_of(a)
        try:
            tx = self._send(self._ud.functions.settleCurrentRound(a, t), "MultiUpDown")
        except Exception as e:
            for name in ("NoUpdateAfterEndTime", "NoValidPriceInWindow"):
                if is_revert_named(e, name):
                    raise OracleNotReadyError(
                        f"The price feed has no usable update past round {round_id}'s end ({rnd['end_time']}) yet "
                        f"({name}). Retry settle_current_round shortly, or use advance_round, which polls the feed.",
                        a, t, rnd["end_time"], name,
                    ) from e
            raise
        return self._round_end(tx, a, t, pool_fmt)

    def cancel_current_round_and_start_next(self, asset: UpDownAssetRef, tf: int) -> Dict[str, Any]:
        """Cancel the current round of ``(asset, tf)`` (bets refund at claim, the seed rolls forward) and open the next
        one. Anyone may call, once ``end_time + FINALIZE_WINDOW`` has passed. Returns the same round-end dict as
        ``settle_current_round`` (outcome Canceled, end price 0)."""
        self._account  # noqa: B018
        a, t = self._args(asset, tf)
        self._pre_check_timing(a, t, "cancel")
        pool_fmt = self._pool_amount_of(a)
        tx = self._send(self._ud.functions.cancelCurrentRoundAndStartNext(a, t), "MultiUpDown")
        return self._round_end(tx, a, t, pool_fmt)

    def _pool_amount_of(self, asset: int) -> AmountOf:
        """Formatter for raw pool-token amounts of ``asset`` (resolved before a write, so the result needs no RPC)."""
        info = self._read_asset(asset)
        return amount_of(self.client, info["pool_token"], self.client.mains.get(info["main"]))

    def _round_end(self, tx: Dict[str, Any], asset: int, tf: int, pool_fmt: AmountOf) -> Dict[str, Any]:
        """The figures of a settle / cancel from its events; pool-token figures through ``pool_fmt``."""
        def ours(e: Dict[str, Any]) -> bool:
            return e["asset"] == asset and e["tf"] == tf

        settled = next((e for e in self._events(tx, self._ud, "RoundSettled") if ours(e)), None)
        opened = next((e for e in self._events(tx, self._ud, "NewRound") if ours(e)), None)
        if settled is None:
            add_missing_event_warning(tx, "MultiUpDown", "RoundSettled",
                                      ("round_id", "outcome", "end_price", "no_winner", "carryover"))
        if opened is None:
            add_missing_event_warning(tx, "MultiUpDown", "NewRound", ("next_round",))
        rid = settled["roundId"] if settled else None
        no_winner = carryover = None
        if settled:
            no_winner = any(ours(e) and e["roundId"] == rid for e in self._events(tx, self._ud, "RoundNoWinner"))
            carryover = sum(e["amount"] for e in self._events(tx, self._ud, "CarryoverCaptured")
                            if ours(e) and e["fromRoundId"] == rid)
        return {
            **tx,
            "round_id": rid,
            "outcome": Outcome(settled["outcome"]) if settled else None,
            "end_price": settled["endPrice"] if settled else None,
            "no_winner": no_winner,
            "carryover": opt_amount(pool_fmt, carryover),
            "next_round": {
                "round_id": opened["roundId"], "start_time": opened["startTime"], "end_time": opened["endTime"],
                "start_price": opened["startPrice"], "seed_bonus": pool_fmt(opened["seedBonus"]),
            } if opened else None,
        }

    def advance_round(self, asset: UpDownAssetRef, tf: int, poll_interval_ms: int = 8000,
                      max_wait_ms: int = 360_000) -> Dict[str, Any]:
        """Settle-or-cancel helper: inside the finalize window it waits for the feed to update past ``end_time``
        (polling every ``poll_interval_ms`` ms, up to ``max_wait_ms`` ms, default 6 min) and settles; after the window
        it cancels. A round without bets settles at once. Can take minutes on a slow feed.
        Returns the tx dict plus ``mode`` ('settle' | 'cancel').
        Raises ``ValidationError`` when the round is not started, still running or already settled (checked in that
        order); ``OracleNotReadyError`` when the feed stays stale for ``max_wait_ms`` or settle finds
        no valid price."""
        self._account  # noqa: B018
        poll_ms = _ms_option(poll_interval_ms, "pollIntervalMs", 8000)
        wait_ms = _ms_option(max_wait_ms, "maxWaitMs", 360_000)
        a, t = self._args(asset, tf)
        cur = self.get_current_round(a, t)
        if not cur:
            raise ValidationError("NoActiveRound", f"Asset {a} tf={t} has not been started.")
        round_id, rnd = cur["round_id"], cur["round"]
        now = self._chain_now()
        fw = self.FINALIZE_WINDOW()
        if now <= rnd["end_time"]:
            raise ValidationError(
                "TooEarlyToSettle",
                f"Round {round_id} is still running: {rnd['end_time'] - now}s left (ends at {rnd['end_time']}).")
        if rnd["outcome"] != Outcome.PENDING:
            raise ValidationError("RoundAlreadySettled", f"Round {round_id} is already settled (outcome {int(rnd['outcome'])}).")
        if now > rnd["end_time"] + fw:
            return {**self.cancel_current_round_and_start_next(a, t), "mode": "cancel"}
        if rnd["shares_bull"] > 0 or rnd["shares_bear"] > 0:
            self._wait_for_oracle(a, t, rnd["end_time"], poll_ms, wait_ms)
        return {**self.settle_current_round(a, t), "mode": "settle"}

    def _wait_for_oracle(self, asset: int, tf: int, end_time: int, poll_interval_ms: int, max_wait_ms: int) -> None:
        """Polls the asset's feed until it has updated at or after ``end_time`` (needed to settle)."""
        feed = self._ud.functions.assets(asset).call()[0]
        agg = self._contract(feed, "AggregatorV3")
        start = time.monotonic()
        while True:
            updated_at = agg.functions.latestRoundData().call()[3]
            if updated_at >= end_time:
                return
            if (time.monotonic() - start) * 1000 >= max_wait_ms:
                break
            time.sleep(poll_interval_ms / 1000)
        raise OracleNotReadyError(
            f"The price feed {feed} has not updated past the round's end ({end_time}) after {round(max_wait_ms / 1000)}s. "
            "It may be stalled; once the finalize window has passed, advance_round cancels the round instead.",
            asset, tf, end_time, "WaitTimeout",
        )

    def _pre_check_timing(self, asset: int, tf: int, mode: str):
        """In this order (time first). settle: NoActiveRound, TooEarlyToSettle (``now <= endTime``),
        RoundAlreadySettled, TooLateForValidPrice (``now + 1 > endTime + FINALIZE_WINDOW``: an upper bound, checked
        for the next block); cancel: NoActiveRound, TooEarlyToSettle (``now <= endTime + FINALIZE_WINDOW``),
        RoundAlreadySettled. Chain time (``now`` = the latest block's)."""
        cur = self.get_current_round(asset, tf)
        if not cur:
            raise ValidationError("NoActiveRound", f"Asset {asset} tf={tf} has not been started.")
        round_id, rnd = cur["round_id"], cur["round"]
        now = self._chain_now()
        deadline = rnd["end_time"] + self.FINALIZE_WINDOW()

        def not_settled() -> None:
            if rnd["outcome"] != Outcome.PENDING:
                raise ValidationError(
                    "RoundAlreadySettled", f"Round {round_id} is already settled (outcome {int(rnd['outcome'])}).")

        if mode == "settle":
            if now <= rnd["end_time"]:
                raise ValidationError(
                    "TooEarlyToSettle",
                    f"Round {round_id} has not ended: {rnd['end_time'] - now}s left (ends at {rnd['end_time']}).")
            not_settled()
            if now + 1 > deadline:
                raise ValidationError(
                    "TooLateForValidPrice",
                    f"The settle window ends at {deadline} (chain time {now}), before a settle sent now can land; call "
                    "cancel_current_round_and_start_next once it has passed (or advance_round).")
        else:
            if now <= deadline:
                raise ValidationError(
                    "TooEarlyToSettle",
                    f"The settle window is open for {deadline - now}s more; call settle_current_round (or advance_round) "
                    "until it closes.")
            not_settled()
        return round_id, rnd




class UpDownAsset:
    """One asset of ``client.updown`` with the asset fixed: the module's methods minus the asset argument.
    The label is resolved on the first call (``ValidationError('InvalidAsset')`` if unknown)."""

    def __init__(self, module: UpDownModule, ref: UpDownAssetRef):
        self._m = module
        self.ref = ref

    def index(self) -> int:
        """The asset's index."""
        return self._m.resolve_asset(self.ref)

    def info(self) -> Dict[str, Any]:
        """The asset's info, as ``get_asset``."""
        return self._m.get_asset(self.ref)

    def current_round_id(self, tf: int) -> int:
        return self._m.current_round_id(self.ref, tf)

    def get_round(self, tf: int, round_id: int) -> Dict[str, Any]:
        return self._m.get_round(self.ref, tf, round_id)

    def get_current_round(self, tf: int) -> Optional[Dict[str, Any]]:
        return self._m.get_current_round(self.ref, tf)

    def get_user_bet(self, tf: int, round_id: int, user: Optional[str] = None) -> Dict[str, Any]:
        return self._m.get_user_bet(self.ref, tf, round_id, user)

    def current_bull_probability(self, tf: int) -> int:
        return self._m.current_bull_probability(self.ref, tf)

    def current_slippage_threshold(self, tf: int) -> int:
        return self._m.current_slippage_threshold(self.ref, tf)

    def pending_carryover(self, tf: int) -> int:
        return self._m.pending_carryover(self.ref, tf)

    def protocol_virt_base(self, tf: int) -> int:
        return self._m.protocol_virt_base(self.ref, tf)

    def tf_duration(self, tf: int) -> int:
        return self._m.tf_duration(tf)

    def min_net_bet(self) -> int:
        """See ``UpDownModule.min_net_bet``."""
        return self._m.min_net_bet(self.ref)

    def min_bet_input(self, input: Optional[str] = None, bettor: Optional[str] = None) -> Dict[str, Any]:
        """See ``UpDownModule.min_bet_input``."""
        return self._m.min_bet_input(self.ref, input=input, bettor=bettor)

    def quote_shares_for_input(self, tf: int, side: SideInput, input_token: str, amount: int,
                               user: Optional[str] = None) -> Dict[str, int]:
        return self._m.quote_shares_for_input(self.ref, tf, side, input_token, amount, user)

    def quote_bet(self, tf: int, side: SideInput, amount: AmountInput, **opts: Any) -> Dict[str, Any]:
        return self._m.quote_bet(self.ref, tf, side, amount, **opts)

    def quote_current_payout(self, tf: int, user: Optional[str] = None) -> Amount:
        return self._m.quote_current_payout(self.ref, tf, user)

    def quote_claim_payout(self, tf: int, round_id: int, user: Optional[str] = None) -> Amount:
        return self._m.quote_claim_payout(self.ref, tf, round_id, user)

    def bet(self, tf: int, side: SideInput, amount: AmountInput, **opts: Any) -> Dict[str, Any]:
        return self._m.bet(self.ref, tf, side, amount, **opts)

    def bet_bull(self, tf: int, amount: AmountInput, **opts: Any) -> Dict[str, Any]:
        return self._m.bet_bull(self.ref, tf, amount, **opts)

    def bet_bear(self, tf: int, amount: AmountInput, **opts: Any) -> Dict[str, Any]:
        return self._m.bet_bear(self.ref, tf, amount, **opts)

    def claim(self, tf: int, round_id: int, **opts: Any) -> Dict[str, Any]:
        return self._m.claim(self.ref, tf, round_id, **opts)

    def settle_current_round(self, tf: int) -> Dict[str, Any]:
        return self._m.settle_current_round(self.ref, tf)

    def cancel_current_round_and_start_next(self, tf: int) -> Dict[str, Any]:
        return self._m.cancel_current_round_and_start_next(self.ref, tf)

    def advance_round(self, tf: int, **opts: Any) -> Dict[str, Any]:
        return self._m.advance_round(self.ref, tf, **opts)
