"""Up/Down domain: /api/v1/updown/*, /api/v1/me/updown, /api/v1/wallet/{address}/updown. TypeScript SDK: src/api/updown.ts.

Everything here is read from the index (no chain read): quotes and live rounds are on chain (``client.updown``).
Numbers are decimal strings: prices are raw feed answers (divide by 10^priceDecimals), pools / shares / seed / stakes
pool-token wei (18 decimals), payouts raw settle-asset units (pairDecimals). Round times are unix seconds, history rows
ISO 8601.
"""

import re
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

from ..core.errors import ValidationError
from .core import ApiCore, seg
from .types import Pagination, RawAmount, UsdE18, UsdSource

UpDownTfSlug = Literal["5m", "15m", "1h", "4h", "24h"]
"""Timeframe slug (= tf 0..4)."""

UpDownTf = Union[int, UpDownTfSlug]
"""Timeframe: numeric 0..4 or its slug."""

UpDownAssetParam = Union[str, int]
"""An asset as the API takes it: its slug or its index (both from ``get_updown_assets()``)."""

UpDownSideStr = Literal["bull", "bear"]
UpDownOutcomeStr = Literal["pending", "bull_wins", "bear_wins", "canceled"]
UpDownRoundStatus = Literal["live", "awaiting_settle", "awaiting_cancel", "settled", "canceled"]
"""Index state of a round: a round closed on chain stays awaiting_* until it is synced."""

TF_SLUGS = ("5m", "15m", "1h", "4h", "24h")
_INT32_MAX = 2147483647
_SLUG_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _check_asset(token: Any) -> None:
    if isinstance(token, bool):
        ok = False
    elif isinstance(token, int):
        ok = 0 <= token <= _INT32_MAX
    else:
        ok = isinstance(token, str) and _SLUG_RE.fullmatch(token) is not None
    if not ok:
        raise ValidationError("token", f"token must be an asset slug or asset index (got {token!r})")


def _check_tf(tf: Any) -> None:
    if isinstance(tf, bool):
        ok = False
    elif isinstance(tf, int):
        ok = 0 <= tf <= 4
    else:
        ok = tf in TF_SLUGS
    if not ok:
        raise ValidationError("tf", f"tf must be 0-4 or one of {', '.join(TF_SLUGS)} (got {tf!r})")


def _check_round_id(round_id: Any) -> None:
    if isinstance(round_id, bool) or not isinstance(round_id, int) or not 1 <= round_id <= _INT32_MAX:
        raise ValidationError("roundId", f"round_id must be an integer 1-{_INT32_MAX} (got {round_id!r})")


class UpDownTimeframeInfo(TypedDict):
    slug: UpDownTfSlug
    tf: int
    durationSec: int


class UpDownAssetRow(TypedDict):
    """One configured asset, joined with its indexed listing (``listed: False`` and None fields before that)."""

    slug: str
    label: str
    ticker: str
    asset: int
    enabled: bool
    listed: bool
    mismatch: Optional[str]
    mainKey: str
    main: Optional[str]
    settle: Optional[str]
    pairAsset: Optional[str]
    pairSymbol: Optional[str]
    pairDecimals: Optional[int]
    poolToken: Optional[str]
    priceFeed: Optional[str]
    paused: Optional[bool]
    priceDecimals: Optional[int]
    virtFloor: Optional[RawAmount]
    minStakePoolTokens: Optional[RawAmount]


class UpDownAssets(TypedDict):
    contract: str
    timeframes: List[UpDownTimeframeInfo]
    assets: List[UpDownAssetRow]


