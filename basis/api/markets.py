"""Markets domain: /api/v1/markets* (incl. approve, draft/headline/answers/describe and their background jobs
/api/v1/markets/drafts/{jobId}, events), /api/v1/me/orders, /api/v1/wallet/{address}/orders,
/api/v1/wallet/{address}/markets.

Every route takes an API key or a SIWE session. Sorted lists page with ``page`` / ``offset`` (``offset`` is rounded down
to a page start, a multiple of ``limit``); histories with ``cursor``.
Responses are the API's JSON (camelCase keys); the TypedDicts below describe them. TypeScript SDK: src/api/markets.ts."""

import math
import re
import time
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

import requests

from ..core.errors import BasisError, ValidationError
from ..core.units import MAX_SAFE_FLOAT_INT, check_text, to_address, to_bool, to_uint
from .core import DEFAULT_API_AI_TIMEOUT_MS, MAX_TIMEOUT_MS, ApiCore, BasisApiError, check_timeout_ms, seg
from .types import E18, Pagination, RawAmount, UsdE18, UsdSource

MarketType = Literal["public", "private"]
MarketResolutionStatus = Literal[
    "active", "awaiting_proposal", "proposed", "proposed_closed", "voting", "veto_window", "disputed", "vetoed",
    "awaiting_finalize", "resolved", "invalidated", "unknown",
]
"""The indexed (or, on ``get_market_resolution``, live) status machine of a market."""
MarketResolutionKind = Literal["resolved", "invalidated", "vetoed"]
MarketSort = Literal["newest", "ending", "volume24h", "volume", "bettors"]
MarketOddsInterval = Literal["raw", "5m", "15m", "1h", "4h", "1d"]
OrderStatus = Literal["ACTIVE", "FILLED", "CANCELLED"]
MarketEventAction = Literal[
    "propose", "dispute", "vote", "veto", "resolve", "invalidate", "ranked_resolved", "resolver_resolved", "redeem",
    "no_winner_swept", "dispute_reset", "bonds_distributed", "bonds_seized", "bond_redirected", "early_reset",
    "vote_reopened", "order_created", "order_cancelled", "order_filled", "bet_unplaced",
]
MarketShape = Literal["binary", "single", "ranked"]
"""binary: two answers; single: one winner among many; ranked: a podium (one entrant per answer)."""


class MarketFinal(TypedDict, total=False):
    """The folded final result (``finalOutcome`` 254 = invalid; ``resolvedAt`` unix seconds)."""

    finalOutcome: Optional[int]
    ranking: Optional[List[int]]
    winners: Optional[List[int]]
    tieMask: Optional[str]
    resolvedAt: Optional[int]


class MarketOutcomeOdds(TypedDict, total=False):
    """An outcome with its odds as of the last synced bet (``reserve`` market-token wei, ``probabilityE18`` x 1e18)."""

    id: int
    name: Optional[str]
    reserve: Optional[RawAmount]
    probabilityE18: Optional[E18]
    derived: bool


class MarketSummary(TypedDict, total=False):
    """A market row of ``get_markets`` (``endTime`` unix seconds, 0 = open-ended; ``pot`` live, market-token wei)."""

    marketToken: str
    host: str
    hostKind: Optional[MarketType]
    creator: str
    main: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    name: Optional[str]
    symbol: Optional[str]
    image: Optional[str]
    category: Optional[str]
    private: bool
    endTime: int
    outcomeCount: Optional[int]
    createdBlock: Optional[int]
    status: MarketResolutionStatus
    statusSource: Literal["indexed"]
    deadline: Optional[int]
    volumeUsdE18: UsdE18
    volume24hUsdE18: UsdE18
    usdSource: UsdSource
    usdCovered: int
    n: int
    bets: int
    fills: int
    bettors: int
    lastActivityAt: Optional[str]
    resolved: bool
    resolution: Optional[MarketResolutionKind]
    final: Optional[MarketFinal]
    outcomes: List[MarketOutcomeOdds]
    pot: Optional[RawAmount]
    noWinnerSwept: bool


class MarketsPage(TypedDict, total=False):
    data: List[MarketSummary]
    pagination: Pagination
    scanned: int
    scanCap: int


class MarketOutcomeDetail(MarketOutcomeOdds, total=False):
    """``get_market`` outcome: indexed odds plus live ``circulatingShares`` (18 dec) and ``totalCost`` (market-token wei)."""

    circulatingShares: Optional[RawAmount]
    totalCost: Optional[RawAmount]
    hasWon: bool


class MarketDetail(TypedDict, total=False):
    """One market (``get_market``). Money fields are market-token wei unless noted; ``seed.amount`` raw pair asset.
    ``invited`` (private markets only, else None): the market token's whitelist over its whole history, at most 1000
    (``invitedTruncated``); ``invitedCount`` counts every invitee."""

    marketToken: str
    host: str
    hostKind: MarketType
    resolver: Optional[str]
    creator: str
    main: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    name: Optional[str]
    symbol: Optional[str]
    description: Optional[str]
    category: Optional[str]
    image: Optional[str]
    private: bool
    creationTime: int
    endTime: int
    status: MarketResolutionStatus
    statusSource: Literal["indexed"]
    deadline: Optional[int]
    resolved: bool
    resolution: Optional[MarketResolutionKind]
    final: Optional[MarketFinal]
    options: List[Dict[str, Any]]
    trancheBP: Optional[List[int]]
    totalVirtualReserve: Optional[RawAmount]
    outcomes: List[MarketOutcomeDetail]
    pot: RawAmount
    generalPot: RawAmount
    seed: Dict[str, Optional[RawAmount]]
    seedUsdE18: UsdE18
    seedUsdSource: UsdSource
    volumeUsdE18: UsdE18
    volume24hUsdE18: UsdE18
    volumeUsdSource: UsdSource
    usdCovered: int
    n: int
    bets: int
    fills: int
    bettors: int
    noWinnerSwept: bool
    invited: Optional[List[Dict[str, Any]]]
    invitedCount: Optional[int]
    invitedTruncated: Optional[bool]


class MarketTrade(TypedDict, total=False):
    """An AMM bet with the outcome weights after it (``paid`` in ``inputToken`` units, ``valuePair`` raw pair asset)."""

    id: int
    host: str
    buyer: str
    outcomeId: int
    shares: RawAmount
    tradeType: str
    newReserve: RawAmount
    newTotalReserve: RawAmount
    tokensNet: RawAmount
    tokensGross: Optional[RawAmount]
    fee: RawAmount
    inputToken: Optional[str]
    paid: Optional[RawAmount]
    inputDecimals: Optional[int]
    valuePair: Optional[RawAmount]
    valueUsdE18: UsdE18
    usdSource: UsdSource
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    probabilityE18: Optional[E18]
    txHash: str
    logIndex: int
    blockNumber: int
    timestamp: str


class MarketTradesPage(TypedDict):
    data: List[MarketTrade]
    pagination: Pagination


class MarketOdds(TypedDict, total=False):
    """Odds history (``series[].time`` unix seconds; probabilities x 1e18)."""

    marketToken: str
    outcomeCount: Optional[int]
    interval: str
    baseDerived: bool
    initial: Optional[List[Dict[str, Any]]]
    series: List[Dict[str, Any]]
    truncated: bool


class Order(TypedDict, total=False):
    """A book order, named by (host, marketToken, orderId). Shares 18 decimals; ``pricePerShare`` whole pair-asset units
    per share x 1e18, not the odds (0 = fill at the live share price, ``marketPriced``); ``createdAt`` / ``updatedAt``
    ISO 8601. ``fillable``: ACTIVE and fillable now. ``unfillableReason``: why an ACTIVE order is not: ``resolved``
    (the market), ``above_price`` (priced above the live book price), ``paused`` (its host), ``invalid_proposed``
    (public host: INVALID is proposed or backed by a vote); None otherwise."""

    id: str
    orderId: int
    marketToken: str
    host: str
    hostKind: Optional[MarketType]
    seller: str
    outcomeId: int
    amount: RawAmount
    totalAmount: RawAmount
    remaining: RawAmount
    filled: Optional[RawAmount]
    pricePerShare: E18
    marketPriced: bool
    priceScale: str
    status: OrderStatus
    fillable: bool
    unfillableReason: Optional[Literal["resolved", "above_price", "paused", "invalid_proposed"]]
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    createdTx: str
    lastBlock: int
    createdAt: str
    updatedAt: str


class OrdersPage(TypedDict):
    data: List[Order]
    pagination: Pagination


class MarketOrderBook(TypedDict, total=False):
    """``view="book"``: fillable orders grouped by outcome and price, cheapest first (pass ``levels[].orderIds`` to
    ``client.order_book.buy_orders``). ``paused``: a market host is paused, its orders are left out of ``levels``
    (none fill until it is unpaused). ``invalidProposed``: public host, INVALID is proposed or backed by a vote, so no listing
    fills until that is settled (left out of ``levels``)."""

    marketToken: str
    main: Optional[str]
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    priceScale: str
    levels: List[Dict[str, Any]]
    truncated: bool
    paused: bool
    invalidProposed: bool
    note: str


class MarketHolders(TypedDict, total=False):
    """Holders per outcome, largest first (``shares`` 18 dec, ``costPair`` raw pair asset)."""

    marketToken: str
    main: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    outcomes: List[Dict[str, Any]]
    data: List[Dict[str, Any]]
    pagination: Pagination


class MarketResolution(TypedDict, total=False):
    """Live resolution state. Bonds raw bond-token units, times unix seconds. ``fastEarly`` .. ``windows``: public
    markets only; ``votingOpen`` .. ``votingWindow``: private markets only. ``requiredBond``: ``{propose, dispute,
    disputeHouse, veto}``, each None when not possible now; ``dispute`` is a regular wallet's figure, ``disputeHouse``
    the reduced dispute bond some wallets pay (may be absent; yours: ``client.resolver`` reads it)."""

    marketToken: str
    hostKind: str
    host: str
    resolver: Optional[str]
    status: MarketResolutionStatus
    phase: str
    statusSource: Literal["live"]
    endTime: int
    creationTime: int
    deadline: Optional[int]
    secondsLeft: Optional[int]
    round: int
    earlyResets: int
    bondToken: Optional[str]
    bondDecimals: Optional[int]
    requiredBond: Optional[Dict[str, Optional[RawAmount]]]
    canProposeAt: Optional[Dict[str, int]]
    proposal: Optional[Dict[str, Any]]
    tally: Optional[Dict[str, Any]]
    finalize: Optional[Literal["finalizeUncontested", "finalizeMarket", "finalize"]]
    fastEarly: bool
    willReopen: bool
    canDispute: bool
    canVeto: bool
    windows: Dict[str, int]
    votingOpen: bool
    firstVoteTime: Optional[int]
    finalizeAt: Optional[int]
    votingWindow: int
    final: Optional[Dict[str, Any]]
    rankings: List[Dict[str, Any]]


class MarketEvent(TypedDict, total=False):
    """A market / resolution event (``amount`` raw units of ``amountAsset``; ``timestamp`` ISO 8601)."""

    wallet: str
    marketToken: str
    host: Optional[str]
    main: Optional[str]
    action: MarketEventAction
    outcomeId: Optional[int]
    ranking: Optional[List[int]]
    round: Optional[int]
    amount: Optional[RawAmount]
    amountAsset: Optional[str]
    amountDecimals: Optional[int]
    data: Optional[str]
    txHash: str
    blockNumber: int
    logIndex: int
    timestamp: str
    walletIsActor: bool