class UpDownStatsRow(TypedDict):
    """Activity of one asset x timeframe. USD at bet / claim time, None unless every row is priced."""

    token: str
    asset: int
    tf: int
    timeframe: UpDownTfSlug
    main: str
    settle: str
    pairDecimals: int
    pairSymbol: str
    volumeUsdE18: UsdE18
    usdSource: UsdSource
    usdCovered: int
    bets: int
    volume24hUsdE18: UsdE18
    volume24hUsdSource: UsdSource
    usdCovered24h: int
    bets24h: int
    bettors: int
    rounds: int
    roundsPlayed: int
    bullWins: int
    bearWins: int
    noWinner: int
    canceled: int
    pending: int
    paidOut: RawAmount
    paidOutUsdE18: UsdE18
    paidOutUsdSource: UsdSource
    paidOutUsdCovered: int
    claims: int


class UpDownStats(TypedDict):
    contract: str
    asOf: int
    data: List[UpDownStatsRow]


class UpDownRoundRow(TypedDict):
    """A round as indexed. Times are unix seconds; prices raw feed answers; pools / shares / seed pool-token wei."""

    contract: str
    asset: int
    main: str
    settle: str
    pairAsset: str
    poolToken: str
    pairDecimals: int
    pairSymbol: str
    tf: int
    roundId: int
    startTime: int
    endTime: int
    settledAt: Optional[int]
    startPrice: str
    endPrice: Optional[str]
    priceFeed: str
    priceDecimals: Optional[int]
    endPriceRoundId: Optional[str]
    bullPool: RawAmount
    bearPool: RawAmount
    sharesBull: RawAmount
    sharesBear: RawAmount
    seedBonus: RawAmount
    outcome: UpDownOutcomeStr
    status: UpDownRoundStatus
    noWinner: bool
    settler: Optional[str]


class UpDownRoundsList(TypedDict):
    token: str
    asset: int
    latestIndexed: Dict[str, Optional[int]]
    rounds: List[UpDownRoundRow]
    pagination: Pagination


class UpDownSideBets(TypedDict):
    bettors: int
    stakePoolTokens: RawAmount
    sharesTotal: RawAmount
    stake: RawAmount


class UpDownPaid(TypedDict):
    inputToken: str
    amount: RawAmount


class UpDownRoundBets(TypedDict):
    totalEvents: int
    distinctBettors: int
    bull: UpDownSideBets
    bear: UpDownSideBets
    paidByToken: List[UpDownPaid]
    paidUsdE18: UsdE18
    usdSource: UsdSource
    stakeUsdCovered: int


class UpDownRoundClaims(TypedDict):
    total: int
    events: int
    won: int
    refunded: int
    fullyClaimed: int
    totalPayout: RawAmount
    amountUsdE18: UsdE18
    usdSource: UsdSource
    amountUsdCovered: int


class UpDownBettor(TypedDict):
    """One bet of a round (``placedAt`` ISO 8601)."""

    user: str
    side: UpDownSideStr
    inputToken: Optional[str]
    paid: Optional[RawAmount]
    stake: RawAmount
    shares: RawAmount
    fee: Optional[RawAmount]
    paidUsdE18: UsdE18
    usdSource: UsdSource
    claimed: bool
    txHash: str
    blockNumber: int
    logIndex: int
    placedAt: str


class UpDownBettorPage(TypedDict):
    data: List[UpDownBettor]
    pagination: Pagination


class UpDownCarry(TypedDict):
    """Carryover captured from the round, pool-token wei. ``kind`` ``unknown``: a kind the index does not name yet."""

    kind: Literal["loser_cut", "seed_excess", "seed_rolled", "unknown"]
    amount: RawAmount
    txHash: str


class UpDownRoundDetail(TypedDict):
    token: str
    asset: int
    round: UpDownRoundRow
    bets: UpDownRoundBets
    claims: UpDownRoundClaims
    bettors: UpDownBettorPage
    carry: List[UpDownCarry]


class UpDownPosition(TypedDict):
    """One (asset, tf, round) position of a wallet. Stakes are pool-token wei."""

    token: str
    contract: str
    asset: Optional[int]
    main: Optional[str]
    settle: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    tf: int
    roundId: int
    side: UpDownSideStr
    stake: RawAmount
    stakePoolTokens: RawAmount
    paid: List[UpDownPaid]


class UpDownActiveBet(UpDownPosition):
    placedAt: str
    endTime: Optional[str]


class UpDownClaimableBet(UpDownPosition):
    """Won or refunded position not fully claimed. The payout is ``client.updown.quote_claim_payout(...)`` on chain."""

    outcome: Literal["bull_wins", "bear_wins", "canceled"]
    refund: bool
    partlyClaimed: bool
    remainingPoolTokens: Optional[RawAmount]


class UpDownStakeByAsset(TypedDict):
    token: str
    asset: Optional[int]
    poolToken: Optional[str]
    amount: RawAmount


class UpDownPayoutBySettle(TypedDict):
    settle: Optional[str]
    pairAsset: Optional[str]
    pairSymbol: Optional[str]
    pairDecimals: Optional[int]
    amount: RawAmount


class UpDownNetBySettle(TypedDict):
    settle: str
    pairSymbol: Optional[str]
    pairDecimals: Optional[int]
    amount: str
    positions: int


class UpDownUnclaimed(TypedDict):
    """Claimable positions per settle asset (no amounts). ``pairSymbol`` / ``pairDecimals`` None when the main is
    unknown."""

    settle: str
    pairAsset: str
    pairSymbol: Optional[str]
    pairDecimals: Optional[int]
    positions: int


class _UpDownHistoryRowBase(TypedDict):
    type: Literal["bet", "won", "refund"]
    txHash: str
    blockNumber: int
    logIndex: int
    time: str
    token: str
    asset: Optional[int]
    tf: int
    roundId: int
    stake: Optional[RawAmount]
    usdSource: UsdSource


class UpDownHistoryRow(_UpDownHistoryRowBase, total=False):
    """One history row (bet / won / refund); ``time`` ISO 8601; ``stake`` pool-token wei (net of the fee on bet rows).
    The API sends the bet-only keys (``side``, ``inputToken``, ``paid`` gross raw input-token units, ``shares``,
    ``fee``, ``valueUsdE18``) on bet rows only and the claim-only keys (``payout`` raw settle-asset units, ``settle``,
    ``pairDecimals``, ``tokens`` / ``remaining`` pool-token wei sold by the claim / still owed after it,
    ``amountUsdE18``) on won / refund rows only: absent on the other type. ``usdSource`` is the source of whichever USD
    value the row has."""

    side: Optional[UpDownSideStr]
    inputToken: Optional[str]
    paid: Optional[RawAmount]
    shares: Optional[RawAmount]
    fee: Optional[RawAmount]
    valueUsdE18: UsdE18
    payout: Optional[RawAmount]
    settle: Optional[str]
    pairDecimals: Optional[int]
    tokens: Optional[RawAmount]
    remaining: Optional[RawAmount]
    amountUsdE18: UsdE18


class UpDownHistoryPage(TypedDict):
    data: List[UpDownHistoryRow]
    pagination: Pagination


class UpDownRecord(TypedDict, total=False):
    """The Up/Down record of a wallet (``get_my_updown`` / ``get_wallet_updown``). ``summaryCapped``: the wallet has more
    than 50,000 bets or claims, so the summary, active bets and claimable list cover the newest 50,000 of each (the
    paged history is not cut)."""

    wallet: str
    totalBetEvents: int
    distinctRoundsBet: int
    totalStake: RawAmount
    stakes: List[UpDownStakeByAsset]
    stakeUsdE18: UsdE18
    stakeUsdSource: UsdSource
    stakeUsdCovered: int
    payouts: List[UpDownPayoutBySettle]
    payoutUsdE18: UsdE18
    payoutUsdSource: UsdSource
    payoutUsdCovered: int
    netUsdE18: UsdE18
    netUsdSource: UsdSource
    netBySettle: List[UpDownNetBySettle]
    wins: int
    losses: int
    refunds: int
    activeBetsCount: int
    claimableCount: int
    activeBets: List[UpDownActiveBet]
    claimableBets: List[UpDownClaimableBet]
    unclaimed: List[UpDownUnclaimed]
    history: UpDownHistoryPage
    summaryCapped: bool