class MarketEventsPage(TypedDict):
    mode: Literal["market", "wallet", "own", "all"]
    data: List[MarketEvent]
    pagination: Pagination


class WalletMarket(TypedDict, total=False):
    """A wallet's position in one market (shares 18 dec, incl. ``sharesInOrders``; cost / redeemed raw pair asset).
    Each outcome: ``{id, shares, sharesInOrders, cost, costUsdE18, usdSource, costBasis, costBasisUsdE18,
    costComplete}``; ``costComplete`` is False when a buy's pair value was unknown (``costBasis`` leaves that buy out:
    a lower bound), None with no cost record."""

    marketToken: str
    host: str
    hostKind: Optional[str]
    main: str
    pairAsset: Optional[str]
    pairDecimals: Optional[int]
    pairSymbol: Optional[str]
    name: Optional[str]
    symbol: Optional[str]
    status: MarketResolutionStatus
    statusSource: Literal["indexed"]
    resolved: Optional[bool]
    lastActivityAt: Optional[str]
    outcomes: List[Dict[str, Any]]
    seed: Optional[Dict[str, Any]]
    soldPair: Optional[RawAmount]
    redeemed: RawAmount
    sharesSource: Optional[Literal["index", "live"]]


class WalletMarketsPage(TypedDict):
    wallet: str
    data: List[WalletMarket]
    pagination: Pagination


class MarketDraftResult(TypedDict, total=False):
    """A drafting answer: ``ok`` True with the draft fields (``draft`` / ``headline`` / ``outcomes`` /
    ``description``), or a refusal ``{ok: False, refused: True, category, reason}``; plus ``model``, ``cooldownMs``."""

    ok: bool
    refused: bool
    marketType: MarketType
    draft: Dict[str, Any]
    headline: Dict[str, Any]
    outcomes: List[str]
    description: str
    category: Optional[str]
    reason: str
    model: str
    cooldownMs: int


DraftKind = Literal["draft", "headline", "answers", "describe"]
"""The four drafting calls, by the name their background jobs carry (``job.kind``)."""
DraftJobStatus = Literal["running", "done", "failed", "cancelled"]


class DraftJob(TypedDict, total=False):
    """A drafting call running in the background (started with ``async_=True``). The API keeps it for 10 minutes after
    it ends, for the wallet that started it only. ``id``: 32 lower-case hex characters; ``elapsedMs``: since the job
    started, or its whole run once it has ended; ``result`` (on ``done``): exactly what the call answers without
    ``async_`` (a draft or a refusal); ``error`` (on ``failed``): ``draft_unavailable`` | ``timeout`` |
    ``internal_error``."""

    id: str
    kind: DraftKind
    status: DraftJobStatus
    elapsedMs: int
    result: MarketDraftResult
    error: str


class DraftJobStart(TypedDict, total=False):
    """The 202 answer of a drafting call with ``async_=True``: the job (or the running job of the same input), and
    ``pollAfterMs``, the least wait before the first :meth:`MarketsApi.get_draft_job`."""

    ok: bool
    job: DraftJob
    pollAfterMs: int


class DraftJobResponse(TypedDict, total=False):
    """The answer of :meth:`MarketsApi.get_draft_job` and :meth:`MarketsApi.cancel_draft_job`."""

    ok: bool
    job: DraftJob


_AUTH = "keyOrSession"

_DRAFT_METHODS = {"draft": "draft_market", "headline": "draft_market_headline", "answers": "draft_market_answers",
                  "describe": "draft_market_description"}
_DRAFT_POLL_MS = 1_500
_DRAFT_BUSY_RETRY_MS = 5_000
_DRAFT_COOLDOWN_MAX_MS = 30_000
_DRAFT_NO_ANSWER_RETRY_MS = 2_000
_DRAFT_START_MIN_LEFT_MS = 5_000
_DRAFT_JOB_ID = re.compile(r"[0-9a-f]{32}")

_DRAFT_HINTS = {
    "drafting_slow": "The AI writer needed longer than one request may last (about 24 s). Run the call in the "
                     "background: api.run_draft_job(kind, **params), or async_=True and api.get_draft_job(job['id']).",
    "drafting_unavailable": "The AI writer did not answer in time, or is down. Try again in a moment. "
                            "draft_market_headline + draft_market_answers are two shorter calls than one draft_market, "
                            "and run_draft_job runs a call in the background where the API offers it.",
    "job_running": "This wallet already has an AI draft running (its jobId is in the body): wait for it with "
                   "api.get_draft_job, or stop it with api.cancel_draft_job.",
    "job_not_found": "That draft job is gone: an unknown id, another wallet's, one that ended more than 10 minutes ago, "
                     "or one lost when the AI writer restarted. Start the call again.",
}
_NO_DRAFT_JOBS_HINT = "This API has no background drafts: it answers a drafting call at once, so call it without async_."


def _check_outcomes(outcomes: Any) -> List[str]:
    """``outcomes`` as a list; ``ValidationError("outcomes")`` unless it is a list (or tuple) of strings: a string would
    become its characters (and spend a review or a drafting call). Same rule in TS."""
    if not isinstance(outcomes, (list, tuple)) or any(not isinstance(o, str) for o in outcomes):
        if isinstance(outcomes, (list, tuple)):
            got = "an array with a non-string entry"
        elif outcomes is None:
            got = "null"
        else:
            got = {str: "string", bool: "boolean", int: "number", float: "number"}.get(type(outcomes), "object")
        raise ValidationError("outcomes", f"outcomes must be an array of answer strings, got {got}.")
    return list(outcomes)