class UpDownApi(ApiCore):
    """Up/Down routes (mixin of ``BasisAPI``)."""

    def get_updown_assets(self) -> UpDownAssets:
        """The deployment's assets and timeframes (the source of slugs / indexes).

        ``GET /api/v1/updown/assets``. API key.
        """
        return self._request("GET", "/api/v1/updown/assets", auth="key")

    def get_updown_stats(self, token: Optional[UpDownAssetParam] = None,
                         tf: Optional[UpDownTf] = None) -> UpDownStats:
        """Activity per asset x timeframe (cached 60 s). Defaults: every listed asset, every timeframe.

        ``GET /api/v1/updown/stats``. API key.
        """
        if token is not None:
            _check_asset(token)
        if tf is not None:
            _check_tf(tf)
        return self._request("GET", "/api/v1/updown/stats", auth="key", params={"token": token, "tf": tf})

    def get_updown_rounds(
        self,
        token: UpDownAssetParam,
        tf: Optional[UpDownTf] = None,
        outcome: Optional[UpDownOutcomeStr] = None,
        no_winner: Optional[bool] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> UpDownRoundsList:
        """Indexed rounds of one asset, newest first per timeframe.

        ``GET /api/v1/updown/rounds``. API key. Cursor paged, rows in ``rounds`` (``cursor`` = the previous
        ``pagination.nextCursor``; ``limit`` 1..100, default 20)::

            api.paginate(lambda **p: (lambda r: {"data": r["rounds"], "pagination": r["pagination"]})(
                api.get_updown_rounds(**p)), token="btc")
        """
        _check_asset(token)
        if tf is not None:
            _check_tf(tf)
        return self._request("GET", "/api/v1/updown/rounds", auth="key", params={
            "token": token, "tf": tf, "outcome": outcome, "noWinner": no_winner, "cursor": cursor, "limit": limit})

    def get_updown_round(self, token: UpDownAssetParam, tf: UpDownTf, round_id: int,
                         cursor: Optional[str] = None, limit: Optional[int] = None) -> UpDownRoundDetail:
        """One indexed round with bet / claim aggregates, its carryover and a cursor-paged bettor list (``bettors``).

        ``GET /api/v1/updown/rounds/{token}/{tf}/{roundId}``. API key. 404 ``not_indexed`` while the round is not
        indexed. Page the bettors with
        ``api.paginate(lambda **p: api.get_updown_round(token, tf, round_id, **p)["bettors"])``.
        """
        _check_asset(token)
        _check_tf(tf)
        _check_round_id(round_id)
        return self._request("GET", f"/api/v1/updown/rounds/{seg(token)}/{seg(tf)}/{seg(round_id)}", auth="key",
                             params={"cursor": cursor, "limit": limit})

    def get_my_updown(self, cursor: Optional[str] = None, limit: Optional[int] = None) -> UpDownRecord:
        """The Up/Down record of the authenticated wallet (summary, active and claimable bets, history).

        ``GET /api/v1/me/updown``. Key or session. ``history`` is cursor paged:
        ``api.paginate(lambda **p: api.get_my_updown(**p)["history"])``. Payouts of ``claimableBets`` are quoted on
        chain (``client.updown.quote_claim_payout``).
        """
        return self._request("GET", "/api/v1/me/updown", auth="keyOrSession", params={"cursor": cursor, "limit": limit})

    def get_wallet_updown(self, address: str, cursor: Optional[str] = None,
                          limit: Optional[int] = None) -> UpDownRecord:
        """The Up/Down record of a wallet (as ``get_my_updown``).

        ``GET /api/v1/wallet/{address}/updown``. API key; ``history`` cursor paged.
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/updown", auth="key",
                             params={"cursor": cursor, "limit": limit})