def _end_time(end_time: Any) -> int:
    """``end_time`` as the route takes it: a whole number of unix seconds (0 = open-ended, the default, for None), at
    most 2^53 - 1; ``ValidationError("endTime")`` otherwise (no truncation of a fraction, no string). Same rule in TS."""
    v = 0 if end_time is None else to_uint(end_time, "endTime")
    if v > MAX_SAFE_FLOAT_INT:
        raise ValidationError("endTime", f"endTime must be unix seconds (at most {MAX_SAFE_FLOAT_INT}), got {v}.")
    return v


def _body(**fields: Any) -> Dict[str, Any]:
    """JSON body with the unset (None) fields left out."""
    return {k: v for k, v in fields.items() if v is not None}


def _with_draft_hint(e: BaseException, fallback: Optional[str] = None) -> BaseException:
    """``e`` with the drafting hint of its code (or ``fallback``) appended to its message; anything else unchanged."""
    if not isinstance(e, BasisApiError):
        return e
    hint = (_DRAFT_HINTS.get(e.code) if e.code else None) or fallback
    if not hint:
        return e
    msg = str(e)
    if msg.startswith("[basis-sdk] "):
        msg = msg[len("[basis-sdk] "):]
    return BasisApiError(e.status, e.code, f"{msg} {hint}", e.body, e.retry_after)


def _check_draft_job_id(job_id: Any) -> str:
    if not isinstance(job_id, str) or not _DRAFT_JOB_ID.fullmatch(job_id):
        raise ValidationError("jobId", f"jobId must be the 32 lower-case hex characters of a draft job's id, got "
                                       f"{job_id!r}.")
    return job_id


def _is_draft_job(v: Any) -> bool:
    return isinstance(v, dict) and isinstance(v.get("id"), str) and isinstance(v.get("status"), str)


def _no_answer(e: BaseException) -> bool:
    """A request that got no answer: its own deadline (BasisApiError status 0 ``timeout``) or a network error
    (requests' own, not an SDK error). It may have reached the server."""
    if isinstance(e, BasisApiError):
        return e.status == 0 and e.code == "timeout"
    return isinstance(e, requests.RequestException) and not isinstance(e, BasisError)


def _draft_retry_ms(e: BasisApiError, fallback: float) -> float:
    """ms to wait after a refused or failed request: the body's ``retryAfterMs``, else the error's ``retry_after``
    (``Retry-After``; the per-minute ``X-RateLimit-Reset`` only for code ``rate_limited``, never for ``gate_busy`` or a
    daily cap), else ``fallback``."""
    b = e.body if isinstance(e.body, dict) else {}
    ms = b.get("retryAfterMs")
    if isinstance(ms, (int, float)) and not isinstance(ms, bool) and 0 < ms < math.inf:
        return float(min(ms, MAX_TIMEOUT_MS))
    if e.retry_after is not None and e.retry_after > 0:
        return min(e.retry_after * 1000.0, MAX_TIMEOUT_MS)
    return fallback


def _draft_async(async_: Any) -> Dict[str, Any]:
    """The ``async`` body field: sent only when True (the plain call's body stays as declared); None is absent (False),
    as TS null. Anything but True / False / None: ``ValidationError("async")``."""
    return {"async": True} if (async_ is not None and to_bool(async_, "async")) else {}


class MarketsApi(ApiCore):
    """Prediction-market routes (mixin of ``BasisAPI``)."""

    def get_markets(
        self,
        host: Optional[str] = None,
        main: Optional[str] = None,
        status: Optional[MarketResolutionStatus] = None,
        creator: Optional[str] = None,
        ends_before: Optional[Union[int, str]] = None,
        ends_after: Optional[Union[int, str]] = None,
        category: Optional[str] = None,
        resolved: Optional[bool] = None,
        sort: Optional[MarketSort] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> MarketsPage:
        """Every market of this deployment, sorted (default ``newest``) and page-paged. Key or session.

        ``GET /api/v1/markets``

        ``host``: ``public``, ``private`` or a host address; ``main``: a main key or address; ``ends_before`` /
        ``ends_after``: unix seconds or an ISO 8601 date.
        """
        return self._request("GET", "/api/v1/markets", auth=_AUTH, params={
            "host": host, "main": main, "status": status, "creator": creator, "endsBefore": ends_before,
            "endsAfter": ends_after, "category": category, "resolved": resolved, "sort": sort, "page": page,
            "offset": offset, "limit": limit})

    def get_market(self, address: str) -> MarketDetail:
        """One market with indexed odds and live pot / outcome totals. Key or session.

        ``GET /api/v1/markets/{address}``. ``address`` must be a 0x address (``ValidationError("address")`` before any
        request: ``get_market("events")`` would otherwise read the events list).
        """
        path = f"/api/v1/markets/{seg(address)}"
        to_address(address, "address")
        return self._request("GET", path, auth=_AUTH)

    def get_market_trades(
        self, address: str, outcome_id: Optional[int] = None, cursor: Optional[str] = None, limit: Optional[int] = None,
    ) -> MarketTradesPage:
        """The AMM bet log, newest first, cursor-paged. Key or session.

        ``GET /api/v1/markets/{address}/trades``. Its old name ``/api/v1/markets/{address}/liquidity`` is an alias and
        gets no wrapper.
        """
        return self._request("GET", f"/api/v1/markets/{seg(address)}/trades", auth=_AUTH,
                             params={"outcomeId": outcome_id, "cursor": cursor, "limit": limit})

    def get_market_odds(
        self,
        address: str,
        interval: Optional[MarketOddsInterval] = None,
        from_ts: Optional[Union[int, str]] = None,
        to_ts: Optional[Union[int, str]] = None,
    ) -> MarketOdds:
        """The odds history per outcome (probability x 1e18). Key or session.

        ``GET /api/v1/markets/{address}/odds``

        ``interval`` default ``1h``; ``from_ts`` / ``to_ts`` (query ``from`` / ``to``): unix seconds or an ISO 8601 date.
        """
        return self._request("GET", f"/api/v1/markets/{seg(address)}/odds", auth=_AUTH,
                             params={"interval": interval, "from": from_ts, "to": to_ts})

    def get_market_orders(
        self,
        address: str,
        view: Optional[Literal["list", "book"]] = None,
        status: Optional[OrderStatus] = None,
        outcome_id: Optional[int] = None,
        host: Optional[str] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Union[OrdersPage, MarketOrderBook]:
        """The market's book: a page of orders (newest orderId first), or with ``view="book"`` the fillable orders
        grouped by outcome and price (:class:`MarketOrderBook`). Key or session.

        ``GET /api/v1/markets/{address}/orders``

        ``status``: ``view="list"`` only; ``host``: ``public``, ``private`` or a host address.
        """
        return self._request("GET", f"/api/v1/markets/{seg(address)}/orders", auth=_AUTH, params={
            "view": view, "status": status, "outcomeId": outcome_id, "host": host, "page": page, "offset": offset,
            "limit": limit})

    def get_market_holders(
        self,
        address: str,
        outcome_id: Optional[int] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> MarketHolders:
        """Share holders per outcome, largest first, page-paged. Key or session.

        ``GET /api/v1/markets/{address}/holders``
        """
        return self._request("GET", f"/api/v1/markets/{seg(address)}/holders", auth=_AUTH,
                             params={"outcomeId": outcome_id, "page": page, "offset": offset, "limit": limit})

    def get_market_resolution(self, address: str) -> MarketResolution:
        """The live resolution state (phase, bonds, proposal, tally). Key or session.

        ``GET /api/v1/markets/{address}/resolution``
        """
        return self._request("GET", f"/api/v1/markets/{seg(address)}/resolution", auth=_AUTH)

    def get_market_events(
        self,
        market_token: Optional[str] = None,
        wallet: Optional[str] = None,
        action: Optional[MarketEventAction] = None,
        scope: Optional[Literal["all"]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> MarketEventsPage:
        """Market and resolution events, newest first, cursor-paged. Key or session.

        ``GET /api/v1/markets/events``

        One market (``market_token``), one wallet (``wallet``), every market (``scope="all"``, not with the other
        two) or, with none, the caller's own.
        """
        return self._request("GET", "/api/v1/markets/events", auth=_AUTH, params={
            "marketToken": market_token, "wallet": wallet, "action": action, "scope": scope, "cursor": cursor,
            "limit": limit})

    def get_my_orders(
        self,
        market_token: Optional[str] = None,
        status: Optional[OrderStatus] = None,
        outcome_id: Optional[int] = None,
        host: Optional[str] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> OrdersPage:
        """The caller's book orders across markets, newest first, page-paged. Key or session.

        ``GET /api/v1/me/orders``
        """
        return self._request("GET", "/api/v1/me/orders", auth=_AUTH, params={
            "marketToken": market_token, "status": status, "outcomeId": outcome_id, "host": host, "page": page,
            "offset": offset, "limit": limit})

    def get_wallet_orders(
        self,
        address: str,
        market_token: Optional[str] = None,
        status: Optional[OrderStatus] = None,
        outcome_id: Optional[int] = None,
        host: Optional[str] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> OrdersPage:
        """Any wallet's book orders, newest first, page-paged. Key or session.

        ``GET /api/v1/wallet/{address}/orders``
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/orders", auth=_AUTH, params={
            "marketToken": market_token, "status": status, "outcomeId": outcome_id, "host": host, "page": page,
            "offset": offset, "limit": limit})

    def get_wallet_markets(
        self,
        address: str,
        status: Optional[MarketResolutionStatus] = None,
        page: Optional[int] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> WalletMarketsPage:
        """Any wallet's market positions, latest activity first, page-paged. Key or session.

        ``GET /api/v1/wallet/{address}/markets``
        """
        return self._request("GET", f"/api/v1/wallet/{seg(address)}/markets", auth=_AUTH, params={
            "status": status, "page": page, "offset": offset, "limit": limit})

    def draft_market(
        self,
        topic: str,
        market_type: MarketType,
        outcome_count: Optional[int] = None,
        shape: Optional[MarketShape] = None,
        async_: bool = False,
    ) -> Union[MarketDraftResult, DraftJobStart]:
        """AI: a whole market (question, ticker, rules, answers) from a topic (1-400 chars). Key or session.

        ``POST /api/v1/markets/draft``

        Unsigned: it must still pass :meth:`approve_market`. A refusal is returned (``ok: False``); route errors raise
        BasisApiError: 429 ``cooldown`` (30 s per wallet, ``retryAfterMs`` in the body), ``rate_limited_ip``,
        ``rate_limited_wallet`` (4 drafting calls a minute across draft, headline, answers and describe),
        ``daily_wallet_cap``, ``daily_global_cap``, ``gate_busy`` (``retryAfterMs``), ``job_running`` (``async_``:
        another job of this wallet is running, its ``jobId`` in the body); 503 ``gate_disabled``, ``drafting_slow``,
        ``drafting_unavailable``.

        The API cuts one request at about 24 s (503 ``drafting_slow``, or ``drafting_unavailable`` where it has no
        background drafts), and a whole draft often runs longer. For a result that may take minutes use
        ``run_draft_job("draft", topic=..., market_type=...)``, or ``async_=True`` and :meth:`get_draft_job`.
        :meth:`draft_market_headline` + :meth:`draft_market_answers` is the faster pair.

        ``async_`` (the body's ``async``; ``async`` is a Python keyword): True starts the call as a background job.
        The API answers 202 at once with ``{ok, job, pollAfterMs}`` (:class:`DraftJobStart`) and
        :meth:`get_draft_job` reads the result, so a slow draft is not cut. An API without background drafts runs the
        call at once and answers its result. :meth:`run_draft_job` does all of this for you.
        """
        return self._draft_call("/api/v1/markets/draft", _body(
            topic=topic, marketType=market_type, outcomeCount=outcome_count, shape=shape, **_draft_async(async_)))

    def draft_market_headline(
        self, topic: str, market_type: MarketType, shape: Optional[MarketShape] = None, async_: bool = False,
    ) -> Union[MarketDraftResult, DraftJobStart]:
        """AI: question, ticker and rules from a topic (no answers). As :meth:`draft_market`.

        ``POST /api/v1/markets/headline``
        """
        return self._draft_call("/api/v1/markets/headline", _body(
            topic=topic, marketType=market_type, shape=shape, **_draft_async(async_)))

    def draft_market_answers(
        self,
        name: str,
        market_type: MarketType,
        description: Optional[str] = None,
        count: Optional[int] = None,
        shape: Optional[MarketShape] = None,
        async_: bool = False,
    ) -> Union[MarketDraftResult, DraftJobStart]:
        """AI: an exhaustive answer set for a question (``name``). As :meth:`draft_market`.

        ``POST /api/v1/markets/answers``
        """
        return self._draft_call("/api/v1/markets/answers", _body(
            name=name, description=description, marketType=market_type, count=count, shape=shape,
            **_draft_async(async_)))

    def draft_market_description(
        self,
        name: str,
        market_type: MarketType,
        outcomes: Optional[List[str]] = None,
        shape: Optional[MarketShape] = None,
        async_: bool = False,
    ) -> Union[MarketDraftResult, DraftJobStart]:
        """AI: resolution rules for a question (``name``). ``outcomes``, when given, a list of strings. As
        :meth:`draft_market`.

        ``POST /api/v1/markets/describe``
        """
        return self._draft_call("/api/v1/markets/describe", _body(
            name=name, outcomes=_check_outcomes(outcomes) if outcomes is not None else None, marketType=market_type,
            shape=shape, **_draft_async(async_)))

    def get_draft_job(self, job_id: str) -> DraftJobResponse:
        """A drafting call started with ``async_=True``: ``running``, ``done`` (``job["result"]`` is the call's own
        answer: a draft or a refusal), ``failed`` (``job["error"]``) or ``cancelled``. Key or session.

        ``GET /api/v1/markets/drafts/{jobId}``

        The API holds the request until the job ends or about 15 s pass, so ask again at once while it runs. Only the
        wallet that started the job can read it, and checking costs no drafting allowance (60 checks a minute per
        wallet, 429 ``rate_limited_poll``). 404 ``job_not_found``: an unknown id, another wallet's, one that ended more
        than 10 minutes ago, or one lost in a writer restart.
        """
        return self._draft_job_request("GET", job_id)

    def cancel_draft_job(self, job_id: str) -> DraftJobResponse:
        """Stop a background draft. Idempotent: an ended job answers with its state. A cancelled job counts as one
        drafting call and starts the 30 s cooldown, like a finished one. Key or session.

        ``DELETE /api/v1/markets/drafts/{jobId}``
        """
        return self._draft_job_request("DELETE", job_id)

    def run_draft_job(self, kind: DraftKind, timeout_ms: Optional[float] = None,
                      poll_interval_ms: Optional[float] = None, **params: Any) -> MarketDraftResult:
        """Runs a drafting call in the background and waits for its answer. Key or session.

        ``kind``: ``draft`` | ``headline`` | ``answers`` | ``describe`` (:meth:`draft_market`,
        :meth:`draft_market_headline`, :meth:`draft_market_answers`, :meth:`draft_market_description`); ``params``:
        that method's keyword arguments (``run_draft_job("draft", topic="...", market_type="public")``).

        Starts the call with ``async_=True``, then asks :meth:`get_draft_job` until the job ends, never sooner than the
        API's ``pollAfterMs`` and never more often than ``poll_interval_ms`` (default 1500; each check is held open by
        the API for up to about 15 s). ``timeout_ms``: give up after this long and cancel the job (default: the client's
        ``api_ai_timeout_ms``, 330 s; a draft runs up to about 3 minutes). Returns what the call returns without
        ``async_``: a draft, or a refusal (``ok: False``). An API without background drafts answers the start at once:
        that answer is returned. Asked again inside the deadline: a busy writer (429 ``gate_busy``) after its
        ``retryAfterMs``; the wallet's cooldown from its last drafting call (429 ``cooldown``, up to 30 s) once it has
        run; a start that got no answer (a network error, its own deadline) after a short pause, since it may have
        started a job: when the API then answers 429 ``job_running``, that job (its ``jobId``, of the same kind) is the
        one followed. A check that fails for a passing reason (429, 5xx, no answer) is asked again inside the deadline.
        Each start and each check is bounded by what is left of ``timeout_ms`` (at least 1 s), and a start is not asked
        again with less than 5 s left after its wait.

        Raises BasisApiError: whatever the start refuses (a cooldown longer than 30 s or past the deadline, the other
        limits, ``job_running`` for another job of this wallet, bad input, 503 ``drafting_unavailable``); a start with
        no answer until the deadline raises its own error; status 0 ``timeout`` when the job still runs after
        ``timeout_ms`` (it is cancelled first); ``drafting_failed`` (the job failed: ``job["error"]`` in the message, the job as ``body``);
        ``draft_cancelled`` (cancelled by :meth:`cancel_draft_job`, maybe from another client of this wallet);
        ``job_not_found`` (the job is gone). TS: ``runDraftJob(kind, params, { timeoutMs, pollIntervalMs })``.
        """
        method = _DRAFT_METHODS.get(kind) if isinstance(kind, str) else None
        if method is None:
            raise ValidationError("kind", f"kind must be draft, headline, answers or describe, got {kind!r}.")
        total = (check_timeout_ms(timeout_ms, "timeoutMs") if timeout_ms is not None
                 else (getattr(self.client, "api_ai_timeout_ms", None) or DEFAULT_API_AI_TIMEOUT_MS))
        min_gap = _DRAFT_POLL_MS if poll_interval_ms is None else poll_interval_ms
        if isinstance(min_gap, bool) or not isinstance(min_gap, (int, float)) or not (0 <= min_gap <= MAX_TIMEOUT_MS):
            raise ValidationError("pollIntervalMs", f"pollIntervalMs must be a number of ms from 0 to {MAX_TIMEOUT_MS}, "
                                                    f"got {min_gap!r}.")
        what = f"run_draft_job({kind})"
        params.pop("async_", None)

        def now_ms() -> float:
            return time.perf_counter() * 1000.0

        deadline = now_ms() + total

        lost = False
        adopted: Optional[BasisApiError] = None
        start: Any = None
        while True:
            try:
                bound = max(1_000.0, min(self._timeout_for("ai"), deadline - now_ms()))
                start = getattr(self.with_options(timeout_ms=bound), method)(**params, async_=True)
                break
            except (BasisApiError, requests.RequestException) as e:
                job_id = e.body.get("jobId") if isinstance(e, BasisApiError) and isinstance(e.body, dict) else None
                if _no_answer(e):
                    lost = True
                    wait = float(_DRAFT_NO_ANSWER_RETRY_MS)
                elif not isinstance(e, BasisApiError):
                    raise
                elif (e.status == 429 and e.code == "job_running" and lost and isinstance(job_id, str)
                      and _DRAFT_JOB_ID.fullmatch(job_id)):
                    adopted = e
                    break
                elif e.status == 429 and e.code == "gate_busy":
                    wait = _draft_retry_ms(e, _DRAFT_BUSY_RETRY_MS)
                elif e.status == 429 and e.code == "cooldown":
                    wait = _draft_retry_ms(e, _DRAFT_COOLDOWN_MAX_MS)
                    if wait > _DRAFT_COOLDOWN_MAX_MS:
                        raise
                else:
                    raise
                if now_ms() + wait + _DRAFT_START_MIN_LEFT_MS >= deadline:
                    raise
                time.sleep(wait / 1000.0)

        if adopted is not None:
            job: Dict[str, Any] = {"id": adopted.body["jobId"], "kind": kind, "status": "running", "elapsedMs": 0}
            gap = float(min_gap)
            nxt = now_ms()
        else:
            if not isinstance(start, dict) or not _is_draft_job(start.get("job")):
                return start
            job = start["job"]
            hint = start.get("pollAfterMs")
            first = (min(float(hint), MAX_TIMEOUT_MS) if isinstance(hint, (int, float)) and not isinstance(hint, bool)
                     and math.isfinite(hint) and hint > 0 else 0.0)
            gap = max(float(min_gap), first)
            nxt = now_ms() + first
        while job.get("status") == "running":
            if nxt >= deadline:
                self._cancel_draft_quietly(job["id"])
                raise BasisApiError(0, "timeout", f"{what}: the AI draft was still running after {total:g} ms, so it "
                                    "was cancelled. Start it again with a longer timeout_ms, or split draft_market into "
                                    "draft_market_headline + draft_market_answers.", job)
            pause = nxt - now_ms()
            if pause > 0:
                time.sleep(pause / 1000.0)
            asked = now_ms()
            nxt = asked + gap
            try:
                bound = max(1_000.0, min(self._timeout_for("default"), deadline - asked))
                r = self.with_options(timeout_ms=bound)._draft_job_request("GET", job["id"])
                if not isinstance(r, dict) or not _is_draft_job(r.get("job")):
                    raise BasisApiError(200, None, f"{what}: the job check answered no job", r)
                job = r["job"]
            except (BasisApiError, requests.RequestException) as e:
                if _no_answer(e):
                    nxt = max(nxt, now_ms() + max(gap, float(_DRAFT_NO_ANSWER_RETRY_MS)))
                    continue
                if not isinstance(e, BasisApiError):
                    raise
                passing = e.status == 429 or (e.status >= 500 and e.code != "gate_disabled")
                if not passing:
                    raise
                nxt = max(nxt, now_ms() + _draft_retry_ms(e, gap))
            if adopted is not None and job.get("kind") != kind:
                raise adopted

        status = job.get("status")
        if status == "done":
            if isinstance(job.get("result"), dict):
                return job["result"]
            raise BasisApiError(200, None, f"{what}: the finished job carries no result", job)
        if status == "cancelled":
            raise BasisApiError(200, "draft_cancelled", f"{what}: the AI draft job was cancelled (cancel_draft_job, "
                                "maybe from another client of this wallet).", job)
        ended = "failed" if status == "failed" else f"ended as {status}"
        raise BasisApiError(200, "drafting_failed", f"{what}: the AI draft {ended} "
                            f"({job.get('error') or 'no reason given'}). Start it again; draft_market_headline + "
                            "draft_market_answers are two shorter calls than one draft_market.", job)

    def _draft_call(self, path: str, body: Dict[str, Any]) -> Any:
        """One drafting call; its errors get a hint."""
        try:
            return self._request("POST", path, auth=_AUTH, kind="ai", body=body)
        except BasisApiError as e:
            raise _with_draft_hint(e) from None

    def _draft_job_request(self, method: str, job_id: str, timeout_ms: Optional[float] = None) -> DraftJobResponse:
        """GET or DELETE /api/v1/markets/drafts/{jobId}; ``timeout_ms`` bounds this one request."""
        jid = _check_draft_job_id(job_id)
        try:
            return self._request(method, f"/api/v1/markets/drafts/{seg(jid)}", auth=_AUTH, timeout_ms=timeout_ms)
        except BasisApiError as e:
            hint = _NO_DRAFT_JOBS_HINT if e.status == 404 and e.code != "job_not_found" else None
            raise _with_draft_hint(e, hint) from None

    def _cancel_draft_quietly(self, job_id: str) -> None:
        """Cancels a job, ignoring any error: best effort (the job ends on its own at the writer's budget)."""
        try:
            self._draft_job_request("DELETE", job_id)
        except Exception:  # noqa: BLE001
            pass

    def approve_market(
        self,
        market_type: MarketType,
        name: str,
        symbol: str,
        outcomes: List[str],
        description: Optional[str] = None,
        main: Optional[str] = None,
        seed_amount: Optional[int] = None,
        private_event: Optional[bool] = None,
        end_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Ask BASIS to approve a market for the authenticated wallet. Key or session.

        ``POST /api/v1/markets/approve``

        ``market_type`` is ``'public'`` or ``'private'``. ``main`` (optional) lets the route check the seed rule;
        ``seed_amount`` (raw pair-asset units) and ``private_event`` are sent only with ``main``. When given, they are
        checked before any request either way: ``seed_amount`` a whole number (``ValidationError("seedAmount")``; a
        float above 2^53 has lost precision and is refused: pass an int), ``private_event`` True or False
        (``ValidationError("privateEvent")``). None is absent.

        ``end_time`` (unix seconds, 0 = open-ended, the default) is always sent: the route requires it and signs it into
        ``content_hash``, so it must equal the ``end_time`` given to ``create_market``. The answer's ``signed`` carries it.
        It must be a whole number (``ValidationError("endTime")`` otherwise), and ``outcomes`` a list
        (``ValidationError("outcomes")``), both before any request.

        Approved: ``{'approved': True, 'decision': 'approve', 'market_type',
        'signed': {'name','symbol','outcomes','endTime'},
        'signature', 'deadline' (int, unix seconds), 'content_hash', 'verifying_contract', 'signer', 'chain_id',
        'host', 'escalated', 'model'}``; pass ``signed``, ``deadline`` and ``signature`` unchanged to ``create_market``
        on ``verifying_contract`` (= ``host``), from this wallet. A rejection (HTTP 200) is returned as
        ``{'approved': False, 'decision': 'reject', 'category', 'reason', 'escalated', 'model', 'cached'}``. A route
        error raises BasisApiError with its ``code`` (``bad_body``, ``seed_invalid``, ``rate_limited_wallet``,
        ``daily_wallet_cap``, ``gate_busy``, ``gate_disabled``, ``signer_unset``, ...).
        503 ``screening_slow``: the review is still running and caches its verdict, so send the same market again after
        ``retry_after`` seconds (``retryAfterMs`` in the body). This wrapper does not retry.
        Each approval is single-use; rate limits apply per wallet and per day.

        ``name``, ``symbol`` and every outcome are signed into ``content_hash``, so each must be well-formed text (it
        encodes as UTF-8: no lone surrogate): ``ValidationError("name" / "symbol" / "outcomes")`` before any request.
        """
        checked_outcomes = _check_outcomes(outcomes)
        check_text(name, "name")
        check_text(symbol, "symbol")
        for o in checked_outcomes:
            check_text(o, "outcomes")
        seed = None if seed_amount is None else to_uint(seed_amount, "seedAmount")
        private = None if private_event is None else to_bool(private_event, "privateEvent")
        body: Dict[str, Any] = {"marketType": market_type, "name": name, "symbol": symbol,
                                "outcomes": checked_outcomes, "endTime": _end_time(end_time)}
        if description is not None:
            body["description"] = description
        if main:
            body["main"] = main
            if seed is not None:
                body["seedAmount"] = str(seed)
            if private is not None:
                body["privateEvent"] = private

        data = self._request("POST", "/api/v1/markets/approve", auth=_AUTH, body=body, kind="ai")
        if not isinstance(data, dict):
            raise BasisApiError(200, None, "POST /api/v1/markets/approve failed [200]: the answer is not a JSON object",
                                data)
        if data.get("approved") is not True:
            return {
                "approved": False,
                "decision": "reject",
                "category": data.get("category"),
                "reason": data.get("reason"),
                "escalated": data.get("escalated"),
                "model": data.get("model"),
                "cached": data.get("cached"),
            }
        signed = data.get("signed") or {}
        chain_id = data.get("chainId")
        return {
            "approved": True,
            "decision": "approve",
            "market_type": data.get("marketType") or market_type,
            "signed": {
                "name": str(signed.get("name")),
                "symbol": str(signed.get("symbol")),
                "outcomes": [str(o) for o in (signed.get("outcomes") or [])],
                **({"endTime": signed["endTime"]} if signed.get("endTime") is not None else {}),
            },
            "signature": data.get("signature"),
            "deadline": int(data.get("deadline")),
            "content_hash": data.get("contentHash"),
            "verifying_contract": data.get("verifyingContract"),
            "signer": data.get("moderator"),
            "chain_id": chain_id if isinstance(chain_id, int) and not isinstance(chain_id, bool) else None,
            "host": data.get("host"),
            "escalated": data.get("escalated"),
            "model": data.get("model"),
        }
